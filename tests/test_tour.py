"""Tour de primeiro acesso.

Duas coisas que estes testes protegem:

* **Ele fala a língua de quem está vendo.** Um tour que menciona "artigo para
  submissão" para uma criança de 7 anos é pior que tour nenhum.
* **Ele não prende ninguém.** Pular tem de funcionar em qualquer cena, e o
  "já viu" mora no servidor: guardado no navegador, trocar de aparelho
  reapresentaria a intro para quem usa o app há meses.
"""
from __future__ import annotations

from agenda.core import profiles, tour
from agenda.models import User
from tests.test_cenarios import _csrf, cadastrar, concluir_onboarding


# --------------------------------------------------------------------------- #
# Roteiro
# --------------------------------------------------------------------------- #
def test_o_roteiro_termina_pedindo_a_primeira_acao():
    """Tour bom não termina explicando: termina pedindo."""
    cenas = tour.para(profiles.PROFILES["UNDERGRAD"])
    assert cenas[-1].chave == "comeco"
    assert cenas[0].chave == "promessa"


def test_o_tour_e_curto():
    """Cada cena a mais é uma pessoa a menos chegando no fim."""
    for chave, perfil in profiles.PROFILES.items():
        assert len(tour.para(perfil)) <= 7, f"{chave} tem tour demais"


def test_cada_nivel_fala_a_propria_lingua():
    infantil = " ".join(c.corpo for c in tour.para(profiles.PROFILES["EARLY_CHILDHOOD"]))
    doutorado = " ".join(c.corpo for c in tour.para(profiles.PROFILES["DOCTORATE"]))
    assert infantil != doutorado, "o roteiro tem de mudar com o nível"


def test_o_nome_entra_quando_existe():
    com = tour.para(profiles.PROFILES["UNDERGRAD"], nome="Ana Paula Silva")
    sem = tour.para(profiles.PROFILES["UNDERGRAD"])
    assert com[0].titulo.startswith("Ana,")
    assert not sem[0].titulo.startswith(",")


def test_familia_so_aparece_para_quem_ela_serve():
    """Uma cena sobre responsável num tour de doutorado é ruído."""
    chaves = lambda p: [c.chave for c in tour.para(p)]
    assert "familia" in chaves(profiles.PROFILES["ELEMENTARY"])
    assert "familia" not in chaves(profiles.PROFILES["DOCTORATE"])


def test_toda_cena_tem_arte_conhecida():
    """Arte sem template correspondente cairia no `else` sem ninguém notar."""
    conhecidas = {"promessa", "captura", "confirmacao", "agenda",
                  "whatsapp", "familia", "comeco"}
    for perfil in profiles.PROFILES.values():
        for cena in tour.para(perfil):
            assert cena.arte in conhecidas, f"arte desconhecida: {cena.arte}"


def test_o_texto_se_sustenta_sem_a_animacao():
    """Quem usa leitor de tela ou pediu menos animação recebe só o texto."""
    for cena in tour.para(profiles.PROFILES["HIGH_SCHOOL"]):
        assert cena.titulo and cena.corpo
        assert len(cena.corpo) > 30


# --------------------------------------------------------------------------- #
# Fluxo
# --------------------------------------------------------------------------- #
def test_o_tour_vem_depois_do_onboarding(app, db):
    """Antes do onboarding a gente ainda não sabe a língua da pessoa."""
    client = cadastrar(app, nome="Bruno", email="bruno@example.com",
                       senha="senhaforte123", ano=2003)
    resposta = concluir_onboarding(client, tipo="UNDERGRAD", institution="Feevale",
                                   course_name="Direito")
    assert resposta.status_code == 302
    assert resposta.headers["Location"].endswith("/apresentacao")


