"""Testes de segurança — postura adversarial (SPEC §78, §79, §111).

Cada teste responde a uma pergunta que um atacante faria.
"""
from __future__ import annotations

import datetime as dt
import io

import pytest

from agenda.core import events as events_core, scope, sessions
from agenda.core.scope import AccessDenied
from agenda.ingest import pipeline
from agenda.models import Document, EducationContext, Event, Subject, User
from agenda.security import hash_password, safe_filename


@pytest.fixture
def outro_usuario(db):
    """Uma segunda conta, com dados próprios — o alvo das tentativas."""
    pessoa = User(
        name="Maria",
        email="maria@example.com",
        password_hash=hash_password("outrasenha123"),
        onboarding_done=True,
    )
    db.add(pessoa)
    db.flush()
    context = EducationContext(user_id=pessoa.id, type="UNDERGRAD", is_active=True)
    db.add(context)
    db.flush()
    evento = events_core.create_event(
        db, pessoa, title="Prova secreta da Maria", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=5), context_id=context.id,
    )
    documento = Document(user_id=pessoa.id, filename="cronograma-maria.pdf", status="READY")
    materia = Subject(user_id=pessoa.id, education_context_id=context.id, name="Cálculo da Maria")
    db.add_all([documento, materia])
    db.commit()
    return {"user": pessoa, "event": evento, "document": documento, "subject": materia}


def _csrf(client) -> str:
    with client.session_transaction() as session:
        return session.get("csrf", "")


# --------------------------------------------------------------------------- #
# Isolamento entre contas (IDOR)
# --------------------------------------------------------------------------- #
def test_scope_nao_devolve_objeto_de_outro_dono(db, user, outro_usuario):
    assert scope.get(db, Event, outro_usuario["event"].id, user.id) is None
    assert scope.get(db, Document, outro_usuario["document"].id, user.id) is None
    assert scope.get(db, Subject, outro_usuario["subject"].id, user.id) is None
    # E devolve normalmente para o dono.
    assert scope.get(db, Event, outro_usuario["event"].id, outro_usuario["user"].id) is not None


def test_scope_falha_fechado_para_modelo_nao_declarado(db, user):
    class ModeloDesconhecido:
        pass

    assert scope.get(db, ModeloDesconhecido, "qualquer-id", user.id) is None
    with pytest.raises(AccessDenied):
        scope.query(ModeloDesconhecido, user.id)


@pytest.mark.parametrize(
    "template",
    [
        "/evento/{event}",
        "/api/events/{event}",
        "/documentos/{document}",
        "/materias/{subject}",
        "/materias/{subject}/notas",
    ],
)
def test_rotas_negam_objeto_de_outra_conta(client, outro_usuario, template):
    caminho = template.format(
        event=outro_usuario["event"].id,
        document=outro_usuario["document"].id,
        subject=outro_usuario["subject"].id,
    )
    assert client.get(caminho).status_code == 404


def test_escrita_em_objeto_alheio_e_recusada(client, db, outro_usuario):
    alvo = outro_usuario["event"].id
    token = _csrf(client)

    patch = client.patch(f"/api/events/{alvo}", json={"title": "invadido"},
                         headers={"X-CSRF-Token": token})
    delete = client.delete(f"/api/events/{alvo}", headers={"X-CSRF-Token": token})
    completar = client.post(f"/api/events/{alvo}/complete", json={"done": True},
                            headers={"X-CSRF-Token": token})

    assert patch.status_code == 400 and delete.status_code == 400 and completar.status_code == 400
    db.expire_all()
    assert db.get(Event, alvo).title == "Prova secreta da Maria"


def test_exportacao_traz_apenas_dados_do_dono(client, outro_usuario):
    dados = client.get("/api/export").get_json()
    conteudo = str(dados)
    assert "Maria" not in conteudo
    assert "Prova secreta" not in conteudo


def test_busca_nao_atravessa_contas(client, outro_usuario):
    pagina = client.get("/buscar?q=secreta").get_data(as_text=True)
    assert "Prova secreta da Maria" not in pagina


# --------------------------------------------------------------------------- #
# Sessão
# --------------------------------------------------------------------------- #
def test_cookie_forjado_nao_autentica(app, user):
    client = app.test_client()
    with client.session_transaction() as session:
        session["sid"] = "token-inventado"
        session["user_id"] = user.id  # formato antigo não vale mais
    assert client.get("/hoje").status_code == 302


def test_logout_revoga_a_sessao(client):
    assert client.get("/hoje").status_code == 200
    client.post("/sair", data={"csrf_token": _csrf(client)})
    assert client.get("/hoje").status_code == 302


def test_sessao_revogada_para_de_valer_no_mesmo_instante(db, client, user):
    assert client.get("/hoje").status_code == 200
    sessions.revoke_all(db, user)
    db.commit()
    assert client.get("/hoje").status_code == 302


