"""Consultas do planner (SPEC §29-§35, §54).

As telas são desenhadas em torno das perguntas do estudante, não das tabelas
(SPEC §145): o que tenho hoje, o que vence, o que está atrasado.
"""
from __future__ import annotations

import calendar as _calendar
import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.core import recurrence
from agenda.core.dates import MONTH_LABELS, WEEKDAY_LABELS, WEEKDAY_SHORT
from agenda.core.events import event_card, tz_of
from agenda.models import Event, EventStatus, EventType, Subject, User


def today_of(user: User) -> dt.date:
    return dt.datetime.now(tz_of(user)).date()


def _events_between(
    db: Session,
    user: User,
    start: dt.date,
    end: dt.date,
    *,
    context_id: str | None = None,
    subject_id: str | None = None,
    include_cancelled: bool = False,
) -> list[Event]:
    stmt = select(Event).where(
        Event.user_id == user.id,
        Event.local_date >= start,
        Event.local_date <= end,
    )
    if context_id:
        stmt = stmt.where(Event.education_context_id == context_id)
    if subject_id:
        stmt = stmt.where(Event.subject_id == subject_id)
    if not include_cancelled:
        stmt = stmt.where(Event.status != EventStatus.CANCELLED.value)
    return list(db.scalars(stmt.order_by(Event.local_date, Event.starts_at)).all())


def day_items(
    db: Session, user: User, date: dt.date, *, context_id: str | None = None
) -> list[dict]:
    """Aulas + eventos do dia, ordenados por horário."""
    today = today_of(user)
    items = [
        occurrence.as_dict()
        for occurrence in recurrence.expand_classes(
            db, user.id, date, date, context_id=context_id
        )
    ]
    for item in items:
        item.update(
            {
                "id": f"class:{item['subject_id']}:{item['date']}:{item['start_time']}",
                "title": item["subject"],
                # O título já é a matéria: não repetimos no rodapé do card.
                "subject": "",
                "type_label": "Aula",
                "when": "",
                "time": item["start_time"],
                "is_deadline": False,
                "status": "CANCELLED" if item["cancelled"] else "UPCOMING",
            }
        )
    for event in _events_between(db, user, date, date, context_id=context_id):
        items.append(event_card(event, user, today=today))
    items.sort(key=lambda i: (i.get("time") or "99:99", i.get("title", "")))
    return items


def today_view(db: Session, user: User, *, context_id: str | None = None) -> dict:
    today = today_of(user)
    horizon = today + dt.timedelta(days=14)
    upcoming = [
        event_card(e, user, today=today)
        for e in _events_between(db, user, today + dt.timedelta(days=1), horizon, context_id=context_id)
        if e.status not in (EventStatus.COMPLETED.value,)
    ]
    overdue = [
        event_card(e, user, today=today)
        for e in _events_between(db, user, today - dt.timedelta(days=45), today - dt.timedelta(days=1), context_id=context_id)
        if e.status == EventStatus.OVERDUE.value
    ]
    items = day_items(db, user, today, context_id=context_id)
    next_class = next(
        (
            occurrence
            for occurrence in recurrence.expand_classes(
                db, user.id, today, today + dt.timedelta(days=14),
                context_id=context_id, include_cancelled=False,
            )
        ),
        None,
    )
    return {
        "date": today,
        "date_label": f"{WEEKDAY_LABELS[today.weekday()].capitalize()}, {today.day} de {MONTH_LABELS[today.month]}",
        "items": items,
        "upcoming": upcoming[:8],
        "overdue": overdue,
        "next_class": next_class.as_dict() if next_class else None,
        "week": week_summary(db, user, context_id=context_id),
    }


def week_view(
    db: Session, user: User, start: dt.date | None = None, *, context_id: str | None = None
) -> dict:
    today = today_of(user)
    start = start or (today - dt.timedelta(days=today.weekday()))
    end = start + dt.timedelta(days=6)
    days = []
    for offset in range(7):
        date = start + dt.timedelta(days=offset)
        days.append(
            {
                "date": date,
                "iso": date.isoformat(),
                "weekday": WEEKDAY_SHORT[date.weekday()],
                "day": date.day,
                "is_today": date == today,
                "items": day_items(db, user, date, context_id=context_id),
            }
        )
    return {"start": start, "end": end, "days": days, "today": today}


