"""Send an authenticated request to a configured OpenAI-compatible server."""
import argparse,json,os,urllib.error,urllib.request
from pathlib import Path
from urllib.parse import urlparse

def request_once(url,headers,payload,timeout=600):
    request=urllib.request.Request(url,data=json.dumps(payload).encode(),headers=headers,method='POST')
    with urllib.request.urlopen(request,timeout=timeout) as response:return json.load(response)

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

def extract_json(payload):
    errors=[]
    for _,content in message_candidates(payload):
        if content.startswith('```'):
            content=content.split('\n',1)[1].rsplit('```',1)[0].strip()
        try:return json.loads(content)
        except json.JSONDecodeError as original:
            errors.append(original)
            # Some reasoning models add prose or thinking before an otherwise valid object.
            decoder=json.JSONDecoder()
            for index,char in enumerate(content):
                if char!='{':continue
                try:value,end=decoder.raw_decode(content,index)
                except json.JSONDecodeError:continue
                if not content[end:].strip().strip('`'):return value
    if errors:raise errors[0]
    raise json.JSONDecodeError('模型返回内容为空','',0)

def validate_shape(result,schema_name='meeting_analysis'):
    if schema_name=='ai_meeting_minutes':
        if not isinstance(result,dict) or not isinstance(result.get('basic_info'),dict):
            raise ValueError('缺少会议基本信息')
        for name in ('core_content','action_items','summary'):
            if not isinstance(result.get(name),list):raise ValueError('缺少必需的会议纪要栏目')
            for item in result[name]:
                if not isinstance(item,dict) or not isinstance(item.get('content'),str) or not item['content'].strip():raise ValueError('纪要条目缺少 content')
                if not isinstance(item.get('source_segment_ids'),list) or not item['source_segment_ids']:raise ValueError('纪要条目缺少原文引用')
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

def complete_json(url,headers,payload,attempts=3):
    current=dict(payload);diagnostics=[]
    schema_name=payload.get('response_format',{}).get('json_schema',{}).get('name','meeting_analysis')
    for attempt in range(attempts):
        response=request_compatible(url,headers,current)
        choice=(response.get('choices') or [{}])[0]
        candidates=message_candidates(response)
        try:return validate_shape(extract_json(response),schema_name)
        except (json.JSONDecodeError,ValueError) as error:
            diagnostics.append({'attempt':attempt+1,'finish_reason':choice.get('finish_reason'),
                'fields':{key:len(value) for key,value in candidates},'error':type(error).__name__})
            if attempt+1>=attempts:break
            current=dict(current);current['temperature']=0;current['max_tokens']=min(16384,max(8192,int(current.get('max_tokens',4096))*2))
            response_type=current.get('response_format',{}).get('type')
            if response_type=='json_schema':current['response_format']={'type':'json_object'}
            elif response_type=='json_object':current.pop('response_format',None)
            current['messages']=[*current.get('messages',[]),{'role':'user','content':'/no_think\n上一份输出不是完整、有效的 JSON。请重新生成更短的完整 JSON；不要解释，不要使用 Markdown。'}]
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
    try:result=complete_json(args.base_url.rstrip('/')+'/chat/completions',headers,payload)
    except urllib.error.HTTPError as error:
        if error.code in (401,403):raise RuntimeError('模型服务 API Key 无效') from error
        raise
    output=Path(args.output);temporary=output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2));temporary.replace(output)
    print(json.dumps({'model':payload['model'],'sections':len(result)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
