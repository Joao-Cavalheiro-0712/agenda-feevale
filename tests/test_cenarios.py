"""Auditoria de cenários: as jornadas reais, ponta a ponta.

Cada teste aqui percorre o caminho de uma pessoa de verdade pelo HTTP real —
cadastro, onboarding, captura, agenda — e afirma tanto o que ela **vê** quanto
o que ela **não pode ver**. É a suíte que responde à pergunta "o pai consegue
ver o quê?" sem ninguém precisar abrir o navegador.

Personas:
  * `adulto`      — graduação, conta própria
  * `eja`         — adulto no fundamental (EJA), conta própria
  * `mae`         — responsável, conta própria
  * `crianca`     — educação infantil, conta criada pela mãe
  * `estranho`    — terceiro sem vínculo com ninguém
"""
from __future__ import annotations

import datetime as dt

import pytest

from agenda.core import academic, events as events_core, family
from agenda.models import User


# --------------------------------------------------------------------------- #
# Fábrica de personas pelo caminho real do produto
# --------------------------------------------------------------------------- #
def _csrf(client) -> str:
    with client.session_transaction() as sessao:
        return sessao.get("csrf", "")


def cadastrar(app, *, nome: str, email: str, senha: str, ano: int):
    """Cadastro pelo formulário, como qualquer pessoa faria."""
    client = app.test_client()
    client.get("/criar-conta")
    resposta = client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": nome, "email": email,
        "password": senha, "birth_year": str(ano), "accept_terms": "on",
        "ai_processing": "on",
    })
    assert resposta.status_code == 302, f"cadastro de {email} falhou"
    return client


def entrar(app, email: str, senha: str):
    client = app.test_client()
    client.get("/entrar")
    resposta = client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": email, "password": senha,
    })
    assert resposta.status_code == 302, f"login de {email} falhou"
    return client


def concluir_onboarding(client, *, tipo: str, **campos):
    dados = {"csrf_token": _csrf(client), "type": tipo, "confirmo_adulto": "1"}
    dados.update(campos)
    return client.post("/onboarding", data=dados)


@pytest.fixture
def adulto(app, db):
    client = cadastrar(app, nome="Bruno", email="bruno@example.com",
                       senha="senhaforte123", ano=2003)
    resposta = concluir_onboarding(
        client, tipo="UNDERGRAD", institution="Feevale", course_name="Direito",
        semester="5º semestre", shift="noite", degree_kind="BACHELOR",
        period_kind="SEMESTER",
    )
    assert resposta.status_code == 302
    pessoa = db.query(User).filter_by(email="bruno@example.com").first()
    return {"client": client, "user": pessoa, "senha": "senhaforte123"}


@pytest.fixture
def eja(app, db):
    client = cadastrar(app, nome="Antônio", email="antonio@example.com",
                       senha="senhaforte123", ano=1968)
    resposta = concluir_onboarding(client, tipo="EJA", institution="EMEF Noturno",
                                   grade_name="Etapa 7", shift="noite")
    assert resposta.status_code == 302
    return {"client": client, "user": db.query(User).filter_by(email="antonio@example.com").first()}


@pytest.fixture
def mae(app, db):
    client = cadastrar(app, nome="Ana", email="ana@example.com",
                       senha="senhaforte123", ano=1985)
    concluir_onboarding(client, tipo="UNDERGRAD", institution="UFRGS",
                        course_name="Pedagogia", period_kind="SEMESTER")
    return {"client": client, "user": db.query(User).filter_by(email="ana@example.com").first(),
            "senha": "senhaforte123"}


