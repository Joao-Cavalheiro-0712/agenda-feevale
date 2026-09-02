"""Webhooks de canais externos (SPEC §67).

O webhook valida a origem, persiste e responde 200 rápido. O processamento
pesado acontece fora do ciclo de request.
"""
from __future__ import annotations

import threading

from flask import Blueprint, Response, current_app, request

from agenda.channels import telegram, whatsapp
from agenda.db import session_scope
from agenda.security import rate_limit

bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")


@bp.get("/whatsapp")
def whatsapp_verify():
    challenge = whatsapp.verify_challenge(
        request.args.get("hub.mode", ""),
        request.args.get("hub.verify_token", ""),
        request.args.get("hub.challenge", ""),
    )
    if challenge is None:
        return "forbidden", 403
    return Response(challenge, mimetype="text/plain")


@bp.post("/whatsapp")
def whatsapp_inbound():
    identity = request.headers.get("X-Forwarded-For") or request.remote_addr or "wa"
    if not rate_limit("webhook", identity):
        return "", 429
    if not whatsapp.valid_signature(request.get_data(), request.headers.get("X-Hub-Signature-256")):
        return "invalid signature", 403

    payload = request.get_json(silent=True) or {}
    message_ids: list[str] = []
    with session_scope() as db:
        for item in whatsapp.parse_webhook(payload):
            message = whatsapp.persist_inbound(db, item)
            if message is not None:
                message_ids.append(message.id)

    if message_ids:
        _spawn(current_app._get_current_object(), message_ids)
    return "", 200


def _spawn(app, message_ids: list[str]) -> None:  # pragma: no cover - infra
    """Processamento assíncrono. Em produção, troque por fila (BullMQ/Celery)."""

    def worker():
        from agenda.models import ChannelMessage

        with app.app_context():
            for message_id in message_ids:
                try:
                    with session_scope() as db:
                        message = db.get(ChannelMessage, message_id)
                        if message is not None and message.status == "RECEIVED":
                            whatsapp.process(db, message)
                except Exception as exc:  # noqa: BLE001
                    print(f"[webhook] falha ao processar {message_id}: {exc}")

    threading.Thread(target=worker, daemon=True, name="wa-worker").start()


@bp.post("/telegram")
def telegram_inbound():
    identity = request.headers.get("X-Forwarded-For") or request.remote_addr or "tg"
    if not rate_limit("webhook", identity):
        return "", 429
    update = request.get_json(silent=True) or {}
    try:
        telegram.handle_update(update)
    except Exception as exc:  # noqa: BLE001
        print(f"[webhook] telegram: {exc}")
    return "", 200
