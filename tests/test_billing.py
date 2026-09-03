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
    # Não fixa o número: o limite é decisão de negócio e muda. O que o teste
    # protege é a FORMA da escada — cada plano pago cabe mais que o anterior.
    assert billing.limit_of(db, user, billing.MAX_DOCUMENT_IMPORTS) > 3


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


def test_captura_resolvida_localmente_nao_consome_quota(client, db, user):
    """A quota segue o CUSTO, não o clique.

    A base de conhecimento local resolve a maior parte das mensagens sem sair
    daqui. Cobrar quota por algo que não gastou nada seria cobrar duas vezes
    pelo mesmo produto — e é o oposto do que o limite existe para fazer.
    """
    antes = billing.usage(db, user, "ai_messages")
    resposta = client.post("/api/capture", json={"text": "prova de história sexta"},
                           headers={"X-CSRF-Token": _csrf(client)})
    assert resposta.status_code == 200
    db.expire_all()
    assert billing.usage(db, user, "ai_messages") == antes


def test_quota_estourada_bloqueia_antes_de_interpretar(client, db, user):
    """Mesmo sem custo por mensagem, o teto do plano continua valendo."""
    limite = billing.limit_of(db, user, billing.MAX_AI_MESSAGES)
    billing.consume(db, user, "ai_messages", amount=limite)
    db.commit()

    resposta = client.post("/api/capture", json={"text": "prova de história sexta"},
                           headers={"X-CSRF-Token": _csrf(client)})
    assert resposta.status_code == 402
    assert resposta.get_json()["status"] == "QUOTA"


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


# --------------------------------------------------------------------------- #
# A escada de planos: forma, ciclo e sustentação
# --------------------------------------------------------------------------- #
def test_a_escada_e_monotonica(db, user):
    """Plano mais caro nunca pode caber menos que o mais barato."""
    from agenda.models import PlanTier as T

    escada = [T.FREE.value, T.STUDENT.value, T.PRO.value, T.FAMILY.value]
    quotas = (billing.MAX_AI_MESSAGES, billing.MAX_DOCUMENT_IMPORTS,
              billing.MAX_AUDIO_MINUTES, billing.MAX_CONTEXTS)
    for quota in quotas:
        valores = [billing.PLANS[p].features[quota] for p in escada]
        assert valores == sorted(valores), f"{quota} não é crescente: {valores}"


def test_precos_sao_os_combinados():
    from agenda.models import PlanTier as T

    assert billing.PLANS[T.STUDENT.value].price_month == 19.90
    assert billing.PLANS[T.PRO.value].price_month == 29.90
    assert billing.PLANS[T.FAMILY.value].price_month == 39.90
    assert billing.PLANS[T.FREE.value].price_month == 0.0


def test_anual_da_vinte_por_cento_de_desconto():
    plano = billing.PLANS["STUDENT"]
    assert plano.price_annual == round(19.90 * 12 * 0.8, 2)
    assert plano.price_annual_monthly < plano.price_month
    assert plano.annual_savings > 0


def test_ciclo_anual_estende_o_periodo(db, user):
    from agenda.models import BillingCycle, PlanTier as T

    sub = billing.change_plan(db, user, T.PRO.value, cycle=BillingCycle.ANNUAL.value)
    db.commit()
    assert sub.cycle == BillingCycle.ANNUAL.value
    restantes = (sub.current_period_end.replace(tzinfo=None) - __import__("datetime").datetime.utcnow()).days
    assert restantes > 300, "assinatura anual tem de valer o ano"


def test_mediana_paga_a_conta_em_todo_plano_pago():
    """A margem agregada se decide na mediana, não na cauda."""
    for linha in billing.margin_report():
        if linha["preco_mes"] == 0:
            continue
        assert linha["percentual_mediano"] < 25, (
            f"{linha['plano']}: o assinante mediano consome "
            f"{linha['percentual_mediano']}% do preço em IA"
        )


def test_a_cauda_tem_teto(db, user):
    """Quota é o que limita o prejuízo de uma conta abusiva.

    Sem teto, uma conta só poderia gerar custo ilimitado. Com teto, o pior
    caso por conta é conhecido — e é isso que torna a cauda aceitável.
    """
    for linha in billing.margin_report():
        if linha["preco_mes"] == 0:
            continue
        prejuizo_maximo = linha["custo_pior_caso"] - linha["preco_mes"]
        assert prejuizo_maximo < linha["preco_mes"], (
            f"{linha['plano']}: uma conta na cauda custa mais que o dobro do preço"
        )


def test_audio_e_medido_e_arredonda_para_cima():
    assert billing.estimate_audio_minutes(0) == 0
    assert billing.estimate_audio_minutes(1000) == 1, "áudio curto conta 1 minuto"
    # 16 kbps ≈ 2 KB/s ≈ 120 KB por minuto.
    assert billing.estimate_audio_minutes(120_000) == 1
    assert billing.estimate_audio_minutes(600_000) == 5


def test_quota_de_audio_bloqueia_transcricao(app, db, user):
    from agenda.models import PlanTier as T

    billing.change_plan(db, user, T.FREE.value)
    limite = billing.limit_of(db, user, billing.MAX_AUDIO_MINUTES)
    billing.consume(db, user, "audio_minutes", amount=limite)
    db.commit()

    client = app.test_client()
    client.get("/entrar")
    with client.session_transaction() as sessao:
        token = sessao.get("csrf")
    client.post("/entrar", data={"csrf_token": token, "email": user.email,
                                 "password": "segredo123"})
    with client.session_transaction() as sessao:
        token = sessao.get("csrf")
    resposta = client.post(
        "/api/capture",
        data={"audio": (__import__("io").BytesIO(b"x" * 300_000), "a.webm")},
        headers={"X-CSRF-Token": token}, content_type="multipart/form-data",
    )
    assert resposta.status_code == 402, "quota de áudio não bloqueou"
