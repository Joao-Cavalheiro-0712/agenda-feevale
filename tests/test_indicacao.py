"""Programa de indicação: atribuição, recompensa e as fraudes que ele resiste.

A defesa central deste programa não é técnica, é econômica: a recompensa só
nasce quando o indicado **paga e passa da janela de reembolso**. Estes testes
existem para garantir que essa ordem nunca seja invertida por um refactor —
inverter é o que transformaria o programa numa torneira de dinheiro.
"""
from __future__ import annotations

import datetime as dt

from agenda.core import billing, referrals
from agenda.models import PlanTier, Referral, ReferralStatus, Reward, User
from agenda.security import hash_password
from tests.test_cenarios import _csrf


def _pessoa(db, email: str, nome: str = "Alguém") -> User:
    pessoa = User(
        name=nome, email=email, password_hash=hash_password("senhaforte123"),
        onboarding_done=True, birth_year=1999,
        accepted_terms_version="2026-09-03", accepted_privacy_version="2026-09-03",
        # E-mail confirmado é o estado normal de quem chegou a pagar — e a
        # qualificação da indicação exige isso (antifraude).
        email_verified_at=dt.datetime.now(dt.timezone.utc),
    )
    db.add(pessoa)
    db.flush()
    return pessoa


def _assinar(db, pessoa: User, plano: str = PlanTier.STUDENT.value):
    billing.change_plan(db, pessoa, plano)
    referrals.mark_paid(db, pessoa)
    db.flush()


# --------------------------------------------------------------------------- #
# Código
# --------------------------------------------------------------------------- #
def test_codigo_e_unico_legivel_e_estavel(db, user):
    codigo = referrals.code_for(db, user)
    assert len(codigo) == referrals.TAMANHO_DO_CODIGO
    assert codigo == referrals.code_for(db, user), "o código não pode mudar"
    # Sem caracteres que se confundem quando alguém dita o código.
    assert not set(codigo) & set("OI015LS")


def test_codigos_de_pessoas_diferentes_nao_colidem(db, user):
    outra = _pessoa(db, "outra@example.com")
    assert referrals.code_for(db, user) != referrals.code_for(db, outra)


# --------------------------------------------------------------------------- #
# Atribuição
# --------------------------------------------------------------------------- #
def test_quem_entra_pelo_codigo_fica_atribuido(db, user):
    codigo = referrals.code_for(db, user)
    convidado = _pessoa(db, "convidado@example.com")

    registro = referrals.attribute(db, convidado, codigo, ip="200.1.1.1")
    db.commit()
    assert registro is not None
    assert registro.referrer_id == user.id
    assert registro.status == ReferralStatus.SIGNED_UP.value
    assert registro.signup_ip_hash and "200.1.1.1" not in registro.signup_ip_hash


def test_ninguem_indica_a_si_mesmo(db, user):
    codigo = referrals.code_for(db, user)
    assert referrals.attribute(db, user, codigo) is None


def test_atribuicao_nao_pode_ser_trocada_depois(db, user):
    """Senão dava para revender a mesma indicação para vários indicadores."""
    primeiro = referrals.code_for(db, user)
    outro = _pessoa(db, "outro@example.com")
    segundo = referrals.code_for(db, outro)
    convidado = _pessoa(db, "convidado@example.com")

    referrals.attribute(db, convidado, primeiro)
    db.commit()
    referrals.attribute(db, convidado, segundo)
    db.commit()

    registros = db.query(Referral).filter_by(referred_id=convidado.id).all()
    assert len(registros) == 1
    assert registros[0].referrer_id == user.id


def test_codigo_inexistente_nao_quebra_o_cadastro(db):
    convidado = _pessoa(db, "convidado@example.com")
    assert referrals.attribute(db, convidado, "NAOEXISTE") is None


def test_mesmo_ip_e_registrado_mas_nao_bloqueia(db, user):
    """Mãe indicando o filho do wi-fi de casa é o caso de uso, não a fraude."""
    codigo = referrals.code_for(db, user)
    filho = _pessoa(db, "filho@example.com")
    registro = referrals.attribute(db, filho, codigo, ip="192.168.0.10")
    db.commit()
    assert registro is not None
    assert registro.status == ReferralStatus.SIGNED_UP.value


# --------------------------------------------------------------------------- #
# A regra que sustenta o programa
# --------------------------------------------------------------------------- #
def test_cadastro_sozinho_nao_gera_recompensa(db, user):
    """Se cadastro valesse recompensa, dez e-mails descartáveis viravam dinheiro."""
    codigo = referrals.code_for(db, user)
    for i in range(referrals.REFERRAL_GOAL + 2):
        convidado = _pessoa(db, f"gratis{i}@example.com")
        referrals.attribute(db, convidado, codigo)
    db.commit()

    referrals.run_qualification(db)
    db.commit()
    assert referrals.pending_months(db, user) == 0
    assert db.query(Reward).filter_by(user_id=user.id).count() == 0