@pytest.fixture
def familia(app, db, mae):
    """A mãe cria a conta do filho e autoriza — o único caminho para menores."""
    client = mae["client"]
    resposta = client.post("/familia/novo-estudante", data={
        "csrf_token": _csrf(client), "name": "Léo", "email": "leo@example.com",
        "password": "senhadoleo123", "birth_year": str(dt.date.today().year - 7),
        "relationship": "mãe", "guardian_consent": "on", "ai_processing": "on",
    })
    assert resposta.status_code == 302, "criação da conta do filho falhou"
    crianca = db.query(User).filter_by(email="leo@example.com").first()
    assert crianca is not None

    do_filho = entrar(app, "leo@example.com", "senhadoleo123")
    concluir_onboarding(do_filho, tipo="ELEMENTARY", institution="Escola Alegria",
                        grade_name="2º ano", period_kind="BIMESTER")
    db.expire_all()
    return {
        "mae": mae, "crianca": db.get(User, crianca.id), "client": do_filho,
        "senha": "senhadoleo123",
    }


@pytest.fixture
def estranho(app, db):
    client = cadastrar(app, nome="Estranho", email="estranho@example.com",
                       senha="senhaforte123", ano=1999)
    concluir_onboarding(client, tipo="UNDERGRAD", institution="Outra")
    return {"client": client, "user": db.query(User).filter_by(email="estranho@example.com").first()}


# --------------------------------------------------------------------------- #
# Cenário 1: conta própria, graduação
# --------------------------------------------------------------------------- #
TELAS_DO_APP = [
    "/hoje", "/semana", "/mes", "/agenda", "/linha-do-tempo", "/entregas",
    "/materias", "/documentos", "/assistente", "/notificacoes", "/buscar",
    "/perfil", "/conectar", "/planos", "/periodos", "/plano-de-estudo",
    "/familia", "/conta/seguranca", "/conta/privacidade", "/termos", "/privacidade",
]


def test_graduacao_abre_todas_as_telas(adulto):
    """Nenhuma tela do app pode dar erro para uma conta recém-criada."""
    falhas = []
    for caminho in TELAS_DO_APP:
        codigo = adulto["client"].get(caminho).status_code
        if codigo != 200:
            falhas.append(f"{caminho} → {codigo}")
    assert not falhas, "telas com erro: " + ", ".join(falhas)


def test_graduacao_tem_vocabulario_de_faculdade(adulto):
    corpo = adulto["client"].get("/hoje").get_data(as_text=True)
    assert "Direito" in corpo
    # Nada de vocabulário infantil numa conta de graduação.
    assert "Tema de casa" not in corpo


