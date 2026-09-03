"""Central de notificações e entrega multicanal (SPEC §51, §52, §76, §77)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agenda.core import reminders as reminders_core
from agenda.core.dates import format_date_pt
from agenda.models import (
    DeliveryStatus,
    Event,
    EventReminder,
    Notification,
    NotificationDelivery,
    User,
)

TYPE_EMOJI = {
    "EXAM": "📝", "QUIZ": "📝", "SIMULATION": "📝",
    "ASSIGNMENT": "📚", "PROJECT": "📚", "PAPER": "📚", "HOMEWORK": "✏️",
    "MATERIAL": "🎒", "PRESENTATION": "🎤", "SEMINAR": "🎤", "READING": "📖",
    "CLASS": "🎓", "LAB": "🔬",
}


def reminder_text(event: Event, offset_days: int) -> tuple[str, str]:
    emoji = TYPE_EMOJI.get(event.type, "🔔")
    when = {0: "é hoje", 1: "é amanhã"}.get(offset_days, f"é em {offset_days} dias")
    subject = f" · {event.subject.display}" if event.subject else ""
    title = f"{emoji} {event.title} {when}"
    body_lines = [f"{event.title}{subject}", format_date_pt(event.local_date)]
    if event.description:
        body_lines.append(event.description[:200])
    if event.location:
        body_lines.append(event.location.label)
    return title, "\n".join(body_lines)


def create(
    db: Session, user: User, *, title: str, body: str, event_id: str | None = None, kind: str = "reminder"
) -> Notification:
    notification = Notification(
        user_id=user.id, title=title[:200], body=body, event_id=event_id, kind=kind
    )
    db.add(notification)
    db.flush()
    return notification


def deliver(db: Session, user: User, notification: Notification) -> list[str]:
    """Entrega pelos canais disponíveis. In-app é sempre garantido."""
    from agenda.channels import push, telegram, whatsapp
    from agenda.core import billing, family

    channels: list[str] = ["inapp"]
    db.add(
        NotificationDelivery(
            notification_id=notification.id, channel="inapp", status=DeliveryStatus.DELIVERED.value
        )
    )

    # Web Push chega mesmo com o app fechado e não depende de plano.
    if push.can_send(db, user):
        enviados = push.send(
            db, user,
            title=notification.title,
            body=notification.body,
            url=f"/evento/{notification.event_id}" if notification.event_id else "/hoje",
        )
        db.add(
            NotificationDelivery(
                notification_id=notification.id,
                channel="webpush",
                status=DeliveryStatus.SENT.value if enviados else DeliveryStatus.FAILED.value,
            )
        )
        if enviados:
            channels.append("webpush")

    # Responsáveis que pediram para acompanhar recebem uma cópia (SPEC §59).
    for guardian in family.reminder_recipients(db, user):
        copia = Notification(
            user_id=guardian.id,
            title=notification.title,
            body=f"{user.name or 'Seu estudante'}: {notification.body}",
            event_id=None,
            kind="guardian",
        )
        db.add(copia)
        if push.can_send(db, guardian):
            push.send(db, guardian, title=copia.title, body=copia.body)
        channels.append("guardian")

    text = f"*{notification.title}*\n{notification.body}"
    if whatsapp.can_send(db, user) and billing.allows(db, user, billing.CAN_USE_WHATSAPP):
        ok, provider_id, error = whatsapp.send_text(db, user, text)
        db.add(
            NotificationDelivery(
                notification_id=notification.id,
                channel="whatsapp",
                status=DeliveryStatus.SENT.value if ok else DeliveryStatus.FAILED.value,
                provider_message_id=provider_id,
                error=error,
            )
        )
        if ok:
            channels.append("whatsapp")
    if telegram.can_send(db, user):
        ok = telegram.send_text(db, user, f"<b>{notification.title}</b>\n{notification.body}")
        db.add(
            NotificationDelivery(
                notification_id=notification.id,
                channel="telegram",
                status=DeliveryStatus.SENT.value if ok else DeliveryStatus.FAILED.value,
            )
        )
        if ok:
            channels.append("telegram")
    db.flush()
    return channels


def _claim(db: Session, reminder: EventReminder) -> bool:
    """Reivindica o lembrete de forma atômica.

    A aplicação roda com vários workers, cada um com seu agendador. Em vez de
    eleger um worker (frágil), cada linha é disputada com um UPDATE
    condicional: só quem consegue mudar PENDING → QUEUED entrega. Assim o
    lembrete sai uma vez só, mesmo com N processos rodando ao mesmo tempo.
    """
    resultado = db.execute(
        update(EventReminder)
        .where(
            EventReminder.id == reminder.id,
            EventReminder.status == DeliveryStatus.PENDING.value,
        )
        .values(status=DeliveryStatus.QUEUED.value)
    )
    if resultado.rowcount != 1:
        return False
    db.commit()  # fecha a disputa antes de gastar tempo entregando
    return True


def run_due_reminders(db: Session, *, now: dt.datetime | None = None) -> int:
    """Processa a fila de lembretes vencidos. Chamado pelo worker."""
    now = now or dt.datetime.now(dt.timezone.utc)
    sent = 0
    for reminder in reminders_core.due_reminders(db, now=now):
        if not _claim(db, reminder):
            continue  # outro worker pegou este

        event = db.get(Event, reminder.event_id)
        user = db.get(User, reminder.user_id)
        if event is None or user is None or event.status in ("COMPLETED", "CANCELLED"):
            reminder.status = DeliveryStatus.CANCELLED.value
            db.commit()
            continue

        title, body = reminder_text(event, reminder.offset_days)
        notification = create(db, user, title=title, body=body, event_id=event.id)
        deliver(db, user, notification)
        reminder.status = DeliveryStatus.SENT.value
        reminder.sent_at = now
        sent += 1
        db.commit()
    return sent


def unread_count(db: Session, user_id: str) -> int:
    return len(
        db.scalars(
            select(Notification).where(
                Notification.user_id == user_id, Notification.read_at.is_(None)
            )
        ).all()
    )


def mark_read(db: Session, user_id: str, notification_id: str | None = None) -> int:
    stmt = select(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )
    if notification_id:
        stmt = stmt.where(Notification.id == notification_id)
    rows = db.scalars(stmt).all()
    now = dt.datetime.now(dt.timezone.utc)
    for row in rows:
        row.read_at = now
    db.flush()
    return len(rows)


def cancel_for_event(db: Session, event_id: str) -> None:
    for reminder in db.scalars(
        select(EventReminder).where(
            EventReminder.event_id == event_id,
            EventReminder.status == DeliveryStatus.PENDING.value,
        )
    ).all():
        reminder.status = DeliveryStatus.CANCELLED.value
