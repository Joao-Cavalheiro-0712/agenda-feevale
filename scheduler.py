"""Job diário que dispara os lembretes (1 semana antes e 1 dia antes)."""
from __future__ import annotations

import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler

import config
import telegram_bot
from database import Event, SessionLocal

_KIND_EMOJI = {"prova": "📝", "trabalho": "📚", "avaliacao": "🎓"}
_scheduler: BackgroundScheduler | None = None


def _format_reminder(event: Event, days: int) -> str:
    emoji = _KIND_EMOJI.get(event.kind, "🎓")
    quando = "amanhã" if days == 1 else f"em {days} dias"
    if days == 0:
        quando = "hoje"
    disc = f"\n📖 Disciplina: {event.discipline}" if event.discipline else ""
    notes = f"\n📌 {event.notes}" if event.notes else ""
    return (
        f"{emoji} <b>Lembrete Feevale</b>\n\n"
        f"<b>{event.title}</b> é <b>{quando}</b> "
        f"({event.date.strftime('%d/%m/%Y')})!{disc}{notes}"
    )


def run_reminders(now: dt.date | None = None) -> int:
    """Verifica eventos e envia lembretes pendentes. Retorna quantos avisos enviou."""
    today = now or dt.datetime.now(config.TIMEZONE).date()
    sent = 0
    with SessionLocal() as db:
        upcoming = db.query(Event).filter(Event.date >= today).all()
        for event in upcoming:
            days_until = (event.date - today).days
            if days_until in config.REMINDER_DAYS and days_until not in event.days_notified():
                if telegram_bot.broadcast(_format_reminder(event, days_until)) > 0:
                    event.mark_notified(days_until)
                    sent += 1
        db.commit()
    print(f"[scheduler] {today}: {sent} lembrete(s) enviado(s).")
    return sent


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone=config.TIMEZONE)
    _scheduler.add_job(
        run_reminders,
        trigger="cron",
        hour=config.DAILY_HOUR,
        minute=config.DAILY_MINUTE,
        id="lembretes_diarios",
        replace_existing=True,
    )
    _scheduler.start()
    print(
        f"[scheduler] agendado para {config.DAILY_HOUR:02d}:{config.DAILY_MINUTE:02d} "
        f"({config.TIMEZONE})."
    )