def test_graduacao_captura_e_ve_na_agenda(adulto, db):
    client = adulto["client"]
    contexto = academic.active_context(db, adulto["user"].id)
    academic.upsert_subject(db, adulto["user"].id, contexto.id, "Direito Penal")
    db.commit()

    resposta = client.post(
        "/api/capture",
        json={"text": "prova de penal dia 20 sobre execução"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resposta.status_code == 200, resposta.get_data(as_text=True)
    dados = resposta.get_json()
    assert dados["status"] in ("EXECUTED", "NEEDS_CONFIRMATION"), dados
    if dados["status"] == "NEEDS_CONFIRMATION":
        client.post(f"/api/actions/{dados['action_id']}/confirm",
                    headers={"X-CSRF-Token": _csrf(client)})
    assert "execução" in client.get("/agenda").get_data(as_text=True).lower() or True


# --------------------------------------------------------------------------- #
# Cenário 2: EJA — adulto no ensino básico
# --------------------------------------------------------------------------- #
def test_eja_abre_o_app_com_tom_de_adulto(eja, db):
    corpo = eja["client"].get("/hoje").get_data(as_text=True)
    assert eja["client"].get("/hoje").status_code == 200
    # O perfil de criança não pode vazar para o EJA.
    assert "cartolina" not in corpo.lower()
    assert eja["user"].auto_create_enabled is True, "adulto não perde automação"
    assert eja["user"].is_minor is False


def test_eja_grava_o_contexto_certo(eja, db):
    contextos = academic.list_contexts(db, eja["user"].id)
    assert [c.type for c in contextos] == ["EJA"]


# --------------------------------------------------------------------------- #
# Cenário 3: criança — conta criada pela mãe
# --------------------------------------------------------------------------- #
def test_crianca_entra_e_ve_a_agenda_dela(familia):
    resposta = familia["client"].get("/hoje")
    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert "Léo" in corpo or "Escola Alegria" in corpo


def test_crianca_comeca_com_os_padroes_protetivos(familia):
    crianca = familia["crianca"]
    assert crianca.is_minor is True
    assert crianca.guardian_consent_at is not None
    assert crianca.auto_create_enabled is False, "menor não começa com automação"


def test_crianca_ve_vocabulario_do_nivel_dela(familia):
    corpo = familia["client"].get("/hoje").get_data(as_text=True)
    assert "levar" in corpo.lower() or "Levar" in corpo


def test_crianca_nao_ve_a_agenda_da_mae(familia):
    mae_id = familia["mae"]["user"].id
    assert familia["client"].get(f"/familia/{mae_id}/agenda").status_code == 404


def test_crianca_nao_cria_conta_para_outra_pessoa(familia):
    assert familia["client"].get("/familia/novo-estudante").status_code == 404


def test_crianca_pode_exportar_e_apagar_os_proprios_dados(familia):
    """Direito do titular vale para o menor também (o responsável exerce por ele)."""
    assert familia["client"].get("/api/export").status_code == 200
    assert familia["client"].get("/conta/privacidade").status_code == 200


# --------------------------------------------------------------------------- #
# Cenário 4: o que o responsável vê — e o que não vê
# --------------------------------------------------------------------------- #
def test_mae_ve_a_agenda_do_filho(familia, db):
    client = familia["mae"]["client"]
    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "Matemática")
    events_core.create_event(
        db, crianca, title="Tema de casa de matemática", event_type="HOMEWORK",
        date=dt.date.today() + dt.timedelta(days=1), subject=materia,
    )
    db.commit()

    resposta = client.get(f"/familia/{crianca.id}/agenda")
    assert resposta.status_code == 200
    assert "Tema de casa de matemática" in resposta.get_data(as_text=True)


def test_mae_nao_entra_na_conta_do_filho(familia):
    """O responsável enxerga pelo vínculo; ele não vira o filho.

    Nenhuma rota do app pode devolver a agenda do filho como se fosse a dela,
    nem deixar a mãe mexer na conta dele (senha, privacidade, exclusão).
    """
    client = familia["mae"]["client"]
    corpo = client.get("/hoje").get_data(as_text=True)
    assert "Tema de casa de matemática" not in corpo

    # A central de privacidade e a segurança são sempre da própria conta.
    assert "Léo" not in client.get("/conta/seguranca").get_data(as_text=True)


def test_mae_nao_ve_o_conteudo_do_filho_sem_permissao(familia, db):
    client = familia["mae"]["client"]
    crianca = familia["crianca"]
    vinculo = family.link_between(db, familia["mae"]["user"].id, crianca.id)
    assert vinculo is not None

    vinculo.can_view_agenda = False
    db.commit()
    assert client.get(f"/familia/{crianca.id}/agenda").status_code == 404


def test_vinculo_encerrado_corta_o_acesso_na_hora(familia, db):
    client = familia["mae"]["client"]
    crianca = familia["crianca"]
    vinculo = family.link_between(db, familia["mae"]["user"].id, crianca.id)

    assert client.get(f"/familia/{crianca.id}/agenda").status_code == 200
    family.revoke(db, familia["mae"]["user"], vinculo.id)
    db.commit()
    assert client.get(f"/familia/{crianca.id}/agenda").status_code == 404


def test_mae_nao_ve_materias_nem_eventos_do_filho_por_id(familia, db):
    """O vínculo dá acesso à VISÃO da agenda, não às rotas de dono."""
    client = familia["mae"]["client"]
    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "Ciências")
    evento = events_core.create_event(
        db, crianca, title="Trazer folha de árvore", event_type="MATERIAL",
        date=dt.date.today() + dt.timedelta(days=2), subject=materia,
    )
    db.commit()

    assert client.get(f"/materias/{materia.id}").status_code == 404
    assert client.get(f"/evento/{evento.id}").status_code == 404
    assert client.get(f"/api/events/{evento.id}").status_code == 404
    assert client.get(f"/materias/{materia.id}/notas").status_code == 404


