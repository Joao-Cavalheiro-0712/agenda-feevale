"""Sessões de acesso: criação, validação e revogação (SPEC §78).

O cookie carrega apenas um token opaco; quem manda é a linha em
``user_sessions``. Isso permite três coisas que um cookie assinado sozinho não
dá: revogar um dispositivo específico, encerrar todas as sessões ao trocar a
senha, e invalidar tudo imediatamente se a conta for comprometida.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.models import UserSession, User
from agenda.security import hash_ip, hash_token, new_session_token

SESSION_MAX_AGE = dt.timedelta(days=30)
SESSION_IDLE_MAX = dt.timedelta(days=14)
_TOUCH_INTERVAL = dt.timedelta(minutes=10)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def create(db: Session, user: User, *, user_agent: str = "", ip: str | None = None) -> str:
    """Cria a sessão e devolve o token que vai para o cookie."""
    token, token_hash = new_session_token()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=token_hash,
            user_agent=(user_agent or "")[:300],
            ip_hash=hash_ip(ip),
        )
    )
    db.flush()
    return token


def resolve(db: Session, token: str | None) -> User | None:
    """Valida o token e devolve o usuário. Falha fechada em qualquer dúvida."""
    if not token:
        return None
    row = db.scalars(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    ).first()
    if row is None or row.revoked_at is not None:
        return None

    agora = _now()
    if _aware(row.created_at) + SESSION_MAX_AGE < agora:
        row.revoked_at = agora
        return None
    if _aware(row.last_seen_at) + SESSION_IDLE_MAX < agora:
        row.revoked_at = agora
        return None

    user = db.get(User, row.user_id)
    if user is None or user.deleted_at is not None:
        row.revoked_at = agora
        return None

    if _aware(row.last_seen_at) + _TOUCH_INTERVAL < agora:
        row.last_seen_at = agora
    return user


def revoke(db: Session, token: str | None) -> bool:
    if not token:
        return False
    row = db.scalars(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    ).first()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = _now()
    db.flush()
    return True


def revoke_id(db: Session, user: User, session_id: str) -> bool:
    """Revoga um dispositivo específico — só do próprio usuário."""
    row = db.get(UserSession, session_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        return False
    row.revoked_at = _now()
    db.flush()
    return True


def revoke_all(db: Session, user: User, *, keep_token: str | None = None) -> int:
    """Encerra todas as sessões (troca de senha, suspeita de invasão)."""
    manter = hash_token(keep_token) if keep_token else None
    ativos = db.scalars(
        select(UserSession).where(
            UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
        )
    ).all()
    encerradas = 0
    for row in ativos:
        if manter and row.token_hash == manter:
            continue
        row.revoked_at = _now()
        encerradas += 1
    db.flush()
    return encerradas


def list_active(db: Session, user: User) -> list[UserSession]:
    return list(
        db.scalars(
            select(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .order_by(UserSession.last_seen_at.desc())
        ).all()
    )


def describe(row: UserSession) -> str:
    """Descrição legível do dispositivo, sem expor a string crua do navegador."""
    ua = (row.user_agent or "").lower()
    if "iphone" in ua or "ipad" in ua:
        sistema = "iPhone/iPad"
    elif "android" in ua:
        sistema = "Android"
    elif "windows" in ua:
        sistema = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        sistema = "Mac"
    elif "linux" in ua:
        sistema = "Linux"
    else:
        sistema = "Dispositivo"
    if "firefox" in ua:
        navegador = "Firefox"
    elif "edg/" in ua:
        navegador = "Edge"
    elif "chrome" in ua or "crios" in ua:
        navegador = "Chrome"
    elif "safari" in ua:
        navegador = "Safari"
    else:
        navegador = ""
    return f"{sistema}{' · ' + navegador if navegador else ''}"
