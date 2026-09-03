"""Fluxos ponta a ponta pela web (SPEC §101, §149)."""
from __future__ import annotations

import datetime as dt
import io

from agenda.models import Document, EducationContext, Event, User


def _csrf(client) -> str:
    with client.session_transaction() as session:
        return session.get("csrf", "")


def test_paginas_publicas_respondem(app):
    client = app.test_client()
    for path in ("/", "/entrar", "/criar-conta", "/healthz", "/offline", "/manifest.webmanifest", "/sw.js"):
        assert client.get(path).status_code == 200


def test_cabecalhos_de_seguranca(app):
    response = app.test_client().get("/entrar")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"

    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    # Sem 'unsafe-inline' em script-src: XSS refletido não executa.
    assert "'unsafe-inline'" not in csp.split("style-src")[0]
    assert "'nonce-" in csp


def test_nonce_muda_a_cada_requisicao(app):
    client = app.test_client()
    primeiro = client.get("/entrar").headers["Content-Security-Policy"]
    segundo = client.get("/entrar").headers["Content-Security-Policy"]
    assert primeiro != segundo


def test_area_logada_exige_sessao(app):
    client = app.test_client()
    response = client.get("/hoje")
    assert response.status_code == 302
    assert "/entrar" in response.headers["Location"]


def test_api_sem_sessao_devolve_401(app):
    response = app.test_client().post("/api/capture", json={"text": "oi"})
    assert response.status_code in (401, 403)


def test_cadastro_leva_ao_onboarding(app, db):
    client = app.test_client()
    client.get("/criar-conta")
    response = client.post(
        "/criar-conta",
        data={
            "csrf_token": _csrf(client), "name": "Ana", "email": "ana@example.com",
            "password": "segredo123", "phone": "(51) 99999-1111",
        },
    )
    assert response.status_code == 302 and "/onboarding" in response.headers["Location"]
    person = db.query(User).filter_by(email="ana@example.com").first()
    assert person is not None and person.phone_e164 == "+5551999991111"
    assert person.password_hash and "segredo123" not in person.password_hash


def test_senha_curta_e_recusada(app, db):
    client = app.test_client()
    client.get("/criar-conta")
    client.post(
        "/criar-conta",
        data={"csrf_token": _csrf(client), "email": "curta@example.com", "password": "123"},
    )
    assert db.query(User).filter_by(email="curta@example.com").first() is None


def test_post_sem_csrf_e_bloqueado(app):
    client = app.test_client()
    client.get("/entrar")
    response = client.post("/entrar", data={"email": "x@y.z", "password": "segredo123"})
    assert response.status_code == 403


def test_onboarding_cria_contexto(client, db, user):
    response = client.post(
        "/onboarding",
        data={
            "csrf_token": _csrf(client), "type": "HIGH_SCHOOL", "institution": "Escola X",
            "grade_name": "2º ano", "class_name": "B", "shift": "manha",
        },
    )
    assert response.status_code == 302
    contexts = db.query(EducationContext).filter_by(user_id=user.id).all()
    assert any(context.type == "HIGH_SCHOOL" for context in contexts)


def test_telas_do_planner_abrem(client):
    for path in ("/hoje", "/semana", "/mes", "/agenda", "/entregas", "/linha-do-tempo",
                 "/materias", "/documentos", "/assistente", "/notificacoes", "/perfil", "/buscar?q=prova"):
        assert client.get(path).status_code == 200, path


