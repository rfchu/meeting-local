"""Local-only HTTP boundary shared by every router and static response."""
import hmac
import os
from urllib.parse import urlparse
from fastapi import Request
from fastapi.responses import JSONResponse
from .host_bridge import read_workspace_key, workspace_binding, workspace_mac

def register_local_only(app):
    key = read_workspace_key(app.state.paths) if os.getenv('TINGJI_PORTABLE_SERVICE') == '1' else None
    app.state.workspace_key = key
    authorization = workspace_mac(key, 'request:' + workspace_binding(app.state.paths)) if key else None
    @app.middleware('http')
    async def local_only(request:Request,call_next):
        host=request.headers.get('host','').split(':')[0]
        if host not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'detail':'只允许本机访问'},status_code=403)
        origin=request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail':'不允许跨站请求'},status_code=403)
        binding=request.cookies.get('tingji_workspace_binding')
        # Optional for legacy browsers; every embedded request carries an HttpOnly binding cookie.
        if binding is not None and binding!=workspace_binding(app.state.paths):
            return JSONResponse({'detail':{'code':'workspace_mismatch','message':'工作空间已变化，请在宿主重新核对连接。'}},status_code=409)
        if authorization and not (request.method == 'GET' and request.url.path in ('/api/runtime/host', '/api/runtime/service')):
            supplied = request.headers.get('x-tingji-authorization') or request.cookies.get('tingji_workspace_authorization', '')
            if not supplied.isascii() or not hmac.compare_digest(supplied, authorization):
                return JSONResponse({'detail':'请从听记工作台重新连接'},status_code=403)
        result=await call_next(request)
        if binding is not None:result.headers['Permissions-Policy']='camera=(), microphone=(), display-capture=(), geolocation=()'
        result.headers['X-Content-Type-Options']='nosniff'
        result.headers['Referrer-Policy']='no-referrer'
        return result
