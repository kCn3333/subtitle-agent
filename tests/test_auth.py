import re
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.core.config import Settings
from app.main import create_app
from app.services.auth import COOKIE, AuthStore
from app.services.system_probe import ToolInfo


@pytest.fixture
def browser(settings, monkeypatch):
    monkeypatch.setattr('app.main.probe_tools', lambda: ToolInfo('ffmpeg', 'ffprobe', 'mkvextract'))
    with TestClient(create_app(settings), client=('198.51.100.10', 1234)) as client:
        yield client


def login(browser, username='test-user', password='test-password-for-auth', headers=None, remember=None):
    page = browser.get('/login')
    csrf = re.search(r'name="login-csrf-token" content="([^\"]+)"', page.text)[1]
    return browser.post('/api/auth/login', json={'username':username,'password':password,
                              **({'remember':remember} if remember is not None else {})},
                        headers={'Origin':'http://testserver','X-CSRF-Token':csrf, **(headers or {})})


def session_csrf(browser):
    return re.search(r'name="csrf-token" content="([^\"]+)"', browser.get('/').text)[1]


def test_pages_require_login(browser):
    for path in ['/', '/ai', '/settings', '/archive']:
        response = browser.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers['location'] == '/login', path


def test_api_downloads_and_sse_require_login(browser):
    for path in [
        '/api/jobs', '/api/tasks/missing', '/api/tasks/missing/download', '/api/jobs/missing/events',
        '/api/archive', '/api/archive/missing/download', '/api/settings/ai', '/api/settings/ai/console',
        '/api/settings/ai/health', '/api/workpacks/config', '/api/workpacks/ocr-health',
        '/api/tasks/missing/ai-sync/download', '/api/tasks/missing/ai-translate/download',
        '/api/tasks/missing/ai-status', '/api%2fsettings/ai',
    ]:
        response = browser.get(path)
        assert response.status_code == 401, path
        assert response.json()['detail']['code'] == 'LOGIN_REQUIRED', path


def test_health_is_minimal_and_login_has_security_headers(browser):
    assert browser.get('/health').json() == {'status':'ok'}
    page = browser.get('/login')
    assert page.status_code == 200
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['x-content-type-options'] == 'nosniff'
    assert page.headers['x-frame-options'] == 'DENY'
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert 'autocomplete="current-password"' in page.text
    assert f'>v{__version__}</a>' in page.text
    assert 'aria-label="Projekt Subtitle Agent na GitHubie"' in page.text
    assert browser.get('/static/github.svg').status_code == 200
    assert browser.get('/static/login.js').status_code == 200
    assert browser.get('/login', headers={'Host':'evil.example'}).status_code == 400


def test_login_logout_csrf_and_opaque_session(browser):
    assert browser.post('/api/auth/login', json={'username':'test-user','password':'test-password-for-auth'}).status_code == 403
    response = login(browser)
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    token = browser.cookies[COOKIE]
    assert 'test-user' not in token and 'test-password' not in token
    csrf = session_csrf(browser)
    assert browser.get('/settings').status_code == 200
    assert browser.get('/docs').status_code == 404
    assert browser.get('/openapi.json').status_code == 404
    assert browser.post('/api/auth/logout').status_code == 403
    assert browser.post('/api/auth/logout', headers={'X-CSRF-Token':csrf,'Origin':'https://evil.example'}).status_code == 403
    assert browser.post('/api/auth/logout', headers={'X-CSRF-Token':csrf,'Origin':'http://testserver'}).status_code == 200
    assert browser.get('/api/settings/ai').status_code == 401
    browser.cookies.set(COOKIE, token)
    assert browser.get('/api/settings/ai').status_code == 401  # Server-side revocation.


def test_mutating_endpoints_require_csrf(browser):
    assert login(browser).status_code == 200
    csrf = session_csrf(browser)
    payload = {'api_url':'http://model/v1','model':'test'}
    assert browser.put('/api/settings/ai', json=payload).status_code == 403
    assert browser.put('/api/settings/ai', json=payload, headers={'X-CSRF-Token':'bad'}).status_code == 403
    assert browser.put('/api/settings/ai', json=payload, headers={'X-CSRF-Token':csrf,'Sec-Fetch-Site':'cross-site'}).status_code == 403
    assert browser.put('/api/settings/ai', json=payload, headers={'X-CSRF-Token':csrf,'Origin':'http://testserver'}).status_code == 200
    assert browser.post('/api/auth/login', headers={'Origin':'https://evil.example'}).status_code == 403


