"""Cálculo e agendamento de lembretes (SPEC §49, §50, §76).

Modelo: evento → regras de lembrete → notificações agendadas (linhas no banco)
→ fila → entrega. Nada de cron varrendo todos os eventos: ao alterar um evento
cancelamos os lembretes pendentes e recalculamos.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.models import DeliveryStatus, Event, EventReminder, EventType, User

# Perfis por tipo de evento (SPEC §50). O usuário sempre pode sobrescrever.
SMART_OFFSETS: dict[str, list[int]] = {
    EventType.EXAM.value: [7, 3, 1],
    EventType.SIMULATION.value: [7, 3, 1],
    EventType.QUIZ.value: [3, 1],
    EventType.PROJECT.value: [14, 7, 2, 1],
    EventType.PAPER.value: [14, 7, 2, 1],
    EventType.MATERIAL.value: [1, 0],
    EventType.READING.value: [3, 1],
    EventType.PRESENTATION.value: [7, 2, 1],
    EventType.SEMINAR.value: [7, 2, 1],
}


def user_tz(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone)
    except Exception:  # noqa: BLE001 - fuso inválido não pode derrubar o app
        return config.TZ


def offsets_for(event: Event, user: User, *, smart: bool = True) -> list[int]:
    """Quais antecedências aplicar a este evento."""
    if smart and event.type in SMART_OFFSETS:
        base = SMART_OFFSETS[event.type]
    else:
        base = user.reminder_offsets() or config.DEFAULT_REMINDER_DAYS
    return sorted({o for o in base if o >= 0}, reverse=True)


def reminder_moment(event: Event, offset_days: int, tz: ZoneInfo) -> dt.datetime:
    """Instante (UTC) em que o lembrete de ``offset_days`` deve sair.

    Para o dia do próprio evento com horário definido, avisamos 2h antes;
    nos demais casos, no horário matinal configurado.
    """
    target_day = event.local_date - dt.timedelta(days=offset_days)
    if offset_days == 0 and event.starts_at is not None:
        return event.starts_at - dt.timedelta(hours=2)
    local = dt.datetime.combine(
        target_day, dt.time(config.REMINDER_HOUR, config.REMINDER_MINUTE), tzinfo=tz
    )
    return local.astimezone(dt.timezone.utc)


def schedule_reminders(
    db: Session, event: Event, user: User, *, now: dt.datetime | None = None
) -> list[EventReminder]:
    """(Re)calcula os lembretes do evento. Cancela os pendentes antes.

    Se o evento foi cadastrado com menos de 7 dias de antecedência, apenas os
    lembretes ainda possíveis são criados (SPEC §49).
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    tz = user_tz(user)

    pending = db.scalars(
        select(EventReminder).where(
            EventReminder.event_id == event.id,
            EventReminder.status.in_(
                [DeliveryStatus.PENDING.value, DeliveryStatus.QUEUED.value]
            ),
        )
    ).all()
    for reminder in pending:
        db.delete(reminder)

    if event.status in ("COMPLETED", "CANCELLED"):
        return []

    created: list[EventReminder] = []
    for offset in offsets_for(event, user):
        moment = reminder_moment(event, offset, tz)
        if moment <= now:
            continue  # antecedência já passou — não avisamos no passado
        reminder = EventReminder(
            event_id=event.id,
            user_id=user.id,
            offset_days=offset,
            scheduled_for=moment,
            channel="auto",
            status=DeliveryStatus.PENDING.value,
        )
        db.add(reminder)
        created.append(reminder)

    if not created and event.local_date >= now.astimezone(tz).date():
        # Evento muito próximo: pelo menos um aviso imediato faz sentido.
        reminder = EventReminder(
            event_id=event.id,
            user_id=user.id,
            offset_days=0,
            scheduled_for=now + dt.timedelta(minutes=1),
            channel="auto",
            status=DeliveryStatus.PENDING.value,
        )
        db.add(reminder)
        created.append(reminder)
    return created


def due_reminders(db: Session, *, now: dt.datetime | None = None, limit: int = 200):
    now = now or dt.datetime.now(dt.timezone.utc)
    return list(
        db.scalars(
            select(EventReminder)
            .where(
                EventReminder.status == DeliveryStatus.PENDING.value,
                EventReminder.scheduled_for <= now,
            )
            .order_by(EventReminder.scheduled_for)
            .limit(limit)
        ).all()
    )


def describe_offset(offset_days: int) -> str:
    if offset_days == 0:
        return "no dia"
    if offset_days == 1:
        return "1 dia antes"
    if offset_days == 7:
        return "1 semana antes"
    if offset_days == 14:
        return "2 semanas antes"
    return f"{offset_days} dias antes"
