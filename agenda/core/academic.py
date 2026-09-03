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
from agenda.knowledge import fuzzy
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

# Pigmentos das disciplinas (SPEC §40). Nomes de pigmento, não de framework —
# fazem parte da identidade do Grifo. A cor nunca é o único portador de
# informação: sempre acompanha rótulo textual (SPEC §41).
SUBJECT_COLORS = [
    "ultramar", "indigo", "carmim", "ocre", "jade", "magenta",
    "terracota", "petroleo", "oliva", "ametista", "musgo", "grafite",
]

# Dicas por área para o pigmento não parecer sorteado.
_COLOR_HINTS = {
    "matematica": "ultramar", "calculo": "ultramar", "algebra": "ultramar",
    "estatistica": "ultramar", "portugues": "ocre", "literatura": "ocre",
    "redacao": "ocre", "historia": "terracota", "geografia": "jade",
    "filosofia": "ametista", "sociologia": "ametista", "biologia": "musgo",
    "ciencias": "musgo", "quimica": "petroleo", "fisica": "indigo",
    "ingles": "magenta", "espanhol": "magenta", "penal": "carmim",
    "civil": "indigo", "constitucional": "ultramar", "processual": "petroleo",
    "artes": "magenta", "educacao fisica": "oliva", "programacao": "petroleo",
    "eletric": "ocre", "mecanic": "grafite", "contabil": "oliva",
    "administr": "grafite", "direito": "carmim", "anatomia": "carmim",
}

# Paleta anterior → pigmentos, para dados criados antes desta identidade.
_LEGACY_COLORS = {
    "violet": "ultramar", "blue": "indigo", "emerald": "musgo", "amber": "ocre",
    "rose": "magenta", "cyan": "petroleo", "orange": "terracota",
    "indigo": "indigo", "teal": "jade", "pink": "magenta", "lime": "oliva",
    "red": "carmim", "slate": "grafite",
}


def pigment(color: str | None) -> str:
    """Normaliza a cor guardada no banco para um pigmento válido."""
    color = (color or "").strip().lower()
    if color in SUBJECT_COLORS:
        return color
    return _LEGACY_COLORS.get(color, "grafite")

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
    db: Session,
    user_id: str,
    text: str,
    *,
    context_id: str | None = None,
    approximate: bool = True,
) -> tuple[Subject | None, list[Subject]]:
    """Encontra a disciplina citada em texto livre.

    Devolve ``(match, ambiguous)``. Quando há mais de um candidato igualmente
    plausível, ``match`` é ``None`` e cabe ao chamador perguntar (SPEC §3.3).

    ``approximate`` liga o casamento por som e por distância de edição. Ele
    existe para **interpretar mensagem** ("cauculo", "istoria") e deve ficar
    desligado para **deduplicar cadastro**: sem isso, criar "Direito
    Constitucional" devolveria a "Direito Penal" já existente e a matéria nova
    nunca seria criada.
    """
    if not text:
        return None, []
    target = norm(text)
    if not target:
        return None, []

    subjects = list_subjects(db, user_id, context_id=context_id)
    if not subjects:
        subjects = list_subjects(db, user_id, context_id=context_id, active_only=False)

    if not approximate:
        # Identidade, não semelhança: só o nome próprio da matéria conta.
        # Apelidos ficam de fora porque são gerados automaticamente e podem
        # colidir — "Cálculo I" e "Cálculo II" geram o mesmo "calculo", e usar
        # isso como critério faria a segunda matéria nunca ser criada.
        iguais = [
            s for s in subjects
            if target in {norm(s.name), norm(s.short_name)} - {""}
        ]
        return (iguais[0], []) if len(iguais) == 1 else (None, iguais)

    exact: list[Subject] = []
    # Cada parcial guarda o tamanho do termo que casou: "processo civil" casando
    # inteiro vale mais que o apelido "civil" de outra matéria. Sem isso, as
    # duas ficam empatadas e o sistema pergunta o que já estava claro na frase.
    partial: list[tuple[int, Subject]] = []
    for subject in subjects:
        names = {norm(subject.name), norm(subject.short_name)} - {""}
        names |= {a.alias_norm for a in subject.aliases}
        if target in names:
            exact.append(subject)
            continue
        casados = [n for n in names if n and _contem_palavra(target, n)]
        if casados:
            partial.append((max(len(n) for n in casados), subject))
        elif _abbreviation_match(target, names):
            partial.append((0, subject))

    if len(exact) == 1:
        return exact[0], []
    if exact:
        return None, exact
    if partial:
        maior = max(peso for peso, _ in partial)
        melhores = [s for peso, s in partial if peso == maior]
        if len(melhores) == 1:
            return melhores[0], []
        return None, melhores

    if not approximate:
        return None, []

    # Nada casou pela escrita. Antes de desistir — e antes de gastar um
    # modelo — tenta pelo som: "cauculo", "fizica", "portuguez", e tudo que a
    # transcrição de áudio erra, que erra exatamente aí.
    # Lista de pares, não dicionário: o mesmo apelido pode pertencer a duas
    # matérias ("calculo" em Cálculo I e Cálculo II), e é justamente esse caso
    # que precisa virar empate — e pergunta — em vez de escolha silenciosa.
    candidatos: list[tuple[str, str]] = [
        (nome, subject.id)
        for subject in subjects
        for nome in {subject.name, subject.short_name} | {a.alias for a in subject.aliases}
        if nome
    ]

    escolhido, score, empatados = fuzzy.best_match(target, candidatos)
    frase_incerta = escolhido is None or score < fuzzy.LIMIAR_CONFIANTE or empatados
    if frase_incerta and _parece_mensagem(target):
        # O texto é a frase inteira ("tem p1 de istoria sexta"), e não um nome.
        # Aí sim vale procurar palavra a palavra, porque é assim que a matéria
        # aparece no meio da mensagem.
        palavras = target.split()
        # Pares de palavras primeiro: "cauculo 2" precisa achar "Cálculo II"
        # antes que "cauculo" sozinho ache "Cálculo" e ignore o número.
        trechos = [f"{a} {b}" for a, b in zip(palavras, palavras[1:])] + palavras
        for trecho in trechos:
            if len(trecho) < 4:
                continue
            candidato, pontos, empates = fuzzy.best_match(
                trecho, candidatos, limiar=fuzzy.LIMIAR_CONFIANTE
            )
            if candidato and pontos > score:
                escolhido, score, empatados = candidato, pontos, empates
    if escolhido is None:
        return None, []
    por_id = {s.id: s for s in subjects}
    if score < fuzzy.LIMIAR_CONFIANTE or empatados:
        # Parecido, mas não o suficiente para decidir sozinho: devolve como
        # ambíguo para quem chamou perguntar com opções concretas.
        opcoes = [por_id[i] for i in ([escolhido] + empatados) if i in por_id]
        return None, opcoes
    return por_id[escolhido], []


