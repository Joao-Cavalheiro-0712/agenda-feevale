"""Cobrança: checkout, webhook e as fraudes que eles precisam resistir.

O endpoint de webhook é o ponto onde dinheiro vira permissão. Se ele cair,
qualquer pessoa com um `curl` vira assinante Família de graça — por isso esta
suíte ataca o webhook antes de testar o caminho feliz.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

import pytest

from agenda import config
from agenda.core import billing, referrals
from agenda.models import PlanTier, SubscriptionStatus, User, WebhookEventLog
from agenda.payments import service
from agenda.security import hash_password

SEGREDO = "whsec_teste_do_grifo"


@pytest.fixture
def com_stripe(monkeypatch):
    """Liga o provedor Stripe com segredo de teste."""
    monkeypatch.setattr(config, "PAYMENT_PROVIDER", "stripe")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setitem(config.FEATURE_FLAGS, "billing_enabled", True)
    service.reset_provider()
    yield
    service.reset_provider()


def _evento(user_id: str, *, tipo: str = "checkout.session.completed",
            plano: str = "PRO", ciclo: str = "MONTHLY", event_id: str = "evt_1") -> bytes:
    return json.dumps({
        "id": event_id,
        "type": tipo,
        "data": {"object": {
            "client_reference_id": user_id,
            "subscription": "sub_123",
            "amount_total": 2990,
            "metadata": {"plan": plano, "cycle": ciclo, "user_id": user_id},
        }},
    }).encode()


def _assinar(corpo: bytes, *, segredo: str = SEGREDO, marca: int | None = None) -> str:
    marca = marca or int(dt.datetime.now(dt.timezone.utc).timestamp())
    assinatura = hmac.new(
        segredo.encode(), f"{marca}.".encode() + corpo, hashlib.sha256
    ).hexdigest()
    return f"t={marca},v1={assinatura}"


# --------------------------------------------------------------------------- #
# Sem gateway configurado: falha fechado
# --------------------------------------------------------------------------- #
def test_sem_chave_o_checkout_nao_finge_que_funcionou():
    service.reset_provider()
    assert service.provider().name == "none"
    assert service.enabled() is False
    sessao = service.provider().create_checkout()
    assert sessao.ok is False and sessao.message


def test_sem_chave_o_webhook_recusa_tudo(app):
    service.reset_provider()
    resposta = app.test_client().post("/webhooks/pagamento", data=b'{"type":"invoice.paid"}')
    assert resposta.status_code == 403


# --------------------------------------------------------------------------- #
# O webhook sob ataque
# --------------------------------------------------------------------------- #
def test_webhook_sem_assinatura_e_recusado(app, com_stripe, user):
    corpo = _evento(user.id)
    assert app.test_client().post("/webhooks/pagamento", data=corpo).status_code == 403


def test_webhook_com_assinatura_de_outro_segredo_e_recusado(app, com_stripe, user):
    corpo = _evento(user.id)
    resposta = app.test_client().post(
        "/webhooks/pagamento", data=corpo,
        headers={"Stripe-Signature": _assinar(corpo, segredo="whsec_do_atacante")},
    )
    assert resposta.status_code == 403


def test_webhook_com_corpo_alterado_apos_assinar_e_recusado(app, com_stripe, user):
    """Assinatura tem de cobrir o corpo inteiro, não só o cabeçalho."""
    corpo = _evento(user.id, plano="STUDENT")
    cabecalho = _assinar(corpo)
    adulterado = _evento(user.id, plano="FAMILY")  # trocou o plano depois
    resposta = app.test_client().post(
        "/webhooks/pagamento", data=adulterado,
        headers={"Stripe-Signature": cabecalho},
    )
    assert resposta.status_code == 403


def test_webhook_antigo_nao_pode_ser_reenviado(app, com_stripe, user):
    """Replay: evento capturado hoje não pode valer amanhã."""
    corpo = _evento(user.id)
    velho = int(dt.datetime.now(dt.timezone.utc).timestamp()) - 3600
    resposta = app.test_client().post(
        "/webhooks/pagamento", data=corpo,
        headers={"Stripe-Signature": _assinar(corpo, marca=velho)},
    )
    assert resposta.status_code == 403


def test_webhook_nao_conta_por_que_recusou(app, com_stripe, user):
    """Quem forja webhook não pode aprender onde errou."""
    corpo = _evento(user.id)
    sem = app.test_client().post("/webhooks/pagamento", data=corpo)
    errado = app.test_client().post(
        "/webhooks/pagamento", data=corpo,
        headers={"Stripe-Signature": _assinar(corpo, segredo="outro")},
    )
    assert sem.status_code == errado.status_code == 403
    assert sem.get_data() == errado.get_data() == b""


# --------------------------------------------------------------------------- #
# O caminho feliz, e a idempotência
# --------------------------------------------------------------------------- #
def test_webhook_valido_ativa_a_assinatura(app, com_stripe, db, user):
    corpo = _evento(user.id, plano="PRO", ciclo="ANNUAL")
    resposta = app.test_client().post(
        "/webhooks/pagamento", data=corpo,
        headers={"Stripe-Signature": _assinar(corpo)},
    )
    assert resposta.status_code == 200

    db.expire_all()
    sub = billing.subscription_of(db, db.get(User, user.id))
    assert sub.plan == PlanTier.PRO.value
    assert sub.cycle == "ANNUAL"
    assert sub.status == SubscriptionStatus.ACTIVE.value


def test_o_mesmo_evento_duas_vezes_nao_concede_dois_periodos(app, com_stripe, db, user):
    corpo = _evento(user.id, event_id="evt_repetido")
    cliente = app.test_client()
    cabecalho = {"Stripe-Signature": _assinar(corpo)}
    assert cliente.post("/webhooks/pagamento", data=corpo, headers=cabecalho).status_code == 200

    db.expire_all()
    primeiro_fim = billing.subscription_of(db, db.get(User, user.id)).current_period_end

    corpo2 = _evento(user.id, event_id="evt_repetido")
    cliente.post("/webhooks/pagamento", data=corpo2,
                 headers={"Stripe-Signature": _assinar(corpo2)})
    db.expire_all()
    segundo_fim = billing.subscription_of(db, db.get(User, user.id)).current_period_end

    assert primeiro_fim == segundo_fim, "evento repetido estendeu a assinatura"
    assert db.query(WebhookEventLog).filter_by(event_id="evt_repetido").count() == 1


def test_evento_para_usuario_inexistente_nao_quebra(app, com_stripe):
    corpo = _evento("nao-existe")
    resposta = app.test_client().post(
        "/webhooks/pagamento", data=corpo,
        headers={"Stripe-Signature": _assinar(corpo)},
    )
    assert resposta.status_code == 200


# --------------------------------------------------------------------------- #
# Reembolso e contestação
# --------------------------------------------------------------------------- #
def test_reembolso_derruba_o_acesso_e_a_recompensa(app, com_stripe, db, user):
    indicador = User(
        name="Indicador", email="ind@example.com",
        password_hash=hash_password("senhaforte123"), onboarding_done=True,
        birth_year=1990, accepted_terms_version="2026-09-03",
        accepted_privacy_version="2026-09-03",
    )
    db.add(indicador)
    db.flush()
    codigo = referrals.code_for(db, indicador)
    referrals.attribute(db, user, codigo)
    db.commit()

    pago = _evento(user.id, event_id="evt_pago")
    cliente = app.test_client()
    cliente.post("/webhooks/pagamento", data=pago,
                 headers={"Stripe-Signature": _assinar(pago)})

    devolvido = _evento(user.id, tipo="charge.refunded", event_id="evt_estorno")
    cliente.post("/webhooks/pagamento", data=devolvido,
                 headers={"Stripe-Signature": _assinar(devolvido)})

    db.expire_all()
    sub = billing.subscription_of(db, db.get(User, user.id))
    assert sub.status == SubscriptionStatus.CANCELED.value
    assert billing.active_plan(db, db.get(User, user.id)).tier == PlanTier.FREE.value
    registro = referrals.referral_of(db, db.get(User, user.id))
    assert registro.status == "REJECTED"


# --------------------------------------------------------------------------- #
# O preço nunca vem do cliente
# --------------------------------------------------------------------------- #
def test_o_valor_do_checkout_sai_do_catalogo(db, user, monkeypatch, com_stripe):
    capturado = {}

    def _falso_checkout(**kwargs):
        capturado.update(kwargs)
        from agenda.payments.base import CheckoutSession
        return CheckoutSession(ok=True, url="https://pagamento/exemplo", external_id="cs_1")

    monkeypatch.setattr(service.provider(), "create_checkout", _falso_checkout)
    service.start_checkout(db, user, plan="PRO", cycle="MONTHLY", base_url="https://x")
    assert capturado["amount_cents"] == int(round(billing.PLANS["PRO"].price_month * 100))


def test_checkout_recusa_plano_invalido(db, user, com_stripe):
    with pytest.raises(ValueError):
        service.start_checkout(db, user, plan="INVENTADO", cycle="MONTHLY", base_url="https://x")
    with pytest.raises(ValueError):
        service.start_checkout(db, user, plan="FREE", cycle="MONTHLY", base_url="https://x")


def test_desconto_de_indicacao_vale_uma_parcela_e_nao_vira_preco(db, user, monkeypatch, com_stripe):
    """Regressão: o desconto embutido no valor virava preço recorrente.

    Assinatura com `unit_amount` reduzido cobra o valor reduzido para SEMPRE.
    Qualquer pessoa conseguia 30% permanente criando uma segunda conta pelo
    próprio link de indicação. O desconto agora viaja separado do preço.
    """
    indicador = User(
        name="Indicador", email="ind2@example.com",
        password_hash=hash_password("senhaforte123"), onboarding_done=True,
        birth_year=1990, accepted_terms_version="2026-09-03",
        accepted_privacy_version="2026-09-03",
    )
    db.add(indicador)
    db.flush()
    referrals.attribute(db, user, referrals.code_for(db, indicador))
    db.commit()

    capturado = {}

    def _falso_checkout(**kwargs):
        capturado.update(kwargs)
        from agenda.payments.base import CheckoutSession
        return CheckoutSession(ok=True, url="https://x", external_id="cs_2")

    monkeypatch.setattr(service.provider(), "create_checkout", _falso_checkout)
    service.start_checkout(db, user, plan="STUDENT", cycle="MONTHLY", base_url="https://x")

    cheio = int(round(billing.PLANS["STUDENT"].price_month * 100))
    assert capturado["amount_cents"] == cheio, "o preço recorrente foi alterado"
    assert 0 < capturado["first_invoice_discount"] < 1, "o desconto não foi repassado"


def test_sem_indicacao_nao_ha_desconto(db, user, monkeypatch, com_stripe):
    capturado = {}

    def _falso_checkout(**kwargs):
        capturado.update(kwargs)
        from agenda.payments.base import CheckoutSession
        return CheckoutSession(ok=True, url="https://x", external_id="cs_9")

    monkeypatch.setattr(service.provider(), "create_checkout", _falso_checkout)
    service.start_checkout(db, user, plan="PRO", cycle="ANNUAL", base_url="https://x")
    assert capturado["first_invoice_discount"] == 0.0


# --------------------------------------------------------------------------- #
# A tela não muda plano quando há gateway
# --------------------------------------------------------------------------- #
def test_com_gateway_a_tela_nao_libera_plano_sozinha(app, com_stripe, db, user, monkeypatch):
    """Clicar "assinar" não pode virar assinante: só o dinheiro faz isso."""
    from agenda.payments.base import CheckoutSession

    monkeypatch.setattr(
        service.provider(), "create_checkout",
        lambda **_k: CheckoutSession(ok=True, url="https://pagamento/exemplo", external_id="cs_3"),
    )
    cliente = app.test_client()
    cliente.get("/entrar")
    with cliente.session_transaction() as sessao:
        token = sessao.get("csrf")
    cliente.post("/entrar", data={"csrf_token": token, "email": user.email,
                                  "password": "segredo123"})
    with cliente.session_transaction() as sessao:
        token = sessao.get("csrf")

    resposta = cliente.post("/planos/assinar", data={
        "csrf_token": token, "plan": "FAMILY", "cycle": "MONTHLY",
    })
    assert resposta.status_code == 302
    assert "pagamento" in resposta.headers["Location"]

    db.expire_all()
    assert billing.active_plan(db, db.get(User, user.id)).tier == PlanTier.FREE.value


def test_janela_de_reembolso_segue_o_cdc(db, user):
    billing.change_plan(db, user, PlanTier.STUDENT.value)
    db.commit()
    assert service.within_refund_window(db, user) is True
    assert config.REFUND_WINDOW_DAYS == 7


def test_sem_gateway_a_rota_nao_concede_plano_pago(app, db, user, monkeypatch):
    """Regressão de falha aberta.

    Havia duas condições para a mesma coisa — a flag `billing_enabled` na rota
    e `provider().configured and flag` no serviço. Quando elas discordavam
    (flag ligada, chave ausente ou rotacionada), a execução caía no
    `change_plan` do fim da função e concedia o plano de graça.
    """
    monkeypatch.setitem(config.FEATURE_FLAGS, "billing_enabled", True)
    monkeypatch.setattr(config, "PAYMENT_PROVIDER", "none")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    service.reset_provider()

    cliente = app.test_client()
    cliente.get("/entrar")
    with cliente.session_transaction() as sessao:
        token = sessao.get("csrf")
    cliente.post("/entrar", data={"csrf_token": token, "email": user.email,
                                  "password": "segredo123"})
    with cliente.session_transaction() as sessao:
        token = sessao.get("csrf")

    resposta = cliente.post("/planos/assinar", data={
        "csrf_token": token, "plan": "FAMILY", "cycle": "ANNUAL",
    })
    assert resposta.status_code == 302

    db.expire_all()
    assert billing.active_plan(db, db.get(User, user.id)).tier == PlanTier.FREE.value, (
        "plano pago concedido sem gateway configurado"
    )
    service.reset_provider()
