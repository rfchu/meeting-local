"""Synthetic archived overview acceptance; no capture, inference or user data.
PYTHONPATH=. .venv/bin/python scripts/overview_quality_ui_fixture.py --port 9876
"""
import argparse,io,tempfile,wave
import uvicorn
import app.main as module
from app.main import create_app
from app.db import uid
from app.recordings import RecordingStore
from app.live_results import LiveResults
from app.live_overview import EnableInput,ReviewInput


def seed(app):
    store=app.state.store;recordings=RecordingStore(store);cache=LiveResults(store,recordings);service=app.state.live_overview
    session=recordings.create('audio/webm');rid=session['id'];pid=uid()
    store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model) VALUES(?,?,?,?,?)',(pid,'合成概览验收','local','http://127.0.0.1:1234/v1','synthetic-only'))
    service.enable(rid,EnableInput(profile_id=pid))
    first=['首版保留录音和文字校对。','导出采用统一版式。','移动端先适配浏览器。','离线模型按需安装。','暂不接入实时翻译。','跨会议问答继续验证。']
    first+=['大家同意下周三上线。','陈明负责周五交付预览稿。','发布检查清单尚待确认。']
    second=['需要验证长会议内存占用。','重连后核对录音覆盖范围。','重点检查音频尾段是否保存。','模型耗时与录音保存分别显示。']
    def append(text,start):
        aid,_=cache.begin(rid,'browser','qwen',start,30,text.encode(),{})
        cache.complete(aid,[{'text':text,'start':start,'end':start+30}]);return aid+':0'
    sid=append(''.join(first),0);recordings.metadata(rid,[],30,1)
    def candidate(kind,text,sid):return {'id':None,'kind':kind,'content':text,'evidence':[{'segment_id':sid,'quote':text}],'note_ids':[]}
    def publish(items):
        run=store.one('SELECT * FROM live_overview_runs WHERE recording_id=?',(rid,))
        service.publish(service.prepare(rid,run,True),{'items':items})
    publish([candidate('point',text,sid) for text in first[:6]]+[candidate(kind,text,sid) for kind,text in zip(('decision','action','question'),first[6:])])
    pinned=next(i for i in service.read(rid)['items'] if i['content']==first[0])
    service.decide(rid,pinned['id'],ReviewInput(state='confirmed',fingerprint=pinned['fingerprint'],version=pinned['review_version']))
    sid2=append(''.join(second),30);recordings.metadata(rid,[],60,1)
    publish([candidate('point',text,sid2) for text in second]+[candidate('point',second[0].rstrip('。'),sid2)])
    audio=io.BytesIO()
    with wave.open(audio,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*960000)
    recordings.chunk(rid,0,audio.getvalue());recordings.finish(rid,1)
    store.execute('UPDATE meetings SET title=? WHERE id=?',('概览质量验收 · 合成会议',rid))
    service.disable(rid)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=9876);args=parser.parse_args()
    module.NativeCapture.capability=lambda self:{'available':False,'supported':True,'screen_permission':False,'microphone_permission':False}
    module.LMStudioProvider.status=lambda self:{'ready':False,'models':[],'error':'合成环境不连接模型','profiles':[]}
    with tempfile.TemporaryDirectory(prefix='overview-quality-ui-') as root:
        app=create_app(root,start_worker=False);app.state.live_overview.start_monitor=lambda:None
        seed(app)
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=3)

if __name__=='__main__':main()