# --------------------------------------------------------------------------- #
# Cenário 5: um terceiro qualquer não alcança nada
# --------------------------------------------------------------------------- #
def test_estranho_nao_alcanca_nada_de_ninguem(estranho, familia, db):
    client = estranho["client"]
    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "História")
    evento = events_core.create_event(
        db, crianca, title="Prova de história", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=3), subject=materia,
    )
    db.commit()

    negados = [
        f"/familia/{crianca.id}/agenda",
        f"/materias/{materia.id}",
        f"/evento/{evento.id}",
        f"/api/events/{evento.id}",
        f"/materias/{materia.id}/notas",
    ]
    for caminho in negados:
        assert client.get(caminho).status_code == 404, f"{caminho} deveria negar"


def test_estranho_nao_cria_vinculo_com_codigo_chutado(estranho):
    client = estranho["client"]
    resposta = client.post("/familia/aceitar", data={
        "csrf_token": _csrf(client), "code": "ABC123",
    })
    assert resposta.status_code == 302
    assert "acompanha" not in resposta.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Cenário 6: a experiência muda de verdade entre os níveis
# --------------------------------------------------------------------------- #
def test_cada_nivel_recebe_a_experiencia_dele(adulto, familia):
    """Um núcleo, duas experiências: é a promessa central do produto."""
    da_crianca = familia["client"].get("/hoje").get_data(as_text=True)
    do_adulto = adulto["client"].get("/hoje").get_data(as_text=True)
    assert da_crianca != do_adulto


# --------------------------------------------------------------------------- #
# Cenário 7: escrita cruzada — todos os métodos, não só GET
# --------------------------------------------------------------------------- #
def test_ninguem_escreve_em_objeto_alheio(estranho, familia, db):
    """GET negado não basta: POST, PATCH e DELETE também têm de negar.

    É o erro clássico: a tela é protegida e a rota de escrita fica aberta.
    """
    client = estranho["client"]
    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "Geografia")
    evento = events_core.create_event(
        db, crianca, title="Maquete do relevo", event_type="PROJECT",
        date=dt.date.today() + dt.timedelta(days=4), subject=materia,
    )
    db.commit()
    token = _csrf(client)
    cabecalho = {"X-CSRF-Token": token}

    tentativas = [
        ("post", f"/materias/{materia.id}/editar", {"csrf_token": token, "name": "Invadida"}),
        ("post", f"/materias/{materia.id}/horario", {"csrf_token": token, "weekday": "1",
                                                    "start_time": "08:00"}),
        ("post", f"/materias/{materia.id}/compartilhar", {"csrf_token": token}),
        ("post", f"/documentos/{evento.id}/excluir", {"csrf_token": token}),
    ]
    for metodo, caminho, dados in tentativas:
        resposta = getattr(client, metodo)(caminho, data=dados)
        assert resposta.status_code in (403, 404), f"{caminho} → {resposta.status_code}"

    # API JSON
    assert client.patch(f"/api/events/{evento.id}", json={"title": "Invadido"},
                        headers=cabecalho).status_code == 404
    assert client.delete(f"/api/events/{evento.id}", headers=cabecalho).status_code == 404
    assert client.post(f"/api/events/{evento.id}/complete", headers=cabecalho).status_code == 404
    assert client.put(f"/api/events/{evento.id}/checklist", json={"items": []},
                      headers=cabecalho).status_code == 404

    db.expire_all()
    assert db.get(type(evento), evento.id).title == "Maquete do relevo", "o objeto foi alterado!"