def _contem_palavra(texto: str, termo: str) -> bool:
    """Contém respeitando fronteira de palavra, nos dois sentidos.

    Sem a fronteira, "calculo i" casa dentro de "calculo ii" e as duas
    matérias viram uma só — foi assim que "Cálculo I" e "Cálculo II"
    colidiam.
    """
    a, b = f" {texto} ", f" {termo} "
    return b in a or a in b


def _parece_mensagem(target: str) -> bool:
    """Distingue "tem prova de istoria sexta" de "Direito Constitucional".

    A diferença importa muito: varrer palavra a palavra é o certo para uma
    frase e é perigoso para um nome — "direito" sozinho reivindicaria "Direito
    Penal" mesmo quando o nome completo diz outra coisa. O sinal usado é a
    presença de uma palavra de atividade do léxico ("prova", "p1", "trampo"),
    que nome de matéria não tem.
    """
    from agenda.knowledge import lexicon

    if len(target.split()) < 3:
        return False
    tipo, _termo, _score = lexicon.find_event_type(target)
    return tipo is not None


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
    """Cria ou reaproveita uma disciplina (idempotente por nome normalizado).

    Sem casamento aproximado de propósito: aqui o critério é identidade, não
    semelhança. "Cálculo I" e "Cálculo II" são duas matérias.
    """
    existing, _ = resolve_subject(
        db, user_id, name, context_id=context_id, approximate=False
    )
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


def copy_subject_to_period(db: Session, subject: Subject, period) -> Subject:
    """Leva uma matéria para o período seguinte, com horários e apelidos.

    Copiamos a estrutura (nome, professor, local, horários), nunca as
    atividades: o cronograma novo é do período novo.
    """
    copia = Subject(
        user_id=subject.user_id,
        education_context_id=subject.education_context_id,
        academic_period_id=period.id,
        name=subject.name,
        short_name=subject.short_name,
        color=subject.color,
        teacher_id=subject.teacher_id,
        default_location_id=subject.default_location_id,
        notes=subject.notes,
        grade_scale=subject.grade_scale,
        passing_grade=subject.passing_grade,
        status=SubjectStatus.ACTIVE.value,
    )
    db.add(copia)
    db.flush()
    for alias in subject.aliases:
        add_alias(db, copia, alias.alias)
    for schedule in db.scalars(
        select(ClassSchedule).where(
            ClassSchedule.user_id == subject.user_id,
            ClassSchedule.subject_id == subject.id,
            ClassSchedule.active.is_(True),
        )
    ).all():
        upsert_schedule(
            db, subject.user_id, copia,
            weekday=schedule.weekday,
            start_time=schedule.start_time,
            end_time=schedule.end_time,
            location_id=schedule.location_id,
            start_date=period.starts_on,
            end_date=period.ends_on,
        )
    subject.status = SubjectStatus.COMPLETED.value
    db.flush()
    return copia
