"""Indicação — o motor de crescimento sem tráfego pago.

## A decisão mais importante aqui é econômica, não técnica

A recompensa só nasce quando o indicado **paga e passa da janela de
reembolso**. Isso, e não limite de IP, é o que mata a fraude: para ganhar um
mês grátis (R$ 19,90) o fraudador precisaria pagar três assinaturas de verdade
(R$ 59,70) e não pedir reembolso. Ninguém faz isso.

Limite por IP, aliás, seria pior que inútil aqui: mãe e filho compartilham o
wi-fi de casa, e a mãe indicando o filho é exatamente o caso de uso que a gente
QUER. Bloquear por IP puniria o cliente legítimo e não pararia o fraudador,
que troca de IP em dois toques. Guardamos o sinal de mesmo IP para auditoria e
seguimos em frente.

## Por que a recompensa é nos dois lados

Quem indica ganha mês grátis; quem entra pelo código ganha desconto na
primeira cobrança. Programa de um lado só depende de altruísmo — a pessoa
manda o link e o amigo não tem motivo para clicar. Com desconto para o
convidado, o link vira um presente, e presente as pessoas mandam.

## A meta é configurável, e a matemática importa

`REFERRAL_GOAL` = quantas indicações qualificadas valem um mês grátis. Cada
indicação qualificada traz, no mínimo, uma assinatura paga; então uma meta de 3
troca R$ 59,70 de receita nova por R$ 19,90 de desconto — um custo de aquisição
de R$ 6,63 por assinante pagante, que nenhum tráfego pago no Brasil bate. Meta
alta (10) parece mais segura e na prática mata o programa: ninguém chega perto
e a peça deixa de ser compartilhada.
"""
from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.core.events import log
from agenda.models import (
    Referral,
    ReferralStatus,
    Reward,
    RewardKind,
    Subscription,
    SubscriptionStatus,
    User,
)
from agenda.security import hash_ip

# Alfabeto sem caracteres que se confundem quando alguém dita o código em voz
# alta ou lê de um print: sem O/0, I/1, L, S/5.
_ALFABETO = "ABCDEFGHJKMNPQRTUVWXYZ2346789"
TAMANHO_DO_CODIGO = 6

# Quantas indicações qualificadas valem um mês grátis.
REFERRAL_GOAL = int(config._env("REFERRAL_GOAL", "3"))
# Desconto na primeira cobrança de quem entra por indicação (fração).
INVITEE_DISCOUNT = float(config._env("REFERRAL_INVITEE_DISCOUNT", "0.30"))
# Teto anual de meses grátis por conta — impede que um caso extremo (ou um bug)
# transforme uma conta em assinatura eterna de graça.
MAX_FREE_MONTHS_PER_YEAR = int(config._env("REFERRAL_MAX_FREE_MONTHS", "12"))
# Dias de carência antes de qualificar: a janela de reembolso do CDC é 7 dias,
# e chargeback aparece depois. Esperar protege contra o golpe "pago, ganho a
# recompensa, peço o dinheiro de volta".
QUALIFY_AFTER_DAYS = int(config._env("REFERRAL_QUALIFY_DAYS", "8"))


# --------------------------------------------------------------------------- #
# Código
# --------------------------------------------------------------------------- #
def code_for(db: Session, user: User) -> str:
    """Código de indicação da pessoa, criado no primeiro uso."""
    if user.referral_code:
        return user.referral_code
    for _ in range(12):
        candidato = "".join(secrets.choice(_ALFABETO) for _ in range(TAMANHO_DO_CODIGO))
        existe = db.scalars(select(User).where(User.referral_code == candidato)).first()
        if existe is None:
            user.referral_code = candidato
            db.flush()
            return candidato
    raise RuntimeError("não consegui gerar um código de indicação único")


def user_by_code(db: Session, code: str) -> User | None:
    codigo = (code or "").strip().upper()[:16]
    if not codigo:
        return None
    return db.scalars(
        select(User).where(User.referral_code == codigo, User.deleted_at.is_(None))
    ).first()


def share_url(code: str) -> str:
    base = config.PUBLIC_URL or ""
    return f"{base}/i/{code}" if base else f"/i/{code}"