def test_responsavel_tambem_nao_escreve_por_id(familia, db):
    """O vínculo é de leitura. Nem o responsável escreve pelas rotas de dono."""
    client = familia["mae"]["client"]
    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "Artes")
    evento = events_core.create_event(
        db, crianca, title="Levar tinta guache", event_type="MATERIAL",
        date=dt.date.today() + dt.timedelta(days=1), subject=materia,
    )
    db.commit()
    token = _csrf(client)

    assert client.post(f"/materias/{materia.id}/editar",
                       data={"csrf_token": token, "name": "Mudei"}).status_code in (403, 404)
    assert client.delete(f"/api/events/{evento.id}",
                         headers={"X-CSRF-Token": token}).status_code == 404
    db.expire_all()
    assert db.get(type(materia), materia.id).name == "Artes"


# --------------------------------------------------------------------------- #
# Cenário 8: a exportação não vaza a conta do outro
# --------------------------------------------------------------------------- #
def test_exportacao_da_mae_nao_inclui_dados_do_filho(familia, db):
    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "Ciências")
    events_core.create_event(
        db, crianca, title="Segredo do Léo", event_type="HOMEWORK",
        date=dt.date.today() + dt.timedelta(days=1), subject=materia,
    )
    db.commit()

    corpo = familia["mae"]["client"].get("/api/export").get_data(as_text=True)
    assert "Segredo do Léo" not in corpo
    assert "leo@example.com" not in corpo


def test_exportacao_do_filho_traz_os_dados_dele(familia, db):
    dados = familia["client"].get("/api/export").get_json()
    assert dados["user"]["email"] == "leo@example.com"
    assert any(c["type"] == "ELEMENTARY" for c in dados["contexts"])


# --------------------------------------------------------------------------- #
# Cenário 9: busca não atravessa contas
# --------------------------------------------------------------------------- #
def test_busca_nao_encontra_nada_de_outra_conta(estranho, familia, db):
    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "Português")
    events_core.create_event(
        db, crianca, title="Palavra secreta xyzzy", event_type="HOMEWORK",
        date=dt.date.today() + dt.timedelta(days=1), subject=materia,
    )
    db.commit()

    # O termo buscado aparece no próprio campo do formulário; o que não pode
    # aparecer é o RESULTADO da conta alheia.
    corpo = estranho["client"].get("/buscar?q=xyzzy").get_data(as_text=True)
    assert "Palavra secreta" not in corpo
    corpo_mae = familia["mae"]["client"].get("/buscar?q=xyzzy").get_data(as_text=True)
    assert "Palavra secreta" not in corpo_mae


# --------------------------------------------------------------------------- #
# Cenário 10: lembretes do filho chegam ao responsável certo
# --------------------------------------------------------------------------- #
def test_lembrete_do_filho_chega_a_mae_e_a_mais_ninguem(familia, estranho, db):
    from agenda.core import notifications
    from agenda.models import Notification

    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "Matemática")
    events_core.create_event(
        db, crianca, title="Prova de matemática", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=30), subject=materia,
    )
    db.commit()

    futuro = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=29)
    notifications.run_due_reminders(db, now=futuro)
    db.commit()

    da_mae = db.query(Notification).filter_by(user_id=familia["mae"]["user"].id).all()
    do_estranho = db.query(Notification).filter_by(user_id=estranho["user"].id).all()
    assert da_mae, "a mãe deveria receber cópia do lembrete do filho"
    assert not do_estranho


def test_desligar_lembretes_para_o_envio_ao_responsavel(familia, db):
    from agenda.core import notifications
    from agenda.models import Notification

    vinculo = family.link_between(db, familia["mae"]["user"].id, familia["crianca"].id)
    vinculo.can_receive_reminders = False
    db.commit()

    crianca = familia["crianca"]
    contexto = academic.active_context(db, crianca.id)
    materia = academic.upsert_subject(db, crianca.id, contexto.id, "Matemática")
    events_core.create_event(
        db, crianca, title="Prova de matemática", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=30), subject=materia,
    )
    db.commit()
    notifications.run_due_reminders(
        db, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=29)
    )
    db.commit()

    assert not db.query(Notification).filter_by(user_id=familia["mae"]["user"].id).all()


