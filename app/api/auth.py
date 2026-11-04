import hmac
import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.services.auth import COOKIE, LOGIN_COOKIE, client_ip

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def error(status, code, message, headers=None):
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status, headers=headers)


def set_cookie(response, name, value, settings, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True, secure=settings.auth_cookie_secure,
                        samesite="strict", path="/")


@router.get("/login", include_in_schema=False)
async def login_page(request: Request):
    token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(request=request, name="login.html", context={
        "app_name": request.app.state.settings.app_name, "login_csrf": token})
    set_cookie(response, LOGIN_COOKIE, token, request.app.state.settings, 900)
    return response


@router.post("/api/auth/login", include_in_schema=False)
async def login(request: Request):
    config, store = request.app.state.settings, request.app.state.auth
    origin, csrf = request.headers.get("origin"), request.headers.get("x-csrf-token", "")
    if (origin != config.auth_public_url or request.headers.get("sec-fetch-site") == "cross-site"
            or not csrf or len(csrf) > 128 or not hmac.compare_digest(csrf.encode(), request.cookies.get(LOGIN_COOKIE, "").encode())):
        return error(403, "CSRF_FAILED", "Odśwież stronę logowania i spróbuj ponownie.")
    try:
        ip = client_ip(request, config.auth_trusted_proxies)
    except ValueError:
        return error(400, "INVALID_PROXY", "Nieprawidłowy adres klienta przekazany przez proxy.")
    remaining = await run_in_threadpool(store.remaining, ip)
    if remaining:
        return error(429, "IP_BLOCKED", "Zbyt wiele nieudanych prób. Adres IP jest czasowo zablokowany.", {"Retry-After": str(remaining)})
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        return error(415, "JSON_REQUIRED", "Nieprawidłowy format żądania.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            return error(413, "LOGIN_TOO_LARGE", "Żądanie logowania jest zbyt duże.")
    try:
        payload = json.loads(raw)
        username, password = payload.get("username"), payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 200 or len(password) > 1024:
            raise ValueError()
        username.encode()
        password.encode()
    except (ValueError, AttributeError, UnicodeError):
        return error(400, "INVALID_LOGIN", "Nieprawidłowe dane logowania.")
    session, blocked = await run_in_threadpool(store.login, ip, username, password, request.cookies.get(COOKIE, ""))
    if not session:
        if blocked:
            return error(429, "IP_BLOCKED", "Zbyt wiele nieudanych prób. Adres IP jest czasowo zablokowany.", {"Retry-After": str(blocked)})
        return error(401, "INVALID_LOGIN", "Nieprawidłowy użytkownik lub hasło.")
    response = JSONResponse({"ok": True})
    set_cookie(response, COOKIE, session[0], config, config.auth_session_seconds)
    response.delete_cookie(LOGIN_COOKIE, path="/", secure=config.auth_cookie_secure, httponly=True, samesite="strict")
    return response


@router.post("/api/auth/logout", include_in_schema=False)
async def logout(request: Request):
    await run_in_threadpool(request.app.state.auth.logout, request.cookies.get(COOKIE, ""))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/", secure=request.app.state.settings.auth_cookie_secure,
                           httponly=True, samesite="strict")
    return response
