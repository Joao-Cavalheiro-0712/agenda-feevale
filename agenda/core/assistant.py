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
        reply = interpretation.reply or (
            "Não entendi. Você pode me dizer, por exemplo: "
            "“prova de matemática sexta” ou “o que tenho essa semana?”."
        )
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
