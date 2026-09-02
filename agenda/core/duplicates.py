"""Fingerprint e detecção de duplicados/atualizações (SPEC §14, §73, §74)."""
from __future__ import annotations

import datetime as dt
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.core.text import norm, slug_key
from agenda.models import Event, EventStatus

# Palavras que, no texto do usuário, indicam remarcação e não um novo evento.
RESCHEDULE_HINTS = (
    "passou para", "passou pro", "foi adiada", "foi adiado", "remarcou",
    "remarcada", "remarcado", "mudou para", "mudou pro", "adiada para",
    "adiado para", "antecipou", "antecipada", "nova data",
)

_TYPE_FAMILY = {
    "EXAM": "avaliacao", "QUIZ": "avaliacao", "SIMULATION": "avaliacao",
    "ASSIGNMENT": "entrega", "HOMEWORK": "entrega", "PROJECT": "entrega",
    "PAPER": "entrega", "PRESENTATION": "entrega", "SEMINAR": "entrega",
}


def type_family(event_type: str) -> str:
    return _TYPE_FAMILY.get(event_type, event_type)


def fingerprint(
    *, user_id: str, subject_id: str | None, event_type: str, date: dt.date, title: str
) -> str:
    """Identidade estável de um evento — usada para não criar duplicados."""
    raw = "|".join(
        [
            user_id,
            subject_id or "-",
            type_family(event_type),
            date.isoformat(),
            slug_key(title)[:40],
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def find_duplicate(db: Session, event: Event) -> Event | None:
    """Evento já existente com o mesmo fingerprint."""
    return db.scalars(
        select(Event).where(
            Event.user_id == event.user_id,
            Event.fingerprint == event.fingerprint,
            Event.id != event.id,
            Event.status != EventStatus.CANCELLED.value,
        )
    ).first()


def find_reschedule_candidate(
    db: Session,
    user_id: str,
    *,
    subject_id: str | None,
    event_type: str,
    title: str,
    new_date: dt.date,
    window_days: int = 45,
) -> Event | None:
    """Procura um evento parecido em outra data — provável remarcação (SPEC §14).

    Só considera candidatos da mesma família de tipo e da mesma disciplina;
    sem disciplina, exige forte semelhança de título.
    """
    low = new_date - dt.timedelta(days=window_days)
    high = new_date + dt.timedelta(days=window_days)
    stmt = select(Event).where(
        Event.user_id == user_id,
        Event.local_date >= low,
        Event.local_date <= high,
        Event.local_date != new_date,
        Event.status.in_([EventStatus.UPCOMING.value, EventStatus.IN_PROGRESS.value]),
    )
    if subject_id:
        stmt = stmt.where(Event.subject_id == subject_id)
    candidates = [e for e in db.scalars(stmt).all() if type_family(e.type) == type_family(event_type)]
    if not candidates:
        return None

    target = slug_key(title)
    best, best_score = None, 0.0
    for candidate in candidates:
        score = _similarity(target, slug_key(candidate.title))
        if subject_id and score < 0.35:
            # Mesma disciplina + mesmo tipo já é sinal forte (ex.: "Prova 1" vs "Prova").
            score = 0.5
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 0.45 else None


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.8
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    common = sum(1 for ch in set(shorter) if ch in longer)
    return common / max(len(set(longer)), 1)


def looks_like_reschedule(text: str) -> bool:
    t = norm(text)
    return any(hint in t for hint in RESCHEDULE_HINTS)