def test_third_failure_locks_ip_for_one_hour_even_for_correct_password(browser):
    assert login(browser, username='missing').status_code == 401
    assert login(browser, password='wrong').status_code == 401
    blocked = login(browser, password='wrong')
    assert blocked.status_code == 429
    assert int(blocked.headers['retry-after']) == 3600
    assert login(browser).status_code == 429
    assert browser.get('/api/settings/ai').status_code == 429
    assert browser.get('/health').status_code == 200
    store = browser.app.state.auth
    restarted = AuthStore(store.path, store.settings)
    assert 3590 <= restarted.remaining('198.51.100.10') <= 3600
    with store.connect() as db:
        db.execute('UPDATE auth_failures SET blocked_until=?', (time.time()-1,))
    assert login(browser).status_code == 200


def test_success_resets_failure_counter_and_expired_session_is_rejected(browser):
    assert login(browser, password='wrong').status_code == 401
    assert login(browser, password='wrong').status_code == 401
    assert login(browser).status_code == 200
    store = browser.app.state.auth
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM auth_failures').fetchone()[0] == 0
        stored = db.execute('SELECT token_hash FROM auth_sessions').fetchone()[0]
        assert stored != browser.cookies[COOKIE]
        db.execute('UPDATE auth_sessions SET expires=?', (time.time()-1,))
    assert browser.get('/api/settings/ai').status_code == 401
    assert login(browser, password='wrong').status_code == 401


def test_untrusted_forwarded_headers_cannot_bypass_lockout(browser):
    for attempt in range(3):
        response = login(browser, password='wrong', headers={'X-Forwarded-For':f'203.0.113.{attempt+1}'})
    assert response.status_code == 429
    assert login(browser, headers={'X-Forwarded-For':'203.0.113.99'}).status_code == 429


def test_trusted_proxy_locks_real_client_and_ignores_spoofed_leftmost_address(settings, monkeypatch):
    settings.auth_trusted_proxies = ['127.0.0.1/32', '10.0.0.0/24']
    monkeypatch.setattr('app.main.probe_tools', lambda: ToolInfo('ffmpeg', 'ffprobe', 'mkvextract'))
    with TestClient(create_app(settings), client=('127.0.0.1', 1234)) as browser:
        for index in range(3):
            response = login(browser, password='wrong', headers={'X-Forwarded-For':f'203.0.113.{index+1}, 198.51.100.1, 10.0.0.2'})
        assert response.status_code == 429
        assert login(browser, headers={'X-Forwarded-For':'198.51.100.1, 10.0.0.2'}).status_code == 429
        assert login(browser, headers={'X-Forwarded-For':'198.51.100.2, 10.0.0.2'}).status_code == 200
        assert login(browser, headers={'X-Forwarded-For':'garbage'}).status_code == 400
        assert login(browser).status_code == 400  # No silent fallback to the proxy IP.


def test_concurrent_failures_are_atomic(browser):
    store = browser.app.state.auth
    other = AuthStore(store.path, store.settings)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: (store if index % 2 else other).login('203.0.113.8','test-user','wrong'), range(8)))
    assert sum(block == 0 for _, block in results) == 2
    assert all(session is None for session, _ in results)
    assert store.login('203.0.113.8','test-user','test-password-for-auth')[1] > 0


@pytest.mark.parametrize('updates', [{'auth_username':'changed-user'}, {'auth_password':'changed-password-for-auth'}])
@pytest.mark.parametrize('remember', [False, True])
def test_credential_change_invalidates_sessions(browser, updates, remember):
    assert login(browser, remember=remember).status_code == 200
    store = browser.app.state.auth
    changed = Settings(**{**store.settings.model_dump(), **updates})
    assert AuthStore(store.path, changed).session(browser.cookies[COOKIE]) is None


def test_secure_cookie_and_hsts(settings, monkeypatch):
    settings.auth_cookie_secure = True
    settings.auth_public_url = 'https://testserver'
    monkeypatch.setattr('app.main.probe_tools', lambda: ToolInfo('ffmpeg', 'ffprobe', 'mkvextract'))
    with TestClient(create_app(settings), base_url='https://testserver') as browser:
        page = browser.get('/login')
        csrf = re.search(r'name="login-csrf-token" content="([^\"]+)"', page.text)[1]
        response = browser.post('/api/auth/login', json={'username':'test-user','password':'test-password-for-auth'},
                                headers={'Origin':'https://testserver','X-CSRF-Token':csrf})
        assert response.status_code == 200
        assert 'Secure' in response.headers['set-cookie']
        assert response.headers['strict-transport-security'] == 'max-age=31536000'


@pytest.mark.parametrize('updates', [
    {'auth_username':None}, {'auth_password':None}, {'auth_public_url':''},
    {'auth_password':'short'}, {'auth_cookie_secure':True},
])
def test_missing_or_unsafe_configuration_fails_closed(settings, updates):
    # Revalidate credentials through Settings to preserve SecretStr type.
    invalid = Settings(**{**settings.model_dump(), **updates})
    with pytest.raises(ValueError):
        invalid.require_auth_config()


@pytest.mark.parametrize('value', ['0.0.0.0/0','::/0','not-an-ip'])
def test_proxy_configuration_rejects_unsafe_networks(value):
    with pytest.raises(ValueError):
        Settings(auth_trusted_proxies=value)


