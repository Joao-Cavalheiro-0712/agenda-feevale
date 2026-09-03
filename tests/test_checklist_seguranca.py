"""As 20 falhas clássicas de SaaS, transformadas em teste.

Este arquivo existe para que "cuidamos da segurança" deixe de ser promessa e
vire coisa verificável a cada commit. Cada teste corresponde a um item das duas
listas de vulnerabilidades comuns, e cada um **prova** o comportamento contra o
app rodando — não afirma por leitura de código.

Se alguém introduzir uma dessas falhas, a suíte quebra antes do deploy.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from agenda.core import academic, events as events_core
from agenda.models import PlanTier, User
from agenda.security import hash_password
from tests.test_cenarios import _csrf


RAIZ = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def outro(app, db):
    """Uma segunda conta, com dados próprios, para os testes de isolamento."""
    pessoa = User(
        name="Vítima", email="vitima@example.com",
        password_hash=hash_password("senhaforte123"), onboarding_done=True,
        birth_year=1995, accepted_terms_version="2026-09-03",
        accepted_privacy_version="2026-09-03",
    )
    db.add(pessoa)
    db.flush()
    from agenda.models import EducationContext

    contexto = EducationContext(user_id=pessoa.id, type="UNDERGRAD", is_active=True)
    db.add(contexto)
    db.flush()
    materia = academic.upsert_subject(db, pessoa.id, contexto.id, "Segredo")
    evento = events_core.create_event(
        db, pessoa, title="Prova confidencial", event_type="EXAM",
        date=__import__("datetime").date.today() + __import__("datetime").timedelta(days=3),
        subject=materia,
    )
    db.commit()
    return {"user": pessoa, "materia": materia, "evento": evento}


# --------------------------------------------------------------------------- #
# 1. Formulário sem validação no servidor
# --------------------------------------------------------------------------- #
def test_1_servidor_valida_o_que_o_formulario_valida(app, db):
    """O `required` e o `minlength` do HTML são sugestão, não defesa.

    Um `curl` ignora os dois. O servidor tem de recusar sozinho.
    """
    client = app.test_client()
    client.get("/criar-conta")
    token = _csrf(client)

    # Senha curta (o HTML pede minlength=10), sem aceite, sem ano, e-mail inválido.
    for dados in (
        {"email": "curta@example.com", "password": "123", "birth_year": "1990",
         "accept_terms": "on"},
        {"email": "sem-aceite@example.com", "password": "senhaforte123",
         "birth_year": "1990"},
        {"email": "sem-ano@example.com", "password": "senhaforte123",
         "accept_terms": "on"},
        {"email": "isso nao e email", "password": "senhaforte123",
         "birth_year": "1990", "accept_terms": "on"},
    ):
        client.post("/criar-conta", data={"csrf_token": token, **dados})

    assert db.query(User).filter(User.email.like("%example.com")).count() == 0, (
        "o servidor aceitou dado que só o HTML barrava"
    )


# --------------------------------------------------------------------------- #
# 2. Rotas protegidas apenas no frontend
# --------------------------------------------------------------------------- #
def test_2_nenhuma_rota_do_app_responde_sem_sessao(app):
    """Esconder o link no menu não protege nada: o teste bate direto na rota."""
    client = app.test_client()
    protegidas = [
        "/hoje", "/semana", "/mes", "/agenda", "/entregas", "/materias",
        "/documentos", "/assistente", "/perfil", "/planos", "/convidar",
        "/familia", "/conta/seguranca", "/conta/privacidade", "/periodos",
        "/plano-de-estudo", "/notificacoes", "/buscar", "/admin",
        "/api/planner/today", "/api/planner/week", "/api/export",
        "/api/notifications", "/api/plan",
        # Superfície nova: exportação, tour e a área de chaves de acesso.
        "/conta/meus-dados.json", "/apresentacao",
    ]
    vazadas = []
    for caminho in protegidas:
        resposta = client.get(caminho)
        if resposta.status_code == 200:
            vazadas.append(caminho)
    assert not vazadas, f"rotas abertas sem login: {vazadas}"


def test_2b_escrita_tambem_exige_sessao(app):
    client = app.test_client()
    for caminho in ("/api/capture", "/api/events"):
        resposta = client.post(caminho, json={})
        assert resposta.status_code in (302, 401, 403, 404), f"{caminho} aceitou anônimo"
    for caminho in ("/planos/assinar", "/familia/convidar", "/conta/excluir",
                    "/conta/reenviar-email", "/conta/telefone/codigo",
                    "/conta/telefone/confirmar", "/apresentacao/pronto",
                    "/apresentacao/rever", "/api/passkey/cadastro",
                    "/api/passkey/cadastro/opcoes"):
        resposta = client.post(caminho, data={})
        assert resposta.status_code in (302, 401, 403, 404), f"{caminho} aceitou anônimo"


# --------------------------------------------------------------------------- #
# 3. Chaves e segredos expostos no código
# --------------------------------------------------------------------------- #
def test_3_nenhum_segredo_literal_no_codigo():
    padrao = re.compile(
        r"(sk_live_|sk_test_[A-Za-z0-9]{10,}|pk_live_|AIza[A-Za-z0-9_-]{30,}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY)"
    )
    achados = []
    for arquivo in list((RAIZ / "agenda").rglob("*.py")) + \
            list((RAIZ / "agenda").rglob("*.html")) + \
            list((RAIZ / "agenda").rglob("*.js")):
        texto = arquivo.read_text(encoding="utf-8", errors="ignore")
        if padrao.search(texto):
            achados.append(str(arquivo.relative_to(RAIZ)))
    assert not achados, f"possível segredo literal em: {achados}"


def test_3b_nenhum_segredo_chega_ao_navegador(app, db, client):
    """Nada do servidor pode aparecer no HTML entregue ao usuário."""
    from agenda import config

    corpo = client.get("/perfil").get_data(as_text=True)
    for segredo in (config.SECRET_KEY, config.GEMINI_API_KEY,
                    config.VAPID_PRIVATE_KEY, config.STRIPE_SECRET_KEY,
                    config.WHATSAPP_TOKEN):
        if segredo and len(segredo) > 8:
            assert segredo not in corpo


# --------------------------------------------------------------------------- #
# 4 e 12. Autorização por usuário — IDOR / BOLA
# --------------------------------------------------------------------------- #
def test_4_leitura_de_recurso_alheio_e_negada(client, outro):
    for caminho in (
        f"/evento/{outro['evento'].id}",
        f"/materias/{outro['materia'].id}",
        f"/materias/{outro['materia'].id}/notas",
        f"/api/events/{outro['evento'].id}",
        f"/familia/{outro['user'].id}/agenda",
    ):
        assert client.get(caminho).status_code == 404, f"{caminho} vazou"


def test_4b_escrita_em_recurso_alheio_e_negada(client, db, outro):
    token = _csrf(client)
    cabecalho = {"X-CSRF-Token": token}
    client.patch(f"/api/events/{outro['evento'].id}",
                 json={"title": "invadido"}, headers=cabecalho)
    client.delete(f"/api/events/{outro['evento'].id}", headers=cabecalho)
    client.post(f"/materias/{outro['materia'].id}/editar",
                data={"csrf_token": token, "name": "invadida"})
    db.expire_all()
    assert db.get(type(outro["evento"]), outro["evento"].id).title == "Prova confidencial"
    assert db.get(type(outro["materia"]), outro["materia"].id).name == "Segredo"


def test_4c_o_registro_de_propriedade_falha_fechado():
    """Modelo sem regra declarada nega acesso em vez de vazar."""
    from agenda.core import scope

    class ModeloNaoDeclarado:
        pass

    assert scope.get(None, ModeloNaoDeclarado, "qualquer", "usuario") is None
    with pytest.raises(scope.AccessDenied):
        scope.query(ModeloNaoDeclarado, "usuario")


# --------------------------------------------------------------------------- #
# 5 e 15. Endpoints sem limite de requisições
# --------------------------------------------------------------------------- #
def test_5_rotas_sensiveis_tem_limite_declarado():
    from agenda import config

    for balde in ("login", "register", "assistant", "upload", "export",
                  "share", "webhook", "referral", "checkout",
                  # Portas de entrada novas: cada uma é um alvo de varredura.
                  "oidc", "passkey", "recover", "verify"):
        teto, janela = config.RATE_LIMITS[balde]
        assert teto > 0 and janela > 0, f"balde {balde} sem limite"


def test_5b_o_limite_de_login_realmente_bloqueia(app):
    from agenda import config

    client = app.test_client()
    client.get("/entrar")
    teto = config.RATE_LIMITS["login"][0]
    respostas = [
        client.post("/entrar", data={"csrf_token": _csrf(client),
                                     "email": "x@y.com", "password": "errada"})
        for _ in range(teto + 2)
    ]
    assert any(r.status_code == 429 for r in respostas), "força bruta não foi barrada"


# --------------------------------------------------------------------------- #
# 6. Upload de arquivos sem validação
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("nome,conteudo", [
    ("virus.exe", b"MZ\x90\x00executavel"),
    ("script.pdf", b"<?php system($_GET['c']); ?>"),
    ("shell.pdf", b"#!/bin/sh\nrm -rf /"),
    ("falso.pdf", b"isto nao e um pdf de verdade"),
    ("pagina.html", b"<script>alert(1)</script>"),
    ("../../../etc/passwd", b"%PDF-1.4"),
])
def test_6_upload_perigoso_e_recusado(nome, conteudo):
    from agenda.ingest import pipeline

    with pytest.raises(pipeline.UploadError):
        pipeline.validate_upload(nome, conteudo)


def test_6b_caminho_de_arquivo_nunca_sai_da_pasta_do_usuario():
    from agenda.ingest import pipeline

    with pytest.raises(pipeline.UploadError):
        pipeline.store_file("../../etc", "passwd", b"%PDF-1.4", ".pdf")


# --------------------------------------------------------------------------- #
# 7. Dados sensíveis expostos nas respostas da API
# --------------------------------------------------------------------------- #
def test_7_a_api_nao_devolve_hash_de_senha_nem_token(client):
    for caminho in ("/api/export", "/api/plan", "/api/planner/today",
                    "/api/notifications"):
        corpo = client.get(caminho).get_data(as_text=True).lower()
        for proibido in ("password_hash", "token_hash", "scrypt:", "ip_hash",
                         "secret", "vapid_private"):
            assert proibido not in corpo, f"{caminho} devolveu {proibido}"


# --------------------------------------------------------------------------- #
# 8 e 19. XSS armazenado e refletido
# --------------------------------------------------------------------------- #
def test_8_script_no_titulo_sai_escapado(client, db, user):
    contexto = academic.active_context(db, user.id)
    materia = academic.upsert_subject(db, user.id, contexto.id, "Matéria")
    events_core.create_event(
        db, user, title="<script>alert(document.cookie)</script>",
        event_type="EXAM",
        date=__import__("datetime").date.today() + __import__("datetime").timedelta(days=1),
        subject=materia,
    )
    db.commit()

    corpo = client.get("/agenda").get_data(as_text=True)
    assert "<script>alert(document.cookie)</script>" not in corpo
    assert "&lt;script&gt;" in corpo, "o título deveria aparecer escapado"


def test_8b_busca_refletida_e_escapada(client):
    corpo = client.get('/buscar?q=<img src=x onerror=alert(1)>').get_data(as_text=True)
    assert "<img src=x onerror=alert(1)>" not in corpo


def test_8c_a_csp_bloqueia_script_injetado(client):
    csp = client.get("/hoje").headers["Content-Security-Policy"]
    assert "script-src" in csp
    trecho = csp.split("script-src")[1].split(";")[0]
    assert "'unsafe-inline'" not in trecho, "CSP com unsafe-inline não protege de XSS"
    assert "'unsafe-eval'" not in trecho
    assert "nonce-" in trecho


# --------------------------------------------------------------------------- #
# 9. CORS aberto demais
# --------------------------------------------------------------------------- #
def test_9_nao_existe_cors_permissivo(client):
    for caminho in ("/hoje", "/api/plan", "/api/export"):
        cabecalhos = client.get(caminho, headers={"Origin": "https://evil.com"}).headers
        assert "Access-Control-Allow-Origin" not in cabecalhos, (
            f"{caminho} responde a origem externa"
        )


# --------------------------------------------------------------------------- #
# 10. Mensagens de erro revelando informações internas
# --------------------------------------------------------------------------- #
def test_10_erro_interno_nao_vaza_stack_trace(app, monkeypatch, client):
    from agenda.web import pages

    def explode(*_a, **_k):
        raise RuntimeError("senha do banco: postgres://user:senha@host/db")

    monkeypatch.setattr(pages.planner, "today_view", explode)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    resposta = client.get("/hoje")
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 500
    for vazamento in ("Traceback", "postgres://", "RuntimeError", "File \""):
        assert vazamento not in corpo, f"a página de erro mostrou {vazamento}"


def test_10b_login_nao_diz_se_o_email_existe(app, user):
    client = app.test_client()
    client.get("/entrar")
    inexistente = client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": "nao-existe@example.com",
        "password": "qualquer",
    }).get_data(as_text=True)
    existente = client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": user.email, "password": "errada",
    }).get_data(as_text=True)
    assert "E-mail ou senha incorretos" in inexistente
    assert "E-mail ou senha incorretos" in existente


# --------------------------------------------------------------------------- #
# 14. Token assinado: assinatura, expiração e claims
# --------------------------------------------------------------------------- #
def test_14_token_assinado_verifica_assinatura_e_expiracao():
    import time

    from agenda.security import sign_payload, verify_payload

    token = sign_payload({"u": "abc"}, ttl_seconds=60)
    assert verify_payload(token)["u"] == "abc"

    # Adulterado.
    corpo, _, assinatura = token.partition(".")
    assert verify_payload(f"{corpo}x.{assinatura}") is None
    assert verify_payload(f"{corpo}.{assinatura}x") is None
    # Sem assinatura nenhuma.
    assert verify_payload(corpo) is None
    # Expirado.
    vencido = sign_payload({"u": "abc"}, ttl_seconds=-1)
    assert verify_payload(vencido) is None
    # Sem exp declarado não passa (o padrão é 0, que já venceu).
    assert verify_payload("eyJ1IjogImFiYyJ9.qualquer") is None
    assert time.time() > 0


# --------------------------------------------------------------------------- #
# 6 (lista 2). Mass assignment
# --------------------------------------------------------------------------- #
def test_16_campos_de_servidor_nao_mudam_por_formulario(client, db, user):
    """Mandar `is_admin`, `plan` ou `id` no form não pode surtir efeito."""
    token = _csrf(client)
    client.post("/perfil", data={
        "csrf_token": token, "name": "Novo Nome",
        "is_admin": "true", "is_minor": "true", "birth_year": "1800",
        "referral_code": "HACKED", "ai_processing_enabled": "false",
        "id": "outro-id",
    })
    db.expire_all()
    atual = db.get(User, user.id)
    assert atual.name == "Novo Nome", "o campo legítimo deveria ter mudado"
    assert atual.is_admin is False
    assert atual.is_minor is False
    assert atual.referral_code != "HACKED"


def test_16b_plano_nao_muda_por_campo_escondido(client, db, user):
    from agenda.core import billing

    client.post("/api/capture", json={"text": "oi", "plan": "FAMILY"},
                headers={"X-CSRF-Token": _csrf(client)})
    db.expire_all()
    assert billing.active_plan(db, db.get(User, user.id)).tier == PlanTier.FREE.value


# --------------------------------------------------------------------------- #
# 7 (lista 2). Webhooks sem assinatura
# --------------------------------------------------------------------------- #
def test_17_todo_webhook_exige_prova_de_origem(app):
    client = app.test_client()
    assert client.post("/webhooks/pagamento", json={"type": "invoice.paid"}).status_code == 403
    assert client.post(
        "/webhooks/telegram", json={"message": {"chat": {"id": "1"}, "text": "oi"}}
    ).status_code in (403, 200)  # 200 só em desenvolvimento sem segredo configurado


def test_17b_em_producao_webhook_sem_segredo_falha_fechado(monkeypatch):
    from agenda import config
    from agenda.channels import telegram

    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "")
    assert telegram.valid_secret(None) is False
    assert telegram.valid_secret("chute") is False


# --------------------------------------------------------------------------- #
# 8 (lista 2). SQL injection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload", [
    "'; DROP TABLE users; --",
    "' OR '1'='1",
    "\" UNION SELECT password_hash FROM users --",
])
def test_18_injecao_de_sql_nao_afeta_nada(client, db, payload):
    antes = db.query(User).count()
    client.get(f"/buscar?q={payload}")
    client.post("/api/capture", json={"text": payload},
                headers={"X-CSRF-Token": _csrf(client)})
    db.expire_all()
    assert db.query(User).count() == antes


# --------------------------------------------------------------------------- #
# 10 (lista 2). SSRF
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("endereco", [
    "https://169.254.169.254/latest/meta-data/",
    "https://interno.railway.internal:8443/admin",
    "http://fcm.googleapis.com/x",
    "https://fcm.googleapis.com.evil.com/x",
    "https://localhost/x",
    "https://127.0.0.1/x",
])
def test_20_endpoint_de_push_recusa_endereco_arbitrario(endereco):
    from agenda.channels import push

    assert push.endpoint_permitido(endereco) is False


def test_20b_midia_do_whatsapp_so_de_host_oficial():
    from agenda.channels.whatsapp import _media_url_permitida

    assert _media_url_permitida("https://lookaside.fbsbx.com/x") is True
    assert _media_url_permitida("https://lookaside.fbsbx.com.evil.com/x") is False
    assert _media_url_permitida("http://lookaside.fbsbx.com/x") is False
    assert _media_url_permitida("https://169.254.169.254/x") is False


# --------------------------------------------------------------------------- #
# 21. Superfície nova: login social, passkey, recuperação e exportação
#
# Cada porta de entrada que a gente abre é um alvo. Estes testes existem para
# que a lista de ameaças acompanhe o produto em vez de virar um retrato de
# como ele era em setembro.
# --------------------------------------------------------------------------- #
def test_21_login_social_verifica_a_assinatura_do_provedor():
    """A falha clássica de OIDC é ler o payload sem conferir quem assinou."""
    import ast
    import inspect
    import textwrap

    from agenda.core import oidc

    # O docstring cita o antipadrão de propósito; o teste tem de olhar o que o
    # código FAZ, não o que ele explica. Por isso a árvore, e não o texto.
    arvore = ast.parse(textwrap.dedent(inspect.getsource(oidc.verificar_id_token)))
    funcao = arvore.body[0]
    if ast.get_docstring(funcao):
        funcao.body = funcao.body[1:]
    codigo = ast.unparse(funcao)

    assert "verify_signature" not in codigo, "assinatura de id_token nunca pode ser pulada"
    assert "audience=" in codigo and "issuer=" in codigo
    assert "nonce" in codigo


def test_21b_passkey_exige_verificacao_do_usuario():
    """Sem isso, um aparelho destravado em cima da mesa entra na conta."""
    import inspect

    from agenda.core import passkeys

    for funcao in (passkeys.concluir_cadastro, passkeys.autenticar):
        assert "require_user_verification=True" in inspect.getsource(funcao)


def test_21c_recuperacao_de_senha_nao_enumera(app, db, user):
    """Diferença observável entre 'existe' e 'não existe' vira lista de clientes."""
    import re as _re

    respostas = []
    for email in (user.email, "nao-existe-mesmo@example.com"):
        client = app.test_client()
        client.get("/recuperar")
        with client.session_transaction() as sessao:
            token = sessao.get("csrf", "")
        resposta = client.post("/recuperar", data={"csrf_token": token, "email": email},
                               follow_redirects=True)
        limpo = _re.sub(rb'(value|content|nonce)="[A-Za-z0-9+/=._:-]{16,}"', b"",
                        resposta.data)
        respostas.append((resposta.status_code, limpo))
    assert respostas[0] == respostas[1]


def test_21d_exportacao_nao_entra_em_cache_compartilhado(app, db, user):
    """É a agenda inteira de uma pessoa: proxy nenhum pode guardar isso."""
    client = app.test_client()
    client.get("/entrar")
    with client.session_transaction() as sessao:
        token = sessao.get("csrf", "")
    client.post("/entrar", data={"csrf_token": token, "email": user.email,
                                 "password": "segredo123"})
    resposta = client.get("/conta/meus-dados.json")
    assert resposta.status_code == 200
    assert "no-store" in resposta.headers.get("Cache-Control", "")


def test_21e_backup_do_usuario_nunca_carrega_material_de_ataque(db, user):
    from agenda.core import backup

    bruto = backup.export_user_json(db, user)
    for proibido in ("password_hash", "token_hash", "ip_hash"):
        assert proibido not in bruto


def test_21f_conta_criada_por_provedor_nao_tem_senha_utilizavel(db):
    """Senha vazia não pode virar login com senha vazia."""
    from agenda.core import oidc
    from agenda.security import verify_password

    identidade = oidc.Identidade(provedor="google", email="nova@example.com",
                                 nome="Nova", sub="1")
    conta = oidc.criar_conta(db, identidade, birth_year=1998)
    db.commit()
    assert not verify_password("", conta.password_hash)
    assert not verify_password("qualquer coisa", conta.password_hash)


def test_21g_pagamento_avulso_realmente_expira(db, user):
    """Dinheiro: um mês pago por Pix não pode virar plano vitalício."""
    import datetime as _dt

    from agenda.core import billing
    from agenda.models import PlanTier

    billing.change_plan(db, user, PlanTier.STUDENT.value, renews=False,
                        payment_method="pix")
    sub = billing.subscription_of(db, user)
    sub.current_period_end = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
    db.commit()
    assert billing.active_plan(db, user).tier == PlanTier.FREE.value