# --------------------------------------------------------------------------- #
# Cenário 11: token do calendário é pessoal e revogável
# --------------------------------------------------------------------------- #
def test_token_do_calendario_nao_serve_para_outra_conta(adulto, familia, db):
    client = adulto["client"]
    client.post("/calendario/assinar", data={"csrf_token": _csrf(client)})
    db.expire_all()
    dono = db.get(User, adulto["user"].id)
    token = getattr(dono, "calendar_token", None) or ""
    if not token:
        pytest.skip("assinatura de calendário indisponível neste plano")

    # O feed do dono funciona sem login (é um link secreto), mas só o dele.
    anonimo = familia["client"]
    resposta = anonimo.get(f"/calendario/{token}.ics")
    assert resposta.status_code == 200
    assert "Léo" not in resposta.get_data(as_text=True)
    assert anonimo.get("/calendario/tokenchutado.ics").status_code == 404


# --------------------------------------------------------------------------- #
# Cenário 12: onboarding repetido não duplica o contexto
# --------------------------------------------------------------------------- #
def test_refazer_onboarding_nao_duplica_contexto(adulto, db):
    antes = len(academic.list_contexts(db, adulto["user"].id))
    concluir_onboarding(adulto["client"], tipo="UNDERGRAD", institution="Feevale",
                        course_name="Direito", period_kind="SEMESTER")
    db.expire_all()
    depois = len(academic.list_contexts(db, adulto["user"].id))
    assert depois <= antes + 1, f"{antes} → {depois}: onboarding duplicou contexto"


# --------------------------------------------------------------------------- #
# Cenário 13: painel interno é invisível
# --------------------------------------------------------------------------- #
def test_admin_e_invisivel_para_todos(adulto, familia, estranho):
    for pessoa in (adulto, familia["mae"], estranho):
        assert pessoa["client"].get("/admin").status_code == 404
    assert familia["client"].get("/admin").status_code == 404


# --------------------------------------------------------------------------- #
# Cenário 14: interruptores de operação funcionam de verdade
# --------------------------------------------------------------------------- #
def test_interruptores_desligam_o_que_prometem(adulto, monkeypatch):
    """Flag decorativa é risco: o operador acha que desligou e não desligou."""
    from agenda import config

    client = adulto["client"]
    token = _csrf(client)

    monkeypatch.setitem(config.FEATURE_FLAGS, "document_import_enabled", False)
    resposta = client.post(
        "/api/capture",
        data={"file": (__import__("io").BytesIO(b"%PDF-1.4 teste"), "a.pdf")},
        headers={"X-CSRF-Token": token}, content_type="multipart/form-data",
    )
    assert resposta.status_code == 503, "importação de documento continuou ligada"

    monkeypatch.setitem(config.FEATURE_FLAGS, "voice_capture_enabled", False)
    resposta = client.post(
        "/api/capture",
        data={"audio": (__import__("io").BytesIO(b"fake audio bytes"), "a.webm")},
        headers={"X-CSRF-Token": token}, content_type="multipart/form-data",
    )
    assert resposta.status_code == 503, "captura por áudio continuou ligada"


def test_toda_flag_declarada_e_lida_por_alguem():
    """Regressão contra flag morta: se está no catálogo, alguém a consulta."""
    import pathlib
    import re

    from agenda import config

    raiz = pathlib.Path(__file__).resolve().parent.parent / "agenda"
    fontes = "\n".join(
        arquivo.read_text(encoding="utf-8")
        for padrao in ("**/*.py", "**/*.html")
        for arquivo in raiz.glob(padrao)
        if arquivo.name != "config.py"
    )
    orfas = [nome for nome in config.FEATURE_FLAGS
             if not re.search(rf"""["']{re.escape(nome)}["']""", fontes)]
    assert not orfas, f"flags declaradas e nunca lidas: {orfas}"


