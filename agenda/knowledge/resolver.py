"""Resolução local: entende antes de gastar modelo.

A ordem das tentativas é a ordem do custo. Cada degrau só é usado quando o
anterior não deu conta:

1. **memória do usuário** — ele já confirmou esse termo antes (grátis, instantâneo);
2. **cadastro dele** — nome, apelido, abreviação da matéria (grátis);
3. **som** — a mesma matéria escrita torta ou transcrita errada (grátis);
4. **léxico** — vocabulário acadêmico brasileiro conhecido (grátis);
5. **modelo externo** — só o que sobrou, e agora com um prompt pequeno.

Na prática, um usuário com duas semanas de uso quase não chega no degrau 5.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from agenda.core import academic
from agenda.knowledge import fuzzy, lexicon, store
from agenda.models import KnowledgeKind, Subject, User


@dataclass
class Resolution:
    """O que a camada local conseguiu entender — e com que certeza."""

    subject: Subject | None = None
    subject_options: list[Subject] = field(default_factory=list)
    event_type: str = ""
    confidence: float = 0.0
    origin: str = ""            # memoria | cadastro | som | lexico | nenhum
    learn_terms: list[dict] = field(default_factory=list)
    suggested_subject_name: str = ""

    @property
    def resolved(self) -> bool:
        return self.subject is not None

    @property
    def ambiguous(self) -> bool:
        return self.subject is None and bool(self.subject_options)


def resolve_subject(db: Session, user: User, texto: str) -> Resolution:
    """Descobre de qual matéria a pessoa está falando."""
    termo = (texto or "").strip()
    if not termo:
        return Resolution()

    # 1. O que este usuário já ensinou.
    valor, confianca, empatados = store.lookup(
        db, user, KnowledgeKind.SUBJECT.value, termo
    )
    if valor and not empatados and confianca >= fuzzy.LIMIAR_CONFIANTE:
        materia = _subject_of(db, user, valor)
        if materia is not None:
            return Resolution(
                subject=materia, confidence=confianca, origin="memoria",
                learn_terms=[_term(KnowledgeKind.SUBJECT.value, termo, materia.id)],
            )

    # 2 e 3. Cadastro do usuário, incluindo o casamento por som, que já mora
    # dentro de `academic.resolve_subject`.
    contexto = academic.active_context(db, user.id)
    materia, opcoes = academic.resolve_subject(
        db, user.id, termo, context_id=contexto.id if contexto else None
    )
    if materia is not None:
        return Resolution(
            subject=materia, confidence=0.95, origin="cadastro",
            learn_terms=[_term(KnowledgeKind.SUBJECT.value, termo, materia.id)],
        )
    if opcoes:
        return Resolution(subject_options=list(opcoes), confidence=0.5, origin="som")

    # 4. Léxico: o termo é uma matéria conhecida do vocabulário brasileiro,
    # mas o usuário ainda não a cadastrou. Não inventamos a matéria — devolvemos
    # o nome canônico para quem chamou propor "quer criar Biologia?".
    canonico, score = lexicon.canonical_subject(termo)
    if canonico:
        return Resolution(
            confidence=round(score * 0.7, 4), origin="lexico",
            suggested_subject_name=canonico,
        )
    return Resolution(origin="nenhum")


def resolve_event_type(db: Session, user: User, frase: str) -> tuple[str, float, list[dict]]:
    """Descobre o tipo de atividade — memória do usuário primeiro, léxico depois."""
    tipo, termo, score = lexicon.find_event_type(frase)
    if tipo:
        return tipo, min(0.98, score), [_term(KnowledgeKind.EVENT_TYPE.value, termo, tipo)]

    # Léxico não conhece: talvez este usuário use uma palavra própria
    # ("gincana da turma", "atividade do Bruno") já confirmada antes.
    for palavra in lexicon.expand_chat(frase).split():
        if len(palavra) < 4:
            continue
        valor, confianca, empatados = store.lookup(
            db, user, KnowledgeKind.EVENT_TYPE.value, palavra
        )
        if valor and not empatados and confianca >= fuzzy.LIMIAR_CONFIANTE:
            return valor, confianca, [_term(KnowledgeKind.EVENT_TYPE.value, palavra, valor)]
    return "", 0.0, []


def learn_from(db: Session, user: User, termos: list[dict]) -> int:
    """Grava o que uma ação bem-sucedida ensinou. Chamado depois de executar.

    Só entra aqui o que o usuário confirmou na prática — proposta recusada não
    ensina nada, senão o sistema aprenderia o próprio erro.
    """
    aprendidos = 0
    for item in termos or []:
        entrada = store.learn(
            db, user,
            kind=item.get("kind", ""),
            term=item.get("term", ""),
            value=item.get("value", ""),
            source=item.get("source", "confirm"),
        )
        aprendidos += 1 if entrada is not None else 0
    return aprendidos


def _term(kind: str, term: str, value: str) -> dict:
    return {"kind": kind, "term": term, "value": value, "source": "confirm"}


def _subject_of(db: Session, user: User, subject_id: str) -> Subject | None:
    from agenda.core import scope

    materia = scope.get(db, Subject, subject_id, user.id)
    if materia is None:
        # A matéria sumiu (arquivada, apagada): a lembrança perdeu o objeto.
        store.forget_value(db, user, subject_id)
    return materia
