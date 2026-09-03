"""Conformidade com a LGPD: prova de consentimento, menores e revogação.

Estes testes existem para uma pergunta que só se responde em juízo: "prove que
o titular consentiu". A prova é o `ConsentRecord` — versão, hash do texto,
data, origem e, para menores, quem autorizou.
"""
from __future__ import annotations

import datetime as dt

import pytest

from agenda.core import family, privacy
from agenda.legal import documents
from agenda.models import ConsentKind, ConsentRecord, User
from agenda.security import hash_password


def _csrf(client) -> str:
    with client.session_transaction() as session:
        return session.get("csrf", "")


def _adulto(db, email="mae@example.com", senha="outrasenha123") -> User:
    pessoa = User(
        name="Ana", email=email, password_hash=hash_password(senha),
        onboarding_done=True, birth_year=1985,
    )
    db.add(pessoa)
    db.flush()
    privacy.accept_documents(db, pessoa, ip="127.0.0.1", user_agent="pytest")
    db.commit()
    return pessoa


def _entrar(app, email, senha):
    client = app.test_client()
    client.get("/entrar")
    resposta = client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": email, "password": senha,
    })
    assert resposta.status_code == 302
    return client


# --------------------------------------------------------------------------- #
# Documentos públicos
# --------------------------------------------------------------------------- #
def test_termos_e_privacidade_sao_publicos(app):
    client = app.test_client()
    for caminho in ("/termos", "/privacidade"):
        resposta = client.get(caminho)
        assert resposta.status_code == 200, caminho
    corpo = client.get("/privacidade").get_data(as_text=True)
    # A política precisa declarar transferência internacional (art. 33).
    assert "Estados Unidos" in corpo
    assert "art" in corpo.lower()


def test_hash_do_documento_muda_quando_o_texto_muda():
    original = documents.plain_text(documents.TERMS_SECTIONS)
    assert privacy.document_hash(original) == privacy.document_hash(original)
    assert privacy.document_hash(original) != privacy.document_hash(original + " ")


# --------------------------------------------------------------------------- #
# Prova do consentimento (art. 8º §1º)
# --------------------------------------------------------------------------- #
def test_cadastro_grava_prova_do_aceite(app, db):
    client = app.test_client()
    client.get("/criar-conta")
    resposta = client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Bia", "email": "bia@example.com",
        "password": "senhaforte123", "birth_year": "1999", "accept_terms": "on",
    })
    assert resposta.status_code == 302

    pessoa = db.query(User).filter_by(email="bia@example.com").first()
    registros = privacy.history(db, pessoa)
    tipos = {r.kind for r in registros}
    assert ConsentKind.TERMS.value in tipos and ConsentKind.PRIVACY.value in tipos

    termo = privacy.latest(db, pessoa, ConsentKind.TERMS.value)
    assert termo.version == privacy.TERMS_VERSION
    assert termo.document_hash == privacy.document_hash(privacy.terms_text())
    # O IP nunca é guardado em claro.
    assert termo.ip_hash and "127.0.0.1" not in termo.ip_hash
    assert pessoa.accepted_terms_version == privacy.TERMS_VERSION


def test_cadastro_sem_aceitar_e_recusado(app, db):
    client = app.test_client()
    client.get("/criar-conta")
    client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Caio", "email": "caio@example.com",
        "password": "senhaforte123", "birth_year": "1999",
    })
    assert db.query(User).filter_by(email="caio@example.com").first() is None


def test_cadastro_sem_ano_de_nascimento_e_recusado(app, db):
    client = app.test_client()
    client.get("/criar-conta")
    client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Duda", "email": "duda@example.com",
        "password": "senhaforte123", "accept_terms": "on",
    })
    assert db.query(User).filter_by(email="duda@example.com").first() is None


# --------------------------------------------------------------------------- #
# Menores de idade (art. 14 + capacidade civil)
# --------------------------------------------------------------------------- #
def test_menor_de_idade_nao_cria_conta_sozinho(app, db):
    menor = dt.date.today().year - 12
    client = app.test_client()
    client.get("/criar-conta")
    resposta = client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Léo", "email": "leo@example.com",
        "password": "senhaforte123", "birth_year": str(menor), "accept_terms": "on",
    })
    assert resposta.status_code == 200
    assert "responsável" in resposta.get_data(as_text=True)
    assert db.query(User).filter_by(email="leo@example.com").first() is None


