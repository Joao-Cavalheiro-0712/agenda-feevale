"""Proteção contra força bruta no login (SPEC §79, §111).

Rate limit por IP sozinho não protege: um atacante distribuído tenta a mesma
conta de vários lugares. Aqui contamos tentativas por CONTA e por IP, com
bloqueio temporário progressivo, e registramos o histórico para auditoria.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.models import LoginAttempt
from agenda.security import hash_ip

JANELA = dt.timedelta(minutes=15)
LIMITE_CONTA = 8
LIMITE_IP = 20
BLOQUEIOS = {8: 1, 12: 5, 20: 30}  # falhas → minutos de espera


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _recentes(db: Session, *, identity: str = "", ip_hash: str = "") -> list[LoginAttempt]:
    desde = _now() - JANELA
    stmt = select(LoginAttempt).where(LoginAttempt.created_at >= desde)
    if identity:
        stmt = stmt.where(LoginAttempt.identity == identity)
    if ip_hash:
        stmt = stmt.where(LoginAttempt.ip_hash == ip_hash)
    return list(db.scalars(stmt).all())


def blocked(db: Session, identity: str, ip: str | None) -> tuple[bool, int]:
    """(bloqueado, minutos_restantes). Falhas seguidas aumentam a espera."""
    identity = (identity or "").strip().lower()
    ip_hash = hash_ip(ip)

    falhas_conta = [a for a in _recentes(db, identity=identity) if not a.success]
    falhas_ip = [a for a in _recentes(db, ip_hash=ip_hash) if not a.success] if ip_hash else []

    def espera(quantidade: int) -> int:
        minutos = 0
        for limite, valor in sorted(BLOQUEIOS.items()):
            if quantidade >= limite:
                minutos = valor
        return minutos

    minutos = max(espera(len(falhas_conta)), espera(len(falhas_ip)) if len(falhas_ip) >= LIMITE_IP else 0)
    if minutos == 0:
        return False, 0

    ultima = max(
        (a.created_at for a in falhas_conta + falhas_ip),
        default=None,
    )
    if ultima is None:
        return False, 0
    ultima = ultima if ultima.tzinfo else ultima.replace(tzinfo=dt.timezone.utc)
    liberado_em = ultima + dt.timedelta(minutes=minutos)
    restante = (liberado_em - _now()).total_seconds() / 60
    if restante <= 0:
        return False, 0
    return True, max(1, int(restante) + 1)


def record(db: Session, identity: str, ip: str | None, *, success: bool) -> None:
    db.add(
        LoginAttempt(
            identity=(identity or "").strip().lower()[:160],
            ip_hash=hash_ip(ip),
            success=success,
        )
    )
    db.flush()


def cleanup(db: Session, *, days: int = 30) -> int:
    """Retenção curta: tentativa de login antiga não serve para nada."""
    corte = _now() - dt.timedelta(days=days)
    antigas = db.scalars(select(LoginAttempt).where(LoginAttempt.created_at < corte)).all()
    for linha in antigas:
        db.delete(linha)
    db.flush()
    return len(antigas)