def test_malformed_and_large_login_payloads_do_not_expose_secrets(browser):
    page = browser.get('/login')
    csrf = re.search(r'name="login-csrf-token" content="([^\"]+)"', page.text)[1]
    headers = {'Origin':'http://testserver','X-CSRF-Token':csrf,'Content-Type':'application/json'}
    assert browser.post('/api/auth/login', content='x'*9000, headers=headers).status_code == 413
    for payload in ['[]', '{"username":1}', '{"username":"x","password":"\\ud800"}']:
        response = browser.post('/api/auth/login', content=payload, headers=headers)
        assert response.status_code == 400
        assert 'test-password-for-auth' not in response.text


def test_forged_session_cookie_cannot_authorize_requests(browser):
    assert login(browser).status_code == 200
    original=browser.cookies[COOKIE]
    browser.cookies.clear()
    browser.cookies.set(COOKIE, original[:-1]+('a' if original[-1]!='a' else 'b'))
    assert browser.get('/api/settings/ai').status_code == 401
    assert browser.post('/api/auth/logout', headers={'X-CSRF-Token':'forged'}).status_code == 401


def test_favicon_does_not_replace_login_csrf(browser):
    page=browser.get('/login')
    csrf=re.search(r'name="login-csrf-token" content="([^\"]+)"',page.text)[1]
    cookie=browser.cookies.get('subtitle_login_csrf')
    icon=browser.get('/favicon.ico',follow_redirects=False)
    assert icon.status_code == 204
    assert 'location' not in icon.headers
    assert 'set-cookie' not in icon.headers
    assert browser.cookies.get('subtitle_login_csrf') == cookie
    response=browser.post('/api/auth/login',json={'username':'test-user','password':'test-password-for-auth'},
                          headers={'Origin':'http://testserver','X-CSRF-Token':csrf})
    assert response.status_code == 200


def test_another_login_page_does_not_invalidate_open_form(browser):
    page=browser.get('/login')
    csrf=re.search(r'name="login-csrf-token" content="([^\"]+)"',page.text)[1]
    second=browser.get('/login')
    assert f'content="{csrf}"' in second.text
    response=browser.post('/api/auth/login',json={'username':'test-user','password':'test-password-for-auth'},
                          headers={'Origin':'http://testserver','X-CSRF-Token':csrf})
    assert response.status_code == 200


def test_login_replaces_malformed_challenge_cookie(browser):
    browser.cookies.set('subtitle_login_csrf','invalid-cookie')
    page=browser.get('/login')
    csrf=re.search(r'name="login-csrf-token" content="([^\"]+)"',page.text)[1]
    assert re.fullmatch(r'[A-Za-z0-9_-]{43}',csrf)
    response=browser.post('/api/auth/login',json={'username':'test-user','password':'test-password-for-auth'},
                          headers={'Origin':'http://testserver','X-CSRF-Token':'wrong-token'})
    assert response.status_code == 403


@pytest.mark.parametrize('remember', [False, True])
def test_remember_me_cookie_and_server_expire_together(browser, monkeypatch, remember):
    from app.services.auth import REMEMBER_SESSION_SECONDS
    before=time.time()
    response=login(browser,remember=remember)
    assert response.status_code == 200
    store=browser.app.state.auth
    expected=REMEMBER_SESSION_SECONDS if remember else store.settings.auth_session_seconds
    assert f'Max-Age={expected}' in response.headers['set-cookie']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    token=browser.cookies[COOKIE]
    csrf,expires=store.session(token)
    assert before+expected <= expires <= time.time()+expected
    assert AuthStore(store.path,store.settings).session(token) is not None
    with monkeypatch.context() as clock:
        clock.setattr('app.services.auth.time.time',lambda:expires-1)
        assert store.session(token) is not None
        clock.setattr('app.services.auth.time.time',lambda:expires)
        assert store.session(token) is None
    if remember:
        with monkeypatch.context() as clock:
            clock.setattr('app.services.auth.time.time',lambda:before+store.settings.auth_session_seconds+1)
            assert store.session(token) is not None
    response=browser.post('/api/auth/logout',headers={'Origin':'http://testserver','X-CSRF-Token':csrf})
    assert response.status_code == 200
    assert store.session(token) is None


def test_login_rejects_non_boolean_remember_flag(browser):
    for remember in ['true', 'false', 1, 0, None, [], {}]:
        page=browser.get('/login')
        csrf=re.search(r'name="login-csrf-token" content="([^\"]+)"',page.text)[1]
        response=browser.post('/api/auth/login',json={'username':'test-user','password':'test-password-for-auth','remember':remember},
                              headers={'Origin':'http://testserver','X-CSRF-Token':csrf})
        assert response.status_code == 400, repr(remember)
        assert browser.app.state.auth.session(browser.cookies.get(COOKIE,'')) is None
