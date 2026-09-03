"""Recuperação: manda para o modelo só o que a frase precisa.

O contexto antigo despejava tudo — todas as matérias, todos os professores,
todos os locais, os 20 próximos eventos, as 6 últimas mensagens. Funciona, e
é caro: cada token daquele vai junto em toda chamada, mesmo quando a pessoa
escreveu "prova de bio sexta" e nada além de Biologia importava.

Aqui a seleção é por relevância à mensagem, com um piso de segurança para o
modelo nunca ficar cego: matérias citadas (por nome, apelido, som ou memória),
os eventos dessas matérias, e o que estiver próximo no tempo. O resultado é um
prompt várias vezes menor — e mais preciso, porque o modelo não precisa
escolher entre 40 matérias quando só 2 fazem sentido.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.core import academic, planner
from agenda.core.dates import WEEKDAY_LABELS
from agenda.core.text import norm
from agenda.knowledge import fuzzy, lexicon
from agenda.models import Event, KnowledgeEntry, Location, Subject, Teacher, User

# Quantas matérias entram no prompt quando a mensagem cita alguma. O piso
# existe para o modelo poder corrigir a nossa escolha se ela estiver errada.
MATERIAS_RELEVANTES = 6
MATERIAS_PISO = 12
EVENTOS_RELEVANTES = 12


def relevant_subjects(
    db: Session, user: User, texto: str, *, limite: int = MATERIAS_RELEVANTES
) -> list[Subject]:
    """Matérias que a mensagem plausivelmente cita, da mais provável para a menos."""
    contexto = academic.active_context(db, user.id)
    materias = academic.list_subjects(db, user.id, context_id=contexto.id if contexto else None)
    if not materias or not texto:
        return materias[:limite]

    alvo = lexicon.expand_chat(texto)
    palavras = [p for p in alvo.split() if len(p) >= 3]

    # A memória do usuário entra com peso: termo já confirmado por ele vale
    # mais que semelhança de escrita.
    aprendidos = {
        e.key_norm: e.value
        for e in db.scalars(
            select(KnowledgeEntry).where(
                KnowledgeEntry.user_id == user.id, KnowledgeEntry.kind == "SUBJECT"
            )
        ).all()
    }

    pontos: dict[str, float] = {}
    for materia in materias:
        termos = {materia.name, materia.short_name} | {a.alias for a in materia.aliases}
        melhor = 0.0
        for termo in filter(None, termos):
            chave = norm(termo)
            if chave and f" {chave} " in f" {alvo} ":
                melhor = max(melhor, 1.0)
                continue
            for palavra in palavras:
                melhor = max(melhor, fuzzy.similarity(palavra, termo))
        for chave, valor in aprendidos.items():
            if valor == materia.id and f" {chave} " in f" {alvo} ":
                melhor = max(melhor, 1.0)
        if melhor >= fuzzy.LIMIAR_MINIMO:
            pontos[materia.id] = melhor

    if not pontos:
        return materias[:limite]

    ordenadas = sorted(materias, key=lambda m: -pontos.get(m.id, 0.0))
    citadas = [m for m in ordenadas if m.id in pontos][:limite]
    # Completa com as demais só até o piso, para o modelo ter alternativa.
    restantes = [m for m in ordenadas if m.id not in pontos]
    return (citadas + restantes)[:max(limite, min(len(materias), MATERIAS_PISO))]


def build_context_block(db: Session, user: User, texto: str = "", *, max_events: int = EVENTOS_RELEVANTES) -> str:
    """Contexto enxuto para o prompt, focado na mensagem em questão."""
    hoje = planner.today_of(user)
    contexto = academic.active_context(db, user.id)
    linhas: list[str] = [f"Estudante: {user.name or 'sem nome'} · fuso {user.timezone}"]

    if contexto:
        linhas.append(
            f"Contexto ativo: {academic.EDUCATION_LABELS.get(contexto.type, contexto.type)}"
            f" · {contexto.title} · {contexto.subtitle or 'sem detalhes'}"
            + (f" · turno {contexto.shift}" if contexto.shift else "")
        )

    materias = relevant_subjects(db, user, texto)
    if materias:
        linhas.append("Matérias (id · nome · apelidos · professor):")
        for materia in materias:
            apelidos = ", ".join(a.alias for a in materia.aliases) or "—"
            professor = materia.teacher.name if materia.teacher else "—"
            linhas.append(f"  - {materia.id} · {materia.name} · [{apelidos}] · {professor}")
    else:
        linhas.append("Matérias: nenhuma cadastrada ainda.")

    # Vocabulário próprio deste usuário: é o que ensina o modelo a falar a
    # língua dele sem precisar de exemplo em todo prompt.
    aprendidos = db.scalars(
        select(KnowledgeEntry)
        .where(KnowledgeEntry.user_id == user.id)
        .order_by(KnowledgeEntry.hits.desc())
        .limit(15)
    ).all()
    if aprendidos:
        linhas.append(
            "Como este estudante costuma falar: "
            + ", ".join(f"{e.key_raw or e.key_norm}={e.value}" for e in aprendidos)
        )

    ids = [m.id for m in materias]
    if ids:
        horarios = [
            f"  - {materia.name}: {WEEKDAY_LABELS[h.weekday]} {h.start_time}–{h.end_time}"
            for materia in materias
            for h in sorted(_horarios(db, materia.id), key=lambda s: (s.weekday, s.start_time))
        ]
        if horarios:
            linhas.append("Aulas recorrentes:")
            linhas.extend(horarios[:12])

    proximos = db.scalars(
        select(Event)
        .where(
            Event.user_id == user.id,
            Event.local_date >= hoje - dt.timedelta(days=7),
            Event.status != "CANCELLED",
        )
        .order_by(Event.local_date)
        .limit(max_events * 2)
    ).all()
    # Prioriza eventos das matérias citadas; completa com os mais próximos.
    relevantes = [e for e in proximos if e.subject_id in ids]
    complemento = [e for e in proximos if e.subject_id not in ids]
    selecionados = (relevantes + complemento)[:max_events]
    if selecionados:
        linhas.append("Eventos já cadastrados (id · data · tipo · título · matéria):")
        for evento in sorted(selecionados, key=lambda e: e.local_date):
            nome = evento.subject.name if evento.subject else "—"
            linhas.append(
                f"  - {evento.id} · {evento.local_date.isoformat()} · {evento.type} · "
                f"{evento.title} · {nome}"
            )

    professores = db.scalars(select(Teacher).where(Teacher.user_id == user.id)).all()
    if professores:
        linhas.append("Professores: " + ", ".join(t.name for t in professores[:12]))
    locais = db.scalars(select(Location).where(Location.user_id == user.id)).all()
    if locais:
        linhas.append("Locais: " + ", ".join(loc.label for loc in locais[:12]))

    return "\n".join(linhas)


def _horarios(db: Session, subject_id: str):
    from agenda.models import ClassSchedule

    return db.scalars(
        select(ClassSchedule).where(
            ClassSchedule.subject_id == subject_id, ClassSchedule.active.is_(True)
        )
    ).all()
