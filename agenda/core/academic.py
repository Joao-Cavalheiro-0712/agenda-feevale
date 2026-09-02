"""Resolução e manutenção do núcleo acadêmico: contextos, matérias,
professores e locais (SPEC §42, §43, §44).

Aqui mora a "memória contextual" que permite ao assistente entender
"o professor Ricardo marcou prova sexta" (SPEC §20).
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.core.text import norm
from agenda.models import (
    ClassSchedule,
    EducationContext,
    EducationType,
    Location,
    Subject,
    SubjectAlias,
    SubjectStatus,
    Teacher,
)

# Paleta usada para sugerir cor de disciplina (SPEC §40). Nunca é o único
# portador de informação — sempre acompanha rótulo textual (SPEC §41).
SUBJECT_COLORS = [
    "violet", "blue", "emerald", "amber", "rose", "cyan",
    "orange", "indigo", "teal", "pink", "lime", "red",
]

# Dicas por área para a cor não parecer aleatória.
_COLOR_HINTS = {
    "matematica": "violet", "calculo": "violet", "algebra": "violet",
    "portugues": "amber", "literatura": "amber", "redacao": "amber",
    "historia": "orange", "geografia": "teal", "filosofia": "indigo",
    "sociologia": "indigo", "biologia": "emerald", "ciencias": "emerald",
    "quimica": "cyan", "fisica": "blue", "ingles": "rose", "espanhol": "rose",
    "penal": "red", "civil": "blue", "constitucional": "indigo",
    "artes": "pink", "educacao fisica": "lime", "programacao": "cyan",
}

EDUCATION_LABELS = {
    EducationType.ELEMENTARY.value: "Ensino fundamental",
    EducationType.MIDDLE_SCHOOL.value: "Ensino fundamental — anos finais",
    EducationType.HIGH_SCHOOL.value: "Ensino médio",
    EducationType.TECHNICAL.value: "Curso técnico",
    EducationType.UNDERGRAD.value: "Faculdade",
    EducationType.POSTGRAD.value: "Pós-graduação",
    EducationType.FREE_COURSE.value: "Curso livre",
    EducationType.OTHER.value: "Outro",
}


def suggest_color(name: str, used: list[str] | None = None) -> str:
    n = norm(name)
    for hint, color in _COLOR_HINTS.items():
        if hint in n:
            return color
    used = used or []
    for color in SUBJECT_COLORS:
        if color not in used:
            return color
    return SUBJECT_COLORS[len(n) % len(SUBJECT_COLORS)]


# --------------------------------------------------------------------------- #
# Contextos
# --------------------------------------------------------------------------- #
def active_context(db: Session, user_id: str) -> EducationContext | None:
    return db.scalars(
        select(EducationContext)
        .where(
            EducationContext.user_id == user_id,
            EducationContext.archived.is_(False),
        )
        .order_by(EducationContext.is_active.desc(), EducationContext.created_at.desc())
    ).first()


def list_contexts(db: Session, user_id: str, *, include_archived: bool = False):
    stmt = select(EducationContext).where(EducationContext.user_id == user_id)
    if not include_archived:
        stmt = stmt.where(EducationContext.archived.is_(False))
    return db.scalars(stmt.order_by(EducationContext.created_at)).all()


def set_active_context(db: Session, user_id: str, context_id: str) -> None:
    for ctx in list_contexts(db, user_id, include_archived=True):
        ctx.is_active = ctx.id == context_id


# --------------------------------------------------------------------------- #
# Matérias
# --------------------------------------------------------------------------- #
def list_subjects(
    db: Session, user_id: str, *, context_id: str | None = None, active_only: bool = True
) -> list[Subject]:
    stmt = select(Subject).where(Subject.user_id == user_id)
    if context_id:
        stmt = stmt.where(Subject.education_context_id == context_id)
    if active_only:
        stmt = stmt.where(Subject.status == SubjectStatus.ACTIVE.value)
    return list(db.scalars(stmt.order_by(Subject.name)).all())


def resolve_subject(
    db: Session, user_id: str, text: str, *, context_id: str | None = None
) -> tuple[Subject | None, list[Subject]]:
    """Encontra a disciplina citada em texto livre.

    Devolve ``(match, ambiguous)``. Quando há mais de um candidato igualmente
    plausível, ``match`` é ``None`` e cabe ao chamador perguntar (SPEC §3.3).
    """
    if not text:
        return None, []
    target = norm(text)
    if not target:
        return None, []

    subjects = list_subjects(db, user_id, context_id=context_id)
    if not subjects:
        subjects = list_subjects(db, user_id, context_id=context_id, active_only=False)

    exact: list[Subject] = []
    partial: list[Subject] = []
    for subject in subjects:
        names = {norm(subject.name), norm(subject.short_name)} - {""}
        names |= {a.alias_norm for a in subject.aliases}
        if target in names:
            exact.append(subject)
            continue
        if any(n and (n in target or target in n) for n in names):
            partial.append(subject)
        elif _abbreviation_match(target, names):
            partial.append(subject)

    if len(exact) == 1:
        return exact[0], []
    if exact:
        return None, exact
    if len(partial) == 1:
        return partial[0], []
    if partial:
        return None, partial
    return None, []


def _abbreviation_match(target: str, names: set[str]) -> bool:
    """Casa abreviações: "const" → "Direito Constitucional".

    Exige prefixo ESTRITO (a abreviação é mais curta que a palavra) e pelo
    menos 4 letras — assim "direito" não casa com toda matéria de Direito.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", target) if len(t) >= 4]
    if not tokens:
        return False
    for name in names:
        for word in name.split():
            if len(word) < 6:
                continue
            if any(len(token) < len(word) and word.startswith(token) for token in tokens):
                return True
    return False


