"""O que fazer quando o gateway avisa alguma coisa.

Este é o único lugar que muda plano por causa de dinheiro. Nem a tela, nem o
redirect de sucesso, nem o JavaScript: só um evento assinado que passou por
aqui. Concentrar isso num arquivo é o que torna possível auditar de verdade
como uma conta virou paga.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.core import billing, referrals
from agenda.core.events import log
from agenda.models import (
    BillingCycle,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    User,
    WebhookEventLog,
)
from agenda.payments.base import (
    EVENT_PAYMENT_DISPUTED,
    EVENT_PAYMENT_REFUNDED,
    EVENT_SUBSCRIPTION_CANCELED,
    EVENT_SUBSCRIPTION_PAID,
    EVENT_SUBSCRIPTION_PAST_DUE,
    NullProvider,
    PaymentProvider,
    WebhookEvent,
)

_provedor: PaymentProvider | None = None


def provider() -> PaymentProvider:
    global _provedor
    if _provedor is None:
        if config.PAYMENT_PROVIDER == "stripe":
            from agenda.payments.stripe_provider import StripeProvider

            _provedor = StripeProvider()
        else:
            _provedor = NullProvider()
    return _provedor


def reset_provider() -> None:
    """Só para teste: força reler a configuração."""
    global _provedor
    _provedor = None


def enabled() -> bool:
    return provider().configured and config.flag("billing_enabled")


# --------------------------------------------------------------------------- #
# Checkout
# --------------------------------------------------------------------------- #
def start_checkout(db: Session, user: User, *, plan: str, cycle: str, base_url: str):
    """Cria a sessão de pagamento. O VALOR sai do nosso catálogo, sempre.

    O cliente escolhe plano e ciclo; quanto isso custa é decisão do servidor.
    Aceitar o valor que vem do navegador é como se vende assinatura por um
    centavo.
    """
    if plan not in billing.PLANS or plan == PlanTier.FREE.value:
        raise ValueError("plano inválido")
    if cycle not in {c.value for c in BillingCycle}:
        cycle = BillingCycle.MONTHLY.value

    plano = billing.PLANS[plan]
    centavos = int(round(plano.price_for(cycle) * 100))

    # O desconto de boas-vindas vale para a PRIMEIRA cobrança e viaja separado
    # do preço. Embutir a redução no valor recorrente faria o assinante pagar
    # 30% a menos para sempre — uma perda permanente de receita que qualquer
    # pessoa consegue sozinha, criando uma segunda conta pelo próprio link.
    # O provedor aplica isso como desconto de uma parcela, não como preço.
    desconto = referrals.invitee_discount_available(db, user)

    sessao = provider().create_checkout(
        user=user, plan=plan, cycle=cycle, amount_cents=centavos,
        first_invoice_discount=desconto,
        success_url=f"{base_url}/planos?pago=1",
        cancel_url=f"{base_url}/planos",
    )
    if sessao.ok:
        log(db, user_id=user.id, actor="user", action="CHECKOUT_STARTED",
            object_type="subscription", object_id=sessao.external_id,
            after={"plan": plan, "cycle": cycle, "centavos": centavos})
    return sessao


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #
def already_processed(db: Session, event_id: str) -> bool:
    """Idempotência: gateway reenvia o mesmo evento, e processar duas vezes
    concede dois meses."""
    if not event_id:
        return False
    return db.scalars(
        select(WebhookEventLog).where(WebhookEventLog.event_id == event_id)
    ).first() is not None


def handle(db: Session, evento: WebhookEvent) -> str:
    """Aplica um evento já validado. Devolve o que foi feito, para o log."""
    if already_processed(db, evento.id):
        return "duplicado"

    db.add(WebhookEventLog(
        event_id=evento.id, provider=provider().name, type=evento.type,
        user_id=evento.user_id or None,
    ))
    db.flush()

    user = db.get(User, evento.user_id) if evento.user_id else None
    if user is None or user.deleted_at is not None:
        return "usuário desconhecido"

    if evento.type == EVENT_SUBSCRIPTION_PAID:
        return _pagou(db, user, evento)
    if evento.type == EVENT_SUBSCRIPTION_PAST_DUE:
        return _atrasou(db, user)
    if evento.type == EVENT_SUBSCRIPTION_CANCELED:
        return _cancelou(db, user)
    if evento.type in (EVENT_PAYMENT_REFUNDED, EVENT_PAYMENT_DISPUTED):
        return _devolveu(db, user, evento)
    return "ignorado"


def _pagou(db: Session, user: User, evento: WebhookEvent) -> str:
    plano = evento.plan if evento.plan in billing.PLANS else PlanTier.STUDENT.value
    ciclo = evento.cycle if evento.cycle in {c.value for c in BillingCycle} else (
        BillingCycle.MONTHLY.value
    )
    billing.change_plan(
        db, user, plano, cycle=ciclo,
        provider=provider().name, external_id=evento.external_id,
    )
    # A indicação entra em carência; a recompensa só nasce depois da janela de
    # reembolso (core/referrals.py).
    referrals.mark_paid(db, user)
    meses = referrals.apply_credits(db, user)
    log(db, user_id=user.id, actor="system", action="SUBSCRIPTION_PAID",
        object_type="subscription", object_id=evento.external_id,
        after={"plan": plano, "cycle": ciclo, "creditos_aplicados": meses})
    return "assinatura ativada"


def _atrasou(db: Session, user: User) -> str:
    sub = billing.subscription_of(db, user)
    sub.status = SubscriptionStatus.PAST_DUE.value
    db.flush()
    log(db, user_id=user.id, actor="system", action="SUBSCRIPTION_PAST_DUE",
        object_type="subscription", object_id=sub.id)
    return "assinatura em atraso"


def _cancelou(db: Session, user: User) -> str:
    billing.cancel(db, user)
    log(db, user_id=user.id, actor="system", action="SUBSCRIPTION_CANCELED",
        object_type="subscription", object_id="")
    return "assinatura cancelada"


def _devolveu(db: Session, user: User, evento: WebhookEvent) -> str:
    """Reembolso ou contestação: desfaz o acesso e a recompensa de indicação.

    É aqui que o programa de indicação se protege do golpe "pago, ganho a
    recompensa, peço o dinheiro de volta".
    """
    sub = billing.subscription_of(db, user)
    sub.status = SubscriptionStatus.CANCELED.value
    sub.canceled_at = dt.datetime.now(dt.timezone.utc)
    sub.current_period_end = dt.datetime.now(dt.timezone.utc)
    db.flush()
    referrals.revoke_for(db, user, reason=evento.type)
    log(db, user_id=user.id, actor="system", action="PAYMENT_REVERSED",
        object_type="subscription", object_id=sub.id, after={"tipo": evento.type})
    return "pagamento revertido"


# --------------------------------------------------------------------------- #
# Reembolso pedido pelo usuário (CDC art. 49)
# --------------------------------------------------------------------------- #
def within_refund_window(db: Session, user: User) -> bool:
    sub = db.scalars(select(Subscription).where(Subscription.user_id == user.id)).first()
    if sub is None or sub.plan == PlanTier.FREE.value:
        return False
    inicio = sub.updated_at or sub.created_at
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=dt.timezone.utc)
    limite = inicio + dt.timedelta(days=config.REFUND_WINDOW_DAYS)
    return dt.datetime.now(dt.timezone.utc) <= limite
