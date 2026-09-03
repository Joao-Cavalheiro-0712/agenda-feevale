"""Períodos letivos (SPEC §132, §133).

Nem todo mundo estuda em semestre. Graduação e pós costumam ser semestrais;
técnico e escola frequentemente trabalham com trimestre, bimestre ou módulo;
curso livre pode não ter divisão nenhuma. Este módulo gera e mantém os
períodos concretos de cada contexto, o que permite arquivar o passado sem
apagar nada e responder perguntas sobre semestres anteriores.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.models import AcademicPeriod, EducationContext, PeriodKind

PERIOD_LABELS = {
    PeriodKind.SEMESTER.value: ("semestre", "Semestre"),
    PeriodKind.TRIMESTER.value: ("trimestre", "Trimestre"),
    PeriodKind.QUADMESTER.value: ("quadrimestre", "Quadrimestre"),
    PeriodKind.BIMESTER.value: ("bimestre", "Bimestre"),
    PeriodKind.ANNUAL.value: ("ano letivo", "Ano letivo"),
    PeriodKind.MODULE.value: ("módulo", "Módulo"),
    PeriodKind.CONTINUOUS.value: ("período", "Período"),
}

PERIODS_PER_YEAR = {
    PeriodKind.SEMESTER.value: 2,
    PeriodKind.TRIMESTER.value: 3,
    PeriodKind.QUADMESTER.value: 3,
    PeriodKind.BIMESTER.value: 4,
    PeriodKind.ANNUAL.value: 1,
    PeriodKind.MODULE.value: 4,
    PeriodKind.CONTINUOUS.value: 1,
}

# Janelas típicas do calendário brasileiro (mês inicial, mês final).
_DEFAULT_WINDOWS: dict[str, list[tuple[int, int]]] = {
    PeriodKind.SEMESTER.value: [(2, 7), (8, 12)],
    PeriodKind.TRIMESTER.value: [(2, 4), (5, 8), (9, 12)],
    PeriodKind.QUADMESTER.value: [(1, 4), (5, 8), (9, 12)],
    PeriodKind.BIMESTER.value: [(2, 3), (4, 6), (8, 9), (10, 12)],
    PeriodKind.ANNUAL.value: [(2, 12)],
    PeriodKind.CONTINUOUS.value: [(1, 12)],
}


def kind_label(kind: str, *, plural: bool = False) -> str:
    singular, titled = PERIOD_LABELS.get(kind, PERIOD_LABELS[PeriodKind.SEMESTER.value])
    if plural:
        return singular + "s"
    return singular


def period_label(kind: str, sequence: int, year: int) -> str:
    if kind == PeriodKind.ANNUAL.value:
        return f"Ano letivo {year}"
    if kind == PeriodKind.CONTINUOUS.value:
        return f"Estudos {year}"
    if kind == PeriodKind.MODULE.value:
        return f"Módulo {sequence}"
    _, titled = PERIOD_LABELS.get(kind, PERIOD_LABELS[PeriodKind.SEMESTER.value])
    return f"{sequence}º {titled.lower()} de {year}"


def _month_range(year: int, start_month: int, end_month: int) -> tuple[dt.date, dt.date]:
    import calendar

    start = dt.date(year, start_month, 1)
    last_day = calendar.monthrange(year, end_month)[1]
    return start, dt.date(year, end_month, last_day)


def plan_periods(
    kind: str,
    *,
    year: int,
    starts_on: dt.date | None = None,
    ends_on: dt.date | None = None,
    count: int | None = None,
    module_months: int = 4,
) -> list[dict]:
    """Calcula os períodos de um contexto sem tocar no banco.

    Quando o usuário informa início e fim, dividimos esse intervalo em partes
    iguais — respeitamos o que ele disse em vez de impor um calendário.
    """
    kind = kind if kind in PERIODS_PER_YEAR else PeriodKind.SEMESTER.value
    total = count or PERIODS_PER_YEAR[kind]

    if kind == PeriodKind.MODULE.value and not (starts_on and ends_on):
        base = starts_on or dt.date(year, 1, 1)
        out = []
        for index in range(total):
            start = _add_months(base, index * module_months)
            end = _add_months(start, module_months) - dt.timedelta(days=1)
            out.append({"sequence": index + 1, "starts_on": start, "ends_on": end})
        return _with_labels(out, kind, year)

    if starts_on and ends_on and ends_on > starts_on:
        span = (ends_on - starts_on).days + 1
        chunk = max(span // total, 1)
        out = []
        for index in range(total):
            start = starts_on + dt.timedelta(days=chunk * index)
            end = ends_on if index == total - 1 else start + dt.timedelta(days=chunk - 1)
            if start > ends_on:
                break
            out.append({"sequence": index + 1, "starts_on": start, "ends_on": min(end, ends_on)})
        return _with_labels(out, kind, year)

    windows = _DEFAULT_WINDOWS.get(kind, _DEFAULT_WINDOWS[PeriodKind.SEMESTER.value])
    out = []
    for index, (start_month, end_month) in enumerate(windows[:total]):
        start, end = _month_range(year, start_month, end_month)
        out.append({"sequence": index + 1, "starts_on": start, "ends_on": end})
    return _with_labels(out, kind, year)


def _with_labels(items: list[dict], kind: str, year: int) -> list[dict]:
    for item in items:
        item["kind"] = kind
        item["year"] = year
        item["label"] = period_label(kind, item["sequence"], year)
    return items


def _add_months(date: dt.date, months: int) -> dt.date:
    import calendar

    index = date.month - 1 + months
    year = date.year + index // 12
    month = index % 12 + 1
    day = min(date.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def ensure_periods(
    db: Session, context: EducationContext, *, today: dt.date | None = None
) -> list[AcademicPeriod]:
    """Cria (se faltarem) os períodos do contexto e marca o atual."""
    today = today or dt.date.today()
    year = (context.starts_on or today).year
    existing = list(
        db.scalars(
            select(AcademicPeriod)
            .where(
                AcademicPeriod.user_id == context.user_id,
                AcademicPeriod.education_context_id == context.id,
            )
            .order_by(AcademicPeriod.sequence)
        ).all()
    )
    if not existing:
        for plan in plan_periods(
            context.period_kind,
            year=year,
            starts_on=context.starts_on,
            ends_on=context.ends_on,
        ):
            period = AcademicPeriod(
                user_id=context.user_id,
                education_context_id=context.id,
                kind=plan["kind"],
                label=plan["label"],
                sequence=plan["sequence"],
                year=plan["year"],
                starts_on=plan["starts_on"],
                ends_on=plan["ends_on"],
            )
            db.add(period)
            existing.append(period)
        db.flush()

    mark_current(existing, today)
    return existing


def mark_current(periods: list[AcademicPeriod], today: dt.date) -> AcademicPeriod | None:
    """Marca como atual o período que contém hoje; senão, o próximo futuro."""
    current = next((p for p in periods if not p.archived and p.contains(today)), None)
    if current is None:
        futuros = [p for p in periods if p.starts_on and p.starts_on > today and not p.archived]
        current = min(futuros, key=lambda p: p.starts_on) if futuros else None
    if current is None:
        passados = [p for p in periods if not p.archived]
        current = max(passados, key=lambda p: (p.year, p.sequence)) if passados else None
    for period in periods:
        period.is_current = period is current
    return current


def current_period(
    db: Session, context: EducationContext, *, today: dt.date | None = None
) -> AcademicPeriod | None:
    if context is None:
        return None
    return next((p for p in ensure_periods(db, context, today=today) if p.is_current), None)


def list_periods(db: Session, user_id: str, *, context_id: str | None = None):
    stmt = select(AcademicPeriod).where(AcademicPeriod.user_id == user_id)
    if context_id:
        stmt = stmt.where(AcademicPeriod.education_context_id == context_id)
    return list(db.scalars(stmt.order_by(AcademicPeriod.year, AcademicPeriod.sequence)).all())


def archive_period(db: Session, period: AcademicPeriod) -> None:
    """Arquiva um período. Nada é apagado (SPEC §132)."""
    period.archived = True
    period.is_current = False
    db.flush()


def start_next_period(
    db: Session,
    context: EducationContext,
    *,
    copy_subjects: bool = True,
    today: dt.date | None = None,
) -> AcademicPeriod:
    """Vira o período: arquiva o atual e opcionalmente copia as matérias."""
    from agenda.core import academic

    today = today or dt.date.today()
    periods = ensure_periods(db, context, today=today)
    current = next((p for p in periods if p.is_current), None)
    if current is not None:
        archive_period(db, current)

    if current is not None and current.sequence < PERIODS_PER_YEAR.get(current.kind, 2):
        sequence, year = current.sequence + 1, current.year
    else:
        sequence, year = 1, (current.year + 1 if current else today.year)

    following = next(
        (p for p in periods if p.sequence == sequence and p.year == year and not p.archived), None
    )
    if following is None:
        plan = plan_periods(context.period_kind, year=year)
        data = next((p for p in plan if p["sequence"] == sequence), plan[0])
        following = AcademicPeriod(
            user_id=context.user_id,
            education_context_id=context.id,
            kind=data["kind"],
            label=data["label"],
            sequence=data["sequence"],
            year=data["year"],
            starts_on=data["starts_on"],
            ends_on=data["ends_on"],
        )
        db.add(following)
        db.flush()

    for period in periods:
        period.is_current = False
    following.is_current = True
    following.archived = False

    if copy_subjects and current is not None:
        for subject in academic.list_subjects(db, context.user_id, context_id=context.id):
            if subject.academic_period_id == current.id:
                academic.copy_subject_to_period(db, subject, following)
    db.flush()
    return following
