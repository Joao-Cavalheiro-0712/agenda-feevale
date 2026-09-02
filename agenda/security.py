"""Autenticação, CSRF, rate limiting e links assinados (SPEC §78, §79, §111, §131)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

from agenda import config

# --------------------------------------------------------------------------- #
# Senhas — scrypt da biblioteca padrão
# --------------------------------------------------------------------------- #
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return hmac.compare_digest(digest, expected)


def password_problems(password: str) -> str:
    if len(password or "") < 8:
        return "A senha precisa de pelo menos 8 caracteres."
    return ""


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def csrf_ok(session_token: str | None, submitted: str | None) -> bool:
    return bool(session_token and submitted and hmac.compare_digest(session_token, submitted))


# --------------------------------------------------------------------------- #
# Rate limiting (janela deslizante, em memória)
# --------------------------------------------------------------------------- #
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(bucket: str, identity: str, *, limit: int | None = None, window: int = 60) -> bool:
    """True se a requisição pode seguir; False se estourou o limite."""
    limit = limit or config.RATE_LIMITS.get(bucket, 60)
    key = f"{bucket}:{identity}"
    now = time.time()
    hits = _hits[key]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


# --------------------------------------------------------------------------- #
# Links assinados (SPEC §131)
# --------------------------------------------------------------------------- #
def sign_payload(payload: dict, *, ttl_seconds: int = 3600) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_seconds
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).rstrip(b"=")
    signature = hmac.new(config.SECRET_KEY.encode(), raw, hashlib.sha256).digest()
    return f"{raw.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def verify_payload(token: str) -> dict | None:
    try:
        raw, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(config.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
    if not hmac.compare_digest(expected_b64, signature):
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        body = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if body.get("exp", 0) < time.time():
        return None
    return body


def share_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))