def test_captura_por_texto_cria_evento(client, db, user):
    client.post(
        "/materias",
        data={"csrf_token": _csrf(client), "name": "Direito Civil", "teacher_name": "Ana Souza"},
    )
    response = client.post(
        "/api/capture",
        json={"text": "Prova de Civil dia 23 sobre contratos"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] in ("EXECUTED", "NEEDS_CONFIRMATION")

    if body["status"] == "NEEDS_CONFIRMATION":
        confirmed = client.post(
            f"/api/actions/{body['action_id']}/confirm", headers={"X-CSRF-Token": _csrf(client)}
        ).get_json()
        assert confirmed["status"] == "EXECUTED"
    assert db.query(Event).filter_by(user_id=user.id).count() == 1


def test_criar_completar_e_desfazer_evento(client, db, user):
    created = client.post(
        "/api/events",
        json={"title": "Trabalho de História", "type": "ASSIGNMENT",
              "date": (dt.date.today() + dt.timedelta(days=10)).isoformat()},
        headers={"X-CSRF-Token": _csrf(client)},
    ).get_json()
    assert created["status"] == "EXECUTED"
    event_id = created["cards"][0]["id"]

    completed = client.post(
        f"/api/events/{event_id}/complete", json={"done": True},
        headers={"X-CSRF-Token": _csrf(client)},
    ).get_json()
    assert completed["status"] == "EXECUTED"
    db.expire_all()
    assert db.get(Event, event_id).status == "COMPLETED"

    undone = client.post(
        f"/api/actions/{completed['action_id']}/undo", headers={"X-CSRF-Token": _csrf(client)}
    ).get_json()
    assert undone["status"] == "EXECUTED"
    db.expire_all()
    assert db.get(Event, event_id).status == "UPCOMING"


def test_upload_e_importacao_de_cronograma(client, db, user):
    content = (
        "Cronograma Direito Penal 2026/2\n"
        "Prova G1 - 18/09/2026 - capitulos 1 a 4\n"
        "Entrega do trabalho - 02/10/2026\n"
    ).encode("utf-8")
    response = client.post(
        "/documentos",
        data={"csrf_token": _csrf(client), "files": (io.BytesIO(content), "cronograma.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    document = db.query(Document).filter_by(user_id=user.id).first()
    assert document is not None

    items = [item.id for item in document.extractions if item.kind == "event"]
    assert items
    client.post(
        f"/documentos/{document.id}/importar",
        data={"csrf_token": _csrf(client), "selected": items},
    )
    db.expire_all()
    assert db.query(Event).filter_by(user_id=user.id).count() == len(items)


def test_upload_de_executavel_e_recusado(client, db, user):
    client.post(
        "/documentos",
        data={"csrf_token": _csrf(client), "files": (io.BytesIO(b"MZ\x90\x00binario"), "app.exe")},
        content_type="multipart/form-data",
    )
    assert db.query(Document).count() == 0


def test_planner_json(client):
    today = client.get("/api/planner/today").get_json()
    assert "items" in today and "week" in today
    week = client.get("/api/planner/week").get_json()
    assert len(week["days"]) == 7


def test_exportacao_de_dados(client):
    data = client.get("/api/export").get_json()
    assert set(["user", "contexts", "subjects", "events"]).issubset(data.keys())


def test_um_usuario_nao_ve_evento_de_outro(app, client, db, user):
    from agenda.core import events as events_core
    from agenda.security import hash_password

    other = User(name="Maria", email="maria@example.com", password_hash=hash_password("segredo123"))
    db.add(other)
    db.flush()
    event = events_core.create_event(
        db, other, title="Prova alheia", event_type="EXAM", date=dt.date(2026, 10, 20)
    )
    db.commit()

    assert client.get(f"/api/events/{event.id}").status_code == 404
    assert client.get(f"/evento/{event.id}").status_code == 404


def test_admin_e_invisivel_para_aluno(client):
    assert client.get("/admin").status_code == 404


def test_compartilhamento_de_materia(client, db, user):
    client.post("/materias", data={"csrf_token": _csrf(client), "name": "Direito Penal"})
    from agenda.models import SharedCollection, Subject

    subject = db.query(Subject).filter_by(user_id=user.id).first()
    response = client.post(
        f"/materias/{subject.id}/compartilhar", data={"csrf_token": _csrf(client)}
    )
    assert response.status_code == 302
    collection = db.query(SharedCollection).first()
    assert collection is not None
    assert client.get(f"/join/{collection.code}").status_code == 200


def test_webhook_whatsapp_verificacao(app):
    from agenda import config

    client = app.test_client()
    response = client.get(
        "/webhooks/whatsapp",
        query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": config.WHATSAPP_VERIFY_TOKEN,
            "hub.challenge": "desafio",
        },
    )
    assert response.status_code == 200 and response.data == b"desafio"
    assert client.get(
        "/webhooks/whatsapp",
        query_string={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "x"},
    ).status_code == 403


def test_webhook_whatsapp_responde_rapido_e_persiste(app, db, user, monkeypatch):
    from agenda.models import ChannelMessage

    # O processamento assíncrono não deve rodar dentro do request.
    monkeypatch.setattr("agenda.web.webhooks._spawn", lambda app, ids: None)

    payload = {
        "entry": [{"changes": [{"value": {"messages": [
            {"id": "wamid.HOOK1", "from": "5551999998888", "type": "text",
             "text": {"body": "prova de civil sexta"}}
        ]}}]}]
    }
    client = app.test_client()
    assert client.post("/webhooks/whatsapp", json=payload).status_code == 200
    assert db.query(ChannelMessage).filter_by(provider_message_id="wamid.HOOK1").count() == 1

    # Reenvio do mesmo evento não duplica (idempotência).
    assert client.post("/webhooks/whatsapp", json=payload).status_code == 200
    assert db.query(ChannelMessage).filter_by(provider_message_id="wamid.HOOK1").count() == 1


def test_lembrete_vencido_gera_notificacao(db, user):
    import datetime as dt

    from agenda.core import events as events_core, notifications
    from agenda.models import Notification

    event = events_core.create_event(
        db, user, title="Prova 1", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=30),
    )
    db.commit()

    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=29)
    sent = notifications.run_due_reminders(db, now=future)
    db.commit()

    assert sent >= 1
    notification = db.query(Notification).filter_by(event_id=event.id).first()
    assert notification is not None
    assert "Prova 1" in notification.title
    assert notifications.unread_count(db, user.id) >= 1


def test_evento_concluido_nao_dispara_lembrete(db, user):
    import datetime as dt

    from agenda.core import events as events_core, notifications
    from agenda.models import Notification

    event = events_core.create_event(
        db, user, title="Trabalho", event_type="ASSIGNMENT",
        date=dt.date.today() + dt.timedelta(days=30),
    )
    events_core.complete_event(db, event)
    db.commit()

    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=29)
    notifications.run_due_reminders(db, now=future)
    db.commit()
    assert db.query(Notification).count() == 0


def test_lembrete_nao_sai_duas_vezes_com_workers_concorrentes(db, user):
    """Dois processos varrendo a fila ao mesmo tempo entregam uma vez só."""
    import datetime as dt

    from agenda.core import events as events_core, notifications
    from agenda.db import SessionLocal
    from agenda.models import Notification

    events_core.create_event(
        db, user, title="Prova concorrente", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=30),
    )
    db.commit()

    futuro = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=29)
    outra_sessao = SessionLocal()
    try:
        primeiro = notifications.run_due_reminders(db, now=futuro)
        segundo = notifications.run_due_reminders(outra_sessao, now=futuro)
    finally:
        outra_sessao.close()

    # A primeira varredura entrega os lembretes vencidos; a segunda não repete
    # nenhum, e o total de avisos é exatamente o que a primeira reportou.
    assert primeiro >= 1 and segundo == 0
    total = db.query(Notification).filter(Notification.title.contains("concorrente")).count()
    assert total == primeiro
