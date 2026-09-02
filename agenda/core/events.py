"""Criação, atualização e serialização de eventos.

Este módulo é o único ponto que escreve na tabela ``events`` — web, WhatsApp
e importação de documentos passam todos por aqui (SPEC §144).
"""
from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from agenda.core import duplicates, reminders
from agenda.core.dates import WEEKDAY_SHORT, human_delta
from agenda.models import (
    AuditLog,
    Event,
    EventStatus,
    EventType,
    Location,
    SourceType,
    Subject,
    User,
)

# Rótulos por nível educacional (SPEC §47: a UI pode usar nomes diferentes).
TYPE_LABELS = {
    EventType.CLASS.value: "Aula",
    EventType.EXAM.value: "Prova",
    EventType.QUIZ.value: "Teste",
    EventType.ASSIGNMENT.value: "Trabalho",
    EventType.HOMEWORK.value: "Tarefa de casa",
    EventType.PROJECT.value: "Projeto",
    EventType.PRESENTATION.value: "Apresentação",
    EventType.READING.value: "Leitura",
    EventType.MATERIAL.value: "Material",
    EventType.LAB.value: "Laboratório",
    EventType.SIMULATION.value: "Simulado",
    EventType.SEMINAR.value: "Seminário",
    EventType.PAPER.value: "Artigo",
    EventType.INTERNSHIP.value: "Estágio",
    EventType.SCHOOL_EVENT.value: "Evento escolar",
    EventType.ADMINISTRATIVE.value: "Prazo administrativo",
    EventType.REMINDER.value: "Lembrete",
    EventType.OTHER.value: "Compromisso",
}

TYPE_LABELS_SCHOOL = {
    **TYPE_LABELS,
    EventType.ASSIGNMENT.value: "Trabalho",
    EventType.PAPER.value: "Pesquisa",
    EventType.ADMINISTRATIVE.value: "Aviso da escola",
}


def type_label(event_type: str, education_type: str = "") -> str:
    if education_type in ("ELEMENTARY", "MIDDLE_SCHOOL"):
        return TYPE_LABELS_SCHOOL.get(event_type, "Compromisso")
    return TYPE_LABELS.get(event_type, "Compromisso")


def tz_of(user: User) -> ZoneInfo:
    return reminders.user_tz(user)


def local_datetime(date: dt.date, time_str: str | None, tz: ZoneInfo) -> dt.datetime | None:
    if not time_str:
        return None
    try:
        hour, minute = (int(p) for p in time_str.split(":")[:2])
    except ValueError:
        return None
    return dt.datetime.combine(date, dt.time(hour, minute), tzinfo=tz).astimezone(dt.timezone.utc)


def create_event(
    db: Session,
    user: User,
    *,
    title: str,
    event_type: str,
    date: dt.date,
    subject: Subject | None = None,
    context_id: str | None = None,
    description: str = "",
    start_time: str | None = None,
    end_time: str | None = None,
    location: Location | None = None,
    confidence: float = 1.0,
    source_type: str = SourceType.MANUAL.value,
    source_id: str | None = None,
    source_reference: dict | None = None,
    created_by: str = "user",
    checklist: list | None = None,
    weight: float | None = None,
    max_grade: float | None = None,
    group_work: bool = False,
    schedule: bool = True,
) -> Event:
    tz = tz_of(user)
    starts_at = local_datetime(date, start_time, tz)
    ends_at = local_datetime(date, end_time, tz)
    event = Event(
        user_id=user.id,
        education_context_id=context_id or (subject.education_context_id if subject else None),
        subject_id=subject.id if subject else None,
        type=event_type,
        title=title.strip()[:300],
        description=description.strip(),
        local_date=date,
        all_day=start_time is None,
        starts_at=starts_at,
        ends_at=ends_at,
        due_at=starts_at or local_datetime(date, "23:59", tz),
        location_id=location.id if location else (subject.default_location_id if subject else None),
        status=EventStatus.UPCOMING.value,
        confidence=confidence,
        source_type=source_type,
        source_id=source_id,
        source_reference=source_reference,
        created_by=created_by,
        checklist=checklist,
        weight=weight,
        max_grade=max_grade,
        group_work=group_work,
    )
    event.fingerprint = duplicates.fingerprint(
        user_id=user.id,
        subject_id=event.subject_id,
        event_type=event_type,
        date=date,
        title=title,
    )
    db.add(event)
    db.flush()
    if schedule:
        reminders.schedule_reminders(db, event, user)
    return event