def month_view(
    db: Session, user: User, year: int, month: int, *, context_id: str | None = None
) -> dict:
    today = today_of(user)
    cal = _calendar.Calendar(firstweekday=0)  # semana começa na segunda
    weeks = cal.monthdatescalendar(year, month)
    start, end = weeks[0][0], weeks[-1][-1]

    per_day: dict[dt.date, list[dict]] = defaultdict(list)
    for event in _events_between(db, user, start, end, context_id=context_id):
        per_day[event.local_date].append(event_card(event, user, today=today))
    for occurrence in recurrence.expand_classes(
        db, user.id, start, end, context_id=context_id, include_cancelled=False
    ):
        per_day[occurrence.date].append({**occurrence.as_dict(), "title": occurrence.subject.display})

    grid = []
    for week in weeks:
        row = []
        for date in week:
            items = sorted(per_day.get(date, []), key=lambda i: i.get("time") or "99:99")
            row.append(
                {
                    "date": date,
                    "iso": date.isoformat(),
                    "day": date.day,
                    "in_month": date.month == month,
                    "is_today": date == today,
                    "items": items,
                    "colors": list(dict.fromkeys(i.get("color", "slate") for i in items))[:4],
                    "count": len(items),
                }
            )
        grid.append(row)
    return {
        "year": year,
        "month": month,
        "month_label": MONTH_LABELS[month].capitalize(),
        "weeks": grid,
        "weekday_labels": WEEKDAY_SHORT,
        "prev": (year - 1, 12) if month == 1 else (year, month - 1),
        "next": (year + 1, 1) if month == 12 else (year, month + 1),
    }


def agenda_view(
    db: Session,
    user: User,
    *,
    start: dt.date | None = None,
    days: int = 60,
    context_id: str | None = None,
) -> list[dict]:
    """Lista cronológica agrupada por dia (SPEC §32)."""
    today = today_of(user)
    start = start or today
    end = start + dt.timedelta(days=days)
    per_day: dict[dt.date, list[dict]] = defaultdict(list)
    for event in _events_between(db, user, start, end, context_id=context_id):
        per_day[event.local_date].append(event_card(event, user, today=today))
    for occurrence in recurrence.expand_classes(
        db, user.id, start, end, context_id=context_id, include_cancelled=False
    ):
        card = occurrence.as_dict()
        card["title"] = occurrence.subject.display
        card["type_label"] = "Aula"
        card["time"] = occurrence.start_time
        per_day[occurrence.date].append(card)

    out = []
    for date in sorted(per_day):
        items = sorted(per_day[date], key=lambda i: i.get("time") or "99:99")
        out.append(
            {
                "date": date,
                "day": f"{date.day:02d}",
                "weekday": WEEKDAY_SHORT[date.weekday()].upper(),
                "month_label": MONTH_LABELS[date.month].upper(),
                "is_today": date == today,
                "items": items,
            }
        )
    return out


def deadlines_view(db: Session, user: User, *, context_id: str | None = None) -> list[dict]:
    """Entregas agrupadas por urgência (SPEC §35)."""
    today = today_of(user)
    horizon = today + dt.timedelta(days=180)
    events = [
        e
        for e in _events_between(db, user, today - dt.timedelta(days=90), horizon, context_id=context_id)
        if e.is_deadline and e.status != EventStatus.COMPLETED.value
    ]
    buckets: dict[str, list[dict]] = {
        "Atrasados": [], "Hoje": [], "Esta semana": [], "Próxima semana": [], "Depois": [],
    }
    end_of_week = today + dt.timedelta(days=6 - today.weekday())
    end_of_next_week = end_of_week + dt.timedelta(days=7)
    for event in sorted(events, key=lambda e: e.local_date):
        card = event_card(event, user, today=today)
        if event.local_date < today:
            buckets["Atrasados"].append(card)
        elif event.local_date == today:
            buckets["Hoje"].append(card)
        elif event.local_date <= end_of_week:
            buckets["Esta semana"].append(card)
        elif event.local_date <= end_of_next_week:
            buckets["Próxima semana"].append(card)
        else:
            buckets["Depois"].append(card)
    return [{"label": k, "items": v} for k, v in buckets.items() if v]


def timeline_view(db: Session, user: User, *, context_id: str | None = None) -> dict:
    """Linha do tempo do período com heatmap de carga (SPEC §33)."""
    today = today_of(user)
    start = today - dt.timedelta(days=30)
    end = today + dt.timedelta(days=150)
    events = _events_between(db, user, start, end, context_id=context_id)

    per_week: dict[dt.date, list[Event]] = defaultdict(list)
    for event in events:
        week_start = event.local_date - dt.timedelta(days=event.local_date.weekday())
        per_week[week_start].append(event)

    weeks = []
    max_load = 1
    week_start = start - dt.timedelta(days=start.weekday())
    while week_start <= end:
        bucket = per_week.get(week_start, [])
        load = sum(3 if e.type in (EventType.EXAM.value, EventType.PROJECT.value) else 1 for e in bucket)
        max_load = max(max_load, load)
        weeks.append(
            {
                "start": week_start,
                "label": f"{week_start.day:02d}/{week_start.month:02d}",
                "month": MONTH_LABELS[week_start.month][:3],
                "count": len(bucket),
                "load": load,
                "is_current": week_start <= today <= week_start + dt.timedelta(days=6),
                "items": [event_card(e, user, today=today) for e in bucket],
            }
        )
        week_start += dt.timedelta(days=7)
    for week in weeks:
        week["intensity"] = round(week["load"] / max_load, 2) if max_load else 0
    return {"weeks": weeks, "max_load": max_load}


