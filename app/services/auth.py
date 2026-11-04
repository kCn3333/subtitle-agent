"""Single-account authentication with opaque sessions and persistent IP lockouts."""
import hashlib
import hmac
import ipaddress
import math
import secrets
import sqlite3
import time
from pathlib import Path


COOKIE = "subtitle_session"
LOGIN_COOKIE = "subtitle_login_csrf"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def client_ip(request, trusted_proxies: list[str]) -> str:
    peer = request.client.host if request.client else "unknown"
    networks = [ipaddress.ip_network(item) for item in trusted_proxies]

    def address(value):
        parsed = ipaddress.ip_address(value)
        return parsed.ipv4_mapped if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped else parsed

    def trusted(value):
        return any(value in network for network in networks)

    try:
        current = address(peer)
    except ValueError:
        return peer  # e.g. TestClient's non-IP transport identifier
    if not trusted(current):
        return str(current)
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded or len(forwarded) > 2048:
        raise ValueError("Trusted proxy must supply X-Forwarded-For")
    chain = forwarded.split(",")
    if len(chain) > 20:
        raise ValueError("Too many proxy hops")
    for hop in reversed(chain):
        if not trusted(current):
            break
        current = address(hop.strip())
    if trusted(current):
        raise ValueError("No client address in proxy chain")
    return str(current)


class AuthStore:
    def __init__(self, path: Path, settings):
        self.path, self.settings = path, settings
        self._pepper = secrets.token_bytes(32)
        self._username = self._credential(settings.auth_username)
        self._password = self._credential(settings.auth_password.get_secret_value())
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS auth_meta (id INTEGER PRIMARY KEY CHECK(id=1), salt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL, credentials TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS auth_failures (
                    ip TEXT PRIMARY KEY, failures INTEGER NOT NULL, updated REAL NOT NULL, blocked_until REAL NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO auth_meta VALUES (1, ?)", (secrets.token_hex(32),))
            salt = db.execute("SELECT salt FROM auth_meta WHERE id=1").fetchone()[0]
        # Credential changes invalidate existing sessions without storing the password.
        credential_bytes = (settings.auth_username + "\0" + settings.auth_password.get_secret_value()).encode()
        self.version = hashlib.pbkdf2_hmac("sha256", credential_bytes, bytes.fromhex(salt), 600_000).hex()

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _credential(self, value: str) -> bytes:
        return hmac.digest(self._pepper, value.encode(), "sha256")

    def remaining(self, ip: str) -> int:
        with self.connect() as db:
            row = db.execute("SELECT blocked_until FROM auth_failures WHERE ip=?", (ip,)).fetchone()
        return max(0, math.ceil(row[0] - time.time())) if row else 0

    def login(self, ip: str, username: str, password: str, old_token: str = ""):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")  # Serialize failures, including across processes.
            db.execute("DELETE FROM auth_failures WHERE updated < ? AND blocked_until <= ?",
                       (now - self.settings.auth_lockout_seconds, now))
            db.execute("DELETE FROM auth_sessions WHERE expires <= ? OR credentials != ?", (now, self.version))
            row = db.execute("SELECT failures, blocked_until FROM auth_failures WHERE ip=?", (ip,)).fetchone()
            if row and row[1] > now:
                return None, math.ceil(row[1] - now)
            user_ok = hmac.compare_digest(self._credential(username), self._username)
            password_ok = hmac.compare_digest(self._credential(password), self._password)
            if not (user_ok & password_ok):
                failures = (row[0] if row and not row[1] else 0) + 1
                blocked = now + self.settings.auth_lockout_seconds if failures >= 3 else 0
                db.execute("INSERT OR REPLACE INTO auth_failures VALUES (?,?,?,?)", (ip, failures, now, blocked))
                return None, self.settings.auth_lockout_seconds if blocked else 0
            db.execute("DELETE FROM auth_failures WHERE ip=?", (ip,))
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest(old_token),))
            # Bound active sessions for this single account.
            db.execute("DELETE FROM auth_sessions WHERE token_hash IN (SELECT token_hash FROM auth_sessions ORDER BY expires DESC LIMIT -1 OFFSET 19)")
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute("INSERT INTO auth_sessions VALUES (?,?,?,?)",
                       (digest(token), csrf, now + self.settings.auth_session_seconds, self.version))
            return (token, csrf), 0

    def session(self, token: str):
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            return db.execute("SELECT csrf, expires FROM auth_sessions WHERE token_hash=? AND expires>? AND credentials=?",
                              (digest(token), time.time(), self.version)).fetchone()

    def logout(self, token: str):
        with self.connect() as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest(token),))