# --------------------------------------------------------------------------- #
# Atribuição
# --------------------------------------------------------------------------- #
def attribute(
    db: Session,
    referred: User,
    code: str,
    *,
    ip: str | None = None,
    user_agent: str = "",
) -> Referral | None:
    """Registra que `referred` entrou pelo código de alguém.

    Chamado logo depois do cadastro. Falha silenciosa de propósito: indicação
    inválida nunca pode impedir alguém de criar a conta.
    """
    indicador = user_by_code(db, code)
    if indicador is None or indicador.id == referred.id:
        return None

    # Uma pessoa é indicada uma vez e para sempre — a atribuição não se troca.
    ja_existe = db.scalars(
        select(Referral).where(Referral.referred_id == referred.id)
    ).first()
    if ja_existe is not None:
        return ja_existe

    ip_hash = hash_ip(ip)
    mesmo_ip = bool(ip_hash) and _indicador_usou_o_ip(db, indicador, ip_hash)

    registro = Referral(
        referrer_id=indicador.id,
        referred_id=referred.id,
        code=(indicador.referral_code or "")[:16],
        status=ReferralStatus.SIGNED_UP.value,
        signup_ip_hash=ip_hash,
        signup_user_agent=(user_agent or "")[:300],
        mesmo_ip_do_indicador=mesmo_ip,
    )
    db.add(registro)
    db.flush()
    log(db, user_id=indicador.id, actor="system", action="REFERRAL_SIGNUP",
        object_type="referral", object_id=registro.id,
        after={"mesmo_ip": mesmo_ip})
    return registro


def _indicador_usou_o_ip(db: Session, indicador: User, ip_hash: str) -> bool:
    """Sinal de auditoria, não de bloqueio.

    Mesmo IP é normal em família (a mãe indica o filho do wi-fi de casa) e em
    escola. Guardamos para poder investigar um padrão, nunca para recusar.
    """
    from agenda.models import UserSession

    return db.scalars(
        select(UserSession).where(
            UserSession.user_id == indicador.id, UserSession.ip_hash == ip_hash
        ).limit(1)
    ).first() is not None


def referral_of(db: Session, referred: User) -> Referral | None:
    return db.scalars(select(Referral).where(Referral.referred_id == referred.id)).first()


def invitee_discount_available(db: Session, user: User) -> float:
    """Desconto de boas-vindas de quem chegou por indicação e ainda não pagou."""
    registro = referral_of(db, user)
    if registro is None or registro.status != ReferralStatus.SIGNED_UP.value:
        return 0.0
    return INVITEE_DISCOUNT


# --------------------------------------------------------------------------- #
# Qualificação e recompensa
# --------------------------------------------------------------------------- #
def mark_paid(db: Session, referred: User) -> Referral | None:
    """Chamado quando o pagamento do indicado é confirmado.

    NÃO qualifica na hora: só marca a data. A qualificação acontece depois da
    carência, em `run_qualification`, porque reembolso e chargeback aparecem
    depois do pagamento — e recompensa paga cedo é recompensa impossível de
    recuperar.
    """
    registro = referral_of(db, referred)
    if registro is None or registro.status != ReferralStatus.SIGNED_UP.value:
        return registro
    registro.qualified_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    return registro


def revoke_for(db: Session, referred: User, *, reason: str) -> None:
    """Reembolso ou chargeback do indicado desfaz a indicação e a recompensa."""
    registro = referral_of(db, referred)
    if registro is None:
        return
    registro.status = ReferralStatus.REJECTED.value
    registro.rejection_reason = reason[:60]
    registro.qualified_at = None
    db.flush()

    # A recompensa é revogada mesmo que JÁ TENHA SIDO APLICADA. Filtrar por
    # `applied_at is None` deixava o golpe passar: a carência é de 8 dias e um
    # chargeback de cartão pode chegar meses depois, quando o crédito já virou
    # tempo de assinatura. Sem esta parte, o argumento antifraude inteiro do
    # módulo cai.
    agora = dt.datetime.now(dt.timezone.utc)
    credito = db.scalars(
        select(Reward).where(
            Reward.user_id == registro.referrer_id,
            Reward.kind == RewardKind.FREE_MONTH.value,
            Reward.revoked_at.is_(None),
        ).order_by(Reward.created_at.desc()).limit(1)
    ).first()
    if credito is None:
        return

    credito.revoked_at = agora
    credito.revoked_reason = reason[:160]
    if credito.applied_at is not None:
        _devolver_tempo(db, registro.referrer_id, meses=credito.months, agora=agora)
    db.flush()