def subject_view(db: Session, user: User, subject: Subject) -> dict:
    today = today_of(user)
    upcoming = [
        event_card(e, user, today=today)
        for e in _events_between(db, user, today, today + dt.timedelta(days=365), subject_id=subject.id)
    ]
    past = [
        event_card(e, user, today=today)
        for e in _events_between(db, user, today - dt.timedelta(days=365), today - dt.timedelta(days=1), subject_id=subject.id)
    ]
    occurrences = recurrence.expand_classes(
        db, user.id, today, today + dt.timedelta(days=21), subject_id=subject.id, include_cancelled=False
    )
    schedules = db.scalars(
        select(recurrence.ClassSchedule).where(
            recurrence.ClassSchedule.subject_id == subject.id,
            recurrence.ClassSchedule.active.is_(True),
        )
    ).all()
    return {
        "subject": subject,
        "upcoming": upcoming,
        "past": list(reversed(past))[:20],
        "next_class": occurrences[0].as_dict() if occurrences else None,
        "schedules": [
            {
                "weekday": WEEKDAY_LABELS[s.weekday].capitalize(),
                "weekday_short": WEEKDAY_SHORT[s.weekday],
                "start_time": s.start_time,
                "end_time": s.end_time,
                "location": (s.location or subject.default_location).label
                if (s.location or subject.default_location)
                else "",
            }
            for s in sorted(schedules, key=lambda s: (s.weekday, s.start_time))
        ],
    }


def week_summary(db: Session, user: User, *, context_id: str | None = None) -> dict:
    """Resumo textual da semana, sem alarmismo (SPEC §54)."""
    today = today_of(user)
    start = today - dt.timedelta(days=today.weekday())
    end = start + dt.timedelta(days=6)
    events = _events_between(db, user, start, end, context_id=context_id)
    classes = recurrence.expand_classes(
        db, user.id, start, end, context_id=context_id, include_cancelled=False
    )

    counts = {
        "aulas": len(classes),
        "provas": sum(1 for e in events if e.type in (EventType.EXAM.value, EventType.SIMULATION.value, EventType.QUIZ.value)),
        "entregas": sum(1 for e in events if e.is_deadline and e.type not in (EventType.EXAM.value, EventType.QUIZ.value)),
    }
    load_by_day: dict[dt.date, int] = defaultdict(int)
    for event in events:
        load_by_day[event.local_date] += 3 if event.type == EventType.EXAM.value else 1
    for occurrence in classes:
        load_by_day[occurrence.date] += 1

    heaviest = max(load_by_day.items(), key=lambda kv: kv[1])[0] if load_by_day else None
    priorities = [
        event_card(e, user, today=today)
        for e in sorted(
            (e for e in events if e.is_deadline and e.status != EventStatus.COMPLETED.value and e.local_date >= today),
            key=lambda e: (e.local_date, 0 if e.type == EventType.EXAM.value else 1),
        )[:3]
    ]
    done = sum(1 for e in events if e.status == EventStatus.COMPLETED.value)
    total = sum(1 for e in events if e.is_deadline)
    return {
        "start": start,
        "end": end,
        "counts": counts,
        "heaviest_day": WEEKDAY_LABELS[heaviest.weekday()] if heaviest else "",
        "priorities": priorities,
        "progress": round(done / total * 100) if total else 0,
        "done": done,
        "total": total,
    }


def search(db: Session, user: User, query: str, *, limit: int = 30) -> dict:
    """Busca universal simples (SPEC §55)."""
    from agenda.core.text import norm

    q = norm(query)
    if not q:
        return {"events": [], "subjects": []}
    today = today_of(user)
    events = [
        event_card(e, user, today=today)
        for e in db.scalars(select(Event).where(Event.user_id == user.id).order_by(Event.local_date.desc())).all()
        if q in norm(f"{e.title} {e.description} {e.subject.name if e.subject else ''}")
    ][:limit]
    subjects = [
        s
        for s in db.scalars(select(Subject).where(Subject.user_id == user.id)).all()
        if q in norm(f"{s.name} {s.short_name}")
    ]
    return {"events": events, "subjects": subjects}
