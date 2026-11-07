"""Send an authenticated request to a configured OpenAI-compatible server."""
import argparse,json,os,urllib.error,urllib.request
from pathlib import Path
from urllib.parse import urlparse

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('模型接口不允许重定向，请确认配置的最终服务地址')

def request_once(url,headers,payload,timeout=600):
    request=urllib.request.Request(url,data=json.dumps(payload).encode(),headers=headers,method='POST')
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=timeout) as response:return json.load(response)

def message_candidates(payload):
    choices=payload.get('choices') or []
    if not choices:raise RuntimeError('LM Studio 没有返回候选结果')
    message=choices[0].get('message',{})
    result=[]
    for key in ('content','reasoning_content','reasoning'):
        content=message.get(key,'') or ''
        if isinstance(content,list):content=''.join(x.get('text','') for x in content if isinstance(x,dict))
        if isinstance(content,str) and content.strip():result.append((key,content.strip()))
    return result

def extract_json(payload, *, strict=True):
    errors=[]
    for _,content in message_candidates(payload):
        if content.startswith('```'):
            content=content.partition('\n')[2].rsplit('```',1)[0].strip()
        try:return json.loads(content,strict=strict)
        except json.JSONDecodeError as original:
            errors.append(original)
            # Some reasoning models add prose or thinking before an otherwise valid object.
            decoder=json.JSONDecoder(strict=strict)
            for index,char in enumerate(content):
                if char!='{':continue
                try:value,end=decoder.raw_decode(content,index)
                except json.JSONDecodeError:continue
                if not content[end:].strip().strip('`'):return value
    if errors:raise errors[0]
    raise json.JSONDecodeError('模型返回内容为空','',0)

def validate_shape(result,schema_name='meeting_analysis'):
    if schema_name=='meeting_overview':
        if not isinstance(result,dict) or not isinstance(result.get('items'),list):raise ValueError('缺少会中概览条目')
        return result
    if schema_name=='meeting_terms':
        if not isinstance(result,dict) or not isinstance(result.get('terms'),list):raise ValueError('缺少术语候选列表')
        return result
    if schema_name=='retrieval_answer':
        if not isinstance(result,dict) or result.get('status') not in ('answered','insufficient') or not isinstance(result.get('claims'),list):
            raise ValueError('引用问答结构不完整')
        return result
    if schema_name=='speaker_review':
        if not isinstance(result,dict) or not isinstance(result.get('suggestions'),list):
            raise ValueError('缺少发言人建议列表')
        return result
    if schema_name=='interview_minutes':
        basic_info=result.get('basic_info') if isinstance(result,dict) else None
        if not isinstance(basic_info,dict) or not isinstance(basic_info.get('topic'),str) or not basic_info['topic'].strip():
            raise ValueError('面试纪要缺少会议主题（basic_info.topic）')
        interview=result.get('interview')
        if not isinstance(interview,dict):
            raise ValueError('缺少结构化面试纪要（interview）')
        for name in ('background','qa','competencies','concerns','followups','summary'):
            if not isinstance(interview.get(name),list):
                raise ValueError(f'面试纪要缺少数组栏目（interview.{name}）')
        return result
    if schema_name=='ai_meeting_minutes':
        if not isinstance(result,dict) or not isinstance(result.get('basic_info'),dict):
            raise ValueError('缺少会议基本信息')
        for name in ('core_content','action_items','summary'):
            if not isinstance(result.get(name),list):raise ValueError('缺少必需的会议纪要栏目')
            for item in result[name]:
                if not isinstance(item,dict) or not isinstance(item.get('content'),str) or not item['content'].strip():raise ValueError('纪要条目缺少 content')
                if not isinstance(item.get('source_segment_ids'),list) or not item['source_segment_ids']:raise ValueError('纪要条目缺少原文引用')
        return result
    if schema_name=='note_expansion':
        if not isinstance(result,dict) or result.get('status') not in ('supported','insufficient') or not isinstance(result.get('text'),str) or not isinstance(result.get('reason'),str) or not isinstance(result.get('evidence'),list):
            raise ValueError('简记扩写结构不完整')
        return result
    sections=('summary','key_points','decisions','action_items','open_questions')
    if not isinstance(result,dict) or any(not isinstance(result.get(name),list) for name in sections):
        raise ValueError('缺少必需的分析栏目')
    for name in sections:
        for item in result[name]:
            if not isinstance(item,dict) or not isinstance(item.get('content'),str) or not item.get('content').strip():
                raise ValueError('分析条目缺少 content')
            if not isinstance(item.get('source_segment_ids'),list) or not item['source_segment_ids']:
                raise ValueError('分析条目缺少原文引用')
    return result