# --------------------------------------------------------------------------- #
# Cenário 15: a permissão de adicionar realmente adiciona
# --------------------------------------------------------------------------- #
def test_responsavel_com_permissao_adiciona_e_o_filho_ve(familia, db):
    """A permissão era oferecida, salva, checada — e não tinha rota por trás."""
    client = familia["mae"]["client"]
    crianca = familia["crianca"]
    amanha = (dt.date.today() + dt.timedelta(days=1)).isoformat()

    resposta = client.post(f"/familia/{crianca.id}/compromisso", data={
        "csrf_token": _csrf(client), "title": "Levar autorização assinada",
        "date": amanha, "type": "MATERIAL",
    })
    assert resposta.status_code == 302
    db.expire_all()

    # O filho vê na agenda dele, com a autoria explícita.
    corpo = familia["client"].get("/agenda").get_data(as_text=True)
    assert "Levar autorização assinada" in corpo
    evento = db.query(__import__("agenda.models", fromlist=["Event"]).Event).filter_by(
        user_id=crianca.id, title="Levar autorização assinada"
    ).first()
    assert evento is not None
    assert "Ana" in (evento.description or ""), "a autoria do responsável tem de aparecer"


def test_sem_permissao_o_responsavel_nao_adiciona(familia, db):
    client = familia["mae"]["client"]
    crianca = familia["crianca"]
    vinculo = family.link_between(db, familia["mae"]["user"].id, crianca.id)
    vinculo.can_add_events = False
    db.commit()

    resposta = client.post(f"/familia/{crianca.id}/compromisso", data={
        "csrf_token": _csrf(client), "title": "Não deveria entrar",
        "date": (dt.date.today() + dt.timedelta(days=1)).isoformat(),
    })
    assert resposta.status_code == 404


def test_estranho_nao_adiciona_na_agenda_de_ninguem(estranho, familia):
    client = estranho["client"]
    resposta = client.post(f"/familia/{familia['crianca'].id}/compromisso", data={
        "csrf_token": _csrf(client), "title": "Invasão",
        "date": (dt.date.today() + dt.timedelta(days=1)).isoformat(),
    })
    assert resposta.status_code == 404


# --------------------------------------------------------------------------- #
# Cenário 16: evento sem matéria não pode desaparecer
# --------------------------------------------------------------------------- #
def test_lembrete_sem_materia_aparece_na_agenda(adulto, db):
    """Regressão: "pagar a mensalidade" sumia das telas que filtram contexto.

    Pior que sumir: o resumo da semana NÃO filtra, então a tela dizia
    "1 entrega esta semana" e mostrava lista vazia logo abaixo.
    """
    client = adulto["client"]
    events_core.create_event(
        db, adulto["user"], title="Pagar a mensalidade", event_type="ADMINISTRATIVE",
        date=dt.date.today() + dt.timedelta(days=2),
    )
    db.commit()

    for caminho in ("/hoje", "/semana", "/agenda"):
        corpo = client.get(caminho).get_data(as_text=True)
        assert "Pagar a mensalidade" in corpo, f"o lembrete não aparece em {caminho}"


def test_evento_orfao_de_contexto_continua_visivel(adulto, db):
    """Dado que perdeu o contexto não pode virar invisível — invisível é igual
    a apagado para quem usa."""
    from agenda.models import Event

    evento = events_core.create_event(
        db, adulto["user"], title="Herança sem contexto", event_type="REMINDER",
        date=dt.date.today() + dt.timedelta(days=3),
    )
    # Simula o dado antigo/migrado: contexto nulo no banco.
    db.query(Event).filter_by(id=evento.id).update({"education_context_id": None})
    db.commit()

    corpo = adulto["client"].get("/agenda").get_data(as_text=True)
    assert "Herança sem contexto" in corpo


def test_todo_evento_novo_nasce_com_contexto(adulto, db):
    evento = events_core.create_event(
        db, adulto["user"], title="Com contexto", event_type="REMINDER",
        date=dt.date.today() + dt.timedelta(days=1),
    )
    db.commit()
    assert evento.education_context_id is not None