def _devolver_tempo(db: Session, user_id: str, *, meses: int, agora: dt.datetime) -> None:
    """Desfaz o tempo de assinatura que um crédito revogado tinha concedido.

    Nunca corta abaixo de agora: quem indicou não perde o período que ele mesmo
    pagou — perde só o que veio da indicação que caiu.
    """
    from agenda.models import Subscription

    sub = db.scalars(select(Subscription).where(Subscription.user_id == user_id)).first()
    if sub is None or sub.current_period_end is None:
        return
    fim = sub.current_period_end
    if fim.tzinfo is None:
        fim = fim.replace(tzinfo=dt.timezone.utc)
    sub.current_period_end = max(agora, fim - dt.timedelta(days=30 * max(1, meses)))
    db.flush()


def _contato_verificado(db: Session, user_id: str) -> bool:
    """Recompensa só nasce de gente real.

    Pagamento já é a barreira principal, mas exigir e-mail confirmado corta a
    fazenda de contas antes dela — e o custo para quem é honesto é zero, porque
    o link de confirmação sai no cadastro.
    """
    indicado = db.get(User, user_id)
    return bool(indicado and indicado.email_verified_at is not None)


def run_qualification(db: Session, *, now: dt.datetime | None = None) -> dict:
    """Passa indicações da carência para QUALIFIED e concede as recompensas.

    Roda no worker. Separar "pagou" de "qualificou" é o que permite esperar a
    janela de reembolso sem travar nada na experiência do usuário.
    """
    agora = now or dt.datetime.now(dt.timezone.utc)
    limite = agora - dt.timedelta(days=QUALIFY_AFTER_DAYS)
    qualificadas = 0

    pendentes = db.scalars(
        select(Referral).where(
            Referral.status == ReferralStatus.SIGNED_UP.value,
            Referral.qualified_at.is_not(None),
        )
    ).all()
    for registro in pendentes:
        marcada = registro.qualified_at
        if marcada.tzinfo is None:
            marcada = marcada.replace(tzinfo=dt.timezone.utc)
        if marcada > limite:
            continue
        if not _indicado_continua_pagando(db, registro.referred_id):
            registro.status = ReferralStatus.REJECTED.value
            registro.rejection_reason = "assinatura não se manteve"
            continue
        if not _contato_verificado(db, registro.referred_id):
            registro.status = ReferralStatus.REJECTED.value
            registro.rejection_reason = "e-mail do indicado não confirmado"
            continue
        registro.status = ReferralStatus.QUALIFIED.value
        qualificadas += 1
    db.flush()

    concedidas = _conceder_recompensas(db, agora=agora)
    return {"qualificadas": qualificadas, "recompensas": concedidas}


def _indicado_continua_pagando(db: Session, referred_id: str) -> bool:
    sub = db.scalars(
        select(Subscription).where(Subscription.user_id == referred_id)
    ).first()
    if sub is None:
        return False
    return sub.status in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value)


def _conceder_recompensas(db: Session, *, agora: dt.datetime) -> int:
    """Para cada indicador, transforma cada `REFERRAL_GOAL` qualificadas em um mês."""
    concedidas = 0
    indicadores = {
        r.referrer_id
        for r in db.scalars(
            select(Referral).where(Referral.status == ReferralStatus.QUALIFIED.value)
        ).all()
    }
    for referrer_id in indicadores:
        qualificadas = list(db.scalars(
            select(Referral).where(
                Referral.referrer_id == referrer_id,
                Referral.status == ReferralStatus.QUALIFIED.value,
            ).order_by(Referral.qualified_at)
        ).all())
        while len(qualificadas) >= REFERRAL_GOAL:
            lote = qualificadas[:REFERRAL_GOAL]
            qualificadas = qualificadas[REFERRAL_GOAL:]
            if _meses_no_ano(db, referrer_id, agora) >= MAX_FREE_MONTHS_PER_YEAR:
                break
            db.add(Reward(
                user_id=referrer_id,
                kind=RewardKind.FREE_MONTH.value,
                months=1,
                reason=f"{REFERRAL_GOAL} indicações que viraram assinantes",
            ))
            for registro in lote:
                registro.status = ReferralStatus.REWARDED.value
                registro.rewarded_at = agora
            concedidas += 1
    db.flush()
    return concedidas


def _meses_no_ano(db: Session, user_id: str, agora: dt.datetime) -> int:
    desde = agora - dt.timedelta(days=365)
    creditos = db.scalars(
        select(Reward).where(
            Reward.user_id == user_id,
            Reward.kind == RewardKind.FREE_MONTH.value,
            Reward.revoked_at.is_(None),
            Reward.created_at >= desde,
        )
    ).all()
    return sum(c.months for c in creditos)


