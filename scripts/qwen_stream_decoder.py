"""Isolated MLX prompt-prefix adapter and exact-audio encoder cache.
Never reuse transformer KV state or encoder features for growing/changed audio.
"""
import hashlib

from app.languages import UnsupportedAutoLanguage

class PrefixHistory:
    def __init__(self):
        self.entries={}
    def choose(self,tokenizer,request,audio_bytes,context,rollback=8):
        request=request or {};previous=self.entries.get(request.get('key'))
        if not request.get('enabled') or request.get('confirmed',True) or not previous:return []
        if previous['context']!=context or previous['rounds']<2:return []
        if len(audio_bytes)<=len(previous['audio']) or not audio_bytes.startswith(previous['audio']):return []
        stable=request.get('stable_text','');text=previous['text']
        if not stable or not text.startswith(stable) or request.get('previous_text')!=text:return []
        ids=tokenizer.encode(text,add_special_tokens=False)
        # Roll back complete tokens until the retained text lies inside the agreed prefix.
        for count in range(len(ids)-rollback,3,-1):
            prefix=ids[:count];decoded=tokenizer.decode(prefix,skip_special_tokens=True)
            if decoded and '\ufffd' not in decoded and '<|' not in decoded and stable.startswith(decoded):return prefix
        return []
    def update(self,request,audio_bytes,context,text):
        request=request or {};key=request.get('key')
        if not key:self.clear();return
        previous=self.entries.pop(key,None)
        if request.get('confirmed',True):return
        growing=previous and previous['context']==context and len(audio_bytes)>len(previous['audio']) and audio_bytes.startswith(previous['audio'])
        self.entries[key]={'audio':audio_bytes,'context':context,'text':text,'rounds':previous['rounds']+1 if growing else 1}
        if len(self.entries)>2:self.entries.pop(next(iter(self.entries)))
    def clear(self):
        self.entries.clear()

class ExactAudioCache:
    def __init__(self,maximum_bytes=64*1024*1024):
        self.maximum_bytes=maximum_bytes;self.key=None;self.value=None
    def get(self,key):return self.value if self.key==key else None
    def put(self,key,value,size):
        self.clear()
        if size<=self.maximum_bytes:self.key=key;self.value=value
    def clear(self):self.key=None;self.value=None

def resolve_generation_model(model):
    return getattr(model.stream_generate,'__self__',model)

class MLXStreamDecoder:
    def __init__(self):
        self.cache=ExactAudioCache();self.enabled=True;self.metrics={}
    def clear(self):self.cache.clear()
    def generate(self,model,samples,*,prefix_tokens=(),max_tokens=4096,sampler=None,language='Chinese',system_prompt=None,**kwargs):
        import mlx.core as mx
        from mlx_audio.lm.generate import generate_step
        # mlx-audio exposes a proxy Model; generation must receive the bound ASR module.
        model=resolve_generation_model(model)
        required=('_preprocess_audio','_build_prompt','get_audio_features','_build_inputs_embeds','_eos_token_ids')
        if not self.enabled or not all(callable(getattr(model,name,None)) for name in required):raise RuntimeError('当前 MLX 模型接口不支持前缀适配')
        raw=samples.tobytes();key=(id(model),str(samples.dtype),tuple(samples.shape),hashlib.sha256(raw).digest())
        cached=self.cache.get(key);hit=cached is not None
        if cached is None:
            features,attention,tokens=model._preprocess_audio(samples)
            audio_features=model.get_audio_features(features,attention);mx.eval(audio_features)
            self.cache.put(key,(audio_features,tokens),audio_features.nbytes)
            del features,attention
        else:audio_features,tokens=cached
        input_ids=model._build_prompt(tokens,language,system_prompt)
        if prefix_tokens:
            prefix=mx.array(list(prefix_tokens),dtype=input_ids.dtype).reshape(1,-1)
            input_ids=mx.concatenate([input_ids,prefix],axis=1)
        embeds=model._build_inputs_embeds(input_ids,audio_features);mx.eval(embeds)
        self.metrics={'feature_cache_hit':hit,'feature_cache_bytes':self.cache.value[0].nbytes if self.cache.value else 0,
                      'kv_cache_reused':False,'prefix_tokens':len(prefix_tokens)}
        eos=model._eos_token_ids()
        # Every call builds a fresh KV state. Audio token counts and representations can change.
        for token,probs in generate_step(prompt=input_ids[0],input_embeddings=embeds[0],model=model,max_tokens=max_tokens,
                                        sampler=sampler,prefill_step_size=2048):
            if int(token) in eos:break
            yield token,probs

def decode_with_fallback(decode,decoder,prefix):
    """decode(adapter, prefix_ids) may emit rewritable partials; only return a valid final."""
    if not decoder.enabled:
        text,attempts=decode(None,[])
        return text,attempts,[],'adapter_disabled'
    try:
        text,attempts=decode(decoder,prefix)
        if prefix and (not text or attempts[0]['generation_tokens']==0):raise RuntimeError('前缀续写未产生有效尾部')
        return text,attempts,prefix,''
    except UnsupportedAutoLanguage:
        raise
    except Exception as error:
        reason=type(error).__name__;decoder.clear()
    if prefix:
        try:
            text,attempts=decode(decoder,[])
            return text,attempts,[],reason
        except Exception:decoder.clear()
    # An incompatible private MLX API is disabled for this model process.
    decoder.enabled=False
    text,attempts=decode(None,[])
    return text,attempts,[],reason
