"""E1 synthetic host connection surface. No database, recording, models or service manager.
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
    paths=RuntimePaths.from_env(data=data);state={'mode':'ready'}
    @app.get('/api/runtime/host')
    def connection():
        value=identity(paths)
        if state['mode']=='mismatch':value['data_fingerprint']='0'*64
        if state['mode']=='incompatible':value['protocol_version']=999
        return value
    @app.get('/',response_class=HTMLResponse)
    def workspace():return '<html lang="zh"><meta charset="utf-8"><title>听记宿主 · 合成连接验收</title><h1>合成工作台已打开</h1><p>仅验证本机宿主连接；没有录音、模型或真实会议数据。</p></html>'
    @app.post('/_fixture/host/{mode}')
    def change(mode:str):
        if mode not in ('ready','mismatch','incompatible'):raise HTTPException(400,'未知合成状态')
        state['mode']=mode;return {'synthetic':True,'mode':mode}
    return app


def main():
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data',type=Path,required=True);parser.add_argument('--port',type=int,default=9876)
    args=parser.parse_args();print('E1 SYNTHETIC HOST ONLY: no capture, models or service control',flush=True)
    uvicorn.run(create_fixture(args.data,args.port),host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
