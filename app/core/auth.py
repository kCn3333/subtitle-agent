"""Authentication is applied before routing, including API, SSE and downloads."""
import hmac
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.services.auth import COOKIE, client_ip


class AuthMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        config = self.settings

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"),
                ])
                if not request.url.path.startswith("/static/"):
                    headers = [(key, value) for key, value in headers if key.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                if config.auth_cookie_secure:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": headers}
            await send(message)

        async def reject(status, code, message, headers=None):
            response = JSONResponse({"detail": {"code": code, "message": message}}, status_code=status, headers=headers)
            await response(scope, receive, secure_send)

        path = request.url.path
        if path != "/health" and request.headers.get("host", "").lower() != urlsplit(config.auth_public_url).netloc.lower():
            return await reject(400, "INVALID_HOST", "Nieprawidłowy adres aplikacji.")
        public = path in {"/health", "/login", "/api/auth/login"} or path.startswith("/static/")
        if public:
            return await self.app(scope, receive, secure_send)
        store = request.app.state.auth
        try:
            ip = client_ip(request, config.auth_trusted_proxies)
        except ValueError:
            return await reject(400, "INVALID_PROXY", "Nieprawidłowy adres klienta przekazany przez proxy.")
        remaining = await run_in_threadpool(store.remaining, ip)
        if remaining:
            return await reject(429, "IP_BLOCKED", "Adres IP jest czasowo zablokowany.", {"Retry-After": str(remaining)})
        session = await run_in_threadpool(store.session, request.cookies.get(COOKIE, ""))
        if not session:
            if path.startswith("/api/") or request.method not in {"GET", "HEAD"}:
                return await reject(401, "LOGIN_REQUIRED", "Zaloguj się, aby kontynuować.")
            return await RedirectResponse("/login", status_code=303)(scope, receive, secure_send)
        scope.setdefault("state", {})["csrf_token"] = session[0]
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (origin is not None and origin != config.auth_public_url
                    or request.headers.get("sec-fetch-site") == "cross-site"
                    or not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), session[0].encode())):
                return await reject(403, "CSRF_FAILED", "Odśwież stronę i spróbuj ponownie.")
        await self.app(scope, receive, secure_send)