def test_a_apresentacao_abre_e_mostra_as_cenas(app, db):
    client = cadastrar(app, nome="Bruno", email="bruno@example.com",
                       senha="senhaforte123", ano=2003)
    concluir_onboarding(client, tipo="UNDERGRAD", institution="Feevale")
    corpo = client.get("/apresentacao").get_data(as_text=True)
    assert "tour-cena" in corpo
    assert "Pular apresentação" in corpo
    # A primeira cena começa visível: JS que falha não pode deixar a tela vazia.
    assert 'class="tour-cena ativa"' in corpo


def test_pular_marca_como_visto_no_servidor(app, db):
    """No servidor: no localStorage, outro aparelho reapresentaria o tour."""
    client = cadastrar(app, nome="Bruno", email="bruno@example.com",
                       senha="senhaforte123", ano=2003)
    concluir_onboarding(client, tipo="UNDERGRAD", institution="Feevale")

    resposta = client.post("/apresentacao/pronto", data={
        "csrf_token": _csrf(client), "next": "/hoje",
    })
    assert resposta.status_code == 302
    db.expire_all()
    pessoa = db.query(User).filter_by(email="bruno@example.com").first()
    assert pessoa.tour_done_at is not None


def test_quem_ja_viu_nao_ve_de_novo(app, db):
    client = cadastrar(app, nome="Bruno", email="bruno@example.com",
                       senha="senhaforte123", ano=2003)
    concluir_onboarding(client, tipo="UNDERGRAD", institution="Feevale")
    client.post("/apresentacao/pronto", data={"csrf_token": _csrf(client)})

    # Refazer o onboarding não pode trazer a apresentação de volta.
    resposta = concluir_onboarding(client, tipo="UNDERGRAD", institution="Outra")
    assert resposta.headers["Location"].endswith("/hoje")


def test_da_para_rever_pelas_configuracoes(app, db):
    client = cadastrar(app, nome="Bruno", email="bruno@example.com",
                       senha="senhaforte123", ano=2003)
    concluir_onboarding(client, tipo="UNDERGRAD", institution="Feevale")
    client.post("/apresentacao/pronto", data={"csrf_token": _csrf(client)})

    resposta = client.post("/apresentacao/rever", data={"csrf_token": _csrf(client)})
    assert resposta.status_code == 302
    assert resposta.headers["Location"].endswith("/apresentacao")
    db.expire_all()
    assert db.query(User).filter_by(email="bruno@example.com").first().tour_done_at is None


def test_destino_forjado_nao_vira_redirecionamento_aberto(app, db):
    """`next` vem do formulário: sem filtro, vira porta de phishing."""
    client = cadastrar(app, nome="Bruno", email="bruno@example.com",
                       senha="senhaforte123", ano=2003)
    concluir_onboarding(client, tipo="UNDERGRAD", institution="Feevale")

    for destino in ("https://site-do-atacante.example/login", "//atacante.example"):
        resposta = client.post("/apresentacao/pronto", data={
            "csrf_token": _csrf(client), "next": destino,
        })
        assert "atacante" not in resposta.headers["Location"]


def test_a_apresentacao_exige_login(app):
    assert app.test_client().get("/apresentacao").status_code in (302, 401)


def test_a_apresentacao_da_crianca_fala_de_responsavel(app, db):
    from tests.test_cenarios import entrar

    mae = cadastrar(app, nome="Ana", email="ana@example.com",
                    senha="senhaforte123", ano=1985)
    concluir_onboarding(mae, tipo="UNDERGRAD", institution="UFRGS")
    import datetime as dt

    mae.post("/familia/novo-estudante", data={
        "csrf_token": _csrf(mae), "name": "Léo", "email": "leo@example.com",
        "password": "senhadoleo123", "birth_year": str(dt.date.today().year - 7),
        "relationship": "mãe", "guardian_consent": "on", "ai_processing": "on",
    })
    filho = entrar(app, "leo@example.com", "senhadoleo123")
    concluir_onboarding(filho, tipo="ELEMENTARY", institution="Escola Alegria",
                        grade_name="2º ano")
    corpo = filho.get("/apresentacao").get_data(as_text=True)
    assert "responsável" in corpo
