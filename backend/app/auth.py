"""Авторизация: единый пароль из окружения, подписанная HMAC cookie-сессия.

Токен: base64url(json{exp, nonce}) + '.' + base64url(hmac_sha256). Секрет подписи — из
MESHBOARD_SECRET_KEY или случайный при старте (тогда сессии сбрасываются при перезапуске).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

COOKIE_NAME = "meshboard_session"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Auth:
    def __init__(self, password: str, secret_key: str = "", session_hours: int = 168):
        self._password_hash = hashlib.sha256(password.encode("utf-8")).digest()
        key = secret_key or secrets.token_hex(32)
        self._key = hashlib.sha256(("meshboard-session:" + key).encode()).digest()
        self.session_seconds = session_hours * 3600
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def check_password(self, candidate: str) -> bool:
        cand = hashlib.sha256((candidate or "").encode("utf-8")).digest()
        return hmac.compare_digest(cand, self._password_hash)

    def rate_limited(self, client: str, limit: int = 10, window: float = 300.0) -> bool:
        """Не более `limit` неудачных попыток за `window` секунд с одного адреса."""
        q = self._attempts[client]
        t = time.monotonic()
        while q and t - q[0] > window:
            q.popleft()
        return len(q) >= limit

    def register_failure(self, client: str) -> None:
        self._attempts[client].append(time.monotonic())

    def reset_failures(self, client: str) -> None:
        self._attempts.pop(client, None)

    def issue(self) -> str:
        body = _b64e(json.dumps({"exp": int(time.time()) + self.session_seconds,
                                 "n": secrets.token_hex(8)}).encode())
        sig = _b64e(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def verify(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        body, sig = token.rsplit(".", 1)
        expected = _b64e(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return False
        try:
            data = json.loads(_b64d(body))
        except ValueError:
            return False
        return isinstance(data, dict) and int(data.get("exp", 0)) > time.time()
