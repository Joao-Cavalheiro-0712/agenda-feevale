"""Fingerprint e detecção de duplicados/atualizações (SPEC §14, §73, §74)."""
from __future__ import annotations

import datetime as dt
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.core.text import norm, slug_key
from agenda.knowledge import fuzzy
from agenda.models import Event, EventStatus

# Palavras que, no texto do usuário, indicam remarcação e não um novo evento.
RESCHEDULE_HINTS = (
    "passou para", "passou pro", "foi adiada", "foi adiado", "remarcou",
    "remarcada", "remarcado", "mudou para", "mudou pro", "adiada para",
    "adiado para", "antecipou", "antecipada", "nova data",
)

_TYPE_FAMILY = {
    "EXAM": "avaliacao", "QUIZ": "avaliacao", "SIMULATION": "avaliacao",
    "ASSIGNMENT": "entrega", "HOMEWORK": "entrega", "PROJECT": "entrega",
    "PAPER": "entrega", "PRESENTATION": "entrega", "SEMINAR": "entrega",
}


def type_family(event_type: str) -> str:
    return _TYPE_FAMILY.get(event_type, event_type)


def fingerprint(
    *, user_id: str, subject_id: str | None, event_type: str, date: dt.date, title: str
) -> str:
    """Identidade estável de um evento — usada para não criar duplicados."""
    raw = "|".join(
        [
            user_id,
            subject_id or "-",
            type_family(event_type),
            date.isoformat(),
            slug_key(title)[:40],
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def find_duplicate(db: Session, event: Event) -> Event | None:
    """Evento já existente com o mesmo fingerprint."""
    return db.scalars(
        select(Event).where(
            Event.user_id == event.user_id,
            Event.fingerprint == event.fingerprint,
            Event.id != event.id,
            Event.status != EventStatus.CANCELLED.value,
        )
    ).first()


def find_reschedule_candidate(
    db: Session,
    user_id: str,
    *,
    subject_id: str | None,
    event_type: str,
    title: str,
    new_date: dt.date,
    window_days: int = 45,
) -> Event | None:
    """Procura um evento parecido em outra data — provável remarcação (SPEC §14).

    A regra decisiva é: **dois assuntos específicos e diferentes são dois
    eventos.** "Trabalho sobre habeas corpus" e "trabalho sobre execução
    penal" são a mesma matéria e o mesmo tipo, e não têm nada a ver um com o
    outro — perguntar "é essa que mudou de data?" nesse caso viraria atrito em
    quase toda captura, porque um semestre normal tem vários trabalhos por
    matéria.

    Então mesma disciplina + mesmo tipo só conta como sinal forte quando pelo
    menos um dos títulos é **genérico** ("Prova de Civil" versus "Prova"), que
    é o caso real de remarcação. Título específico contra título específico
    exige que os assuntos se pareçam de verdade.
    """
    low = new_date - dt.timedelta(days=window_days)
    high = new_date + dt.timedelta(days=window_days)
    stmt = select(Event).where(
        Event.user_id == user_id,
        Event.local_date >= low,
        Event.local_date <= high,
        Event.local_date != new_date,
        Event.status.in_([EventStatus.UPCOMING.value, EventStatus.IN_PROGRESS.value]),
    )
    if subject_id:
        stmt = stmt.where(Event.subject_id == subject_id)
    candidates = [e for e in db.scalars(stmt).all() if type_family(e.type) == type_family(event_type)]
    if not candidates:
        return None

    # O nome da matéria também não é assunto: "Prova de Direito Civil" é um
    # título genérico, porque não diz sobre o que a prova é.
    nome_materia = ""
    if candidates and candidates[0].subject is not None:
        nome_materia = candidates[0].subject.name

    assunto_novo = _topic(title, nome_materia)
    best, best_score = None, 0.0
    for candidate in candidates:
        assunto_antigo = _topic(candidate.title, nome_materia)
        if assunto_novo and assunto_antigo:
            # Os dois lados dizem sobre o que são: decide a semelhança do assunto.
            score = fuzzy.similarity(assunto_novo, assunto_antigo)
        elif subject_id:
            # Algum lado é genérico ("Prova", "Trabalho"): aí mesma matéria e
            # mesmo tipo realmente sugerem que é o mesmo compromisso.
            score = 0.6
        else:
            # Sem matéria identificada e com título genérico não há evidência
            # nenhuma: dois compromissos quaisquer do mesmo tipo no mês não são
            # o mesmo compromisso. Aqui só o texto explícito ("passou para")
            # justifica remarcar, e isso quem decide é o chamador.
            score = 0.0
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 0.55 else None


# Palavras que não dizem sobre o que o compromisso é — sobra delas é o assunto.
_VAZIAS = {
    "prova", "provinha", "provao", "exame", "avaliacao", "teste", "simulado",
    "trabalho", "trampo", "entrega", "tarefa", "atividade", "tema", "dever",
    "licao", "exercicio", "exercicios", "lista", "questoes", "seminario",
    "apresentacao", "artigo", "fichamento", "resenha", "resumo", "redacao",
    "projeto", "relatorio", "leitura", "aula", "revisao", "levar", "trazer",
    "de", "da", "do", "das", "dos", "em", "no", "na", "sobre", "para", "pra",
    "e", "o", "a", "os", "as", "um", "uma", "com", "grupo", "dupla", "final",
    "parcial", "1", "2", "3", "i", "ii", "iii",
}


def _topic(title: str, subject_name: str = "") -> str:
    """O assunto do compromisso: o que sobra depois de tirar o óbvio.

    "Trabalho em grupo — habeas corpus" → "habeas corpus".
    "Prova de Direito Civil" (em Direito Civil) → "" — genérico, porque não
    diz sobre o que a prova é.
    """
    ignorar = set(_VAZIAS) | set(norm(subject_name).split())
    palavras = [p for p in norm(title).replace("—", " ").split() if p not in ignorar]
    return " ".join(palavras)


def looks_like_reschedule(text: str) -> bool:
    t = norm(text)
    return any(hint in t for hint in RESCHEDULE_HINTS)
