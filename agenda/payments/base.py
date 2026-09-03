"""Interface do provedor de pagamento e o provedor nulo.

Regras que valem para QUALQUER provedor que entre aqui:

1. **O preço nunca vem do cliente.** O navegador manda o plano e o ciclo; o
   valor sai de `billing.PLANS`, no servidor. Aceitar preço do cliente é a
   forma mais direta de vender assinatura por um centavo.
2. **Plano só muda por confirmação do servidor.** Nem redirect de sucesso, nem
   JavaScript, nem "voltei da tela de pagamento" liberam nada — só o webhook
   assinado ou uma consulta nossa ao gateway.
3. **Desconto não é preço.** Promoção vale um ciclo e viaja como desconto de
   parcela. Reduzir o `unit_amount` de uma assinatura cria um preço recorrente
   menor — o desconto de boas-vindas passaria a valer para sempre.
4. **Todo webhook é assinado, datado e idempotente.** Assinatura para provar a
   origem, data para barrar replay, idempotência porque gateway reenvia o mesmo
   evento — e processar duas vezes é conceder dois meses.
5. **Falhar fechado.** Sem chave configurada, o checkout não finge que
   funcionou: ele diz que a cobrança não está ligada.
"""
from __future__ import annotations

import dataclasses
import datetime as dt


@dataclasses.dataclass(frozen=True)
class CheckoutSession:
    """O que o cliente precisa para pagar."""

    url: str = ""
    external_id: str = ""
    ok: bool = False
    message: str = ""


@dataclasses.dataclass(frozen=True)
class WebhookEvent:
    """Um evento do gateway, já validado e normalizado.

    Normalizar aqui é o que impede o vocabulário de um provedor de vazar para o
    resto do sistema: `core` só conhece estes tipos, nunca os nomes do gateway.
    """

    id: str
    type: str          # subscription.paid | subscription.canceled | payment.refunded | ...
    user_id: str = ""
    plan: str = ""
    cycle: str = ""
    external_id: str = ""
    amount_cents: int = 0
    occurred_at: dt.datetime | None = None
    raw: dict = dataclasses.field(default_factory=dict)


# Tipos de evento que o núcleo entende. Qualquer outro é ignorado com 200 —
# responder erro faria o gateway reenviar para sempre um evento que não nos
# interessa.
EVENT_SUBSCRIPTION_PAID = "subscription.paid"
EVENT_SUBSCRIPTION_CANCELED = "subscription.canceled"
EVENT_SUBSCRIPTION_PAST_DUE = "subscription.past_due"
EVENT_PAYMENT_REFUNDED = "payment.refunded"
EVENT_PAYMENT_DISPUTED = "payment.disputed"

EVENTOS_CONHECIDOS = frozenset({
    EVENT_SUBSCRIPTION_PAID,
    EVENT_SUBSCRIPTION_CANCELED,
    EVENT_SUBSCRIPTION_PAST_DUE,
    EVENT_PAYMENT_REFUNDED,
    EVENT_PAYMENT_DISPUTED,
})


class PaymentProvider:
    """Contrato que todo gateway precisa cumprir."""

    name = "base"

    @property
    def configured(self) -> bool:
        raise NotImplementedError

    def create_checkout(
        self, *, user, plan: str, cycle: str, amount_cents: int,
        success_url: str, cancel_url: str, first_invoice_discount: float = 0.0,
    ) -> CheckoutSession:
        """`first_invoice_discount` é fração (0.30 = 30%) e vale UMA parcela.

        Ele viaja separado do preço de propósito: desconto embutido no valor de
        uma assinatura vira preço recorrente, e um desconto de boas-vindas que
        nunca acaba é receita perdida para sempre.
        """
        raise NotImplementedError

    def verify_webhook(self, body: bytes, headers) -> WebhookEvent | None:
        """Valida assinatura e data. Devolve None quando não confia."""
        raise NotImplementedError

    def cancel_subscription(self, external_id: str) -> bool:
        raise NotImplementedError


class NullProvider(PaymentProvider):
    """Provedor usado enquanto não há chave.

    Não simula sucesso. Um checkout falso que "funciona" é a maneira mais fácil
    de descobrir em produção que a cobrança nunca esteve ligada.
    """

    name = "none"

    @property
    def configured(self) -> bool:
        return False

    def create_checkout(self, **_kwargs) -> CheckoutSession:
        return CheckoutSession(
            ok=False,
            message="A cobrança ainda não está ligada. Configure o gateway de pagamento.",
        )

    def verify_webhook(self, body: bytes, headers) -> WebhookEvent | None:
        return None

    def cancel_subscription(self, external_id: str) -> bool:
        return False