def upsert_subject(
    db: Session,
    user_id: str,
    context_id: str,
    name: str,
    *,
    short_name: str = "",
    color: str = "",
    teacher_id: str | None = None,
    location_id: str | None = None,
    notes: str = "",
) -> Subject:
    """Cria ou reaproveita uma disciplina (idempotente por nome normalizado)."""
    existing, _ = resolve_subject(db, user_id, name, context_id=context_id)
    if existing:
        if teacher_id and not existing.teacher_id:
            existing.teacher_id = teacher_id
        if location_id and not existing.default_location_id:
            existing.default_location_id = location_id
        if notes and not existing.notes:
            existing.notes = notes
        return existing

    used = [s.color for s in list_subjects(db, user_id, context_id=context_id)]
    subject = Subject(
        user_id=user_id,
        education_context_id=context_id,
        name=name.strip()[:200],
        short_name=short_name.strip()[:60],
        color=color or suggest_color(name, used),
        teacher_id=teacher_id,
        default_location_id=location_id,
        notes=notes,
    )
    db.add(subject)
    db.flush()
    for alias in default_aliases(name):
        add_alias(db, subject, alias)
    return subject


def default_aliases(name: str) -> list[str]:
    """Apelidos óbvios: "Direito Constitucional I" → "constitucional", "const"."""
    n = norm(name)
    out: set[str] = set()
    words = [w for w in n.split() if len(w) > 3 and w not in ("direito", "introducao", "estudos")]
    if words:
        out.add(words[-1])
        out.add(" ".join(words))
    stripped = n.rstrip(" i").strip()
    if stripped and stripped != n:
        out.add(stripped)
    return [a for a in out if a and a != n]


def add_alias(db: Session, subject: Subject, alias: str) -> None:
    alias_norm = norm(alias)
    if not alias_norm:
        return
    if any(a.alias_norm == alias_norm for a in subject.aliases):
        return
    db.add(SubjectAlias(subject_id=subject.id, alias=alias.strip()[:120], alias_norm=alias_norm))


# --------------------------------------------------------------------------- #
# Professores
# --------------------------------------------------------------------------- #
def resolve_teacher(db: Session, user_id: str, text: str) -> tuple[Teacher | None, list[Teacher]]:
    if not text:
        return None, []
    target = norm(text)
    teachers = list(db.scalars(select(Teacher).where(Teacher.user_id == user_id)).all())
    matches = [
        t for t in teachers
        if target and (target in norm(t.name) or norm(t.name) in target
                       or (t.nickname and norm(t.nickname) == target))
    ]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def subjects_of_teacher(db: Session, user_id: str, teacher_id: str) -> list[Subject]:
    return list(
        db.scalars(
            select(Subject).where(Subject.user_id == user_id, Subject.teacher_id == teacher_id)
        ).all()
    )


def upsert_teacher(db: Session, user_id: str, name: str, *, nickname: str = "") -> Teacher:
    teacher, candidates = resolve_teacher(db, user_id, name)
    if teacher:
        return teacher
    if candidates:
        return candidates[0]
    teacher = Teacher(user_id=user_id, name=name.strip()[:160], nickname=nickname.strip()[:80])
    db.add(teacher)
    db.flush()
    return teacher


# --------------------------------------------------------------------------- #
# Locais
# --------------------------------------------------------------------------- #
def upsert_location(
    db: Session, user_id: str, name: str, *, building: str = "", room: str = "", campus: str = ""
) -> Location:
    target = norm(f"{building} {room} {name}")
    for loc in db.scalars(select(Location).where(Location.user_id == user_id)).all():
        if norm(f"{loc.building} {loc.room} {loc.name}") == target:
            return loc
    location = Location(
        user_id=user_id,
        name=(name or building or room).strip()[:160],
        building=building.strip()[:120],
        room=room.strip()[:60],
        campus=campus.strip()[:120],
    )
    db.add(location)
    db.flush()
    return location


def upsert_schedule(
    db: Session,
    user_id: str,
    subject: Subject,
    *,
    weekday: int,
    start_time: str,
    end_time: str,
    location_id: str | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
) -> ClassSchedule:
    existing = db.scalars(
        select(ClassSchedule).where(
            ClassSchedule.user_id == user_id,
            ClassSchedule.subject_id == subject.id,
            ClassSchedule.weekday == weekday,
            ClassSchedule.start_time == start_time,
        )
    ).first()
    if existing:
        existing.end_time = end_time or existing.end_time
        existing.active = True
        if location_id:
            existing.location_id = location_id
        return existing
    schedule = ClassSchedule(
        user_id=user_id,
        subject_id=subject.id,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
        location_id=location_id,
        start_date=start_date,
        end_date=end_date,
    )
    db.add(schedule)
    db.flush()
    return schedule
