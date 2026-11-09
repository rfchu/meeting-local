"""Local-only HTTP boundary shared by every router and static response."""
import hmac
import os
import re
import secrets
import time
from urllib.parse import urlparse
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from .host_bridge import read_workspace_key, workspace_binding, workspace_mac

BROWSER_OPEN = '/_tingji/browser'
BROWSER_SESSION = 'tingji_browser_session'
BROWSER_APIS = frozenset({
    'meetings', 'jobs', 'meeting-library', 'followups', 'retrieval',
    'terminology', 'audience-templates', 'minutes-templates', 'model-preferences', 'onboarding', 'status',
})
BROWSER_LANDING = """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>听记 · 浏览器工作台</title><p id="status">正在连接听记…</p>
<script>
const code = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
fetch(location.pathname, {method:"POST", headers:{"X-Tingji-Browser-Code":code}})
  .then(r => {if (!r.ok) throw Error(); location.replace("/");})
  .catch(() => {document.getElementById("status").textContent = "链接已失效，请从听记 App 重新打开浏览器工作台。";});
</script></html>"""


def issue_ticket(pool, lifetime):
    now = time.monotonic()
    for token, expiry in list(pool.items()):
        if expiry <= now:
            del pool[token]
    while len(pool) >= 32:
        del pool[next(iter(pool))]
    token = secrets.token_hex(32)
    pool[token] = now + lifetime
    return token


def register_local_only(app):
    key = read_workspace_key(app.state.paths) if os.getenv('TINGJI_PORTABLE_SERVICE') == '1' else None
    app.state.workspace_key = key
    authorization = workspace_mac(key, 'request:' + workspace_binding(app.state.paths)) if key else None
    tickets = {}
    browser_sessions = {}
    @app.middleware('http')
    async def local_only(request:Request,call_next):
        host=request.headers.get('host','').split(':')[0]
        if host not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'detail':'只允许本机访问'},status_code=403)
        origin=request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail':'不允许跨站请求'},status_code=403)
        path = request.url.path
        native_token = request.headers.get('x-tingji-authorization') or request.cookies.get('tingji_workspace_authorization', '')
        native = bool(authorization and native_token.isascii() and hmac.compare_digest(native_token, authorization))
        browser = bool(authorization and not native and
                       browser_sessions.get(request.cookies.get(BROWSER_SESSION, ''), 0) > time.monotonic())
        if path == '/api/runtime/workspace-mode' and request.method == 'GET':
            return JSONResponse({'mode': 'browser' if browser else 'standard'}, headers={'Cache-Control': 'no-store'})
        if authorization and path == BROWSER_OPEN:
            headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}
            if request.method == 'GET':
                nonce = secrets.token_hex(16)
                headers['Content-Security-Policy'] = f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'"
                return HTMLResponse(BROWSER_LANDING.replace('<script>', f'<script nonce="{nonce}">'), headers=headers)
            if request.method == 'POST' and native and request.headers.get('x-tingji-authorization'):
                return JSONResponse({'code': issue_ticket(tickets, 60)}, headers=headers)
            if request.method == 'POST' and origin == f'http://{request.headers.get("host")}':
                code = request.headers.get('x-tingji-browser-code', '')
                if re.fullmatch(r'[0-9a-f]{64}', code) and tickets.pop(code, 0) > time.monotonic():
                    response = JSONResponse({'connected': True}, headers=headers)
                    response.set_cookie(BROWSER_SESSION, issue_ticket(browser_sessions, 8 * 3600), httponly=True, samesite='strict')
                    return response
            return JSONResponse({'detail': '请从听记 App 重新打开浏览器工作台'}, status_code=403, headers=headers)

        binding=request.cookies.get('tingji_workspace_binding')
        # Optional for legacy browsers; every embedded request carries an HttpOnly binding cookie.
        if binding is not None and binding!=workspace_binding(app.state.paths):
            return JSONResponse({'detail':{'code':'workspace_mismatch','message':'工作空间已变化，请在宿主重新核对连接。'}},status_code=409)
        if authorization and not native and not (request.method == 'GET' and path in ('/api/runtime/host', '/api/runtime/service')):
            if not browser:
                return JSONResponse({'detail':'请从听记工作台重新连接'},status_code=403)
            api_blocked = path.startswith('/api/') and path.split('/')[2] not in BROWSER_APIS
            write_from_other_origin = request.method not in ('GET', 'HEAD') and origin != f'http://{request.headers.get("host")}'
            if api_blocked or write_from_other_origin:
                return JSONResponse({'detail':'此操作请在听记 App 中完成'},status_code=403)
        result=await call_next(request)
        if binding is not None or browser:result.headers['Permissions-Policy']='camera=(), microphone=(), display-capture=(), geolocation=()'
        if browser:
            result.headers['X-Frame-Options'] = 'DENY'
            result.headers['Cache-Control'] = 'no-store'
        result.headers['X-Content-Type-Options']='nosniff'
        result.headers['Referrer-Policy']='no-referrer'
        return result
