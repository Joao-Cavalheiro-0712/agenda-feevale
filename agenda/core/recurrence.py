"""Expansão de aulas recorrentes respeitando exceções (SPEC §45, §135).

As aulas não são materializadas como linhas em ``events``: elas são geradas
sob demanda a partir de ``class_schedules``, o que mantém o banco enxuto e
permite que feriados/recessos importados alterem o passado e o futuro sem
precisar reescrever registros.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.models import ClassSchedule, ScheduleException, Subject


@dataclass
class ClassOccurrence:
    date: dt.date
    start_time: str
    end_time: str
    subject: Subject
    schedule: ClassSchedule
    location_label: str = ""
    cancelled: bool = False
    note: str = ""
    kind: str = "CLASS"

    @property
    def sort_key(self) -> tuple:
        return (self.date, self.start_time)

    def as_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "subject_id": self.subject.id,
            "subject": self.subject.display,
            "color": self.subject.color,
            "location": self.location_label,
            "cancelled": self.cancelled,
            "note": self.note,
            "type": "CLASS",
        }


@dataclass
class ExceptionIndex:
    """Exceções indexadas por data para consulta O(1)."""

    by_date: dict[dt.date, list[ScheduleException]] = field(default_factory=dict)

    def add(self, exc: ScheduleException) -> None:
        end = exc.end_date or exc.date
        day = exc.date
        while day <= end:
            self.by_date.setdefault(day, []).append(exc)
            day += dt.timedelta(days=1)

    def for_schedule(self, date: dt.date, schedule_id: str) -> ScheduleException | None:
        for exc in self.by_date.get(date, []):
            if exc.schedule_id in (None, schedule_id):
                return exc
        return None


def load_exceptions(db: Session, user_id: str, start: dt.date, end: dt.date) -> ExceptionIndex:
    rows = db.scalars(
        select(ScheduleException).where(
            ScheduleException.user_id == user_id,
            ScheduleException.date <= end,
        )
    ).all()
    index = ExceptionIndex()
    for row in rows:
        if (row.end_date or row.date) >= start:
            index.add(row)
    return index


def expand_classes(
    db: Session,
    user_id: str,
    start: dt.date,
    end: dt.date,
    *,
    context_id: str | None = None,
    subject_id: str | None = None,
    include_cancelled: bool = True,
) -> list[ClassOccurrence]:
    """Todas as ocorrências de aula no intervalo [start, end]."""
    if end < start:
        return []

    stmt = (
        select(ClassSchedule)
        .join(Subject, Subject.id == ClassSchedule.subject_id)
        .where(ClassSchedule.user_id == user_id, ClassSchedule.active.is_(True))
    )
    if context_id:
        stmt = stmt.where(Subject.education_context_id == context_id)
    if subject_id:
        stmt = stmt.where(ClassSchedule.subject_id == subject_id)
    schedules = db.scalars(stmt).all()
    if not schedules:
        return []

    exceptions = load_exceptions(db, user_id, start, end)
    out: list[ClassOccurrence] = []

    for schedule in schedules:
        subject = schedule.subject
        if subject is None or subject.status in ("ARCHIVED", "DROPPED"):
            continue
        window_start = max(start, schedule.start_date or start)
        window_end = min(end, schedule.end_date or end)
        if window_end < window_start:
            continue

        delta = (schedule.weekday - window_start.weekday()) % 7
        day = window_start + dt.timedelta(days=delta)
        location = schedule.location or subject.default_location
        label = location.label if location else ""

        while day <= window_end:
            exc = exceptions.for_schedule(day, schedule.id)
            cancelled = False
            note = ""
            if exc is not None:
                if exc.kind in ("HOLIDAY", "BREAK", "CANCELLED"):
                    cancelled = True
                    note = exc.label or {
                        "HOLIDAY": "Feriado",
                        "BREAK": "Recesso",
                        "CANCELLED": "Aula cancelada",
                    }.get(exc.kind, "")
                elif exc.kind == "MOVED" and exc.moved_to:
                    note = f"Remarcada para {exc.moved_to.strftime('%d/%m')}"
                    cancelled = True
            if cancelled and not include_cancelled:
                day += dt.timedelta(days=7)
                continue
            out.append(
                ClassOccurrence(
                    date=day,
                    start_time=schedule.start_time,
                    end_time=schedule.end_time,
                    subject=subject,
                    schedule=schedule,
                    location_label=label,
                    cancelled=cancelled,
                    note=note,
                )
            )
            day += dt.timedelta(days=7)

    out.sort(key=lambda o: o.sort_key)
    return out


def next_class_date(
    db: Session, user_id: str, subject_id: str, after: dt.date, *, horizon_days: int = 120
) -> dt.date | None:
    """Data da próxima aula de uma disciplina — âncora para "na próxima aula"."""
    occurrences = expand_classes(
        db,
        user_id,
        after + dt.timedelta(days=1),
        after + dt.timedelta(days=horizon_days),
        subject_id=subject_id,
        include_cancelled=False,
    )
    return occurrences[0].date if occurrences else None