def test_login_cria_sessao_nova_sem_fixacao(app, db, user):
    client = app.test_client()
    client.get("/entrar")
    with client.session_transaction() as session:
        session["marcador"] = "valor-antigo"
    client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": "joao@example.com", "password": "segredo123",
    })
    with client.session_transaction() as session:
        assert "marcador" not in session
        assert session.get("sid")


def test_trocar_senha_encerra_outros_dispositivos(app, db, user, client):
    outro = app.test_client()
    outro.get("/entrar")
    outro.post("/entrar", data={
        "csrf_token": _csrf(outro), "email": "joao@example.com", "password": "segredo123",
    })
    assert outro.get("/hoje").status_code == 200

    client.post("/conta/senha", data={
        "csrf_token": _csrf(client),
        "current_password": "segredo123",
        "new_password": "novasenha2026",
    })
    db.expire_all()
    assert outro.get("/hoje").status_code == 302   # o outro dispositivo caiu
    assert client.get("/hoje").status_code == 200  # quem trocou continua


# --------------------------------------------------------------------------- #
# Login: enumeração e força bruta
# --------------------------------------------------------------------------- #
def test_mensagem_de_erro_nao_revela_se_o_email_existe(app, user):
    client = app.test_client()
    client.get("/entrar")
    inexistente = client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": "ninguem@example.com", "password": "qualquer123",
    }).get_data(as_text=True)
    errada = client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": "joao@example.com", "password": "senhaerrada123",
    }).get_data(as_text=True)
    assert "E-mail ou senha incorretos" in inexistente
    assert "E-mail ou senha incorretos" in errada


def test_forca_bruta_e_bloqueada_apos_tentativas(app, db, user):
    client = app.test_client()
    client.get("/entrar")
    for _ in range(9):
        client.post("/entrar", data={
            "csrf_token": _csrf(client), "email": "joao@example.com", "password": "errada12345",
        })
    resposta = client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": "joao@example.com", "password": "segredo123",
    })
    assert resposta.status_code == 429
    assert "Muitas tentativas" in resposta.get_data(as_text=True)


def test_senha_fraca_e_recusada_no_cadastro(app, db):
    client = app.test_client()
    client.get("/criar-conta")
    for fraca in ("12345678", "senha123", "abcdefghij"):
        client.post("/criar-conta", data={
            "csrf_token": _csrf(client), "email": f"{fraca}@example.com", "password": fraca,
        })
        assert db.query(User).filter_by(email=f"{fraca}@example.com").first() is None


# --------------------------------------------------------------------------- #
# CSRF, redirect aberto e cabeçalhos
# --------------------------------------------------------------------------- #
def test_post_sem_csrf_e_recusado_na_api(client):
    resposta = client.post("/api/capture", json={"text": "prova sexta"})
    assert resposta.status_code == 403


def test_redirect_aberto_e_bloqueado(app, user):
    client = app.test_client()
    client.get("/entrar")
    resposta = client.post("/entrar?next=https://evil.example.com", data={
        "csrf_token": _csrf(client), "email": "joao@example.com", "password": "segredo123",
    })
    assert resposta.headers["Location"] in ("/hoje",)


@pytest.mark.parametrize(
    "destino", ["//evil.com", "https://evil.com", "javascript:alert(1)", "\\\\evil.com", None]
)
def test_next_externo_nunca_e_seguido(app, user, destino):
    from agenda.web.auth import _safe_next

    with app.test_request_context():
        assert _safe_next(destino) == "/hoje"


def test_next_interno_e_preservado(app, user):
    from agenda.web.auth import _safe_next

    with app.test_request_context():
        assert _safe_next("/entregas") == "/entregas"


def test_admin_invisivel_para_usuario_comum(client):
    assert client.get("/admin").status_code == 404


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
def test_upload_rejeita_conteudo_incompativel_com_a_extensao():
    with pytest.raises(pipeline.UploadError):
        pipeline.validate_upload("cronograma.pdf", b"isto nao e um pdf de verdade")


@pytest.mark.parametrize(
    "conteudo",
    [b"MZ\x90\x00", b"\x7fELF\x02", b"#!/bin/sh\nrm -rf /", b"<?php system($_GET[0]); ?>"],
)
def test_upload_rejeita_executavel_e_script(conteudo):
    with pytest.raises(pipeline.UploadError):
        pipeline.validate_upload("cronograma.txt", conteudo)


def test_nome_de_arquivo_nao_permite_travessia():
    assert "/" not in safe_filename("../../../etc/passwd")
    assert safe_filename("..") == "arquivo"
    assert safe_filename("nota\x00.pdf") == "nota.pdf"


def test_storage_recusa_identificador_forjado():
    with pytest.raises(pipeline.UploadError):
        pipeline.store_file("../root", "doc-id-valido-1234", "x.pdf", b"%PDF-")


