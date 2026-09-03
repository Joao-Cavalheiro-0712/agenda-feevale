"""Pix, Apple Pay e Google Pay.

Duas verdades que este arquivo protege:

1. **Apple Pay e Google Pay não são meios de pagamento separados.** São um
   cartão apresentado sem digitar número. Tratá-los como um terceiro método
   levaria a um checkout duplicado que o gateway rejeita.
2. **Pix não faz cobrança recorrente.** É pagamento avulso, e por isso compra
   um período em vez de abrir assinatura. O teste mais importante aqui é o que
   garante que esse período REALMENTE acaba: sem isso, quem paga um mês por Pix
   fica com plano pago para sempre.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

import pytest

from agenda import config
from agenda.core import billing
from agenda.models import BillingCycle, PlanTier, SubscriptionStatus
from agenda.payments import base, service

SEGREDO = "whsec_teste_do_grifo"


@pytest.fixture
def com_stripe(monkeypatch):
    monkeypatch.setattr(config, "PAYMENT_PROVIDER", "stripe")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setitem(config.FEATURE_FLAGS, "billing_enabled", True)
    service.reset_provider()
    yield
    service.reset_provider()


class _Resposta:
    status_code = 200

    def __init__(self, corpo):
        self._corpo = corpo

    def json(self):
        return self._corpo


@pytest.fixture
def capturar_pedido(monkeypatch):
    """Intercepta o POST ao gateway e devolve o que teríamos enviado."""
    enviados = []

    def falso_post(url, data=None, **_kwargs):
        enviados.append({"url": url, "data": dict(data or {})})
        return _Resposta({"url": "https://checkout.test/sessao", "id": "cs_test_1"})

    import requests

    monkeypatch.setattr(requests, "post", falso_post)
    return enviados


def _assinar(corpo: bytes) -> str:
    marca = int(dt.datetime.now(dt.timezone.utc).timestamp())
    assinatura = hmac.new(
        SEGREDO.encode(), f"{marca}.".encode() + corpo, hashlib.sha256
    ).hexdigest()
    return f"t={marca},v1={assinatura}"


def _evento_pix(user_id: str, *, ciclo: str = "MONTHLY", event_id: str = "evt_pix_1") -> bytes:
    return json.dumps({
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": user_id,
            "id": "cs_pix_1",
            "mode": "payment",
            "amount_total": 1990,
            "metadata": {"plan": "STUDENT", "cycle": ciclo,
                         "user_id": user_id, "method": "pix"},
        }},
    }).encode()


# --------------------------------------------------------------------------- #
# Carteiras
# --------------------------------------------------------------------------- #
def test_carteiras_andam_no_cartao(db, user, com_stripe, capturar_pedido):
    """Apple Pay e Google Pay aparecem sozinhos: são cartão, não um terceiro meio."""
    sessao = service.start_checkout(
        db, user, plan=PlanTier.STUDENT.value, cycle=BillingCycle.MONTHLY.value,
        base_url="https://grifo.test",
    )
    assert sessao.ok
    dados = capturar_pedido[0]["data"]
    assert dados["mode"] == "subscription"
    assert dados["payment_method_types[0]"] == "card"
    assert "payment_method_types[1]" not in dados


def test_o_valor_sai_do_nosso_catalogo(db, user, com_stripe, capturar_pedido):
    """O cliente escolhe o plano; quanto custa é decisão do servidor."""
    service.start_checkout(
        db, user, plan=PlanTier.PRO.value, cycle=BillingCycle.MONTHLY.value,
        base_url="https://grifo.test",
    )
    esperado = int(round(billing.PLANS[PlanTier.PRO.value].price_month * 100))
    assert capturar_pedido[0]["data"]["line_items[0][price_data][unit_amount]"] == str(esperado)


# --------------------------------------------------------------------------- #
# Pix é pagamento avulso
# --------------------------------------------------------------------------- #
def test_pix_abre_pagamento_e_nao_assinatura(db, user, com_stripe, capturar_pedido):
    """Fingir que Pix é assinatura faria a segunda cobrança simplesmente não vir."""
    sessao = service.start_checkout(
        db, user, plan=PlanTier.STUDENT.value, cycle=BillingCycle.MONTHLY.value,
        method="pix", base_url="https://grifo.test",
    )
    assert sessao.ok
    dados = capturar_pedido[0]["data"]
    assert dados["mode"] == "payment"
    assert dados["payment_method_types[0]"] == "pix"
    assert "line_items[0][price_data][recurring][interval]" not in dados
    assert dados["metadata[method]"] == "pix"


def test_pix_anual_cobra_o_ano_de_uma_vez(db, user, com_stripe, capturar_pedido):
    service.start_checkout(
        db, user, plan=PlanTier.STUDENT.value, cycle=BillingCycle.ANNUAL.value,
        method="pix", base_url="https://grifo.test",
    )
    esperado = int(round(billing.PLANS[PlanTier.STUDENT.value].price_annual * 100))
    dados = capturar_pedido[0]["data"]
    assert dados["line_items[0][price_data][unit_amount]"] == str(esperado)
    assert "1 ano" in dados["line_items[0][price_data][product_data][name]"]


def test_metodo_inventado_cai_no_cartao(db, user, com_stripe, capturar_pedido):
    service.start_checkout(
        db, user, plan=PlanTier.STUDENT.value, cycle=BillingCycle.MONTHLY.value,
        method="bitcoin", base_url="https://grifo.test",
    )
    assert capturar_pedido[0]["data"]["mode"] == "subscription"


# --------------------------------------------------------------------------- #
# O que o webhook faz com um pagamento avulso
# --------------------------------------------------------------------------- #
def test_pagamento_por_pix_libera_o_plano(app, db, user, com_stripe):
    corpo = _evento_pix(user.id)
    resposta = app.test_client().post(
        "/webhooks/pagamento", data=corpo,
        headers={"Stripe-Signature": _assinar(corpo), "Content-Type": "application/json"},
    )
    assert resposta.status_code == 200
    db.expire_all()
    sub = billing.subscription_of(db, user)
    assert sub.plan == PlanTier.STUDENT.value
    assert sub.status == SubscriptionStatus.ACTIVE.value
    assert sub.renews is False, "Pix não renova sozinho"
    assert sub.payment_method == "pix"


def test_periodo_pago_por_pix_acaba_de_verdade(db, user):
    """O teste que impede o vazamento: sem ele, um mês de Pix vira vitalício."""
    billing.change_plan(db, user, PlanTier.STUDENT.value, renews=False,
                        payment_method="pix")
    db.commit()
    assert billing.active_plan(db, user).tier == PlanTier.STUDENT.value

    sub = billing.subscription_of(db, user)
    sub.current_period_end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    db.commit()
    assert billing.active_plan(db, user).tier == PlanTier.FREE.value


def test_assinatura_de_cartao_nao_expira_sozinha(db, user):
    """Cartão renova: a data vencida significa "o gateway ainda vai cobrar",
    não "acabou". Quem decide é o webhook."""
    billing.change_plan(db, user, PlanTier.STUDENT.value)
    sub = billing.subscription_of(db, user)
    sub.current_period_end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    db.commit()
    assert billing.active_plan(db, user).tier == PlanTier.STUDENT.value


# --------------------------------------------------------------------------- #
# Aviso antes de acabar
# --------------------------------------------------------------------------- #
def test_avisa_antes_do_periodo_acabar(db, user):
    """Sem cobrança recorrente, o silêncio é uma armadilha."""
    from agenda.models import Notification

    billing.change_plan(db, user, PlanTier.STUDENT.value, renews=False,
                        payment_method="pix")
    sub = billing.subscription_of(db, user)
    sub.current_period_end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    db.commit()

    assert billing.avisar_vencimentos(db) == 1
    db.commit()
    aviso = db.query(Notification).filter_by(user_id=user.id, kind="billing").one()
    assert "Pix" in aviso.body


def test_o_aviso_nao_se_repete(db, user):
    billing.change_plan(db, user, PlanTier.STUDENT.value, renews=False,
                        payment_method="pix")
    sub = billing.subscription_of(db, user)
    sub.current_period_end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    db.commit()

    assert billing.avisar_vencimentos(db) == 1
    db.commit()
    assert billing.avisar_vencimentos(db) == 0, "aviso repetido vira ruído que ninguém lê"


def test_cartao_nao_recebe_aviso_de_vencimento(db, user):
    billing.change_plan(db, user, PlanTier.STUDENT.value)
    sub = billing.subscription_of(db, user)
    sub.current_period_end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    db.commit()
    assert billing.avisar_vencimentos(db) == 0


def test_periodo_longe_ainda_nao_avisa(db, user):
    billing.change_plan(db, user, PlanTier.STUDENT.value, renews=False,
                        payment_method="pix")
    sub = billing.subscription_of(db, user)
    sub.current_period_end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=20)
    db.commit()
    assert billing.avisar_vencimentos(db) == 0


# --------------------------------------------------------------------------- #
# Vocabulário
# --------------------------------------------------------------------------- #
def test_so_o_cartao_e_recorrente():
    assert base.recorrente(base.METODO_CARTAO) is True
    assert base.recorrente(base.METODO_PIX) is False
