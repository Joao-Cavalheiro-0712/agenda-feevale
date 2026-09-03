"""Planos, entitlements e quotas (SPEC §96).

Regra de arquitetura: NENHUM condicional de plano espalhado pelo código. Todo
lugar pergunta "posso fazer isto?" para `allows()` / `check_quota()`, e o
catálogo abaixo é a única fonte de verdade.

O provedor de cobrança é plugável: `provider.py` define a interface e aqui só
existe o efeito no banco. A integração real com gateway depende de chave e é
ligada por variável de ambiente.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.models import PlanTier, Subscription, SubscriptionStatus, UsageCounter, User

# Entitlements — nomes estáveis, usados pelo código e pelos testes.
CAN_USE_WHATSAPP = "CAN_USE_WHATSAPP"
CAN_USE_FAMILY = "CAN_USE_FAMILY"
CAN_SYNC_CALENDAR = "CAN_SYNC_CALENDAR"
CAN_USE_STUDY_PLANNER = "CAN_USE_STUDY_PLANNER"
CAN_SHARE = "CAN_SHARE"
MAX_DOCUMENT_IMPORTS = "MAX_DOCUMENT_IMPORTS"
MAX_AI_MESSAGES = "MAX_AI_MESSAGES"
MAX_CONTEXTS = "MAX_CONTEXTS"
MAX_STUDENTS = "MAX_STUDENTS"

UNLIMITED = -1


@dataclass(frozen=True)
class Plan:
    tier: str
    name: str
    tagline: str
    price_month: float
    features: dict[str, object]
    highlights: tuple[str, ...]


PLANS: dict[str, Plan] = {
    PlanTier.FREE.value: Plan(
        tier=PlanTier.FREE.value,
        name="Grátis",
        tagline="Para experimentar e organizar o essencial",
        price_month=0.0,
        features={
            CAN_USE_WHATSAPP: False,
            CAN_USE_FAMILY: False,
            CAN_SYNC_CALENDAR: False,
            CAN_USE_STUDY_PLANNER: False,
            CAN_SHARE: True,
            MAX_DOCUMENT_IMPORTS: 3,
            MAX_AI_MESSAGES: 30,
            MAX_CONTEXTS: 1,
            MAX_STUDENTS: 0,
        },
        highlights=(
            "Agenda completa, hoje/semana/mês",
            "3 documentos por mês",
            "30 capturas por texto ou voz",
            "Lembretes no app e por push",
        ),
    ),
    PlanTier.STUDENT.value: Plan(
        tier=PlanTier.STUDENT.value,
        name="Estudante",
        tagline="O produto inteiro, sem contar arquivo",
        price_month=19.9,
        features={
            CAN_USE_WHATSAPP: True,
            CAN_USE_FAMILY: False,
            CAN_SYNC_CALENDAR: True,
            CAN_USE_STUDY_PLANNER: True,
            CAN_SHARE: True,
            MAX_DOCUMENT_IMPORTS: 100,
            MAX_AI_MESSAGES: 1000,
            MAX_CONTEXTS: 5,
            MAX_STUDENTS: 0,
        },
        highlights=(
            "WhatsApp: áudio, foto e cronograma",
            "100 documentos por mês",
            "Planejador de estudos",
            "Exportar para Google/Apple Calendar",
            "Vários contextos (faculdade + curso)",
        ),
    ),
    PlanTier.FAMILY.value: Plan(
        tier=PlanTier.FAMILY.value,
        name="Família",
        tagline="Para responsáveis acompanharem até 5 estudantes",
        price_month=34.9,
        features={
            CAN_USE_WHATSAPP: True,
            CAN_USE_FAMILY: True,
            CAN_SYNC_CALENDAR: True,
            CAN_USE_STUDY_PLANNER: True,
            CAN_SHARE: True,
            MAX_DOCUMENT_IMPORTS: 300,
            MAX_AI_MESSAGES: 3000,
            MAX_CONTEXTS: 10,
            MAX_STUDENTS: 5,
        },
        highlights=(
            "Tudo do Estudante",
            "Até 5 estudantes na conta",
            "Responsável recebe os lembretes",
            "Visão da agenda de cada filho",
        ),
    ),
    PlanTier.INSTITUTION.value: Plan(
        tier=PlanTier.INSTITUTION.value,
        name="Institucional",
        tagline="Escolas e faculdades — sob contrato",
        price_month=0.0,
        features={
            CAN_USE_WHATSAPP: True,
            CAN_USE_FAMILY: True,
            CAN_SYNC_CALENDAR: True,
            CAN_USE_STUDY_PLANNER: True,
            CAN_SHARE: True,
            MAX_DOCUMENT_IMPORTS: UNLIMITED,
            MAX_AI_MESSAGES: UNLIMITED,
            MAX_CONTEXTS: UNLIMITED,
            MAX_STUDENTS: UNLIMITED,
        },
        highlights=("Uso ilimitado", "Turmas oficiais", "Suporte dedicado"),
    ),
}

TRIAL_DAYS = 14


def subscription_of(db: Session, user: User) -> Subscription:
    """Toda conta tem assinatura; sem registro, é o plano grátis."""
    sub = db.scalars(select(Subscription).where(Subscription.user_id == user.id)).first()
    if sub is None:
        sub = Subscription(user_id=user.id, plan=PlanTier.FREE.value)
        db.add(sub)
        db.flush()
    return sub


def active_plan(db: Session, user: User) -> Plan:
    sub = subscription_of(db, user)
    if sub.status in (SubscriptionStatus.CANCELED.value, SubscriptionStatus.PAST_DUE.value):
        expirou = sub.current_period_end is None or _aware(sub.current_period_end) < _now()
        if expirou:
            return PLANS[PlanTier.FREE.value]
    if sub.status == SubscriptionStatus.TRIALING.value and sub.trial_ends_at:
        if _aware(sub.trial_ends_at) < _now():
            return PLANS[PlanTier.FREE.value]
    return PLANS.get(sub.plan, PLANS[PlanTier.FREE.value])


def allows(db: Session, user: User, entitlement: str) -> bool:
    valor = active_plan(db, user).features.get(entitlement, False)
    return bool(valor) if isinstance(valor, bool) else valor != 0


def limit_of(db: Session, user: User, entitlement: str) -> int:
    valor = active_plan(db, user).features.get(entitlement, 0)
    return int(valor) if isinstance(valor, (int, float)) else 0


def _period_key(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    return f"{today.year:04d}-{today.month:02d}"


def usage(db: Session, user: User, metric: str, *, today: dt.date | None = None) -> int:
    row = db.scalars(
        select(UsageCounter).where(
            UsageCounter.user_id == user.id,
            UsageCounter.metric == metric,
            UsageCounter.period == _period_key(today),
        )
    ).first()
    return row.count if row else 0


def check_quota(db: Session, user: User, entitlement: str, metric: str) -> tuple[bool, str]:
    """(pode_seguir, mensagem). A mensagem é escrita para o usuário ler."""
    limite = limit_of(db, user, entitlement)
    if limite == UNLIMITED:
        return True, ""
    atual = usage(db, user, metric)
    if atual < limite:
        return True, ""
    plano = active_plan(db, user)
    return False, (
        f"Você usou {atual} de {limite} neste mês no plano {plano.name}. "
        "Assine para continuar sem contar."
    )


def consume(db: Session, user: User, metric: str, *, amount: int = 1) -> int:
    periodo = _period_key()
    row = db.scalars(
        select(UsageCounter).where(
            UsageCounter.user_id == user.id,
            UsageCounter.metric == metric,
            UsageCounter.period == periodo,
        )
    ).first()
    if row is None:
        row = UsageCounter(user_id=user.id, metric=metric, period=periodo, count=0)
        db.add(row)
    row.count += amount
    db.flush()
    return row.count


def start_trial(db: Session, user: User, plan: str = PlanTier.STUDENT.value) -> Subscription:
    sub = subscription_of(db, user)
    if sub.trial_ends_at is not None:
        return sub
    sub.plan = plan
    sub.status = SubscriptionStatus.TRIALING.value
    sub.trial_ends_at = _now() + dt.timedelta(days=TRIAL_DAYS)
    sub.current_period_end = sub.trial_ends_at
    db.flush()
    return sub


def change_plan(
    db: Session, user: User, plan: str, *, provider: str = "manual", external_id: str = ""
) -> Subscription:
    if plan not in PLANS:
        raise ValueError("plano desconhecido")
    sub = subscription_of(db, user)
    sub.plan = plan
    sub.status = (
        SubscriptionStatus.ACTIVE.value if plan != PlanTier.FREE.value
        else SubscriptionStatus.CANCELED.value
    )
    sub.provider = provider
    sub.external_id = external_id[:120]
    sub.canceled_at = None if plan != PlanTier.FREE.value else _now()
    if plan != PlanTier.FREE.value:
        sub.current_period_end = _now() + dt.timedelta(days=30)
    db.flush()
    return sub


def cancel(db: Session, user: User) -> Subscription:
    sub = subscription_of(db, user)
    sub.status = SubscriptionStatus.CANCELED.value
    sub.canceled_at = _now()
    db.flush()
    return sub


def summary(db: Session, user: User) -> dict:
    sub = subscription_of(db, user)
    plano = active_plan(db, user)
    return {
        "plan": plano,
        "subscription": sub,
        "trial_days_left": (
            max(0, (_aware(sub.trial_ends_at) - _now()).days) if sub.trial_ends_at else 0
        ),
        "usage": {
            "documentos": usage(db, user, "document_imports"),
            "documentos_limite": limit_of(db, user, MAX_DOCUMENT_IMPORTS),
            "mensagens": usage(db, user, "ai_messages"),
            "mensagens_limite": limit_of(db, user, MAX_AI_MESSAGES),
        },
    }


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
