"""Telegram como canal alternativo de captura e notificação.

Mesma arquitetura do WhatsApp: identificação por vínculo, interpretação pelo
núcleo compartilhado e resposta curta. Útil onde o WhatsApp Business ainda não
está aprovado.
"""
from __future__ import annotations

import datetime as dt
import hmac
import threading
import time

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.core import assistant
from agenda.db import session_scope
from agenda.models import SourceType, User, UserPhone

CHANNEL = "telegram"
_poller_started = False


def is_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN)


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def can_send(db: Session, user: User) -> bool:
    return is_configured() and bool(_chat_of(db, user))


def _chat_of(db: Session, user: User) -> str:
    link = db.scalars(
        select(UserPhone).where(
            UserPhone.user_id == user.id,
            UserPhone.channel == CHANNEL,
            UserPhone.active.is_(True),
        )
    ).first()
    return link.external_id if link else ""


def send_text(db: Session, user: User, text: str) -> bool:
    chat_id = _chat_of(db, user)
    return bool(chat_id) and _send_raw(chat_id, text)


def _send_raw(chat_id: str, text: str) -> bool:
    if not is_configured():
        print(f"[telegram] (simulado) → chat {chat_id[-4:]}: {len(text)} caracteres")
        return False
    try:
        response = requests.post(
            _api("sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        return response.status_code == 200
    except requests.RequestException as exc:
        print(f"[telegram] falha de rede: {exc}")
        return False


def link_chat(db: Session, user: User, chat_id: str) -> UserPhone:
    existing = db.scalars(
        select(UserPhone).where(
            UserPhone.channel == CHANNEL, UserPhone.external_id == str(chat_id)
        )
    ).first()
    if existing is not None:
        existing.user_id = user.id
        existing.active = True
        return existing
    link = UserPhone(
        user_id=user.id,
        phone_e164=f"tg:{chat_id}",
        channel=CHANNEL,
        external_id=str(chat_id),
        verified=True,
        active=True,
    )
    db.add(link)
    db.flush()
    return link


def valid_secret(header: str | None) -> bool:
    """Confere o segredo que o Telegram devolve no webhook.

    Sem isto, o endpoint é um caminho de ESCRITA não autenticado: quem
    descobrir o chat_id de alguém (um grupo em comum, uma mensagem
    encaminhada, um bot de "qual é meu id") manda uma requisição e cria,
    remarca ou apaga compromissos na conta daquela pessoa.

    Em produção falha fechado: sem segredo configurado, nenhuma requisição
    passa. Em desenvolvimento, sem segredo, o webhook segue aberto para não
    travar quem está testando com um túnel local.
    """
    esperado = config.TELEGRAM_WEBHOOK_SECRET
    if not esperado:
        return not config.IS_PRODUCTION
    return bool(header) and hmac.compare_digest(esperado, header)


def _user_of_chat(db: Session, chat_id: str) -> User | None:
    link = db.scalars(
        select(UserPhone).where(
            UserPhone.channel == CHANNEL,
            UserPhone.external_id == str(chat_id),
            UserPhone.active.is_(True),
        )
    ).first()
    return db.get(User, link.user_id) if link else None


def handle_update(update: dict) -> None:
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    text = (message.get("text") or "").strip()
    if not chat_id:
        return

    with session_scope() as db:
        user = _user_of_chat(db, chat_id)

        if text.lower().startswith("/start"):
            parts = text.split()
            if len(parts) > 1:
                from agenda.channels import whatsapp as whatsapp_channel
                from agenda.models import LinkToken

                # O propósito faz parte da identidade do token: um código
                # emitido para vincular WhatsApp não pode vincular Telegram.
                token = db.scalars(
                    select(LinkToken).where(
                        LinkToken.token == parts[1].strip().upper(),
                        LinkToken.purpose.in_(whatsapp_channel.CHANNEL_LINK_PURPOSES),
                    )
                ).first()
                expires = token.expires_at if token else None
                if expires is not None and expires.tzinfo is None:
                    expires = expires.replace(tzinfo=dt.timezone.utc)
                if token and token.used_at is None and expires > dt.datetime.now(dt.timezone.utc):
                    owner = db.get(User, token.user_id)
                    if owner is not None:
                        link_chat(db, owner, chat_id)
                        token.used_at = dt.datetime.now(dt.timezone.utc)
                        _send_raw(chat_id, "✅ Conectado! Agora é só me contar o que precisa lembrar.")
                        return
            if user is None:
                base = config.PUBLIC_URL or ""
                _send_raw(
                    chat_id,
                    "Este chat ainda não está conectado a uma conta.\n"
                    f"Abra {base}/perfil e use o código de conexão.",
                )
                return
            _send_raw(chat_id, "Já estamos conectados. Pode mandar o que precisa lembrar.")
            return

        if user is None:
            base = config.PUBLIC_URL or ""
            _send_raw(chat_id, f"Este chat não está conectado a uma conta. Abra {base}/perfil.")
            return

        # A trava de consentimento também vale aqui: o webhook não passa pelo
        # before_request, então cada canal repete a checagem. Sem isto, uma
        # conta pausada por falta de autorização do responsável continuaria
        # funcionando por este caminho.
        from agenda.core import privacy

        pendencia = privacy.blocked_reason(db, user)
        if pendencia is not None:
            _send_raw(chat_id, (
                "Sua conta está pausada esperando a autorização do responsável."
                if pendencia == "responsavel" else
                "Atualizamos os termos. Abra o aplicativo para aceitar a nova versão."
            ))
            return

        if not text:
            _send_raw(chat_id, "Por enquanto eu leio texto por aqui. Manda escrito?")
            return

        result = assistant.handle_message(
            db, user, text, channel=CHANNEL, source_type=SourceType.TELEGRAM.value
        )
        from agenda.channels.whatsapp import format_reply

        _send_raw(chat_id, format_reply(result))


def _poll_loop() -> None:  # pragma: no cover - laço de rede
    offset = 0
    print("[telegram] poller iniciado.")
    while True:
        try:
            response = requests.get(
                _api("getUpdates"), params={"offset": offset, "timeout": 50}, timeout=60
            )
            for update in response.json().get("result", []):
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception as exc:  # noqa: BLE001
                    print(f"[telegram] erro ao processar update: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram] poller: {exc}")
            time.sleep(5)


def start_poller() -> None:  # pragma: no cover - infra
    global _poller_started
    if _poller_started or not is_configured():
        return
    _poller_started = True
    threading.Thread(target=_poll_loop, daemon=True, name="telegram-poller").start()
