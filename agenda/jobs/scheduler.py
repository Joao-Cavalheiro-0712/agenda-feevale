"""Workers em background (SPEC §76, §99).

No MVP rodam como threads do processo web; a fronteira já está desenhada para
virar filas (BullMQ/Celery) sem tocar no núcleo.
"""
from __future__ import annotations

import datetime as dt
import os

from apscheduler.schedulers.background import BackgroundScheduler

from agenda import config
from agenda.core import notifications
from agenda.db import session_scope

_scheduler: BackgroundScheduler | None = None


def notification_tick() -> int:
    """notification.send — entrega os lembretes vencidos."""
    with session_scope() as db:
        sent = notifications.run_due_reminders(db)
    if sent:
        print(f"[jobs] {sent} lembrete(s) entregue(s).")
    return sent


def refresh_statuses() -> int:
    """event.reconcile — marca atrasados."""
    from sqlalchemy import select

    from agenda.core.events import refresh_statuses as refresh
    from agenda.models import User

    changed = 0
    with session_scope() as db:
        for user in db.scalars(select(User).where(User.deleted_at.is_(None))).all():
            changed += refresh(db, user)
    return changed


def cleanup_media() -> int:
    """cleanup.media — política de retenção de áudio/documento (SPEC §82, §83)."""
    from sqlalchemy import select

    from agenda.models import ChannelMessage, Document

    removed = 0
    now = dt.datetime.now(dt.timezone.utc)
    with session_scope() as db:
        if config.AUDIO_RETENTION_DAYS > 0:
            cutoff = now - dt.timedelta(days=config.AUDIO_RETENTION_DAYS)
            for message in db.scalars(
                select(ChannelMessage).where(
                    ChannelMessage.kind == "audio",
                    ChannelMessage.created_at < cutoff,
                    ChannelMessage.media_id != "",
                )
            ).all():
                message.media_id = ""  # guardamos apenas a transcrição
                removed += 1
        if config.DOCUMENT_RETENTION_DAYS > 0:
            cutoff = now - dt.timedelta(days=config.DOCUMENT_RETENTION_DAYS)
            for document in db.scalars(
                select(Document).where(
                    Document.created_at < cutoff, Document.storage_path != ""
                )
            ).all():
                try:
                    if os.path.exists(document.storage_path):
                        os.remove(document.storage_path)
                except OSError:
                    pass
                document.storage_path = ""
                removed += 1
    return removed


def start() -> None:  # pragma: no cover - infra
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone=config.TZ)
    _scheduler.add_job(
        notification_tick, "interval", seconds=config.NOTIFICATION_TICK_SECONDS,
        id="notification.send", replace_existing=True, max_instances=1,
    )
    _scheduler.add_job(
        refresh_statuses, "cron", hour=0, minute=5, id="event.reconcile", replace_existing=True
    )
    _scheduler.add_job(
        cleanup_media, "cron", hour=3, minute=0, id="cleanup.media", replace_existing=True
    )
    _scheduler.start()
    print(f"[jobs] scheduler ativo (tick {config.NOTIFICATION_TICK_SECONDS}s).")
