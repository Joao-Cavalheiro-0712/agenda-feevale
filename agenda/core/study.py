"""Planejador de sessões de estudo (SPEC §93, §94).

Regra do produto: blocos de estudo são SUGESTÕES do sistema e nunca se
misturam visualmente com compromissos obrigatórios. Eles nascem de provas e
entregas grandes, respeitam a disponibilidade declarada e podem ser
descartados sem culpa.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.core import recurrence
from agenda.models import Event, EventStatus, EventType, StudyBlock, User

# Peso de estudo por tipo: quanto de preparo o evento pede, em minutos.
STUDY_WEIGHT = {
    EventType.EXAM.value: 240,
    EventType.SIMULATION.value: 180,
    EventType.QUIZ.value: 90,
    EventType.PAPER.value: 300,
    EventType.PROJECT.value: 300,
    EventType.PRESENTATION.value: 150,
    EventType.SEMINAR.value: 150,
    EventType.ASSIGNMENT.value: 120,
}

DEFAULT_BLOCK_MINUTES = 45
DEFAULT_SLOT = "19:00"


def candidates(db: Session, user: User, *, today: dt.date, horizon_days: int = 30) -> list[Event]:
    """Eventos que merecem preparo nas próximas semanas."""
    limite = today + dt.timedelta(days=horizon_days)
    rows = db.scalars(
        select(Event).where(
            Event.user_id == user.id,
            Event.local_date >= today,
            Event.local_date <= limite,
            Event.status.in_([EventStatus.UPCOMING.value, EventStatus.IN_PROGRESS.value]),
        )
    ).all()
    return sorted(
        [e for e in rows if e.type in STUDY_WEIGHT], key=lambda e: e.local_date
    )


def busy_days(db: Session, user: User, start: dt.date, end: dt.date) -> dict[dt.date, int]:
    """Carga já existente por dia — para não empilhar estudo em dia cheio."""
    carga: dict[dt.date, int] = {}
    for occurrence in recurrence.expand_classes(db, user.id, start, end, include_cancelled=False):
        carga[occurrence.date] = carga.get(occurrence.date, 0) + 1
    for event in db.scalars(
        select(Event).where(
            Event.user_id == user.id, Event.local_date >= start, Event.local_date <= end
        )
    ).all():
        peso = 3 if event.type == EventType.EXAM.value else 1
        carga[event.local_date] = carga.get(event.local_date, 0) + peso
    return carga


def propose(
    db: Session,
    user: User,
    *,
    today: dt.date,
    minutes_per_day: int = 90,
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6),
    horizon_days: int = 30,
) -> list[dict]:
    """Monta uma proposta de blocos. Não grava nada — quem decide é o usuário."""
    alvos = candidates(db, user, today=today, horizon_days=horizon_days)
    if not alvos:
        return []

    carga = busy_days(db, user, today, today + dt.timedelta(days=horizon_days))
    usados: dict[dt.date, int] = {}
    propostas: list[dict] = []

    for event in alvos:
        total = STUDY_WEIGHT.get(event.type, 120)
        # Quanto mais perto, menos dias disponíveis: distribui no que sobrou.
        dias = [
            today + dt.timedelta(days=offset)
            for offset in range((event.local_date - today).days)
            if (today + dt.timedelta(days=offset)).weekday() in weekdays
        ]
        dias = [d for d in dias if d < event.local_date]
        if not dias:
            continue
        # Prioriza os dias mais próximos da prova, mas evita a véspera cheia.
        dias.sort(key=lambda d: (carga.get(d, 0) + usados.get(d, 0), -(event.local_date - d).days))
        restante = total
        for dia in dias:
            if restante <= 0:
                break
            disponivel = minutes_per_day - usados.get(dia, 0)
            if disponivel < DEFAULT_BLOCK_MINUTES:
                continue
            minutos = min(DEFAULT_BLOCK_MINUTES, restante)
            usados[dia] = usados.get(dia, 0) + minutos
            restante -= minutos
            propostas.append(
                {
                    "event_id": event.id,
                    "subject_id": event.subject_id,
                    "local_date": dia,
                    "minutes": minutos,
                    "topic": f"Estudar {event.title}",
                    "start_time": DEFAULT_SLOT,
                }
            )
    propostas.sort(key=lambda p: (p["local_date"], p["start_time"]))
    return propostas


def save(db: Session, user: User, propostas: list[dict]) -> int:
    """Persiste os blocos aceitos, sem duplicar o que já existe."""
    existentes = {
        (b.event_id, b.local_date)
        for b in db.scalars(select(StudyBlock).where(StudyBlock.user_id == user.id)).all()
    }
    criados = 0
    for proposta in propostas:
        chave = (proposta["event_id"], proposta["local_date"])
        if chave in existentes:
            continue
        db.add(
            StudyBlock(
                user_id=user.id,
                event_id=proposta["event_id"],
                subject_id=proposta.get("subject_id"),
                local_date=proposta["local_date"],
                start_time=proposta.get("start_time", DEFAULT_SLOT),
                minutes=proposta.get("minutes", DEFAULT_BLOCK_MINUTES),
                topic=proposta.get("topic", "")[:200],
            )
        )
        existentes.add(chave)
        criados += 1
    db.flush()
    return criados


def clear(db: Session, user: User) -> int:
    blocos = db.scalars(
        select(StudyBlock).where(StudyBlock.user_id == user.id, StudyBlock.status == "PLANNED")
    ).all()
    for bloco in blocos:
        db.delete(bloco)
    db.flush()
    return len(blocos)


def complete(db: Session, user: User, block_id: str, *, done: bool = True) -> bool:
    bloco = db.get(StudyBlock, block_id)
    if bloco is None or bloco.user_id != user.id:
        return False
    bloco.status = "DONE" if done else "PLANNED"
    db.flush()
    return True


def suggest_message(propostas: list[dict]) -> str:
    """Sugestão proativa, sem alarmismo (SPEC §54, §93)."""
    if not propostas:
        return ""
    dias = len({p["local_date"] for p in propostas})
    horas = sum(p["minutes"] for p in propostas) / 60
    return (
        f"Dá para distribuir {horas:.0f}h de estudo em {dias} dia(s) antes das suas provas. "
        "Quer que eu coloque na agenda?"
    )
