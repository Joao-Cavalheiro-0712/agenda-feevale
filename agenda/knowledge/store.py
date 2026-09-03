"""Memória de vocabulário por usuário — o software fica mais esperto usando.

O ganho aqui é composto: cada confirmação do usuário vira conhecimento local,
e conhecimento local é resolução sem chamada de modelo. Um usuário ativo, em
poucas semanas, passa a ter quase todas as suas mensagens resolvidas sem
custo nenhum de IA — e com precisão maior, porque o vocabulário é o dele.

Isolamento: toda consulta e toda escrita passam por `user_id`. "bio" é
Biologia Celular para um e Bioquímica para outro; a base é pessoal.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.core.text import norm
from agenda.knowledge import fuzzy
from agenda.knowledge.phonetics import phonetic_key
from agenda.models import KnowledgeEntry, KnowledgeKind, User

# Quantas confirmações bastam para o sistema parar de perguntar. Duas: a
# primeira pode ser sorte, a segunda já é padrão.
CONFIANCA_APRENDIDA = 2


def learn(
    db: Session,
    user: User,
    *,
    kind: str,
    term: str,
    value: str,
    source: str = "confirm",
) -> KnowledgeEntry | None:
    """Guarda (ou reforça) que, para este usuário, `term` significa `value`."""
    chave = norm(term)
    if not chave or len(chave) > 120 or not value:
        return None
    # Termos genéricos demais não viram conhecimento: ensinariam errado.
    if chave in _RUIDO or len(chave) < 2:
        return None

    existente = db.scalars(
        select(KnowledgeEntry).where(
            KnowledgeEntry.user_id == user.id,
            KnowledgeEntry.kind == kind,
            KnowledgeEntry.key_norm == chave,
        )
    ).first()

    agora = dt.datetime.now(dt.timezone.utc)
    if existente is not None:
        if existente.value == value:
            existente.hits += 1
        else:
            # Mudou de ideia: o novo significado vale, e o contador reinicia
            # porque a confiança antiga não se transfere.
            existente.value = value
            existente.hits = 1
            existente.source = source
        existente.last_used_at = agora
        db.flush()
        return existente

    entrada = KnowledgeEntry(
        user_id=user.id,
        kind=kind,
        key_raw=term.strip()[:120],
        key_norm=chave,
        key_phonetic=phonetic_key(chave)[:120],
        value=value,
        hits=1,
        source=source,
        created_at=agora,
        last_used_at=agora,
    )
    db.add(entrada)
    db.flush()
    return entrada


def lookup(
    db: Session, user: User, kind: str, term: str
) -> tuple[str | None, float, list[str]]:
    """Procura o termo na memória do usuário.

    Devolve `(valor, confiança, empatados)`. A confiança sobe com o número de
    acertos: um termo confirmado cinco vezes vale mais que um confirmado uma.
    """
    chave = norm(term)
    if not chave:
        return None, 0.0, []

    entradas = list(db.scalars(
        select(KnowledgeEntry).where(
            KnowledgeEntry.user_id == user.id, KnowledgeEntry.kind == kind
        )
    ).all())
    if not entradas:
        return None, 0.0, []

    # Acerto exato: o caminho barato, que é o que mais acontece.
    for entrada in entradas:
        if entrada.key_norm == chave:
            return entrada.value, _confianca(entrada, 1.0), []

    # Depois o som, que resolve a escrita torta do mesmo termo já aprendido.
    som = phonetic_key(chave)
    for entrada in entradas:
        if som and entrada.key_phonetic == som:
            return entrada.value, _confianca(entrada, 0.94), []

    candidatos = {e.key_norm: e.value for e in entradas}
    valor, score, empatados = fuzzy.best_match(chave, candidatos)
    if valor is None:
        return None, 0.0, []
    entrada = next(e for e in entradas if e.value == valor)
    return valor, _confianca(entrada, score), empatados


def _confianca(entrada: KnowledgeEntry, base: float) -> float:
    """Acertos repetidos empurram a confiança para cima, com teto."""
    bonus = min(0.06, 0.02 * max(0, entrada.hits - 1))
    return round(min(0.99, base + bonus), 4)


def touch(db: Session, user: User, kind: str, term: str) -> None:
    """Marca uso sem alterar o significado — mantém o que é vivo no topo."""
    chave = norm(term)
    entrada = db.scalars(
        select(KnowledgeEntry).where(
            KnowledgeEntry.user_id == user.id,
            KnowledgeEntry.kind == kind,
            KnowledgeEntry.key_norm == chave,
        )
    ).first()
    if entrada is not None:
        entrada.hits += 1
        entrada.last_used_at = dt.datetime.now(dt.timezone.utc)
        db.flush()


def forget(db: Session, user: User, kind: str, term: str) -> bool:
    """Esquece um termo — usado quando o usuário corrige uma resolução errada."""
    chave = norm(term)
    entrada = db.scalars(
        select(KnowledgeEntry).where(
            KnowledgeEntry.user_id == user.id,
            KnowledgeEntry.kind == kind,
            KnowledgeEntry.key_norm == chave,
        )
    ).first()
    if entrada is None:
        return False
    db.delete(entrada)
    db.flush()
    return True


def forget_value(db: Session, user: User, value: str) -> int:
    """Apaga tudo que apontava para um objeto que deixou de existir."""
    entradas = db.scalars(
        select(KnowledgeEntry).where(
            KnowledgeEntry.user_id == user.id, KnowledgeEntry.value == value
        )
    ).all()
    for entrada in entradas:
        db.delete(entrada)
    db.flush()
    return len(entradas)


def entries(db: Session, user: User, kind: str | None = None) -> list[KnowledgeEntry]:
    consulta = select(KnowledgeEntry).where(KnowledgeEntry.user_id == user.id)
    if kind:
        consulta = consulta.where(KnowledgeEntry.kind == kind)
    return list(db.scalars(consulta.order_by(KnowledgeEntry.hits.desc())).all())


def stats(db: Session, user: User) -> dict[str, int]:
    todas = entries(db, user)
    return {
        "termos": len(todas),
        "materias": len([e for e in todas if e.kind == KnowledgeKind.SUBJECT.value]),
        "tipos": len([e for e in todas if e.kind == KnowledgeKind.EVENT_TYPE.value]),
        "confiaveis": len([e for e in todas if e.hits >= CONFIANCA_APRENDIDA]),
    }


# Palavras que aparecem em toda frase e não identificam nada. Aprender "de" ou
# "prova" como apelido de matéria envenenaria a base inteira.
_RUIDO = frozenset({
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "em", "no", "na",
    "um", "uma", "e", "ou", "que", "para", "pra", "por", "com", "sem", "the",
    "aula", "prova", "trabalho", "tema", "tarefa", "atividade", "materia",
    "professor", "professora", "amanha", "hoje", "ontem", "semana", "mes",
})
