"""Serviço do assistente — o mesmo para web e WhatsApp (SPEC §144).

Fluxo: normalizar → interpretar → propor → validar → executar → responder.
O tom das respostas segue SPEC §128: direto, curto, sem euforia.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from agenda.ai.interpreter import interpret
from agenda.core import actions as actions_core
from agenda.core.actions import ActionProposal, ActionResult
from agenda.models import AssistantMessage, User


def handle_message(
    db: Session,
    user: User,
    text: str,
    *,
    channel: str = "web",
    source_type: str = "WEB_CAPTURE",
    source_id: str | None = None,
    confirm_action_id: str | None = None,
) -> dict:
    """Processa uma mensagem do usuário e devolve a resposta estruturada."""
    if confirm_action_id:
        result = actions_core.execute_pending(db, user, confirm_action_id)
        _record(db, user, "assistant", result.message, channel, result.as_dict())
        return result.as_dict()

    _record(db, user, "user", text, channel, None)

    interpretation = interpret(
        db, user, text, channel=channel, source_type=source_type, source_id=source_id
    )
    proposals = interpretation.proposals
    if not proposals:
        reply = interpretation.reply or _pergunta_util(db, user, text)
        _record(db, user, "assistant", reply, channel, None)
        return ActionResult("REJECTED", message=reply).as_dict()

    # Lote grande exige um resumo antes de executar (SPEC §22).
    writes = [p for p in proposals if p.intent not in actions_core.READ_INTENTS]
    if len(writes) > 2:
        return _handle_batch(db, user, writes, channel)

    results = [actions_core.execute(db, user, proposal) for proposal in proposals]
    merged = _merge(results)
    _record(db, user, "assistant", merged.message, channel, merged.as_dict())
    return merged.as_dict()


def confirm(db: Session, user: User, action_id: str) -> dict:
    result = actions_core.execute_pending(db, user, action_id)
    _record(db, user, "assistant", result.message, "web", result.as_dict())
    return result.as_dict()


def undo(db: Session, user: User, action_id: str) -> dict:
    result = actions_core.undo(db, user, action_id)
    _record(db, user, "assistant", result.message, "web", result.as_dict())
    return result.as_dict()


def _pergunta_util(db: Session, user: User, text: str) -> str:
    """Resposta de quando não deu para montar nada.

    Regra: nunca devolver só "não entendi". Isso empurra o trabalho de volta
    para quem já escreveu uma vez, e é o jeito mais rápido de fazer alguém
    desistir do app. Aqui a resposta sempre diz **o que foi reconhecido** e
    pede exatamente **a peça que falta** — e, quando não reconheceu nada,
    oferece exemplos no vocabulário do nível do estudante.
    """
    from agenda.core import academic, profiles
    from agenda.knowledge import lexicon, resolver

    contexto = academic.active_context(db, user.id)
    perfil = profiles.profile_for(contexto.type if contexto else None)

    if lexicon.looks_like_question(text):
        return (
            "Não achei nada com isso. Tenta assim: “o que eu tenho amanhã?”, "
            "“quais provas estão chegando?” ou “o que está atrasado?”."
        )

    tipo, _termo, _score = lexicon.find_event_type(text)
    materia = resolver.resolve_subject(db, user, text)
    rotulo = profiles.type_label(tipo, contexto.type if contexto else None) if tipo else ""

    # Reconheceu a atividade, faltou a data — a peça que mais falta.
    if tipo and materia.resolved:
        return f"Entendi: {rotulo.lower()} de {materia.subject.display}. Para quando é?"
    if tipo:
        return f"Entendi que é {rotulo.lower()}. De qual matéria, e para quando?"
    if materia.resolved:
        return (
            f"Entendi que é de {materia.subject.display}, mas não peguei o que é. "
            "É prova, trabalho, tema de casa?"
        )
    if materia.suggested_subject_name:
        return (
            f"Você quis dizer {materia.suggested_subject_name}? "
            "Ainda não tenho essa matéria cadastrada — me diga o que é e para quando."
        )

    exemplos = " ou ".join(f"“{e}”" for e in perfil.capture_examples[:2])
    return f"Não peguei essa. Pode mandar assim: {exemplos}."


def _handle_batch(db: Session, user: User, proposals: list[ActionProposal], channel: str) -> dict:
    """Executa em lote quando todas as propostas são confiáveis."""
    low_confidence = [p for p in proposals if p.confidence < 0.8 or p.question]
    if low_confidence:
        message = actions_core.summarize_batch(proposals)
        results = [actions_core.execute(db, user, p) for p in proposals]
        cards = [card for r in results for card in r.cards]
        merged = ActionResult("NEEDS_CONFIRMATION", message=message, cards=cards)
        _record(db, user, "assistant", message, channel, merged.as_dict())
        return merged.as_dict()

    results = [actions_core.execute(db, user, p, confirmed=True) for p in proposals]
    ok = [r for r in results if r.ok]
    message = f"Pronto. Cadastrei {len(ok)} item(ns)."
    merged = ActionResult(
        "EXECUTED", message=message, cards=[card for r in ok for card in r.cards]
    )
    _record(db, user, "assistant", message, channel, merged.as_dict())
    return merged.as_dict()


def _merge(results: list[ActionResult]) -> ActionResult:
    if len(results) == 1:
        return results[0]
    blocking = next(
        (r for r in results if r.status in ("NEEDS_CLARIFICATION", "NEEDS_CONFIRMATION")), None
    )
    if blocking is not None:
        return blocking
    cards = [card for r in results for card in r.cards]
    messages = [r.message for r in results if r.message]
    return ActionResult(
        "EXECUTED" if any(r.ok for r in results) else results[0].status,
        message=" ".join(messages)[:400],
        cards=cards,
        action_id=next((r.action_id for r in results if r.action_id), None),
        undoable=any(r.undoable for r in results),
    )


def _record(
    db: Session, user: User, role: str, text: str, channel: str, payload: dict | None
) -> None:
    db.add(
        AssistantMessage(
            user_id=user.id, role=role, channel=channel, text=(text or "")[:4000], payload=payload
        )
    )
    db.flush()
