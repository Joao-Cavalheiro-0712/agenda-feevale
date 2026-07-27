"""Integração com o Telegram: envio de mensagens e captura de chat_ids.

Não usamos webhooks — um thread faz long-polling via getUpdates para
descobrir automaticamente quem mandou /start e registrar o chat.
"""
from __future__ import annotations

import threading
import time

import requests

import config
from database import SessionLocal, Subscriber

API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

_poller_started = False


def send_message(chat_id: str, text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN:
        print("[telegram] TELEGRAM_BOT_TOKEN ausente; não enviei.")
        return False
    try:
        r = requests.post(
            f"{API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[telegram] erro {r.status_code}: {r.text[:200]}")
        return r.status_code == 200
    except requests.RequestException as exc:
        print(f"[telegram] falha de rede: {exc}")
        return False


def broadcast(text: str) -> int:
    """Envia a mensagem para todos os inscritos ativos. Retorna quantos receberam."""
    sent = 0
    with SessionLocal() as db:
        subs = db.query(Subscriber).filter_by(active=True).all()
        chat_ids = [s.chat_id for s in subs]
    # Garante o chat configurado por env, mesmo que ainda não tenha dado /start.
    if config.TELEGRAM_CHAT_ID and config.TELEGRAM_CHAT_ID not in chat_ids:
        chat_ids.append(config.TELEGRAM_CHAT_ID)
    for cid in chat_ids:
        if send_message(cid, text):
            sent += 1
    return sent


def _register_chat(chat: dict) -> Subscriber:
    chat_id = str(chat["id"])
    name = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
    with SessionLocal() as db:
        sub = db.query(Subscriber).filter_by(chat_id=chat_id).first()
        if not sub:
            sub = Subscriber(chat_id=chat_id, name=name, active=True)
            db.add(sub)
        else:
            sub.active = True
            sub.name = name or sub.name
        db.commit()
        db.refresh(sub)
        return sub


def _handle_update(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat = msg.get("chat", {})
    text = (msg.get("text") or "").strip().lower()
    chat_id = str(chat.get("id"))

    if text.startswith("/start") or text.startswith("/inscrever"):
        _register_chat(chat)
        send_message(
            chat_id,
            "✅ <b>Inscrito!</b> Vou te avisar sobre suas avaliações da Feevale "
            "1 semana antes e 1 dia antes de cada uma.\n\n"
            "Envie /proximas para ver o que está por vir ou /sair para cancelar.",
        )
    elif text.startswith("/sair") or text.startswith("/parar"):
        with SessionLocal() as db:
            sub = db.query(Subscriber).filter_by(chat_id=chat_id).first()
            if sub:
                sub.active = False
                db.commit()
        send_message(chat_id, "🚫 Você não receberá mais avisos. Envie /start para voltar.")
    elif text.startswith("/proximas") or text.startswith("/próximas"):
        send_message(chat_id, _upcoming_text())
    elif text.startswith("/"):
        send_message(
            chat_id,
            "Comandos: /start (inscrever), /proximas (ver avaliações), /sair (cancelar).",
        )


def _upcoming_text() -> str:
    import datetime as dt

    from database import Event

    today = dt.date.today()
    with SessionLocal() as db:
        events = (
            db.query(Event)
            .filter(Event.date >= today)
            .order_by(Event.date)
            .limit(15)
            .all()
        )
    if not events:
        return "Nenhuma avaliação futura cadastrada. Envie seu cronograma na interface web."
    lines = ["📅 <b>Próximas avaliações</b>", ""]
    for e in events:
        faltam = (e.date - today).days
        disc = f" — {e.discipline}" if e.discipline else ""
        lines.append(
            f"• <b>{e.date.strftime('%d/%m')}</b> ({faltam}d) {e.title}{disc}"
        )
    return "\n".join(lines)


def _poll_loop() -> None:
    offset = 0
    print("[telegram] poller iniciado.")
    while True:
        try:
            r = requests.get(
                f"{API}/getUpdates",
                params={"offset": offset, "timeout": 50},
                timeout=60,
            )
            data = r.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    _handle_update(update)
                except Exception as exc:  # noqa: BLE001
                    print(f"[telegram] erro ao processar update: {exc}")
        except requests.RequestException as exc:
            print(f"[telegram] poller sem rede: {exc}")
            time.sleep(5)
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram] poller erro: {exc}")
            time.sleep(5)


def start_poller() -> None:
    global _poller_started
    if _poller_started or not config.TELEGRAM_BOT_TOKEN:
        if not config.TELEGRAM_BOT_TOKEN:
            print("[telegram] sem token; poller não iniciado.")
        return
    _poller_started = True
    t = threading.Thread(target=_poll_loop, daemon=True, name="telegram-poller")
    t.start()
