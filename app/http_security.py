"""Local-only HTTP boundary shared by every router and static response."""
from urllib.parse import urlparse
from fastapi import Request
from fastapi.responses import JSONResponse
from .host_bridge import workspace_binding

def register_local_only(app):
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
        result=await call_next(request)
        if binding is not None:result.headers['Permissions-Policy']='camera=(), microphone=(), display-capture=(), geolocation=()'
        result.headers['X-Content-Type-Options']='nosniff'
        result.headers['Referrer-Policy']='no-referrer'
        return result