# --------------------------------------------------------------------------- #
# Aplicar o crédito na assinatura
# --------------------------------------------------------------------------- #
def pending_months(db: Session, user: User) -> int:
    creditos = db.scalars(
        select(Reward).where(
            Reward.user_id == user.id,
            Reward.applied_at.is_(None),
            Reward.revoked_at.is_(None),
        )
    ).all()
    return sum(c.months for c in creditos)


def apply_credits(db: Session, user: User) -> int:
    """Empurra o fim do período pago para frente, um mês por crédito.

    Só faz sentido com assinatura ativa: crédito de quem está no grátis fica
    guardado esperando a primeira assinatura, e é isso que faz a recompensa
    valer para quem indicou antes de assinar.
    """
    from agenda.core import billing

    sub = billing.subscription_of(db, user)
    if sub.plan == "FREE":
        return 0

    creditos = list(db.scalars(
        select(Reward).where(
            Reward.user_id == user.id,
            Reward.applied_at.is_(None),
            Reward.revoked_at.is_(None),
        ).order_by(Reward.created_at)
    ).all())
    if not creditos:
        return 0

    agora = dt.datetime.now(dt.timezone.utc)
    base = sub.current_period_end
    if base is None or (base.replace(tzinfo=dt.timezone.utc) if base.tzinfo is None else base) < agora:
        base = agora
    elif base.tzinfo is None:
        base = base.replace(tzinfo=dt.timezone.utc)

    meses = 0
    for credito in creditos:
        base = base + dt.timedelta(days=30 * credito.months)
        credito.applied_at = agora
        meses += credito.months
    sub.current_period_end = base
    db.flush()
    log(db, user_id=user.id, actor="system", action="REFERRAL_REWARD_APPLIED",
        object_type="subscription", object_id=sub.id, after={"meses": meses})
    return meses


# --------------------------------------------------------------------------- #
# Painel do usuário
# --------------------------------------------------------------------------- #
def peças_de_compartilhamento(resumo: dict, user: User) -> list[dict]:
    """Textos prontos para colar. É o que faz alguém realmente compartilhar.

    Um botão "compartilhe" com campo vazio não é compartilhado por ninguém: a
    pessoa não sabe o que escrever e desiste. Escrever por ela, em português de
    WhatsApp e curto o bastante para caber num status, é a diferença entre um
    programa de indicação que roda e um que fica na tela.
    """
    link = resumo["link"]
    desconto = resumo["desconto_do_convidado"]
    nome = (user.name or "").split(" ")[0]
    return [
        {
            "titulo": "Para o grupo da turma",
            "texto": (
                f"gente, achei um app que monta a agenda da facul sozinho — "
                f"manda foto do quadro ou áudio e ele organiza. "
                f"entrando por aqui vocês ganham {desconto}% no primeiro mês: {link}"
            ),
        },
        {
            "titulo": "Para uma pessoa só",
            "texto": (
                f"lembra que tu vive perdendo prazo? testa isso: manda áudio e ele "
                f"marca tudo. usa meu link que tu ganha {desconto}% de desconto — {link}"
            ),
        },
        {
            "titulo": "Para pais e responsáveis",
            "texto": (
                f"esse app organiza a agenda escolar da criança e manda os lembretes "
                f"no meu celular também. com meu link dá {desconto}% de desconto: {link}"
            ),
        },
        {
            "titulo": "Para o status",
            "texto": f"parei de esquecer prova. {link}" + (f" — {nome}" if nome else ""),
        },
    ]


def summary(db: Session, user: User) -> dict:
    codigo = code_for(db, user)
    indicacoes = list(db.scalars(
        select(Referral).where(Referral.referrer_id == user.id)
        .order_by(Referral.created_at.desc())
    ).all())

    contagem = {status.value: 0 for status in ReferralStatus}
    for registro in indicacoes:
        contagem[registro.status] = contagem.get(registro.status, 0) + 1

    valendo = contagem[ReferralStatus.QUALIFIED.value]
    faltam = max(0, REFERRAL_GOAL - (valendo % REFERRAL_GOAL)) if REFERRAL_GOAL else 0
    if valendo and valendo % REFERRAL_GOAL == 0:
        faltam = REFERRAL_GOAL

    return {
        "codigo": codigo,
        "link": share_url(codigo),
        "meta": REFERRAL_GOAL,
        "convidados": len(indicacoes),
        "entraram": contagem[ReferralStatus.SIGNED_UP.value],
        "valendo": valendo,
        "recompensados": contagem[ReferralStatus.REWARDED.value],
        "faltam_para_o_proximo": faltam,
        "meses_a_receber": pending_months(db, user),
        "desconto_do_convidado": int(INVITEE_DISCOUNT * 100),
    }
