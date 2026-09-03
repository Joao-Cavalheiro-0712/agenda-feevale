"""Planos, entitlements e quotas (SPEC §96)."""
from __future__ import annotations

import io


from agenda.core import billing
from agenda.models import PlanTier, SubscriptionStatus


def _csrf(client) -> str:
    with client.session_transaction() as session:
        return session.get("csrf", "")


def test_conta_nova_comeca_no_gratis(db, user):
    assert billing.active_plan(db, user).tier == PlanTier.FREE.value
    assert not billing.allows(db, user, billing.CAN_USE_WHATSAPP)
    assert not billing.allows(db, user, billing.CAN_USE_STUDY_PLANNER)


def test_plano_pago_libera_recursos(db, user):
    billing.change_plan(db, user, PlanTier.STUDENT.value)
    db.commit()
    assert billing.allows(db, user, billing.CAN_USE_WHATSAPP)
    assert billing.allows(db, user, billing.CAN_SYNC_CALENDAR)
    assert billing.limit_of(db, user, billing.MAX_DOCUMENT_IMPORTS) == 100


def test_quota_bloqueia_quando_estoura(db, user):
    limite = billing.limit_of(db, user, billing.MAX_DOCUMENT_IMPORTS)
    for _ in range(limite):
        pode, _aviso = billing.check_quota(
            db, user, billing.MAX_DOCUMENT_IMPORTS, "document_imports"
        )
        assert pode
        billing.consume(db, user, "document_imports")
    db.commit()

    pode, aviso = billing.check_quota(db, user, billing.MAX_DOCUMENT_IMPORTS, "document_imports")
    assert not pode and "Assine" in aviso


def test_plano_ilimitado_nao_conta(db, user):
    billing.change_plan(db, user, PlanTier.INSTITUTION.value)
    billing.consume(db, user, "document_imports", amount=5000)
    db.commit()
    pode, _ = billing.check_quota(db, user, billing.MAX_DOCUMENT_IMPORTS, "document_imports")
    assert pode


def test_teste_gratis_expira_e_volta_ao_free(db, user):
    import datetime as dt

    sub = billing.start_trial(db, user)
    db.commit()
    assert sub.status == SubscriptionStatus.TRIALING.value
    assert billing.allows(db, user, billing.CAN_USE_WHATSAPP)

    sub.trial_ends_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    db.commit()
    assert billing.active_plan(db, user).tier == PlanTier.FREE.value


def test_teste_gratis_nao_pode_ser_repetido(db, user):
    primeiro = billing.start_trial(db, user)
    prazo = primeiro.trial_ends_at
    segundo = billing.start_trial(db, user)
    assert segundo.trial_ends_at == prazo


def test_upload_alem_da_quota_devolve_402(client, db, user):
    billing.consume(db, user, "document_imports", amount=99)
    db.commit()
    resposta = client.post(
        "/api/capture",
        data={"file": (io.BytesIO(b"%PDF-1.4 conteudo"), "cronograma.pdf")},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resposta.status_code == 402
    corpo = resposta.get_json()
    assert corpo["status"] == "QUOTA" and corpo["upgrade"] == "/planos"


def test_captura_de_texto_consome_quota(client, db, user):
    antes = billing.usage(db, user, "ai_messages")
    client.post("/api/capture", json={"text": "prova de história sexta"},
                headers={"X-CSRF-Token": _csrf(client)})
    db.expire_all()
    assert billing.usage(db, user, "ai_messages") == antes + 1


def test_planejador_de_estudo_exige_plano(client, db, user):
    resposta = client.post("/api/study/generate", headers={"X-CSRF-Token": _csrf(client)})
    assert resposta.status_code == 402

    billing.change_plan(db, user, PlanTier.STUDENT.value)
    db.commit()
    assert client.post("/api/study/generate", headers={"X-CSRF-Token": _csrf(client)}).status_code == 200


def test_tela_de_planos_abre(client):
    assert client.get("/planos").status_code == 200


def test_plano_invalido_e_recusado(client, db, user):
    client.post("/planos/assinar", data={"csrf_token": _csrf(client), "plan": "PLANO_PIRATA"})
    db.expire_all()
    assert billing.active_plan(db, user).tier == PlanTier.FREE.value
