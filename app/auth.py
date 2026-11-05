"""Single-admin login, signed-cookie session, CSRF token, login throttle. Stdlib crypto only."""
import base64
import hashlib
import hmac
import os
import secrets
import time

N, R, P = 2 ** 15, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=64 * 1024 * 1024)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt:{N}:{R}:{P}:{b(salt)}:{b(h)}"   # ":" not "$": Docker Compose interpolates $ in env files


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, want = stored.split(":")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                             p=int(p), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(got, base64.b64decode(want))
    except Exception:
        return False


def new_csrf() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(session: dict, submitted: str | None) -> bool:
    want = session.get("csrf")
    return bool(want and submitted and hmac.compare_digest(want, submitted))


def safe_next(value: str | None) -> str:
    """Only local paths: blocks //evil.example and https://evil.example open redirects."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


class Throttle:
    """5 failures from one address lock that address out for 60 s. In memory: a restart clears
    it, which is acceptable for one admin on a LAN."""
    def __init__(self, limit=5, window=900, lock=60, clock=time.monotonic):
        self.limit, self.window, self.lock, self.clock = limit, window, lock, clock
        self.fails: dict[str, list[float]] = {}
        self.locked_until: dict[str, float] = {}

    def blocked(self, who: str) -> bool:
        return self.clock() < self.locked_until.get(who, 0)

    def failure(self, who: str) -> None:
        now = self.clock()
        xs = [t for t in self.fails.get(who, []) if now - t < self.window] + [now]
        self.fails[who] = xs
        if len(xs) >= self.limit:
            self.locked_until[who] = now + self.lock
            self.fails[who] = []

    def success(self, who: str) -> None:
        self.fails.pop(who, None)
