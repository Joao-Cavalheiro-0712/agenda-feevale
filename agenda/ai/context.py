"""Contexto amplo para quando não há uma frase para focar (SPEC §71).

Usado na leitura de documentos: um cronograma inteiro pode citar qualquer
matéria, então aqui vale mandar o quadro completo.

Para mensagem de conversa — que é a maioria esmagadora das chamadas — quem
monta o contexto é `agenda.knowledge.retrieval`, que recupera só o que a frase
precisa e derruba o tamanho do prompt.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.core import academic, planner
from agenda.core.dates import WEEKDAY_LABELS
from agenda.models import AssistantMessage, Event, Location, Teacher, User


def build_context_block(db: Session, user: User, *, max_events: int = 20) -> str:
    today = planner.today_of(user)
    context = academic.active_context(db, user.id)
    lines: list[str] = []

    lines.append(f"Estudante: {user.name or 'sem nome'} · fuso {user.timezone}")
    if context:
        lines.append(
            f"Contexto ativo: {academic.EDUCATION_LABELS.get(context.type, context.type)}"
            f" · {context.title} · {context.subtitle or 'sem detalhes'}"
            + (f" · turno {context.shift}" if context.shift else "")
        )

    subjects = academic.list_subjects(db, user.id, context_id=context.id if context else None)
    if subjects:
        lines.append("Matérias (id · nome · apelidos · professor):")
        for subject in subjects:
            aliases = ", ".join(a.alias for a in subject.aliases) or "—"
            teacher = subject.teacher.name if subject.teacher else "—"
            lines.append(f"  - {subject.id} · {subject.name} · [{aliases}] · {teacher}")
    else:
        lines.append("Matérias: nenhuma cadastrada ainda.")

    teachers = db.scalars(select(Teacher).where(Teacher.user_id == user.id)).all()
    if teachers:
        lines.append("Professores: " + ", ".join(t.name for t in teachers))

    locations = db.scalars(select(Location).where(Location.user_id == user.id)).all()
    if locations:
        lines.append("Locais: " + ", ".join(loc.label for loc in locations))

    schedules = []
    for subject in subjects:
        for schedule in sorted(
            [s for s in _schedules_of(db, subject.id)], key=lambda s: (s.weekday, s.start_time)
        ):
            schedules.append(
                f"  - {subject.name}: {WEEKDAY_LABELS[schedule.weekday]} "
                f"{schedule.start_time}–{schedule.end_time}"
            )
    if schedules:
        lines.append("Aulas recorrentes:")
        lines.extend(schedules)

    upcoming = db.scalars(
        select(Event)
        .where(
            Event.user_id == user.id,
            Event.local_date >= today - dt.timedelta(days=7),
            Event.status != "CANCELLED",
        )
        .order_by(Event.local_date)
        .limit(max_events)
    ).all()
    if upcoming:
        lines.append("Eventos já cadastrados (id · data · tipo · título · matéria):")
        for event in upcoming:
            subject_name = event.subject.name if event.subject else "—"
            lines.append(
                f"  - {event.id} · {event.local_date.isoformat()} · {event.type} · "
                f"{event.title} · {subject_name}"
            )

    recent = db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.user_id == user.id)
        .order_by(AssistantMessage.created_at.desc())
        .limit(6)
    ).all()
    if recent:
        lines.append("Mensagens recentes (mais nova primeiro):")
        for message in recent:
            lines.append(f"  - {message.role}: {message.text[:160]}")

    return "\n".join(lines)


def _schedules_of(db: Session, subject_id: str):
    from agenda.models import ClassSchedule

    return db.scalars(
        select(ClassSchedule).where(
            ClassSchedule.subject_id == subject_id, ClassSchedule.active.is_(True)
        )
    ).all()
