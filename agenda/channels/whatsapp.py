"""WhatsApp como segunda interface do produto (SPEC §15-§19, §67, §68).

O webhook apenas valida, persiste e devolve 200; todo o trabalho pesado
(transcrição, leitura de PDF) acontece fora dele.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.core import assistant, phone as phone_utils
from agenda.core.events import log
from agenda.models import ChannelMessage, LinkToken, SourceType, User, UserPhone

API_BASE = "https://graph.facebook.com"
CHANNEL = "whatsapp"


def is_configured() -> bool:
    return bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID)


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #
def verify_challenge(mode: str, token: str, challenge: str) -> str | None:
    if mode == "subscribe" and token and hmac.compare_digest(token, config.WHATSAPP_VERIFY_TOKEN):
        return challenge
    return None


def valid_signature(body: bytes, header: str | None) -> bool:
    """Valida X-Hub-Signature-256 (SPEC §67)."""
    if not config.WHATSAPP_APP_SECRET:
        # Sem segredo configurado não há como validar; em produção isso é bloqueio.
        return not config.IS_PRODUCTION
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(
        config.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def parse_webhook(payload: dict) -> list[dict]:
    """Extrai as mensagens do formato da Cloud API."""
    out: list[dict] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for message in value.get("messages", []) or []:
                kind = message.get("type", "text")
                item = {
                    "provider_message_id": message.get("id", ""),
                    "from_phone": phone_utils.normalize("+" + str(message.get("from", ""))),
                    "kind": kind,
                    "text": "",
                    "media_id": "",
                    "mime_type": "",
                    "filename": "",
                    "raw": message,
                }
                if kind == "text":
                    item["text"] = (message.get("text") or {}).get("body", "")
                elif kind in ("audio", "voice"):
                    media = message.get("audio") or {}
                    item["kind"] = "audio"
                    item["media_id"] = media.get("id", "")
                    item["mime_type"] = media.get("mime_type", "audio/ogg")
                elif kind == "image":
                    media = message.get("image") or {}
                    item["media_id"] = media.get("id", "")
                    item["mime_type"] = media.get("mime_type", "image/jpeg")
                    item["text"] = media.get("caption", "")
                    item["filename"] = "foto.jpg"
                elif kind == "document":
                    media = message.get("document") or {}
                    item["media_id"] = media.get("id", "")
                    item["mime_type"] = media.get("mime_type", "")
                    item["filename"] = media.get("filename", "documento")
                    item["text"] = media.get("caption", "")
                elif kind == "interactive":
                    interactive = message.get("interactive") or {}
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    item["kind"] = "text"
                    item["text"] = reply.get("title", "")
                if item["provider_message_id"]:
                    out.append(item)
    return out


def persist_inbound(db: Session, item: dict) -> ChannelMessage | None:
    """Grava a mensagem — idempotente por provider_message_id (SPEC §73)."""
    existing = db.scalars(
        select(ChannelMessage).where(
            ChannelMessage.channel == CHANNEL,
            ChannelMessage.provider_message_id == item["provider_message_id"],
        )
    ).first()
    if existing is not None:
        return None
    message = ChannelMessage(
        channel=CHANNEL,
        provider_message_id=item["provider_message_id"],
        from_phone=item.get("from_phone", ""),
        kind=item.get("kind", "text"),
        text=item.get("text", "") or "",
        media_id=item.get("media_id", ""),
        raw=item.get("raw"),
        status="RECEIVED",
    )
    db.add(message)
    db.flush()
    return message


# --------------------------------------------------------------------------- #
# Processamento
# --------------------------------------------------------------------------- #
def find_user(db: Session, phone_e164: str) -> User | None:
    if not phone_e164:
        return None
    candidates = phone_utils.variants(phone_e164)
    link = db.scalars(
        select(UserPhone).where(
            UserPhone.channel == CHANNEL,
            UserPhone.active.is_(True),
            UserPhone.phone_e164.in_(candidates),
        )
    ).first()
    if link is None:
        return None
    return db.get(User, link.user_id)


def process(db: Session, message: ChannelMessage) -> str:
    """Processa uma mensagem já persistida e devolve a resposta enviada."""
    user = find_user(db, message.from_phone)
    if user is None:
        reply = _unlinked_reply(db, message)
        message.status = "UNLINKED"
        message.processed_at = dt.datetime.now(dt.timezone.utc)
        _send_raw(message.from_phone, reply)
        return reply

    message.user_id = user.id
    text = message.text or ""

    if message.kind == "audio":
        text = _transcribe(db, user, message)
        if not text:
            reply = "Não consegui entender o áudio. Pode mandar de novo ou escrever?"
            message.status = "FAILED"
            send_text(db, user, reply)
            return reply
        message.transcript = text
    elif message.kind in ("document", "image"):
        return _handle_media(db, user, message)

    result = assistant.handle_message(
        db, user, text, channel=CHANNEL, source_type=SourceType.WHATSAPP.value, source_id=message.id
    )
    message.status = "EXECUTED"
    message.processed_at = dt.datetime.now(dt.timezone.utc)
    reply = format_reply(result)
    send_text(db, user, reply)
    return reply


def _unlinked_reply(db: Session, message: ChannelMessage) -> str:
    """Nunca mostrar dados acadêmicos antes da vinculação (SPEC §16)."""
    base = config.PUBLIC_URL or "https://app.exemplo.com"
    return (
        "Oi! Este número ainda não está conectado a uma conta.\n"
        f"Toque para vincular: {base}/conectar"
    )


def _transcribe(db: Session, user: User, message: ChannelMessage) -> str:
    from agenda.ai.providers import get_speech_provider, record_usage

    audio = download_media(message.media_id)
    if not audio:
        return ""
    message.status = "TRANSCRIBING"
    result = get_speech_provider().transcribe(audio, "audio/ogg")
    if not result.ok:
        message.error = result.error
        return ""
    record_usage(db, user_id=user.id, operation="transcribe", result=result)
    return result.text


def _handle_media(db: Session, user: User, message: ChannelMessage) -> str:
    from agenda.ingest import pipeline

    raw = message.raw or {}
    media = raw.get("document") or raw.get("image") or {}
    filename = media.get("filename") or ("foto.jpg" if message.kind == "image" else "documento.pdf")
    send_text(db, user, "Recebi. Estou analisando…")
    data = download_media(message.media_id)
    if not data:
        message.status = "FAILED"
        reply = "Não consegui baixar o arquivo. Pode tentar de novo?"
        send_text(db, user, reply)
        return reply
    try:
        document = pipeline.ingest(
            db, user, filename, data,
            source_channel=SourceType.WHATSAPP.value,
            mime_type=media.get("mime_type", ""),
        )
    except pipeline.UploadError as exc:
        message.status = "FAILED"
        send_text(db, user, str(exc))
        return str(exc)

    message.status = "EXECUTED"
    message.processed_at = dt.datetime.now(dt.timezone.utc)
    summary = pipeline.summary(db, document)
    if document.status == "FAILED":
        reply = document.error or "Não consegui ler esse arquivo."
    else:
        lines = ["Encontrei:"]
        if summary["subjects"]:
            lines.append(f"{summary['subjects']} matérias")
        if summary["schedules"]:
            lines.append(f"{summary['schedules']} aulas")
        if summary["events"]:
            lines.append(f"{summary['events']} atividades")
        if summary["needs_review"]:
            lines.append(f"{summary['needs_review']} item(ns) para revisar")
        base = config.PUBLIC_URL or ""
        lines.append(f"\nRevisar e importar: {base}/documentos/{document.id}")
        reply = "\n".join(lines)
    send_text(db, user, reply)
    return reply


def format_reply(result: dict) -> str:
    """Resposta curta e estruturada (SPEC §128)."""
    lines = [result.get("message", "")]
    for card in (result.get("cards") or [])[:8]:
        bits = [b for b in (card.get("time"), card.get("date_label"), card.get("title")) if b]
        line = " · ".join(bits)
        if card.get("subject"):
            line += f" ({card['subject']})"
        lines.append(f"• {line}")
    if result.get("status") == "NEEDS_CONFIRMATION":
        lines.append("\nResponda *sim* para confirmar.")
    return "\n".join(l for l in lines if l).strip()


# --------------------------------------------------------------------------- #
# Envio
# --------------------------------------------------------------------------- #
def can_send(db: Session, user: User) -> bool:
    if not is_configured() or not config.flag("whatsapp_enabled"):
        return False
    return bool(_phone_of(db, user))


def _phone_of(db: Session, user: User) -> str:
    link = db.scalars(
        select(UserPhone).where(
            UserPhone.user_id == user.id,
            UserPhone.channel == CHANNEL,
            UserPhone.active.is_(True),
        )
    ).first()
    return link.phone_e164 if link else ""


def send_text(db: Session, user: User, text: str) -> tuple[bool, str, str]:
    phone = _phone_of(db, user)
    if not phone:
        return False, "", "usuário sem WhatsApp vinculado"
    return _send_raw(phone, text)


def _send_raw(phone: str, text: str) -> tuple[bool, str, str]:
    if not is_configured():
        print(f"[whatsapp] (simulado) → {phone}: {text[:120]}")
        return False, "", "WhatsApp não configurado"
    url = f"{API_BASE}/{config.WHATSAPP_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": phone.lstrip("+"),
                "type": "text",
                "text": {"preview_url": False, "body": text[:4000]},
            },
            timeout=20,
        )
        if response.status_code >= 300:
            return False, "", f"{response.status_code}: {response.text[:200]}"
        data = response.json()
        provider_id = (data.get("messages") or [{}])[0].get("id", "")
        return True, provider_id, ""
    except requests.RequestException as exc:
        return False, "", str(exc)


def download_media(media_id: str) -> bytes:
    """Baixa a mídia pelo endpoint oficial, com limite de tamanho (SPEC §68)."""
    if not media_id or not is_configured():
        return b""
    headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
    try:
        meta = requests.get(
            f"{API_BASE}/{config.WHATSAPP_API_VERSION}/{media_id}", headers=headers, timeout=20
        )
        if meta.status_code >= 300:
            return b""
        url = meta.json().get("url")
        if not url:
            return b""
        response = requests.get(url, headers=headers, timeout=60, stream=True)
        if response.status_code >= 300:
            return b""
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > config.MAX_UPLOAD_BYTES:
                return b""
            chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException:
        return b""


# --------------------------------------------------------------------------- #
# Vinculação de conta (SPEC §17)
# --------------------------------------------------------------------------- #
def create_link_token(db: Session, user: User, *, ttl_minutes: int = 30) -> LinkToken:
    token = LinkToken(
        user_id=user.id,
        token=secrets.token_urlsafe(9)[:12].upper(),
        purpose="whatsapp_link",
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=ttl_minutes),
    )
    db.add(token)
    db.flush()
    return token


def deep_link(token: LinkToken) -> str:
    number = (config.WHATSAPP_NUMBER or "").lstrip("+")
    text = f"CONECTAR {token.token}"
    return f"https://wa.me/{number}?text={text.replace(' ', '%20')}"


def consume_link_token(db: Session, raw_token: str, phone_e164: str) -> User | None:
    """Valida o token e grava o vínculo. Token é de uso único."""
    now = dt.datetime.now(dt.timezone.utc)
    token = db.scalars(
        select(LinkToken).where(LinkToken.token == raw_token.strip().upper())
    ).first()
    if token is None or token.used_at is not None:
        return None
    expires = token.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if expires < now:
        return None
    user = db.get(User, token.user_id)
    if user is None:
        return None
    link_phone(db, user, phone_e164)
    token.used_at = now
    return user


def link_phone(db: Session, user: User, phone_e164: str) -> UserPhone:
    existing = db.scalars(
        select(UserPhone).where(
            UserPhone.phone_e164 == phone_e164, UserPhone.channel == CHANNEL
        )
    ).first()
    if existing is not None:
        existing.user_id = user.id
        existing.active = True
        existing.verified = True
        link = existing
    else:
        link = UserPhone(
            user_id=user.id, phone_e164=phone_e164, channel=CHANNEL, verified=True, active=True
        )
        db.add(link)
    user.phone_e164 = user.phone_e164 or phone_e164
    user.phone_verified = True
    log(db, user_id=user.id, actor="user", action="LINK_WHATSAPP", object_type="user_phone",
        object_id=link.id, origin=CHANNEL)
    db.flush()
    return link


def unlink(db: Session, user: User) -> int:
    """Vinculação é sempre revogável (SPEC §17)."""
    links = db.scalars(
        select(UserPhone).where(UserPhone.user_id == user.id, UserPhone.channel == CHANNEL)
    ).all()
    for link in links:
        link.active = False
    log(db, user_id=user.id, actor="user", action="UNLINK_WHATSAPP", object_type="user_phone")
    db.flush()
    return len(links)
