from scripts.qwen_stream_decoder import PrefixHistory,ExactAudioCache

class Tokenizer:
    def encode(self,text,**kwargs):return list(text.encode('utf-8'))
    def decode(self,tokens,**kwargs):return bytes(tokens).decode('utf-8',errors='replace')

def request(**values):return {'key':'recording-a:0','enabled':True,'confirmed':False,'previous_text':'','stable_text':'',**values}

def prepared():
    history=PrefixHistory();text='我们正在讨论听记产品的下一步功能和交付安排。'
    history.update(request(),b'abc','context',text)
    history.update(request(),b'abcdef','context',text)
    return history,text

def test_prefix_requires_two_rounds_stability_and_rolls_back_whole_unicode_tokens():
    history,text=prepared();tokenizer=Tokenizer()
    ids=history.choose(tokenizer,request(previous_text=text,stable_text=text[:-6]),b'abcdefghi','context')
    prefix=tokenizer.decode(ids)
    assert ids and '\ufffd' not in prefix and text[:-6].startswith(prefix)
    assert len(tokenizer.encode(text))-len(ids)>=8

def test_final_changed_audio_context_session_or_disabled_option_never_reuses_prefix():
    history,text=prepared();tokenizer=Tokenizer();base=request(previous_text=text,stable_text=text[:-6])
    for req,audio,context in [(base|{'confirmed':True},b'abcdefghi','context'),
        (base|{'enabled':False},b'abcdefghi','context'),(base|{'key':'other:0'},b'abcdefghi','context'),
        (base,b'abcXXXghi','context'),(base,b'abcdef','context'),(base,b'abcdefghi','changed'),
        (base|{'previous_text':'stale text'},b'abcdefghi','context')]:
        assert history.choose(tokenizer,req,audio,context)==[]
    history.update(base|{'confirmed':True},b'abcdefghi','context',text)
    assert history.last is None

def test_process_restart_or_changed_waveform_resets_prefix_warmup():
    history,text=prepared();history.clear()
    assert history.choose(Tokenizer(),request(previous_text=text,stable_text=text[:-6]),b'abcdefghi','context')==[]
    history.update(request(),b'first','context',text)
    history.update(request(),b'changed','context',text)
    assert history.last['rounds']==1

def test_exact_cache_never_returns_different_audio_and_has_one_bounded_entry():
    cache=ExactAudioCache(maximum_bytes=10)
    cache.put('exact-audio-a',('features',3),8)
    assert cache.get('exact-audio-a')==('features',3)
    assert cache.get('growing-audio-a') is None
    cache.put('different-audio',('new',4),9)
    assert cache.get('exact-audio-a') is None
    cache.put('oversized',('huge',4),11)
    assert cache.value is None

def test_failed_continuation_rewinds_to_full_decode_without_disabling_compatible_adapter():
    from scripts.qwen_stream_decoder import decode_with_fallback
    class Adapter:
        enabled=True
        def clear(self):pass
    adapter=Adapter();calls=[]
    def decode(engine,prefix):
        calls.append((engine,prefix))
        if prefix:raise RuntimeError('合成续写失败')
        return '完整音频校验结果',[{'generation_tokens':12}]
    text,attempts,prefix,reason=decode_with_fallback(decode,adapter,[1,2,3,4])
    assert text=='完整音频校验结果' and not prefix and reason=='RuntimeError'
    assert len(calls)==2 and adapter.enabled

def test_incompatible_mlx_api_uses_standard_path_and_does_not_retry_bad_adapter():
    from scripts.qwen_stream_decoder import decode_with_fallback
    class Adapter:
        enabled=True
        def clear(self):pass
    adapter=Adapter();calls=[]
    def decode(engine,prefix):
        calls.append(engine)
        if engine:raise TypeError('不兼容的私有接口')
        return '原始流式结果',[{'generation_tokens':7}]
    assert decode_with_fallback(decode,adapter,[])[0]=='原始流式结果'
    assert not adapter.enabled and calls==[adapter,None]
    assert decode_with_fallback(decode,adapter,[])[-1]=='adapter_disabled'
    assert calls==[adapter,None,None]

def test_empty_continuation_and_failure_in_all_paths_never_return_success():
    import pytest
    from scripts.qwen_stream_decoder import decode_with_fallback
    class Adapter:
        enabled=True
        def clear(self):pass
    def empty(engine,prefix):return ('prefix' if prefix else 'full'),[{'generation_tokens':0 if prefix else 4}]
    assert decode_with_fallback(empty,Adapter(),[1,2,3,4])[0]=='full'
    def failing(*_):raise RuntimeError('模型异常')
    with pytest.raises(RuntimeError):decode_with_fallback(failing,Adapter(),[1,2,3,4])

def test_mlx_proxy_resolves_the_bound_model_with_an_explicit_forward_signature():
    from scripts.qwen_stream_decoder import resolve_generation_model
    class Core:
        def stream_generate(self,*args,**kwargs):pass
    class Proxy:
        def __init__(self):self.core=Core()
        def __getattr__(self,name):return getattr(self.core,name)
    proxy=Proxy()
    assert resolve_generation_model(proxy) is proxy.core