def request_compatible(url,headers,payload):
    variants=[payload]
    if payload.get('response_format',{}).get('type')=='json_schema':
        simple=dict(payload);simple['response_format']={'type':'json_object'};variants.append(simple)
    plain=dict(payload);plain.pop('response_format',None);variants.append(plain)
    last=None
    for variant in variants:
        try:return request_once(url,headers,variant)
        except urllib.error.HTTPError as error:
            last=error
            if error.code!=400:raise
    raise last

def native_minutes_requester():
    """Native LM Studio can disable reasoning; unsupported servers retain compatibility."""
    supported=True
    def request(url,headers,payload):
        nonlocal supported
        if supported:
            root=url.removesuffix('/chat/completions').removesuffix('/v1')
            system='\n'.join(m['content'] for m in payload['messages'] if m['role']=='system')
            schema=payload.get('response_format',{}).get('json_schema',{}).get('schema')
            if schema:system+='\n只返回满足以下结构的完整 JSON：'+json.dumps(schema,ensure_ascii=False)
            native={'model':payload['model'],'system_prompt':system,
                    'input':'\n'.join(m['content'] for m in payload['messages'] if m['role']!='system'),
                    'temperature':payload.get('temperature',0),'max_output_tokens':payload.get('max_tokens',12288),
                    'reasoning':'off','store':False,'integrations':[]}
            try:response=request_once(root+'/api/v1/chat',headers,native)
            except urllib.error.HTTPError as error:
                if error.code not in (400,404,405,422):raise
                supported=False
            else:
                content='\n'.join(item.get('content','') for item in response.get('output',[]) if item.get('type')=='message')
                return {'choices':[{'finish_reason':'stop','message':{'content':content}}]}
        return request_compatible(url,headers,payload)
    return request


def complete_json(url,headers,payload,attempts=3,requester=None):
    current=dict(payload);diagnostics=[]
    schema_spec=payload.get('response_format',{}).get('json_schema',{})
    schema_name=schema_spec.get('name','meeting_analysis')
    for attempt in range(attempts):
        response=(requester or request_compatible)(url,headers,current)
        choice=(response.get('choices') or [{}])[0]
        candidates=message_candidates(response)
        try:return validate_shape(extract_json(response),schema_name)
        except (json.JSONDecodeError,ValueError) as error:
            diagnostics.append({'attempt':attempt+1,'finish_reason':choice.get('finish_reason'),
                'fields':{key:len(value) for key,value in candidates},
                'error':error.msg if isinstance(error,json.JSONDecodeError) else str(error)})
            if attempt+1>=attempts:break
            current=dict(current);current['temperature']=0;current['max_tokens']=int(payload.get('max_tokens',2048)) if schema_name in ('retrieval_answer','meeting_terms','meeting_overview') else min(16384,max(8192,int(current.get('max_tokens',4096))*2))
            response_type=current.get('response_format',{}).get('type')
            if response_type=='json_schema':current['response_format']={'type':'json_object'}
            elif response_type=='json_object':current.pop('response_format',None)
            correction='/no_think\n上一份输出未通过校验：'+diagnostics[-1]['error']+'。请重新生成完整 JSON；不要解释，不要使用 Markdown。'
            if schema_name=='interview_minutes' and schema_spec.get('schema'):
                correction+='\n请保留面试纪要结构，无依据的栏目返回空数组：'+json.dumps(schema_spec['schema'],ensure_ascii=False)
            current['messages']=[*payload.get('messages',[]),{'role':'user','content':correction}]
    raise RuntimeError('模型连续返回不完整或不符合结构的 JSON：'+json.dumps(diagnostics,ensure_ascii=False))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',required=True);parser.add_argument('--request',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();parsed=urlparse(args.base_url)
    local=parsed.hostname in ('127.0.0.1','localhost','::1')
    if not ((parsed.scheme=='http' and local) or parsed.scheme=='https'):
        raise RuntimeError('模型地址必须是本机 HTTP 或云端 HTTPS')
    token=os.getenv('MEETING_LLM_API_KEY',os.getenv('LM_API_TOKEN',''))
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    payload=json.loads(Path(args.request).read_text())
    requester=native_minutes_requester() if local and os.getenv('MEETING_LMSTUDIO_REASONING_OFF')=='1' else None
    try:result=complete_json(args.base_url.rstrip('/')+'/chat/completions',headers,payload,requester=requester)
    except urllib.error.HTTPError as error:
        if error.code in (401,403):raise RuntimeError('模型服务 API Key 无效') from error
        raise
    output=Path(args.output);temporary=output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2));temporary.replace(output)
    print(json.dumps({'model':payload['model'],'sections':len(result)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
