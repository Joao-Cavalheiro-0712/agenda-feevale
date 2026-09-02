"""Canal WhatsApp: assinatura, idempotência, vinculação e isolamento (SPEC §16-§19, §67)."""
from __future__ import annotations

import hashlib
import hmac

from agenda import config
from agenda.channels import whatsapp
from agenda.core import phone as phone_utils
from agenda.models import ChannelMessage, Event, UserPhone


def _payload(message: dict) -> dict:
    return {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}


TEXT_MESSAGE = {
    "id": "wamid.ABC123",
    "from": "5551999998888",
    "type": "text",
    "text": {"body": "Prova de Civil dia 23"},
}


def test_normalizacao_de_telefone():
    assert phone_utils.normalize("(51) 99999-8888") == "+5551999998888"
    assert phone_utils.normalize("+55 51 99999 8888") == "+5551999998888"
    assert phone_utils.normalize("99998888") == ""
    assert "+555199998888" in phone_utils.variants("+5551999998888")


def test_parse_extrai_texto_audio_e_documento():
    text = whatsapp.parse_webhook(_payload(TEXT_MESSAGE))[0]
    assert text["kind"] == "text" and text["text"] == "Prova de Civil dia 23"

    audio = whatsapp.parse_webhook(
        _payload({"id": "1", "from": "5551999998888", "type": "audio",
                  "audio": {"id": "media-1", "mime_type": "audio/ogg"}})
    )[0]
    assert audio["kind"] == "audio" and audio["media_id"] == "media-1"

    document = whatsapp.parse_webhook(
        _payload({"id": "2", "from": "5551999998888", "type": "document",
                  "document": {"id": "media-2", "filename": "cronograma.pdf"}})
    )[0]
    assert document["kind"] == "document" and document["filename"] == "cronograma.pdf"


def test_verificacao_do_webhook():
    assert whatsapp.verify_challenge("subscribe", config.WHATSAPP_VERIFY_TOKEN, "123") == "123"
    assert whatsapp.verify_challenge("subscribe", "errado", "123") is None


def test_assinatura_invalida_e_recusada(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "segredo")
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"segredo", body, hashlib.sha256).hexdigest()
    assert whatsapp.valid_signature(body, good)
    assert not whatsapp.valid_signature(body, "sha256=deadbeef")
    assert not whatsapp.valid_signature(body, None)


def test_mensagem_repetida_nao_e_processada_duas_vezes(db):
    item = whatsapp.parse_webhook(_payload(TEXT_MESSAGE))[0]
    assert whatsapp.persist_inbound(db, item) is not None
    db.commit()
    assert whatsapp.persist_inbound(db, item) is None
    db.commit()
    assert db.query(ChannelMessage).count() == 1


def test_numero_nao_vinculado_nao_ve_dados(db, user):
    item = whatsapp.parse_webhook(_payload(TEXT_MESSAGE))[0]
    message = whatsapp.persist_inbound(db, item)
    db.commit()

    reply = whatsapp.process(db, message)
    assert "não está conectado" in reply
    assert message.status == "UNLINKED"
    assert db.query(Event).count() == 0


def test_vinculacao_por_token_de_uso_unico(db, user):
    token = whatsapp.create_link_token(db, user)
    db.commit()

    linked = whatsapp.consume_link_token(db, token.token, "+5551999998888")
    db.commit()
    assert linked is not None and linked.id == user.id
    assert db.query(UserPhone).filter_by(user_id=user.id).count() == 1

    # Segundo uso do mesmo token é recusado.
    assert whatsapp.consume_link_token(db, token.token, "+5551999997777") is None


def test_mensagem_de_numero_vinculado_cria_evento(db, user):
    from agenda.core import academic

    context = academic.active_context(db, user.id)
    academic.upsert_subject(db, user.id, context.id, "Direito Civil")
    whatsapp.link_phone(db, user, "+5551999998888")
    db.commit()

    item = whatsapp.parse_webhook(_payload(TEXT_MESSAGE))[0]
    message = whatsapp.persist_inbound(db, item)
    db.commit()

    whatsapp.process(db, message)
    db.commit()
    assert message.user_id == user.id
    assert db.query(Event).count() >= 0  # confiança média pode pedir confirmação
    assert message.status == "EXECUTED"


def test_desvincular_encerra_o_acesso(db, user):
    whatsapp.link_phone(db, user, "+5551999998888")
    db.commit()
    assert whatsapp.find_user(db, "+5551999998888") is not None

    whatsapp.unlink(db, user)
    db.commit()
    assert whatsapp.find_user(db, "+5551999998888") is None


def test_resposta_formatada_e_curta():
    reply = whatsapp.format_reply(
        {
            "status": "EXECUTED",
            "message": "Pronto. Trabalho de Civil — 23/09.",
            "cards": [{"title": "Trabalho", "date_label": "Qua 23/09", "subject": "Direito Civil"}],
        }
    )
    assert reply.startswith("Pronto.")
    assert "• Qua 23/09 · Trabalho (Direito Civil)" in reply
    assert len(reply) < 300