def update_event(db: Session, user: User, event: Event, changes: dict[str, Any]) -> Event:
    """Aplica alterações e recalcula lembretes quando a data muda."""
    tz = tz_of(user)
    date_changed = False

    if "title" in changes and changes["title"]:
        event.title = str(changes["title"]).strip()[:300]
    if "description" in changes and changes["description"] is not None:
        event.description = str(changes["description"]).strip()
    if "type" in changes and changes["type"]:
        event.type = str(changes["type"])
    if "subject_id" in changes:
        event.subject_id = changes["subject_id"]
    if "location_id" in changes:
        event.location_id = changes["location_id"]
    if "status" in changes and changes["status"]:
        event.status = str(changes["status"])
    if "weight" in changes:
        event.weight = changes["weight"]
    if "checklist" in changes:
        event.checklist = changes["checklist"]

    if changes.get("date"):
        new_date = changes["date"]
        if isinstance(new_date, str):
            new_date = dt.date.fromisoformat(new_date)
        date_changed = new_date != event.local_date
        event.local_date = new_date

    if "start_time" in changes:
        start_time = changes["start_time"]
        event.all_day = not start_time
        event.starts_at = local_datetime(event.local_date, start_time, tz)
        date_changed = True
    elif date_changed and event.starts_at is not None:
        old_local = event.starts_at.astimezone(tz)
        event.starts_at = local_datetime(
            event.local_date, old_local.strftime("%H:%M"), tz
        )

    if "end_time" in changes:
        event.ends_at = local_datetime(event.local_date, changes["end_time"], tz)

    if date_changed:
        event.due_at = event.starts_at or local_datetime(event.local_date, "23:59", tz)

    event.fingerprint = duplicates.fingerprint(
        user_id=user.id,
        subject_id=event.subject_id,
        event_type=event.type,
        date=event.local_date,
        title=event.title,
    )
    event.updated_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    reminders.schedule_reminders(db, event, user)
    return event


def complete_event(db: Session, event: Event, *, done: bool = True) -> Event:
    event.status = EventStatus.COMPLETED.value if done else EventStatus.UPCOMING.value
    event.completed_at = dt.datetime.now(dt.timezone.utc) if done else None
    db.flush()
    return event


def snapshot(event: Event) -> dict:
    """Estado serializável para permitir undo (SPEC §27)."""
    return {
        "id": event.id,
        "user_id": event.user_id,
        "education_context_id": event.education_context_id,
        "subject_id": event.subject_id,
        "type": event.type,
        "title": event.title,
        "description": event.description,
        "local_date": event.local_date.isoformat() if event.local_date else None,
        "all_day": event.all_day,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "due_at": event.due_at.isoformat() if event.due_at else None,
        "location_id": event.location_id,
        "status": event.status,
        "checklist": event.checklist,
        "weight": event.weight,
        "max_grade": event.max_grade,
        "confidence": event.confidence,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "source_reference": event.source_reference,
        "created_by": event.created_by,
        "fingerprint": event.fingerprint,
    }


def restore(db: Session, user: User, data: dict) -> Event:
    """Recria/reverte um evento a partir de um snapshot."""
    event = db.get(Event, data["id"])
    if event is None:
        event = Event(id=data["id"], user_id=user.id)
        db.add(event)
    for field in (
        "education_context_id", "subject_id", "type", "title", "description",
        "all_day", "location_id", "status", "checklist", "weight", "max_grade",
        "confidence", "source_type", "source_id", "source_reference", "created_by",
        "fingerprint",
    ):
        if field in data:
            setattr(event, field, data[field])
    if data.get("local_date"):
        event.local_date = dt.date.fromisoformat(data["local_date"])
    for field in ("starts_at", "ends_at", "due_at"):
        raw = data.get(field)
        setattr(event, field, dt.datetime.fromisoformat(raw) if raw else None)
    db.flush()
    reminders.schedule_reminders(db, event, user)
    return event


def event_card(event: Event, user: User, *, today: dt.date | None = None) -> dict:
    """Representação usada pela UI, pelo WhatsApp e pelas respostas do assistente."""
    tz = tz_of(user)
    today = today or dt.datetime.now(tz).date()
    time_label = ""
    if not event.all_day and event.starts_at:
        time_label = event.starts_at.astimezone(tz).strftime("%H:%M")
    subject = event.subject
    location = event.location
    return {
        "id": event.id,
        "title": event.title,
        "type": event.type,
        "type_label": type_label(event.type),
        "date": event.local_date.isoformat(),
        "date_label": f"{WEEKDAY_SHORT[event.local_date.weekday()]} {event.local_date.strftime('%d/%m')}",
        "when": human_delta(event.local_date, today),
        "time": time_label,
        "all_day": event.all_day,
        "subject": subject.display if subject else "",
        "subject_id": event.subject_id,
        "color": subject.color if subject else "slate",
        "location": location.label if location else "",
        "description": event.description,
        "status": event.status,
        "is_deadline": event.is_deadline,
        "days_left": (event.local_date - today).days,
        "confidence": event.confidence,
        "source_type": event.source_type,
        "checklist": event.checklist or [],
    }


def log(
    db: Session,
    *,
    user_id: str | None,
    actor: str,
    action: str,
    object_type: str = "",
    object_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    origin: str = "web",
    confidence: float | None = None,
    ai_model: str = "",
    prompt_version: str = "",
) -> None:
    """Registro de auditoria (SPEC §84)."""
    db.add(
        AuditLog(
            user_id=user_id,
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            before_state=before,
            after_state=after,
            origin=origin,
            confidence=confidence,
            ai_model=ai_model,
            prompt_version=prompt_version,
        )
    )


def refresh_statuses(db: Session, user: User) -> int:
    """Marca como OVERDUE o que passou do prazo sem conclusão."""
    tz = tz_of(user)
    today = dt.datetime.now(tz).date()
    changed = 0
    stale = (
        db.query(Event)
        .filter(
            Event.user_id == user.id,
            Event.local_date < today,
            Event.status == EventStatus.UPCOMING.value,
        )
        .all()
    )
    for event in stale:
        event.status = (
            EventStatus.OVERDUE.value if event.is_deadline else EventStatus.COMPLETED.value
        )
        changed += 1
    return changed
