"""Planos, entitlements e quotas (SPEC §96).

Regra de arquitetura: NENHUM condicional de plano espalhado pelo código. Todo
lugar pergunta "posso fazer isto?" para `allows()` / `check_quota()`, e o
catálogo abaixo é a única fonte de verdade.

## Como a escada foi desenhada

Todos os planos pagos têm o **produto inteiro**. O que muda é quanto de uso
cabe. Num produto de IA isso é o formato honesto: o custo escala com o uso, não
com o recurso. Quem manda quarenta áudios por dia custa muito mais que quem
manda dois, mesmo abrindo exatamente as mesmas telas — cobrar os dois igual é
ou perder dinheiro no primeiro ou roubar o segundo.

## Aviso sobre os limites

Os números vêm de `UNIT_COST_BRL`, que é uma **estimativa** de custo por
operação. Eles precisam ser calibrados contra a fatura real do provedor de IA
antes do lançamento comercial — `margin_report()` existe para isso: ele mostra,
por plano, quanto sobra se o assinante usar 100% da quota. Enquanto a fatura
real não entrar ali, trate a margem como hipótese, não como fato.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.models import (
    BillingCycle,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    UsageCounter,
    User,
)

# Entitlements — nomes estáveis, usados pelo código e pelos testes.
CAN_USE_WHATSAPP = "CAN_USE_WHATSAPP"
CAN_USE_FAMILY = "CAN_USE_FAMILY"
CAN_SYNC_CALENDAR = "CAN_SYNC_CALENDAR"
CAN_USE_STUDY_PLANNER = "CAN_USE_STUDY_PLANNER"
CAN_SHARE = "CAN_SHARE"
# Receber no próprio celular os lembretes do estudante acompanhado. É a razão
# número um de um pai pagar, então é o gancho do plano Família.
CAN_RECEIVE_STUDENT_REMINDERS = "CAN_RECEIVE_STUDENT_REMINDERS"
MAX_DOCUMENT_IMPORTS = "MAX_DOCUMENT_IMPORTS"
MAX_AI_MESSAGES = "MAX_AI_MESSAGES"
MAX_AUDIO_MINUTES = "MAX_AUDIO_MINUTES"
MAX_CONTEXTS = "MAX_CONTEXTS"
MAX_STUDENTS = "MAX_STUDENTS"

UNLIMITED = -1

# Desconto do ciclo anual. 20% é o padrão de mercado e paga a si mesmo: reduz
# cancelamento e antecipa caixa, que é o que sustenta a conta de IA.
ANNUAL_DISCOUNT = 0.20

# --------------------------------------------------------------------------- #
# Modelo de custo — o número que governa os limites
# --------------------------------------------------------------------------- #
# Custo estimado por operação, em reais. ESTIMATIVA: precisa ser calibrado
# contra a fatura real. A base local de conhecimento derruba boa parte destas
# chamadas antes de sair daqui (ver docs/CONHECIMENTO.md), então o custo real
# por captura tende a ficar ABAIXO destes valores — o que é margem a favor.
UNIT_COST_BRL = {
    "ai_messages": 0.006,       # uma captura por texto, com prompt recuperado
    "document_imports": 0.14,   # leitura de um cronograma (texto ou visão)
    "audio_minutes": 0.05,      # um minuto de transcrição
}

# Quanto do preço pode virar custo de IA no PIOR caso (assinante usando 100%
# da quota, e toda captura indo para o modelo). Ficar acima disto não condena o
# plano: condena a cauda dele. Ver `margin_report` para a leitura correta.
TETO_DE_CUSTO = 0.45

# Fração das capturas que a base de conhecimento local resolve sem chamar
# modelo (docs/CONHECIMENTO.md). ESTIMATIVA conservadora — o valor real sobe
# com o tempo de uso, porque cada confirmação ensina o sistema.
TAXA_RESOLUCAO_LOCAL = 0.60

# Uso do assinante MEDIANO, por mês. É por este perfil que a margem agregada
# se decide; a quota existe para conter a cauda, não para taxar a mediana.
USO_MEDIANO = {"ai_messages": 60, "document_imports": 4, "audio_minutes": 10}


@dataclass(frozen=True)
class Plan:
    tier: str
    name: str
    tagline: str
    price_month: float
    features: dict[str, object]
    highlights: tuple[str, ...] = field(default_factory=tuple)
    # Para quem este plano é, em uma linha — aparece no cartão.
    para_quem: str = ""

    @property
    def price_annual(self) -> float:
        """Preço do ano inteiro, já com o desconto."""
        return round(self.price_month * 12 * (1 - ANNUAL_DISCOUNT), 2)

    @property
    def price_annual_monthly(self) -> float:
        """Quanto sai por mês no plano anual — é o número que convence."""
        return round(self.price_annual / 12, 2)

    @property
    def annual_savings(self) -> float:
        return round(self.price_month * 12 - self.price_annual, 2)

    def price_for(self, cycle: str) -> float:
        return self.price_annual if cycle == BillingCycle.ANNUAL.value else self.price_month


def _features(
    *, whatsapp: bool, family: bool, calendar: bool, planner: bool, share: bool,
    guardian_reminders: bool, documents: int, messages: int, audio: int,
    contexts: int, students: int,
) -> dict[str, object]:
    return {
        CAN_USE_WHATSAPP: whatsapp,
        CAN_USE_FAMILY: family,
        CAN_SYNC_CALENDAR: calendar,
        CAN_USE_STUDY_PLANNER: planner,
        CAN_SHARE: share,
        CAN_RECEIVE_STUDENT_REMINDERS: guardian_reminders,
        MAX_DOCUMENT_IMPORTS: documents,
        MAX_AI_MESSAGES: messages,
        MAX_AUDIO_MINUTES: audio,
        MAX_CONTEXTS: contexts,
        MAX_STUDENTS: students,
    }


PLANS: dict[str, Plan] = {
    PlanTier.FREE.value: Plan(
        tier=PlanTier.FREE.value,
        name="Grátis",
        tagline="Para experimentar e organizar o essencial",
        para_quem="Quem quer ver se o app resolve antes de pagar",
        price_month=0.0,
        features=_features(
            whatsapp=False, family=False, calendar=False, planner=False, share=True,
            guardian_reminders=False,
            documents=3, messages=30, audio=5, contexts=1, students=1,
        ),
        highlights=(
            "Agenda completa: hoje, semana e mês",
            "30 capturas por texto ou voz no mês",
            "3 documentos no mês",
            "Lembretes no app e por push",
        ),
    ),
    PlanTier.STUDENT.value: Plan(
        tier=PlanTier.STUDENT.value,
        name="Estudante",
        tagline="O produto inteiro, no uso de quem estuda todo dia",
        para_quem="Um curso, uso diário, sem contar arquivo",
        price_month=19.90,
        features=_features(
            whatsapp=True, family=False, calendar=True, planner=True, share=True,
            guardian_reminders=False,
            documents=25, messages=400, audio=90, contexts=3, students=1,
        ),
        highlights=(
            "WhatsApp: áudio, foto e cronograma",
            "400 capturas e 25 documentos no mês",
            "90 minutos de áudio no mês",
            "Planejador de estudos",
            "Exportar para Google e Apple Calendar",
        ),
    ),
    PlanTier.PRO.value: Plan(
        tier=PlanTier.PRO.value,
        name="Pro",
        tagline="Para quem manda muita coisa e cursa mais de uma frente",
        para_quem="Faculdade + curso técnico, ou quem vive de áudio",
        price_month=29.90,
        features=_features(
            whatsapp=True, family=False, calendar=True, planner=True, share=True,
            guardian_reminders=False,
            documents=80, messages=1500, audio=300, contexts=6, students=1,
        ),
        highlights=(
            "Tudo do Estudante, com folga de sobra",
            "1.500 capturas e 80 documentos no mês",
            "300 minutos de áudio no mês",
            "Até 6 contextos ao mesmo tempo",
            "Prioridade quando o serviço estiver cheio",
        ),
    ),
    PlanTier.FAMILY.value: Plan(
        tier=PlanTier.FAMILY.value,
        name="Família",
        tagline="Para acompanhar os estudos dos filhos do seu celular",
        para_quem="Responsável com um ou mais filhos na escola",
        price_month=39.90,
        features=_features(
            whatsapp=True, family=True, calendar=True, planner=True, share=True,
            guardian_reminders=True,
            documents=100, messages=2000, audio=360, contexts=10, students=5,
        ),
        highlights=(
            "Tudo do Pro",
            "Até 5 estudantes na conta",
            "Você recebe no seu celular os lembretes deles",
            "Visão da agenda de cada filho",
            "Cada filho usa o app dele, com a cara dele",
        ),
    ),
    PlanTier.INSTITUTION.value: Plan(
        tier=PlanTier.INSTITUTION.value,
        name="Institucional",
        tagline="Escolas e faculdades — sob contrato",
        para_quem="Instituição que quer a turma inteira organizada",
        price_month=0.0,
        features=_features(
            whatsapp=True, family=True, calendar=True, planner=True, share=True,
            guardian_reminders=True,
            documents=UNLIMITED, messages=UNLIMITED, audio=UNLIMITED,
            contexts=UNLIMITED, students=UNLIMITED,
        ),
        highlights=("Uso ilimitado", "Turmas oficiais", "Suporte dedicado"),
    ),
}

# A ordem em que os planos aparecem na tela.
PLANOS_PUBLICOS = (
    PlanTier.FREE.value, PlanTier.STUDENT.value,
    PlanTier.PRO.value, PlanTier.FAMILY.value,
)

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
    # Pagamento avulso (Pix) não renova sozinho: quando o período vence, a
    # conta volta para o grátis. Sem esta linha, quem pagou um mês por Pix
    # ficaria com o plano pago para sempre.
    if not sub.renews and sub.plan != PlanTier.FREE.value:
        fim = sub.current_period_end
        if fim is None or _aware(fim) < _now():
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


class QuotaExceeded(Exception):
    """A conta bateu o limite do plano. A mensagem é escrita para o usuário ler."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def enforce(
    db: Session, user: User, entitlement: str, metric: str, *, amount: int = 1
) -> None:
    """Confere a quota e levanta se estourou. NÃO consome.

    Existe para que a checagem possa morar dentro da operação (ingestão,
    assistente, transcrição) em vez de em cada chamador. Toda vez que a
    checagem fica no chamador, um caminho novo nasce sem ela — foi assim que a
    importação por formulário, o onboarding por texto e o WhatsApp ficaram sem
    limite nenhum enquanto a API tinha.
    """
    pode, aviso = check_quota(db, user, entitlement, metric)
    if not pode:
        raise QuotaExceeded(aviso)


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
    db: Session,
    user: User,
    plan: str,
    *,
    cycle: str = BillingCycle.MONTHLY.value,
    provider: str = "manual",
    external_id: str = "",
    renews: bool = True,
    payment_method: str = "card",
) -> Subscription:
    if plan not in PLANS:
        raise ValueError("plano desconhecido")
    if cycle not in {c.value for c in BillingCycle}:
        cycle = BillingCycle.MONTHLY.value
    sub = subscription_of(db, user)
    sub.plan = plan
    sub.cycle = cycle
    sub.status = (
        SubscriptionStatus.ACTIVE.value if plan != PlanTier.FREE.value
        else SubscriptionStatus.CANCELED.value
    )
    sub.provider = provider
    sub.external_id = external_id[:120]
    sub.renews = renews if plan != PlanTier.FREE.value else True
    sub.payment_method = payment_method[:20]
    sub.canceled_at = None if plan != PlanTier.FREE.value else _now()
    if plan != PlanTier.FREE.value:
        dias = 365 if cycle == BillingCycle.ANNUAL.value else 30
        sub.current_period_end = _now() + dt.timedelta(days=dias)
    db.flush()
    return sub