def test_responsavel_cria_conta_do_filho_com_consentimento_registrado(app, db):
    mae = _adulto(db)
    client = _entrar(app, "mae@example.com", "outrasenha123")

    nascimento = dt.date.today().year - 10
    resposta = client.post("/familia/novo-estudante", data={
        "csrf_token": _csrf(client), "name": "Léo", "email": "leo@example.com",
        "password": "senhadofilho1", "birth_year": str(nascimento),
        "relationship": "mãe", "guardian_consent": "on",
    })
    assert resposta.status_code == 302

    filho = db.query(User).filter_by(email="leo@example.com").first()
    assert filho is not None
    assert filho.is_minor and filho.guardian_consent_at is not None
    # Para menor, automação silenciosa e IA começam desligadas.
    assert filho.auto_create_enabled is False
    assert filho.ai_processing_enabled is False

    consentimento = privacy.latest(db, filho, ConsentKind.GUARDIAN_MINOR.value)
    assert consentimento is not None and consentimento.granted
    assert consentimento.guardian_email == mae.email
    assert consentimento.guardian_relationship == "mãe"

    # O vínculo já nasce ativo: o responsável acompanha do celular dele.
    assert family.can_view(db, mae, filho.id)


def test_filho_entra_com_a_propria_conta(app, db):
    _adulto(db)
    client = _entrar(app, "mae@example.com", "outrasenha123")
    client.post("/familia/novo-estudante", data={
        "csrf_token": _csrf(client), "name": "Léo", "email": "leo@example.com",
        "password": "senhadofilho1", "birth_year": str(dt.date.today().year - 10),
        "relationship": "mãe", "guardian_consent": "on",
    })

    do_filho = _entrar(app, "leo@example.com", "senhadofilho1")
    # A conta do filho funciona: o consentimento do responsável já está lá.
    assert do_filho.get("/onboarding").status_code == 200


def test_menor_sem_consentimento_do_responsavel_fica_travado(app, db):
    orfa = User(
        name="Sem autorização", email="sem@example.com",
        password_hash=hash_password("senhaforte123"), onboarding_done=True,
        birth_year=dt.date.today().year - 10, is_minor=True,
    )
    db.add(orfa)
    db.flush()
    privacy.accept_documents(db, orfa, ip="127.0.0.1")
    db.commit()

    client = _entrar(app, "sem@example.com", "senhaforte123")
    resposta = client.get("/hoje")
    assert resposta.status_code == 403
    assert "responsável" in resposta.get_data(as_text=True)
    # A API responde no mesmo tom, sem vazar nada da agenda.
    api = client.get("/api/planner/today")
    assert api.status_code == 403 and api.get_json()["reason"] == "responsavel"


def test_menor_nao_cria_conta_para_ninguem(app, db):
    _adulto(db)
    client = _entrar(app, "mae@example.com", "outrasenha123")
    client.post("/familia/novo-estudante", data={
        "csrf_token": _csrf(client), "name": "Léo", "email": "leo@example.com",
        "password": "senhadofilho1", "birth_year": str(dt.date.today().year - 10),
        "relationship": "mãe", "guardian_consent": "on",
    })
    do_filho = _entrar(app, "leo@example.com", "senhadofilho1")
    assert do_filho.get("/familia/novo-estudante").status_code == 404


def test_responsavel_precisa_declarar_o_vinculo(app, db):
    _adulto(db)
    client = _entrar(app, "mae@example.com", "outrasenha123")
    client.post("/familia/novo-estudante", data={
        "csrf_token": _csrf(client), "name": "Léo", "email": "leo@example.com",
        "password": "senhadofilho1", "birth_year": str(dt.date.today().year - 10),
        "relationship": "mãe",  # sem guardian_consent
    })
    assert db.query(User).filter_by(email="leo@example.com").first() is None


# --------------------------------------------------------------------------- #
# Novo aceite quando a versão muda
# --------------------------------------------------------------------------- #
def test_versao_nova_dos_documentos_exige_novo_aceite(app, db, client, user):
    assert client.get("/hoje").status_code == 200

    user.accepted_terms_version = "2020-01-01"
    db.commit()

    resposta = client.get("/hoje")
    assert resposta.status_code == 302 and "/aceite" in resposta.headers["Location"]
    # Ler os documentos continua liberado mesmo travado.
    assert client.get("/termos").status_code == 200

    client.get("/aceite")
    client.post("/aceite", data={"csrf_token": _csrf(client), "accept_terms": "on"})
    db.refresh(user)
    assert user.accepted_terms_version == privacy.TERMS_VERSION
    assert client.get("/hoje").status_code == 200


def test_conta_antiga_que_se_revela_menor_e_travada(app, db, client, user):
    user.accepted_terms_version = ""
    user.birth_year = None
    db.commit()

    client.get("/aceite")
    resposta = client.post("/aceite", data={
        "csrf_token": _csrf(client), "accept_terms": "on",
        "birth_year": str(dt.date.today().year - 13),
    })
    assert resposta.status_code == 200
    assert "responsável" in resposta.get_data(as_text=True)
    db.refresh(user)
    assert user.is_minor is True
    assert client.get("/hoje").status_code == 403