def test_upload_pelo_web_recusa_executavel(client, db, user):
    client.post(
        "/documentos",
        data={"csrf_token": _csrf(client), "files": (io.BytesIO(b"MZ\x90\x00exe"), "malware.pdf")},
        content_type="multipart/form-data",
    )
    assert db.query(Document).filter_by(user_id=user.id).count() == 0


# --------------------------------------------------------------------------- #
# SSRF no download de mídia
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url,permitido",
    [
        ("https://lookaside.fbsbx.com/whatsapp/media", True),
        ("https://scontent.xx.fbcdn.net/v/t62", True),
        ("http://lookaside.fbsbx.com/media", False),          # sem TLS
        ("https://169.254.169.254/latest/meta-data/", False),  # metadata da nuvem
        ("https://localhost:8080/admin", False),
        ("https://lookaside.fbsbx.com.evil.com/x", False),      # sufixo forjado
        ("file:///etc/passwd", False),
        ("", False),
    ],
)
def test_download_de_midia_so_aceita_hosts_oficiais(url, permitido):
    from agenda.channels.whatsapp import _media_url_permitida

    assert _media_url_permitida(url) is permitido


def test_webhook_sem_assinatura_e_recusado_em_producao(app, monkeypatch):
    from agenda import config
    from agenda.channels import whatsapp

    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    assert whatsapp.valid_signature(b"{}", None) is False


# --------------------------------------------------------------------------- #
# Privacidade nos registros
# --------------------------------------------------------------------------- #
def test_ip_nunca_e_guardado_em_claro(db, user):
    from agenda.core import login_guard
    from agenda.models import LoginAttempt

    login_guard.record(db, "joao@example.com", "203.0.113.10", success=False)
    db.commit()
    linha = db.query(LoginAttempt).first()
    assert linha.ip_hash and "203.0.113.10" not in linha.ip_hash


def test_token_de_sessao_nao_fica_em_claro_no_banco(db, user):
    from agenda.models import UserSession

    token = sessions.create(db, user, user_agent="pytest", ip="203.0.113.10")
    db.commit()
    linha = db.query(UserSession).filter_by(user_id=user.id).first()
    assert linha.token_hash != token
    assert len(linha.token_hash) == 64


# --------------------------------------------------------------------------- #
# Cobertura de isolamento nas rotas novas
# --------------------------------------------------------------------------- #
def test_todas_as_rotas_com_id_negam_objeto_alheio(client, outro_usuario, db):
    """Varredura: qualquer rota que receba um id de outra conta responde 404."""
    from agenda.models import GuardianLink, StudyBlock

    bloco = StudyBlock(
        user_id=outro_usuario["user"].id, local_date=dt.date.today(), minutes=45
    )
    vinculo = GuardianLink(
        student_id=outro_usuario["user"].id, invite_code="ALHEIO", status="PENDING"
    )
    db.add_all([bloco, vinculo])
    db.commit()

    token = _csrf(client)
    alvos_get = [
        f"/evento/{outro_usuario['event'].id}",
        f"/materias/{outro_usuario['subject'].id}",
        f"/materias/{outro_usuario['subject'].id}/notas",
        f"/documentos/{outro_usuario['document'].id}",
        f"/api/events/{outro_usuario['event'].id}",
        f"/api/documents/{outro_usuario['document'].id}/status",
        f"/familia/{outro_usuario['user'].id}/agenda",
    ]
    for caminho in alvos_get:
        assert client.get(caminho).status_code == 404, caminho

    alvos_post = [
        (f"/materias/{outro_usuario['subject'].id}/horario", {"weekday": 0, "start_time": "10:00"}),
        (f"/materias/{outro_usuario['subject'].id}/editar", {"name": "invadido"}),
        (f"/materias/{outro_usuario['subject'].id}/compartilhar", {}),
        (f"/documentos/{outro_usuario['document'].id}/excluir", {}),
        (f"/familia/{vinculo.id}/permissoes", {}),
    ]
    for caminho, dados in alvos_post:
        resposta = client.post(caminho, data={"csrf_token": token, **dados})
        assert resposta.status_code == 404, caminho

    # Bloco de estudo de outra conta: a API responde 404 e nada muda.
    assert client.post(
        f"/api/study/{bloco.id}/complete", json={"done": True},
        headers={"X-CSRF-Token": token},
    ).status_code == 404
    db.expire_all()
    assert db.get(StudyBlock, bloco.id).status == "PLANNED"


def test_dados_de_outra_conta_nao_aparecem_em_nenhuma_tela(client, outro_usuario):
    """Nenhuma tela do app pode renderizar conteúdo de outro usuário."""
    telas = ["/hoje", "/semana", "/mes", "/agenda", "/entregas", "/linha-do-tempo",
             "/materias", "/documentos", "/notificacoes", "/perfil", "/planos", "/familia"]
    for tela in telas:
        corpo = client.get(tela).get_data(as_text=True)
        assert "Prova secreta da Maria" not in corpo, tela
        assert "cronograma-maria.pdf" not in corpo, tela
        assert "Cálculo da Maria" not in corpo, tela