# Quantos dias antes do fim avisar quem pagou avulso (Pix).
AVISO_DE_VENCIMENTO_DIAS = 3


def avisar_vencimentos(db: Session, *, now: dt.datetime | None = None) -> int:
    """Avisa quem pagou por Pix que o período está acabando.

    Sem cobrança recorrente, o silêncio é uma armadilha: a pessoa descobre que
    perdeu o plano quando abre o app na véspera da prova. Um aviso três dias
    antes custa nada e é a diferença entre renovar e xingar.
    """
    agora = now or _now()
    limite = agora + dt.timedelta(days=AVISO_DE_VENCIMENTO_DIAS)
    avisados = 0

    candidatas = db.scalars(
        select(Subscription).where(
            Subscription.renews.is_(False),
            Subscription.plan != PlanTier.FREE.value,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.current_period_end.is_not(None),
        )
    ).all()
    for sub in candidatas:
        fim = _aware(sub.current_period_end)
        if not (agora < fim <= limite):
            continue
        user = db.get(User, sub.user_id)
        if user is None or user.deleted_at is not None:
            continue
        # Uma vez por período: sem esta marca, o worker mandaria o mesmo aviso
        # a cada rodada e a notificação viraria ruído que ninguém lê.
        if sub.renewal_notice_at is not None and _aware(sub.renewal_notice_at) > agora - dt.timedelta(days=AVISO_DE_VENCIMENTO_DIAS + 1):
            continue
        from agenda.core import notifications

        dias = max((fim - agora).days, 0)
        quando = "hoje" if dias == 0 else ("amanhã" if dias == 1 else f"em {dias} dias")
        notifications.create(
            db, user,
            title=f"Seu {PLANS[sub.plan].name} acaba {quando}",
            body=(
                "Você pagou por Pix, que não renova sozinho. Para continuar com "
                "tudo liberado, é só pagar de novo em Planos — leva um minuto."
            ),
            kind="billing",
        )
        sub.renewal_notice_at = agora
        avisados += 1
    db.flush()
    return avisados


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
        "cycle": sub.cycle,
        "usage": {
            "documentos": usage(db, user, "document_imports"),
            "documentos_limite": limit_of(db, user, MAX_DOCUMENT_IMPORTS),
            "mensagens": usage(db, user, "ai_messages"),
            "mensagens_limite": limit_of(db, user, MAX_AI_MESSAGES),
            "audio": usage(db, user, "audio_minutes"),
            "audio_limite": limit_of(db, user, MAX_AUDIO_MINUTES),
        },
    }


