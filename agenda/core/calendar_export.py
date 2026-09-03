"""Exportação para calendários externos em iCalendar (SPEC §95).

Export puro, sem dependência de terceiros: gera .ics que Google Calendar,
Apple Calendar e Outlook importam. A sincronização bidirecional depende de
credenciais OAuth do usuário e fica para a fase seguinte.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from agenda.core import recurrence, scope
from agenda.core.events import tz_of
from agenda.models import Event, EventStatus, User

# Escapa o que o formato exige E remove todo caractere de controle. O CR era o
# furo: sem ele na tabela, um título com "\r" emitia uma quebra de linha crua
# dentro de SUMMARY, e o parser do calendário passava a ler a linha seguinte
# como um campo novo — texto e link plantados na agenda de quem assinou o feed.
_ESCAPE = str.maketrans({",": r"\,", ";": r"\;", "\\": "\\\\", "\n": r"\n", "\r": ""})

STATUS_MAP = {
    EventStatus.CANCELLED.value: "CANCELLED",
    EventStatus.COMPLETED.value: "CONFIRMED",
}


def _line(name: str, value: str) -> str:
    """Dobra linhas em 75 octetos, como manda o RFC 5545."""
    raw = f"{name}:{value}"
    encoded = raw.encode("utf-8")
    if len(encoded) <= 75:
        return raw
    partes, atual = [], b""
    for char in raw:
        bytes_char = char.encode("utf-8")
        limite = 75 if not partes else 74
        if len(atual) + len(bytes_char) > limite:
            partes.append(atual.decode("utf-8"))
            atual = b""
        atual += bytes_char
    partes.append(atual.decode("utf-8"))
    return "\r\n ".join(partes)


def _escape(text: str) -> str:
    return (text or "").translate(_ESCAPE)


def _stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _uid(event_id: str, domain: str = "grifo.app") -> str:
    return f"{hashlib.sha256(event_id.encode()).hexdigest()[:24]}@{domain}"


def event_to_ics(event: Event, tz: ZoneInfo, *, subject_name: str = "") -> list[str]:
    linhas = ["BEGIN:VEVENT", _line("UID", _uid(event.id))]
    linhas.append(_line("DTSTAMP", _stamp(dt.datetime.now(dt.timezone.utc))))

    if event.all_day or not event.starts_at:
        dia = event.local_date
        linhas.append(_line("DTSTART;VALUE=DATE", dia.strftime("%Y%m%d")))
        linhas.append(_line("DTEND;VALUE=DATE", (dia + dt.timedelta(days=1)).strftime("%Y%m%d")))
    else:
        fim = event.ends_at or (event.starts_at + dt.timedelta(hours=1))
        linhas.append(_line("DTSTART", _stamp(event.starts_at)))
        linhas.append(_line("DTEND", _stamp(fim)))

    titulo = event.title if not subject_name else f"{event.title} · {subject_name}"
    linhas.append(_line("SUMMARY", _escape(titulo)))
    if event.description:
        linhas.append(_line("DESCRIPTION", _escape(event.description)))
    if event.location is not None:
        linhas.append(_line("LOCATION", _escape(event.location.label)))
    linhas.append(_line("STATUS", STATUS_MAP.get(event.status, "CONFIRMED")))
    # Escapado como todo o resto: era o único campo de texto que saía cru.
    linhas.append(_line("CATEGORIES", _escape(event.type)))
    linhas.append("END:VEVENT")
    return linhas


def _class_to_ics(occurrence, tz: ZoneInfo) -> list[str]:
    inicio = dt.datetime.combine(
        occurrence.date, dt.time(*map(int, occurrence.start_time.split(":"))), tzinfo=tz
    )
    fim_txt = occurrence.end_time or occurrence.start_time
    fim = dt.datetime.combine(occurrence.date, dt.time(*map(int, fim_txt.split(":"))), tzinfo=tz)
    if fim <= inicio:
        fim = inicio + dt.timedelta(hours=1)
    chave = f"{occurrence.schedule.id}:{occurrence.date.isoformat()}"
    return [
        "BEGIN:VEVENT",
        _line("UID", _uid(chave)),
        _line("DTSTAMP", _stamp(dt.datetime.now(dt.timezone.utc))),
        _line("DTSTART", _stamp(inicio)),
        _line("DTEND", _stamp(fim)),
        _line("SUMMARY", _escape(occurrence.subject.display)),
        _line("LOCATION", _escape(occurrence.location_label)),
        _line("CATEGORIES", "CLASS"),
        _line("STATUS", "CANCELLED" if occurrence.cancelled else "CONFIRMED"),
        "END:VEVENT",
    ]


def build_calendar(
    db: Session,
    user: User,
    *,
    days_back: int = 60,
    days_ahead: int = 365,
    include_classes: bool = True,
    app_name: str = "Grifo",
) -> str:
    """Calendário completo do usuário — só com dados dele (escopo forçado)."""
    tz = tz_of(user)
    hoje = dt.datetime.now(tz).date()
    inicio, fim = hoje - dt.timedelta(days=days_back), hoje + dt.timedelta(days=days_ahead)

    eventos = db.scalars(
        scope.query(Event, user.id)
        .where(Event.local_date >= inicio, Event.local_date <= fim)
        .order_by(Event.local_date)
    ).all()

    linhas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{app_name}//PT-BR//",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _line("X-WR-CALNAME", _escape(f"{app_name} — {user.name or 'minha agenda'}")),
        _line("X-WR-TIMEZONE", str(tz)),
    ]
    for evento in eventos:
        nome = evento.subject.display if evento.subject else ""
        linhas.extend(event_to_ics(evento, tz, subject_name=nome))

    if include_classes:
        for ocorrencia in recurrence.expand_classes(db, user.id, hoje, fim, include_cancelled=False):
            linhas.extend(_class_to_ics(ocorrencia, tz))

    linhas.append("END:VCALENDAR")
    return "\r\n".join(linhas) + "\r\n"
