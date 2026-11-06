"""E3 synthetic host connection surface. No database, recording, models or service manager.
Run only for native UI acceptance; these controls are never registered in production.
"""
import argparse,os
from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse
from app.paths import RuntimePaths
from app.host_bridge import identity
from app.http_security import register_local_only


def create_fixture(data,port=9876):
    app=FastAPI();register_local_only(app)
    paths=RuntimePaths.from_env(data=data);app.state.paths=paths;state={'mode':'ready','writes':0}
    @app.get('/api/runtime/host')
    def connection():
        value=identity(app.state.paths)
        if state['mode']=='mismatch':value['data_fingerprint']='0'*64
        if state['mode']=='incompatible':value['protocol_version']=999
        return value
    @app.get('/',response_class=HTMLResponse)
    def workspace():return """<html lang="zh"><meta charset="utf-8"><title>听记 E3 合成工作区</title>
    <h1>内嵌工作区合成验收</h1><p>没有真实会议、录音、模型或服务控制。</p>
    <label>未保存草稿 <input aria-label="未保存草稿" id="draft"></label>
    <p><a href="/local">本机页面</a> <a href="https://example.com/">外部导航测试</a>
    <a href="/api/meetings/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/minutes/export/pdf?inline=true" target="_blank">独立 PDF 预览</a></p>
    <p><a download href="/export.txt">保存合成文字稿</a> <a download href="/redirect">下载跳转测试</a></p>
    <button onclick="fetch('/write',{method:'POST'}).then(r=>document.getElementById('result').textContent='写入状态 '+r.status)">合成写入测试</button>
    <button onclick="document.getElementById('result').textContent=window.__TINGJI_HOST__?.web_capture===false?'采集权限已禁用':'普通浏览器'">检查权限提示</button>
    <p id="result" role="status"></p></html>"""
    @app.get('/local',response_class=HTMLResponse)
    def local():return '<meta charset="utf-8"><h1>本机导航成功</h1><a href="/">返回工作区</a>'
    @app.post('/write')
    def write():state['writes']+=1;return {'synthetic':True}
    @app.get('/_fixture/status')
    def status():return {'synthetic':True,'writes':state['writes'],'mode':state['mode']}
    @app.get('/export.txt')
    def export():
        from fastapi.responses import Response
        return Response('E3 synthetic meeting export',media_type='text/plain',headers={'Content-Disposition':'attachment; filename="synthetic-meeting.txt"'})
    @app.get('/redirect')
    def redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse('https://example.com/')
    @app.get('/api/meetings/{mid}/minutes/export/pdf')
    def pdf(mid:str):
        import io
        from reportlab.pdfgen import canvas
        from fastapi.responses import Response
        output=io.BytesIO();page=canvas.Canvas(output);page.drawString(72,740,'E3 synthetic PDF preview');page.save()
        return Response(output.getvalue(),media_type='application/pdf')
    @app.post('/_fixture/host/{mode}')
    def change(mode:str):
        if mode not in ('ready','mismatch','incompatible'):raise HTTPException(400,'未知合成状态')
        app.state.paths=paths if mode!='mismatch' else RuntimePaths(paths.repository,paths.data.parent/(paths.data.name+'-changed'),paths.backups)
        state['mode']=mode;return {'synthetic':True,'mode':mode}
    return app


def main():
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data',type=Path,required=True);parser.add_argument('--port',type=int,default=9876)
    args=parser.parse_args();print('E3 SYNTHETIC HOST ONLY: no capture, models or service control',flush=True)
    uvicorn.run(create_fixture(args.data,args.port),host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