# --------------------------------------------------------------------------- #
# Revogação (art. 8º §5º) e direitos do titular (art. 18)
# --------------------------------------------------------------------------- #
def test_revogar_ia_desliga_o_envio_para_provedor_externo(app, db, client, user):
    assert privacy.ai_allowed(user)

    resposta = client.post("/conta/privacidade/ia", data={"csrf_token": _csrf(client)})
    assert resposta.status_code == 302
    db.refresh(user)
    assert user.ai_processing_enabled is False
    assert not privacy.ai_allowed(user)

    registro = privacy.latest(db, user, ConsentKind.AI_PROCESSING.value)
    assert registro is not None and registro.granted is False
    # Revogar não apaga a prova anterior: o histórico é a prova.
    todos = db.query(ConsentRecord).filter_by(
        user_id=user.id, kind=ConsentKind.AI_PROCESSING.value
    ).all()
    assert len(todos) >= 2


def test_audio_e_recusado_com_ia_desligada(app, db, client, user):
    privacy.set_ai_processing(db, user, enabled=False)
    db.commit()
    resposta = client.post(
        "/api/capture",
        data={"audio": (__import__("io").BytesIO(b"fake"), "a.webm")},
        headers={"X-CSRF-Token": _csrf(client)},
        content_type="multipart/form-data",
    )
    assert resposta.status_code == 403


def test_termos_nao_sao_revogaveis_com_a_conta_aberta(db, user):
    assert privacy.revoke(db, user, ConsentKind.TERMS.value) is None
    assert privacy.revoke(db, user, ConsentKind.PRIVACY.value) is None
    # O caminho para "revogar" os termos é excluir a conta.
    assert privacy.has_consent(db, user, ConsentKind.TERMS.value)


def test_central_de_privacidade_mostra_o_historico(client):
    corpo = client.get("/conta/privacidade").get_data(as_text=True)
    assert "Termos de uso" in corpo
    assert "Base legal" in corpo or "base legal" in corpo


def test_registro_de_tratamento_declara_base_legal_de_tudo():
    """Art. 37: toda operação precisa de finalidade, dados, base legal e prazo."""
    for linha in privacy.TREATMENT_RECORD:
        for campo in ("finalidade", "dados", "base_legal", "retencao"):
            assert linha.get(campo), f"{linha} sem {campo}"
    for linha in privacy.SUBPROCESSORS:
        for campo in ("nome", "papel", "pais", "dados"):
            assert linha.get(campo), f"{linha} sem {campo}"


@pytest.mark.parametrize("ano,esperado", [(2000, True), (dt.date.today().year - 17, False)])
def test_maioridade(ano, esperado):
    assert privacy.is_adult(ano) is esperado


# --------------------------------------------------------------------------- #
# Segunda linha: idade declarada que não bate com o nível escolhido
# --------------------------------------------------------------------------- #
def test_conta_adulta_em_nivel_infantil_pergunta_de_quem_e_a_agenda(app, db):
    """Três públicos caem aqui: adulto no EJA, pai na conta errada, criança que mentiu."""
    client = app.test_client()
    client.get("/criar-conta")
    client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Tinho", "email": "t@example.com",
        "password": "senhaforte123", "birth_year": "1990", "accept_terms": "on",
    })

    resposta = client.post("/onboarding", data={
        "csrf_token": _csrf(client), "type": "ELEMENTARY", "grade_name": "5º ano",
    })
    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert "Quem vai usar essa" in corpo
    assert "EJA" in corpo  # o adulto que voltou a estudar tem saída própria
    # Nada foi criado ainda: o onboarding não passou.
    pessoa = db.query(User).filter_by(email="t@example.com").first()
    assert pessoa.onboarding_done is False


def test_adulto_escolhe_eja_e_ganha_perfil_de_adulto(app, db):
    """Quem voltou a estudar não recebe a tela de uma criança de 8 anos."""
    from agenda.core import academic

    client = app.test_client()
    client.get("/criar-conta")
    client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Seu Antônio", "email": "antonio@example.com",
        "password": "senhaforte123", "birth_year": "1968", "accept_terms": "on",
    })
    client.post("/onboarding", data={
        "csrf_token": _csrf(client), "type": "ELEMENTARY", "institution": "EMEF Noturno",
    })
    resposta = client.post("/onboarding", data={
        "csrf_token": _csrf(client), "type": "EJA", "institution": "EMEF Noturno",
        "confirmo_adulto": "1",
    })
    assert resposta.status_code == 302

    db.expire_all()
    pessoa = db.query(User).filter_by(email="antonio@example.com").first()
    contexto = academic.list_contexts(db, pessoa.id)[0]
    assert contexto.type == "EJA"
    # Adulto é adulto: nada de automação desligada por causa da série.
    assert pessoa.auto_create_enabled is True
    assert pessoa.is_minor is False