# --------------------------------------------------------------------------- #
# Áudio: medir para poder limitar
# --------------------------------------------------------------------------- #
# Bitrate assumido do áudio que chega (Opus em WebM/OGG, que é o que o
# navegador e o WhatsApp produzem). Deliberadamente BAIXO: subestimar o bitrate
# superestima a duração, e errar para o lado de cobrar mais minutos protege a
# fatura. O usuário nunca perde conteúdo por causa disto — só chega mais rápido
# no limite do plano dele.
AUDIO_BITRATE_KBPS = 16


def estimate_audio_minutes(byte_length: int) -> int:
    """Minutos de áudio a partir do tamanho do arquivo, arredondando para cima.

    Não decodificamos o áudio só para contar quota: seria caro e frágil. O
    tamanho é uma aproximação boa o suficiente, e o arredondamento para cima
    garante que áudio curtinho conte como um minuto — que é o que custa mesmo,
    porque o custo tem um piso por chamada.
    """
    if byte_length <= 0:
        return 0
    segundos = (byte_length * 8) / (AUDIO_BITRATE_KBPS * 1000)
    return max(1, int(segundos // 60) + (1 if segundos % 60 else 0))


# --------------------------------------------------------------------------- #
# Margem: o número que decide se a escada de planos se sustenta
# --------------------------------------------------------------------------- #
_METRICAS = (
    (MAX_AI_MESSAGES, "ai_messages"),
    (MAX_DOCUMENT_IMPORTS, "document_imports"),
    (MAX_AUDIO_MINUTES, "audio_minutes"),
)


def _custo(volumes: dict[str, int], *, com_resolucao_local: bool) -> float:
    total = 0.0
    for metrica, quantidade in volumes.items():
        unitario = UNIT_COST_BRL.get(metrica, 0.0)
        if com_resolucao_local and metrica == "ai_messages":
            quantidade = quantidade * (1 - TAXA_RESOLUCAO_LOCAL)
        total += quantidade * unitario
    return round(total, 2)


def plan_ai_cost(plan: Plan) -> float:
    """Custo de IA no pior caso: 100% da quota, tudo indo para o modelo."""
    volumes = {}
    for entitlement, metrica in _METRICAS:
        limite = plan.features.get(entitlement, 0)
        if limite == UNLIMITED or not isinstance(limite, int):
            continue
        volumes[metrica] = limite
    return _custo(volumes, com_resolucao_local=False)


def margin_report() -> list[dict]:
    """Margem por plano, em dois cenários — é assim que se lê uma quota.

    **Mediano** é o que decide a margem agregada: quase todo assinante usa uma
    fração da quota, e é essa fração que multiplica pela base.

    **Pior caso** é a exposição da cauda: quanto custa o assinante que usa tudo.
    Um plano estourar o teto no pior caso NÃO condena o plano — condena a cauda
    dele, e é aceitável enquanto a mediana pagar a conta com folga. O que não é
    aceitável é não saber o número.

    Enquanto `UNIT_COST_BRL` não vier da fatura real do provedor, tudo isto é
    hipótese: serve para ver a FORMA da escada, não para prometer margem.
    """
    linhas = []
    for chave in PLANOS_PUBLICOS:
        plano = PLANS[chave]
        preco = plano.price_month
        pior = plan_ai_cost(plano)
        # Na mediana o assinante não passa da própria quota.
        mediano_volumes = {
            metrica: min(
                USO_MEDIANO[metrica],
                plano.features.get(ent, 0) if plano.features.get(ent, 0) != UNLIMITED
                else USO_MEDIANO[metrica],
            )
            for ent, metrica in _METRICAS
        }
        mediano = _custo(mediano_volumes, com_resolucao_local=True)
        linhas.append({
            "plano": plano.name,
            "preco_mes": preco,
            "preco_anual_mes": plano.price_annual_monthly,
            "custo_mediano": mediano,
            "custo_pior_caso": pior,
            "teto_aceitavel": round(preco * TETO_DE_CUSTO, 2),
            "sobra_mediana": round(preco - mediano, 2),
            "percentual_mediano": round(100 * mediano / preco, 1) if preco else 0.0,
            "percentual_pior_caso": round(100 * pior / preco, 1) if preco else 0.0,
            "cauda_no_prejuizo": preco > 0 and pior > preco,
        })
    return linhas


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
