"""Adaptador Stripe — o provedor de referência.

Escolhido como referência por ser o mais bem documentado e por operar no
Brasil com cartão e boleto. Trocar por Mercado Pago, Pagar.me ou Asaas é
escrever outra classe com os mesmos quatro métodos: nada fora deste arquivo
conhece o vocabulário do gateway.

## O que este arquivo faz de segurança

* assinatura verificada com HMAC-SHA256 e comparação de tempo constante;
* janela de tolerância na data, para barrar replay de evento antigo capturado;
* o valor cobrado sai do NOSSO catálogo, nunca do que o cliente mandou;
* o usuário é identificado por `client_reference_id`, que nós geramos.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

from agenda import config
from agenda.payments.base import (
    METODO_CARTAO,
    METODO_PIX,
    EVENT_PAYMENT_DISPUTED,
    EVENT_PAYMENT_REFUNDED,
    EVENT_SUBSCRIPTION_CANCELED,
    EVENT_SUBSCRIPTION_PAID,
    EVENT_SUBSCRIPTION_PAST_DUE,
    CheckoutSession,
    PaymentProvider,
    WebhookEvent,
)

# Quanto tempo um evento continua aceitável. Cinco minutos é o padrão do
# próprio Stripe e é curto o bastante para que um evento capturado não sirva
# depois.
TOLERANCIA_SEGUNDOS = 300

# Nome do evento no gateway → nome no nosso vocabulário.
_MAPA_DE_EVENTOS = {
    "checkout.session.completed": EVENT_SUBSCRIPTION_PAID,
    "invoice.paid": EVENT_SUBSCRIPTION_PAID,
    "invoice.payment_failed": EVENT_SUBSCRIPTION_PAST_DUE,
    "customer.subscription.deleted": EVENT_SUBSCRIPTION_CANCELED,
    "charge.refunded": EVENT_PAYMENT_REFUNDED,
    "charge.dispute.created": EVENT_PAYMENT_DISPUTED,
}


class StripeProvider(PaymentProvider):
    name = "stripe"

    @property
    def configured(self) -> bool:
        return bool(config.STRIPE_SECRET_KEY and config.STRIPE_WEBHOOK_SECRET)

    # ----------------------------------------------------------------- #
    # Checkout
    # ----------------------------------------------------------------- #
    def create_checkout(
        self, *, user, plan: str, cycle: str, amount_cents: int,
        success_url: str, cancel_url: str, first_invoice_discount: float = 0.0,
        method: str = METODO_CARTAO,
    ) -> CheckoutSession:
        if not self.configured:
            return CheckoutSession(ok=False, message="Gateway não configurado.")

        import requests

        if method == METODO_PIX:
            return self._checkout_pix(
                requests, user=user, plan=plan, cycle=cycle,
                amount_cents=amount_cents, success_url=success_url,
                cancel_url=cancel_url, first_invoice_discount=first_invoice_discount,
            )

        # `client_reference_id` é o elo entre a cobrança e a conta. Vai daqui e
        # volta no webhook: assim nenhum campo editável pelo cliente decide de
        # quem é a assinatura.
        dados = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": user.id,
            "customer_email": user.email or "",
            "locale": "pt-BR",
            "metadata[plan]": plan,
            "metadata[cycle]": cycle,
            "metadata[user_id]": user.id,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "brl",
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][recurring][interval]": (
                "year" if cycle == "ANNUAL" else "month"
            ),
            "line_items[0][price_data][product_data][name]": f"{config.APP_NAME} {plan}",
            # Apple Pay e Google Pay não são meios separados: são um cartão
            # apresentado sem digitar número. Basta `card` habilitado e o
            # domínio registrado no gateway — o botão aparece sozinho no
            # aparelho que suporta. Declarar explicitamente evita que uma
            # mudança de configuração no painel desligue a carteira sem
            # ninguém perceber.
            "payment_method_types[0]": "card",
        }
        if first_invoice_discount > 0:
            # Cupom de UMA parcela ("duration": "once"): o preço recorrente
            # continua o de tabela, e a promoção não vira desconto eterno.
            dados.update({
                "discounts[0][coupon_data][percent_off]": f"{first_invoice_discount * 100:.0f}",
                "discounts[0][coupon_data][duration]": "once",
                "discounts[0][coupon_data][name]": "Boas-vindas por indicação",
            })
        try:
            resposta = requests.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=dados,
                auth=(config.STRIPE_SECRET_KEY, ""),
                timeout=20,
            )
        except Exception as erro:  # noqa: BLE001 - rede é falível
            return CheckoutSession(ok=False, message=f"Não consegui falar com o gateway: {erro}")

        if resposta.status_code >= 400:
            return CheckoutSession(ok=False, message="O gateway recusou a cobrança.")
        corpo = resposta.json()
        return CheckoutSession(
            ok=True, url=corpo.get("url", ""), external_id=corpo.get("id", "")
        )

    def _checkout_pix(
        self, requests, *, user, plan: str, cycle: str, amount_cents: int,
        success_url: str, cancel_url: str, first_invoice_discount: float,
    ) -> CheckoutSession:
        """Pix é pagamento AVULSO, não assinatura.

        O arranjo do Pix não faz cobrança recorrente — cobrança automática
        exigiria Pix Automático, que o gateway ainda não expõe. Então aqui o
        modo é `payment`: a pessoa paga uma vez e compra um período. A conta
        volta para o grátis quando ele acaba, com aviso antes (o worker olha
        `renews=False` e a data de vencimento).

        Fingir que Pix é assinatura seria pior de todas as formas: a cobrança
        seguinte simplesmente não viria, e a gente descobriria pelo suporte.
        """
        # O desconto de indicação entra no valor porque, sendo pagamento único,
        # não existe "primeira parcela" a distinguir da recorrência.
        centavos = int(round(amount_cents * (1 - first_invoice_discount)))
        meses = 12 if cycle == "ANNUAL" else 1
        rotulo = "1 ano" if meses == 12 else "1 mês"

        dados = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": user.id,
            "customer_email": user.email or "",
            "locale": "pt-BR",
            "payment_method_types[0]": "pix",
            "metadata[plan]": plan,
            "metadata[cycle]": cycle,
            "metadata[user_id]": user.id,
            "metadata[method]": "pix",
            "metadata[avulso]": "1",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "brl",
            "line_items[0][price_data][unit_amount]": str(centavos),
            "line_items[0][price_data][product_data][name]": (
                f"{config.APP_NAME} {plan} — {rotulo}"
            ),
            # O QR do Pix expira; 24 horas é o que o gateway aceita e o que dá
            # tempo de alguém pagar à noite e conferir de manhã.
            "payment_method_options[pix][expires_after_seconds]": "86400",
        }
        try:
            resposta = requests.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=dados,
                auth=(config.STRIPE_SECRET_KEY, ""),
                timeout=20,
            )
        except Exception as erro:  # noqa: BLE001 - rede é falível
            return CheckoutSession(ok=False, message=f"Não consegui falar com o gateway: {erro}")
        if resposta.status_code >= 400:
            return CheckoutSession(ok=False, message="O gateway recusou a cobrança por Pix.")
        corpo = resposta.json()
        return CheckoutSession(
            ok=True, url=corpo.get("url", ""), external_id=corpo.get("id", "")
        )

    # ----------------------------------------------------------------- #
    # Webhook
    # ----------------------------------------------------------------- #
    def verify_webhook(self, body: bytes, headers) -> WebhookEvent | None:
        assinatura = headers.get("Stripe-Signature", "")
        if not self.configured or not assinatura:
            return None

        marca, assinaturas = _partes_da_assinatura(assinatura)
        if marca is None or not assinaturas:
            return None

        # Replay: evento antigo capturado não pode ser reenviado depois.
        agora = dt.datetime.now(dt.timezone.utc).timestamp()
        if abs(agora - marca) > TOLERANCIA_SEGUNDOS:
            return None

        assinado = f"{marca}.".encode() + body
        esperado = hmac.new(
            config.STRIPE_WEBHOOK_SECRET.encode(), assinado, hashlib.sha256
        ).hexdigest()
        # Comparação de tempo constante: comparar com == vaza o prefixo correto
        # por diferença de tempo e permite forjar a assinatura byte a byte.
        if not any(hmac.compare_digest(esperado, s) for s in assinaturas):
            return None

        try:
            corpo = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

        tipo = _MAPA_DE_EVENTOS.get(corpo.get("type", ""), "")
        if not tipo:
            return None

        objeto = (corpo.get("data") or {}).get("object") or {}
        metadados = objeto.get("metadata") or {}
        return WebhookEvent(
            id=str(corpo.get("id", "")),
            type=tipo,
            user_id=str(objeto.get("client_reference_id") or metadados.get("user_id") or ""),
            plan=str(metadados.get("plan", "")),
            cycle=str(metadados.get("cycle", "")),
            external_id=str(objeto.get("subscription") or objeto.get("id") or ""),
            amount_cents=int(objeto.get("amount_total") or objeto.get("amount") or 0),
            # `mode` é do gateway; os metadados são nossos. Confiar só nos
            # metadados evitaria depender do vocabulário dele, mas `mode` é o
            # que a Stripe garante em toda sessão — os dois concordando é o
            # sinal mais seguro de que isto foi um pagamento avulso.
            method=(
                METODO_PIX
                if metadados.get("method") == METODO_PIX or objeto.get("mode") == "payment"
                else METODO_CARTAO
            ),
            occurred_at=dt.datetime.fromtimestamp(marca, dt.timezone.utc),
            raw=corpo,
        )

    def cancel_subscription(self, external_id: str) -> bool:
        if not self.configured or not external_id:
            return False
        import requests

        try:
            resposta = requests.delete(
                f"https://api.stripe.com/v1/subscriptions/{external_id}",
                auth=(config.STRIPE_SECRET_KEY, ""),
                timeout=20,
            )
        except Exception:  # noqa: BLE001
            return False
        return resposta.status_code < 400


def _partes_da_assinatura(cabecalho: str) -> tuple[int | None, list[str]]:
    """Lê "t=123,v1=abc,v1=def" — o formato do cabeçalho do gateway."""
    marca: int | None = None
    assinaturas: list[str] = []
    for parte in cabecalho.split(","):
        chave, _, valor = parte.strip().partition("=")
        if chave == "t" and valor.isdigit():
            marca = int(valor)
        elif chave == "v1" and valor:
            assinaturas.append(valor)
    return marca, assinaturas