def test_pagamento_recente_ainda_nao_qualifica(db, user):
    """A carência existe porque reembolso e chargeback vêm depois do pagamento."""
    codigo = referrals.code_for(db, user)
    for i in range(referrals.REFERRAL_GOAL):
        convidado = _pessoa(db, f"pagante{i}@example.com")
        referrals.attribute(db, convidado, codigo)
        _assinar(db, convidado)
    db.commit()

    referrals.run_qualification(db)
    db.commit()
    assert referrals.pending_months(db, user) == 0


def test_meta_batida_apos_a_carencia_vira_mes_gratis(db, user):
    codigo = referrals.code_for(db, user)
    for i in range(referrals.REFERRAL_GOAL):
        convidado = _pessoa(db, f"pagante{i}@example.com")
        referrals.attribute(db, convidado, codigo)
        _assinar(db, convidado)
    db.commit()

    futuro = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        days=referrals.QUALIFY_AFTER_DAYS + 1
    )
    resultado = referrals.run_qualification(db, now=futuro)
    db.commit()

    assert resultado["qualificadas"] == referrals.REFERRAL_GOAL
    assert resultado["recompensas"] == 1
    assert referrals.pending_months(db, user) == 1


def test_reembolso_do_indicado_desfaz_a_recompensa(db, user):
    codigo = referrals.code_for(db, user)
    convidados = []
    for i in range(referrals.REFERRAL_GOAL):
        convidado = _pessoa(db, f"pagante{i}@example.com")
        referrals.attribute(db, convidado, codigo)
        _assinar(db, convidado)
        convidados.append(convidado)
    db.commit()
    futuro = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=9)
    referrals.run_qualification(db, now=futuro)
    db.commit()
    assert referrals.pending_months(db, user) == 1

    referrals.revoke_for(db, convidados[0], reason="reembolso")
    db.commit()
    assert referrals.pending_months(db, user) == 0, "recompensa não foi revogada"


def test_indicado_que_cancela_antes_da_carencia_nao_conta(db, user):
    codigo = referrals.code_for(db, user)
    convidado = _pessoa(db, "sumiu@example.com")
    referrals.attribute(db, convidado, codigo)
    _assinar(db, convidado)
    db.commit()

    billing.cancel(db, convidado)
    db.commit()

    referrals.run_qualification(
        db, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=9)
    )
    db.commit()
    registro = referrals.referral_of(db, convidado)
    assert registro.status == ReferralStatus.REJECTED.value


def test_existe_teto_anual_de_meses_gratis(db, user):
    """Nem caso extremo nem bug podem virar assinatura eterna de graça."""
    codigo = referrals.code_for(db, user)
    total = referrals.REFERRAL_GOAL * (referrals.MAX_FREE_MONTHS_PER_YEAR + 3)
    for i in range(total):
        convidado = _pessoa(db, f"muitos{i}@example.com")
        referrals.attribute(db, convidado, codigo)
        _assinar(db, convidado)
    db.commit()

    referrals.run_qualification(
        db, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=9)
    )
    db.commit()
    assert referrals.pending_months(db, user) <= referrals.MAX_FREE_MONTHS_PER_YEAR


# --------------------------------------------------------------------------- #
# Aplicação do crédito
# --------------------------------------------------------------------------- #
def test_credito_estende_o_periodo_pago(db, user):
    db.add(Reward(user_id=user.id, months=1, reason="teste"))
    billing.change_plan(db, user, PlanTier.STUDENT.value)
    db.commit()
    antes = billing.subscription_of(db, user).current_period_end

    aplicados = referrals.apply_credits(db, user)
    db.commit()
    depois = billing.subscription_of(db, user).current_period_end
    assert aplicados == 1
    assert (depois - antes).days >= 29


def test_credito_de_quem_esta_no_gratis_fica_guardado(db, user):
    """Quem indica antes de assinar não pode perder a recompensa."""
    db.add(Reward(user_id=user.id, months=1, reason="teste"))
    db.commit()
    assert referrals.apply_credits(db, user) == 0
    assert referrals.pending_months(db, user) == 1


def test_credito_nao_e_aplicado_duas_vezes(db, user):
    db.add(Reward(user_id=user.id, months=1, reason="teste"))
    billing.change_plan(db, user, PlanTier.STUDENT.value)
    db.commit()
    assert referrals.apply_credits(db, user) == 1
    db.commit()
    assert referrals.apply_credits(db, user) == 0


