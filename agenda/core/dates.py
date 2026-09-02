"""Resolução determinística de expressões temporais em português (SPEC §21).

Regra do produto: o LLM apenas **identifica** a expressão ("sexta que vem");
quem transforma em data é este módulo, sempre com o fuso do usuário. Quando
não é possível resolver com segurança, devolvemos ``needs_clarification`` em
vez de inventar (SPEC §92).
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass
from typing import Callable

from agenda.core.text import norm

WEEKDAYS = {
    "segunda": 0, "segunda-feira": 0, "seg": 0, "2a": 0, "2ª": 0,
    "terca": 1, "terca-feira": 1, "ter": 1, "3a": 1,
    "quarta": 2, "quarta-feira": 2, "qua": 2, "4a": 2,
    "quinta": 3, "quinta-feira": 3, "qui": 3, "5a": 3,
    "sexta": 4, "sexta-feira": 4, "sex": 4, "6a": 4,
    "sabado": 5, "sab": 5,
    "domingo": 6, "dom": 6,
}

MONTHS = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "marco": 3, "mar": 3,
    "abril": 4, "abr": 4, "maio": 5, "mai": 5, "junho": 6, "jun": 6,
    "julho": 7, "jul": 7, "agosto": 8, "ago": 8, "setembro": 9, "set": 9,
    "outubro": 10, "out": 10, "novembro": 11, "nov": 11, "dezembro": 12, "dez": 12,
}

NUMBER_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
    "treze": 13, "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16,
    "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
}

WEEKDAY_LABELS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
WEEKDAY_SHORT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
MONTH_LABELS = [
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


@dataclass
class DateResolution:
    date: dt.date | None = None
    matched: str = ""
    kind: str = ""            # explicit | relative | weekday | monthday | anchor
    confidence: float = 0.0
    needs_clarification: bool = False
    question: str = ""

    @property
    def ok(self) -> bool:
        return self.date is not None


def _word_number(token: str) -> int | None:
    """Aceita "23", "vinte e tres", "trinta e um"."""
    token = norm(token)
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in NUMBER_WORDS:
        return NUMBER_WORDS[token]
    match = re.fullmatch(r"(vinte|trinta)\s+e\s+(\w+)", token)
    if match:
        tens = NUMBER_WORDS.get(match.group(1))
        units = NUMBER_WORDS.get(match.group(2))
        if tens and units and units < 10:
            return tens + units
    return None


def _next_weekday(today: dt.date, weekday: int, *, strictly_after: bool = False) -> dt.date:
    delta = (weekday - today.weekday()) % 7
    if delta == 0 and strictly_after:
        delta = 7
    return today + dt.timedelta(days=delta)


def _same_iso_week(a: dt.date, b: dt.date) -> bool:
    return a.isocalendar()[:2] == b.isocalendar()[:2]


def resolve_year(day: int, month: int, today: dt.date, *, tolerance_days: int = 60) -> int:
    """Escolhe o ano quando ele não está escrito.

    Datas de cronograma quase sempre olham para frente; só aceitamos passado
    recente (dentro da tolerância) antes de assumir o ano seguinte.
    """
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            candidate = dt.date(year, month, day)
        except ValueError:
            continue
        if candidate >= today - dt.timedelta(days=tolerance_days):
            return year
    return today.year


def parse_explicit_date(text: str, today: dt.date) -> DateResolution:
    """dd/mm[/aaaa], aaaa-mm-dd, "15 de abril [de 2026]"."""
    t = norm(text)

    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return DateResolution(
                dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                m.group(0), "explicit", 0.99,
            )
        except ValueError:
            pass

    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            raw_year = m.group(3)
            if raw_year:
                year = int(raw_year)
                year += 2000 if year < 100 else 0
            else:
                year = resolve_year(day, month, today)
            try:
                return DateResolution(
                    dt.date(year, month, day), m.group(0), "explicit",
                    0.98 if raw_year else 0.92,
                )
            except ValueError:
                pass

    m = re.search(
        r"\b(\d{1,2}|(?:vinte|trinta)\s+e\s+[a-z]+|[a-z]+)\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?\b", t
    )
    if m:
        day = _word_number(m.group(1))
        month = MONTHS.get(m.group(2))
        if day and month and 1 <= day <= 31:
            year = int(m.group(3)) if m.group(3) else resolve_year(day, month, today)
            try:
                return DateResolution(
                    dt.date(year, month, day), m.group(0), "explicit",
                    0.97 if m.group(3) else 0.92,
                )
            except ValueError:
                pass
    return DateResolution()


def resolve_expression(
    text: str,
    today: dt.date,
    *,
    next_class_date: Callable[[], dt.date | None] | None = None,
) -> DateResolution:
    """Transforma uma expressão temporal em data.

    ``next_class_date`` resolve âncoras como "na próxima aula"; sem ela (ou
    sem horário cadastrado) devolvemos uma pergunta em vez de um chute.
    """
    if not text:
        return DateResolution()
    t = norm(text)

    # 1) Data explícita sempre vence.
    explicit = parse_explicit_date(t, today)
    if explicit.ok:
        return explicit

    # 2) Âncoras relativas simples.
    if re.search(r"\bdepois de amanha\b", t):
        return DateResolution(today + dt.timedelta(days=2), "depois de amanhã", "relative", 0.98)
    if re.search(r"\bamanha\b", t):
        return DateResolution(today + dt.timedelta(days=1), "amanhã", "relative", 0.98)
    if re.search(r"\bhoje\b", t):
        return DateResolution(today, "hoje", "relative", 0.98)
    if re.search(r"\bontem\b", t):
        return DateResolution(today - dt.timedelta(days=1), "ontem", "relative", 0.95)

    # 3) "daqui a N dias/semanas/meses", "em N dias"
    m = re.search(r"\b(?:daqui a|em|dentro de)\s+([a-z]+|\d+)\s+(dias?|semanas?|mes(?:es)?)\b", t)
    if m:
        n = _word_number(m.group(1))
        if n:
            unit = m.group(2)
            if unit.startswith("dia"):
                return DateResolution(today + dt.timedelta(days=n), m.group(0), "relative", 0.95)
            if unit.startswith("semana"):
                return DateResolution(today + dt.timedelta(weeks=n), m.group(0), "relative", 0.93)
            return DateResolution(_add_months(today, n), m.group(0), "relative", 0.88)

    # 4) Fim/início de mês.
    if re.search(r"\b(fim|final) do mes\b", t):
        last = calendar.monthrange(today.year, today.month)[1]
        return DateResolution(dt.date(today.year, today.month, last), "fim do mês", "relative", 0.85)
    if re.search(r"\b(inicio|comeco) do mes que vem\b", t):
        nxt = _add_months(today.replace(day=1), 1)
        return DateResolution(nxt, "início do mês que vem", "relative", 0.85)

    # 5) Dia do mês: "dia 15", "no dia 23".
    m = re.search(r"\bdia\s+(\d{1,2}|(?:vinte|trinta)\s+e\s+[a-z]+|[a-z]+)\b", t)
    if m:
        day = _word_number(m.group(1))
        if day and 1 <= day <= 31:
            candidate = _day_of_month(today, day)
            if candidate:
                return DateResolution(candidate, m.group(0), "monthday", 0.9)

    # 6) Dias da semana, com ou sem "que vem"/"próxima".
    next_week = bool(re.search(r"\b(que vem|proxim[ao]s?|seguinte)\b", t))
    for word, weekday in sorted(WEEKDAYS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b", t):
            date = _next_weekday(today, weekday, strictly_after=next_week)
            if next_week and _same_iso_week(date, today):
                date += dt.timedelta(days=7)
            return DateResolution(date, word, "weekday", 0.9 if not next_week else 0.88)

    # 7) "semana que vem" sem dia → segunda-feira da próxima semana.
    if re.search(r"\b(semana que vem|proxima semana)\b", t):
        monday = today + dt.timedelta(days=(7 - today.weekday()))
        return DateResolution(monday, "semana que vem", "relative", 0.75)

    # 8) Âncoras que dependem do horário da disciplina (SPEC §92).
    if re.search(r"\b(proxima aula|aula seguinte|proximo encontro|na aula que vem)\b", t):
        date = next_class_date() if next_class_date else None
        if date:
            return DateResolution(date, "próxima aula", "anchor", 0.9)
        return DateResolution(
            needs_clarification=True,
            matched="próxima aula",
            question="Não sei quando é a próxima aula dessa matéria. Qual é a data?",
        )

    return DateResolution()


def _day_of_month(today: dt.date, day: int) -> dt.date | None:
    for offset in (0, 1, 2):
        base = _add_months(today.replace(day=1), offset)
        last = calendar.monthrange(base.year, base.month)[1]
        if day > last:
            continue
        candidate = dt.date(base.year, base.month, day)
        if candidate >= today:
            return candidate
    return None


def _add_months(date: dt.date, months: int) -> dt.date:
    month_index = date.month - 1 + months
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


# --------------------------------------------------------------------------- #
# Horários
# --------------------------------------------------------------------------- #
_TIME_WORDS = {
    "meia": 30, "quinze": 15, "e meia": 30, "em ponto": 0,
}


def parse_time(text: str, *, shift: str = "") -> str | None:
    """Devolve "HH:MM" a partir de "19:30", "19h30", "7h", "sete e meia".

    ``shift`` ("noite", "tarde") desambigua horas de 1 a 11 informadas sem
    período — "sete e meia" no turno da noite é 19:30 (SPEC §7).
    """
    if not text:
        return None
    t = norm(text)

    m = re.search(r"\b(\d{1,2})\s*(?::|h|hs|horas?)\s*(\d{2})?\b", t)
    hour = minute = None
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        if "e meia" in t[m.end(): m.end() + 12]:
            minute = 30
    else:
        for candidate in re.finditer(r"\b([a-z]+)(?:\s+e\s+(meia|quinze|[a-z]+))?\b", t):
            if norm(candidate.group(1)) not in NUMBER_WORDS:
                continue
            hour = NUMBER_WORDS[norm(candidate.group(1))]
            rest = candidate.group(2)
            minute = (_TIME_WORDS.get(norm(rest)) or NUMBER_WORDS.get(norm(rest)) or 0) if rest else 0
            break
        if hour is None:
            bare = re.search(r"\b(\d{1,2})\b(?!\s*[/:.\-]\s*\d)", t)
            if bare:
                hour, minute = int(bare.group(1)), 0

    if hour is None:
        return None
    if not 0 <= hour <= 23 or not 0 <= (minute or 0) <= 59:
        return None

    if re.search(r"\b(da noite|da tarde|pm)\b", t) and hour < 12:
        hour += 12
    elif re.search(r"\b(da manha|am)\b", t):
        pass
    elif 1 <= hour <= 11 and shift in ("noite", "NIGHT", "noturno"):
        hour += 12
    elif 1 <= hour <= 6 and shift in ("tarde", "AFTERNOON", "vespertino"):
        hour += 12
    return f"{hour:02d}:{(minute or 0):02d}"


def format_date_pt(date: dt.date, *, with_weekday: bool = True) -> str:
    if with_weekday:
        return f"{WEEKDAY_LABELS[date.weekday()]}, {date.day:02d}/{date.month:02d}"
    return f"{date.day:02d}/{date.month:02d}/{date.year}"


def human_delta(date: dt.date, today: dt.date) -> str:
    days = (date - today).days
    if days == 0:
        return "hoje"
    if days == 1:
        return "amanhã"
    if days == -1:
        return "ontem"
    if days < 0:
        return f"há {abs(days)} dias"
    if days < 7:
        return f"em {days} dias"
    if days < 14:
        return "em 1 semana"
    if days < 60:
        return f"em {days // 7} semanas"
    return f"em {days // 30} meses"
