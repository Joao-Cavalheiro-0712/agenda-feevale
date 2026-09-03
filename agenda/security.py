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
MAX_PASSWORD_BYTES = 256  # senha gigante vira DoS de CPU no KDF

# Hash descartável usado quando o e-mail não existe: o login gasta o mesmo
# tempo com conta válida e inválida, o que impede enumerar usuários pelo relógio.
_DUMMY_HASH = ""


def hash_password(password: str) -> str:
    _guard_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    if len((password or "").encode()) > MAX_PASSWORD_BYTES:
        return False
    try:
        scheme, salt_b64, digest_b64 = (stored or "").split("$", 2)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return hmac.compare_digest(digest, expected)


def dummy_verify(password: str) -> None:
    """Gasta o mesmo tempo de um login válido, sem revelar se a conta existe."""
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(24))
    verify_password(password or "", _DUMMY_HASH)


def _guard_password(password: str) -> None:
    if len((password or "").encode()) > MAX_PASSWORD_BYTES:
        raise ValueError("senha longa demais")


# Senhas óbvias que aparecem em toda lista de vazamento.
_COMMON = {
    "12345678", "123456789", "senha123", "password", "qwertyui", "11111111",
    "abcd1234", "estudante", "faculdade", "brasil123", "1q2w3e4r",
}


def password_problems(password: str) -> str:
    password = password or ""
    if len(password) < 10:
        return "A senha precisa de pelo menos 10 caracteres."
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return "Senha longa demais."
    if password.lower() in _COMMON:
        return "Essa senha é fácil demais de adivinhar. Escolha outra."
    if password.isdigit() or password.isalpha():
        return "Misture letras e números para a senha ficar mais difícil."
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


def rate_limit(
    bucket: str, identity: str, *, limit: int | None = None, window: int | None = None
) -> bool:
    """True se a requisição pode seguir; False se estourou o limite.

    Cada balde tem teto e janela próprios (`config.RATE_LIMITS`): rajada de
    cadastro numa sala de aula é legítima, rajada de login não é.
    """
    padrao = config.RATE_LIMITS.get(bucket, (60, 60))
    limit = limit or padrao[0]
    window = window or padrao[1]
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


# --------------------------------------------------------------------------- #
# Sessões persistidas (SPEC §78)
# --------------------------------------------------------------------------- #
SESSION_TOKEN_BYTES = 32


def new_session_token() -> tuple[str, str]:
    """(token para o cookie, hash guardado no banco).

    O banco nunca guarda o token em claro: um vazamento de dump não permite
    assumir sessões.
    """
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_ip(ip: str | None) -> str:
    """IP nunca é guardado em claro (minimização, SPEC §80)."""
    if not ip:
        return ""
    return hashlib.sha256((ip + config.SECRET_KEY).encode()).hexdigest()[:32]


def safe_filename(filename: str, *, fallback: str = "arquivo") -> str:
    """Nome de arquivo sem diretório, sem caractere de controle, sem surpresa."""
    import os
    import re
    import unicodedata

    nome = os.path.basename(filename or "").replace("\\", "/").split("/")[-1]
    nome = unicodedata.normalize("NFKD", nome)
    nome = re.sub(r"[\x00-\x1f\x7f]", "", nome)
    nome = re.sub(r"[^A-Za-z0-9._ ()\-\u00c0-\u024f]", "_", nome).strip(" .")
    nome = re.sub(r"_{2,}", "_", nome)
    if not nome or nome in (".", ".."):
        return fallback
    return nome[:180]


def csp_nonce() -> str:
    return base64.b64encode(secrets.token_bytes(16)).decode()