# --------------------------------------------------------------------------- #
# O caminho pelo navegador
# --------------------------------------------------------------------------- #
def test_link_de_convite_atribui_no_cadastro(app, db, user):
    codigo = referrals.code_for(db, user)
    db.commit()

    client = app.test_client()
    assert client.get(f"/i/{codigo}").status_code == 302
    client.get("/criar-conta")
    resposta = client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Vinda do link",
        "email": "dolink@example.com", "password": "senhaforte123",
        "birth_year": "2002", "accept_terms": "on",
    })
    assert resposta.status_code == 302

    convidado = db.query(User).filter_by(email="dolink@example.com").first()
    registro = referrals.referral_of(db, convidado)
    assert registro is not None and registro.referrer_id == user.id


def test_codigo_invalido_no_link_nao_impede_o_cadastro(app, db):
    client = app.test_client()
    client.get("/i/ZZZZZZ")
    client.get("/criar-conta")
    resposta = client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Sem indicação",
        "email": "sem@example.com", "password": "senhaforte123",
        "birth_year": "2002", "accept_terms": "on",
    })
    assert resposta.status_code == 302
    assert db.query(User).filter_by(email="sem@example.com").first() is not None


def test_cookie_de_indicacao_e_assinado(app, db, user):
    """Cookie forjado não pode inventar atribuição."""
    from agenda.web.pages import REFERRAL_COOKIE

    codigo = referrals.code_for(db, user)
    db.commit()

    client = app.test_client()
    client.set_cookie(REFERRAL_COOKIE, f'{{"c":"{codigo}"}}')  # sem assinatura
    client.get("/criar-conta")
    client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Forjado", "email": "forjado@example.com",
        "password": "senhaforte123", "birth_year": "2002", "accept_terms": "on",
    })
    convidado = db.query(User).filter_by(email="forjado@example.com").first()
    assert referrals.referral_of(db, convidado) is None


def test_tela_de_convite_traz_peca_pronta(app, db, user):
    client = app.test_client()
    client.get("/entrar")
    client.post("/entrar", data={"csrf_token": _csrf(client), "email": user.email,
                                 "password": "segredo123"})
    corpo = client.get("/convidar").get_data(as_text=True)
    assert "Copiar meu link" in corpo
    assert "Mensagens prontas" in corpo
    # O código foi criado dentro da requisição; a sessão do teste precisa
    # reler para não gerar um segundo por engano.
    db.expire_all()
    assert db.get(User, user.id).referral_code in corpo


def test_indicacao_de_outra_pessoa_nao_aparece_no_meu_painel(db, user):
    outro = _pessoa(db, "outro@example.com")
    codigo_do_outro = referrals.code_for(db, outro)
    convidado = _pessoa(db, "convidado@example.com")
    referrals.attribute(db, convidado, codigo_do_outro)
    db.commit()

    assert referrals.summary(db, user)["convidados"] == 0
    assert referrals.summary(db, outro)["convidados"] == 1


def test_estorno_revoga_recompensa_ja_aplicada(db, user):
    """Regressão: a carência é de 8 dias e o chargeback vem meses depois.

    Filtrar só recompensas não aplicadas deixava o golpe passar inteiro —
    quando o estorno chegava, o crédito já tinha virado tempo de assinatura e
    ficava invisível para a revogação.
    """
    codigo = referrals.code_for(db, user)
    convidados = []
    for i in range(referrals.REFERRAL_GOAL):
        convidado = _pessoa(db, f"pagante{i}@example.com")
        referrals.attribute(db, convidado, codigo)
        _assinar(db, convidado)
        convidados.append(convidado)
    db.commit()
    referrals.run_qualification(
        db, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=9)
    )
    db.commit()

    # Quem indicou assina e o crédito é APLICADO no período dele.
    billing.change_plan(db, user, PlanTier.STUDENT.value)
    db.commit()
    assert referrals.apply_credits(db, user) == 1
    db.commit()
    com_bonus = billing.subscription_of(db, user).current_period_end

    # Meses depois, o indicado contesta a cobrança.
    referrals.revoke_for(db, convidados[0], reason="charge.dispute.created")
    db.commit()

    credito = db.query(Reward).filter_by(user_id=user.id).first()
    assert credito.revoked_at is not None, "recompensa aplicada não foi revogada"
    sem_bonus = billing.subscription_of(db, user).current_period_end
    assert sem_bonus < com_bonus, "o tempo concedido pela indicação não foi devolvido"


def test_devolver_tempo_nunca_corta_o_que_a_pessoa_pagou(db, user):
    """Revogar a indicação não pode encurtar o período que ela mesma comprou."""
    db.add(Reward(user_id=user.id, months=1, reason="teste"))
    billing.change_plan(db, user, PlanTier.STUDENT.value)
    db.commit()
    referrals.apply_credits(db, user)
    db.commit()

    referrals._devolver_tempo(
        db, user.id, meses=99, agora=dt.datetime.now(dt.timezone.utc)
    )
    db.commit()
    fim = billing.subscription_of(db, user).current_period_end
    if fim.tzinfo is None:
        fim = fim.replace(tzinfo=dt.timezone.utc)
    assert fim >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