def test_adulto_em_eja_segue_confirmando(app, db):
    client = app.test_client()
    client.get("/criar-conta")
    client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Dona Rita", "email": "rita@example.com",
        "password": "senhaforte123", "birth_year": "1970", "accept_terms": "on",
    })
    resposta = client.post("/onboarding", data={
        "csrf_token": _csrf(client), "type": "ELEMENTARY", "grade_name": "5º ano",
        "confirmo_adulto": "1",
    })
    assert resposta.status_code == 302
    db.expire_all()
    assert db.query(User).filter_by(email="rita@example.com").first().onboarding_done is True


def test_ensino_medio_nao_dispara_a_pergunta(app, db):
    """Aos 18 é comum estar no ensino médio: barrar seria falso positivo."""
    client = app.test_client()
    client.get("/criar-conta")
    client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Bea", "email": "bea@example.com",
        "password": "senhaforte123", "birth_year": str(dt.date.today().year - 18),
        "accept_terms": "on",
    })
    resposta = client.post("/onboarding", data={
        "csrf_token": _csrf(client), "type": "HIGH_SCHOOL", "grade_name": "3º ano",
    })
    assert resposta.status_code == 302


def test_admin_marca_conta_como_de_menor_e_ela_trava(app, db, user):
    admin = User(
        name="Operação", email="admin@example.com",
        password_hash=hash_password("senhaforte123"), onboarding_done=True,
        birth_year=1980, is_admin=True,
    )
    db.add(admin)
    db.flush()
    privacy.accept_documents(db, admin, ip="127.0.0.1")
    db.commit()

    painel = _entrar(app, "admin@example.com", "senhaforte123")
    resposta = painel.post(
        f"/admin/usuarios/{user.id}/menor", data={"csrf_token": _csrf(painel)}
    )
    assert resposta.status_code == 302

    db.expire_all()
    marcado = db.get(User, user.id)
    assert marcado.is_minor and marcado.guardian_consent_at is None
    # A sessão do titular foi encerrada e a conta responde travada.
    dele = _entrar(app, "joao@example.com", "segredo123")
    assert dele.get("/hoje").status_code == 403


# --------------------------------------------------------------------------- #
# Links públicos não são porta dos fundos da trava de consentimento
# --------------------------------------------------------------------------- #
def test_conta_pausada_para_de_servir_o_feed_de_calendario(app, db, client, user):
    """O feed é anônimo, então não passa pelo before_request.

    Sem checagem própria, a agenda de um menor sem autorização do responsável
    continuaria saindo por aqui com a conta pausada.
    """
    from agenda.models import LinkToken

    resposta = client.post("/calendario/assinar", data={"csrf_token": _csrf(client)})
    assert resposta.status_code == 302
    db.expire_all()
    linha = db.query(LinkToken).filter_by(user_id=user.id, purpose="calendar").first()
    if linha is None:
        pytest.skip("assinatura de calendário indisponível neste plano")

    anonimo = app.test_client()
    assert anonimo.get(f"/calendario/{linha.token}.ics").status_code == 200

    # Conta vira de menor sem autorização: o feed tem de fechar junto.
    user.is_minor = True
    user.guardian_consent_at = None
    db.commit()
    assert anonimo.get(f"/calendario/{linha.token}.ics").status_code == 404


def test_conta_pausada_para_de_servir_o_link_compartilhado(app, db, client, user):
    from agenda.core import academic
    from agenda.models import SharedCollection

    contexto = academic.active_context(db, user.id)
    materia = academic.upsert_subject(db, user.id, contexto.id, "História")
    db.commit()

    resposta = client.post(f"/materias/{materia.id}/compartilhar",
                           data={"csrf_token": _csrf(client)})
    assert resposta.status_code == 302
    db.expire_all()
    colecao = db.query(SharedCollection).filter_by(owner_id=user.id).first()
    if colecao is None:
        pytest.skip("compartilhamento indisponível neste plano")

    anonimo = app.test_client()
    assert anonimo.get(f"/join/{colecao.code}").status_code == 200

    user.is_minor = True
    user.guardian_consent_at = None
    db.commit()
    assert anonimo.get(f"/join/{colecao.code}").status_code == 404
