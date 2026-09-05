"""Verificação de e-mail e de telefone, e recuperação de senha.

Três coisas são testadas aqui porque as três já derrubaram SaaS de verdade:

1. **Recuperação não pode enumerar clientes.** A resposta de "e-mail existe" e
   "e-mail não existe" tem de ser byte a byte a mesma.
2. **Token é de uso único e expira.** Link de recuperação que serve duas vezes é
   um backdoor com data marcada.
3. **Código de telefone não pode ser força-brutável.** Seis dígitos sem contagem
   de tentativa é um milhão de requisições — o que uma botnet faz num café.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from agenda import config
from agenda.channels import email as email_channel
from agenda.core import referrals, verification
from agenda.models import (
    LinkToken,
    PlanTier,
    ReferralStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from agenda.security import hash_password, verify_password


# --------------------------------------------------------------------------- #
# Apoio
# --------------------------------------------------------------------------- #
class _Caixa:
    """Provedor de e-mail falso: guarda o que seria enviado."""

    def __init__(self) -> None:
        self.mensagens: list[dict] = []

    def send(self, *, to: str, subject: str, text: str, html: str = ""):
        self.mensagens.append({"to": to, "subject": subject, "text": text})
        return email_channel.Envio(ok=True)

    def ultimo_link(self) -> str:
        texto = self.mensagens[-1]["text"]
        for pedaco in texto.split():
            if pedaco.startswith("http"):
                return pedaco
        raise AssertionError(f"nenhum link em: {texto}")

    def ultimo_token(self) -> str:
        return self.ultimo_link().rsplit("/", 1)[-1]


@pytest.fixture
def caixa(monkeypatch):
    postal = _Caixa()
    monkeypatch.setattr(email_channel, "provider", lambda: postal)
    return postal


def _csrf(client) -> str:
    with client.session_transaction() as session:
        return session.get("csrf", "")


def _entrar(app, email, senha):
    client = app.test_client()
    client.get("/entrar")
    resposta = client.post("/entrar", data={
        "csrf_token": _csrf(client), "email": email, "password": senha,
    })
    assert resposta.status_code == 302
    return client


# --------------------------------------------------------------------------- #
# E-mail
# --------------------------------------------------------------------------- #
def test_cadastro_dispara_confirmacao(app, caixa):
    client = app.test_client()
    client.get("/criar-conta")
    resposta = client.post("/criar-conta", data={
        "csrf_token": _csrf(client), "name": "Bia", "email": "bia@example.com",
        "password": "senhaforte123", "birth_year": "1998",
        "accept_terms": "on",
    })
    assert resposta.status_code == 302
    assert caixa.mensagens, "criar conta tem de disparar o e-mail de confirmação"
    assert caixa.mensagens[-1]["to"] == "bia@example.com"


def test_confirmacao_marca_o_usuario(app, db, user, caixa):
    assert verification.send_email_verification(db, user, base_url="https://x.app")
    db.commit()
    token = caixa.ultimo_token()

    client = app.test_client()
    assert client.get(f"/confirmar-email/{token}").status_code == 302
    db.expire_all()
    assert verification.email_verificado(db.get(User, user.id))


def test_link_de_confirmacao_e_de_uso_unico(app, db, user, caixa):
    verification.send_email_verification(db, user, base_url="https://x.app")
    db.commit()
    token = caixa.ultimo_token()
    client = app.test_client()
    client.get(f"/confirmar-email/{token}")

    db.expire_all()
    quem = db.get(User, user.id)
    quem.email_verified_at = None
    db.commit()

    assert verification.confirm_email(db, token) is None


def test_pedir_novo_link_invalida_o_anterior(app, db, user, caixa):
    verification.send_email_verification(db, user, base_url="https://x.app")
    db.commit()
    antigo = caixa.ultimo_token()

    db.expire_all()
    verification.send_email_verification(db, db.get(User, user.id), base_url="https://x.app")
    db.commit()
    novo = caixa.ultimo_token()
    assert antigo != novo

    assert verification.confirm_email(db, antigo) is None
    assert verification.confirm_email(db, novo) is not None


def test_token_expirado_nao_confirma(app, db, user, caixa):
    verification.send_email_verification(db, user, base_url="https://x.app")
    token = caixa.ultimo_token()
    linha = db.query(LinkToken).filter_by(purpose=verification.PROPOSITO_EMAIL).one()
    linha.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    db.commit()

    assert verification.confirm_email(db, token) is None


def test_token_guardado_em_hash(app, db, user, caixa):
    """Dump do banco não pode virar link de recuperação."""
    verification.send_email_verification(db, user, base_url="https://x.app")
    db.commit()
    token = caixa.ultimo_token()
    guardado = db.query(LinkToken).filter_by(purpose=verification.PROPOSITO_EMAIL).one()
    assert token not in guardado.token
    assert guardado.token != token


# --------------------------------------------------------------------------- #
# Recuperação de senha
# --------------------------------------------------------------------------- #
def test_recuperacao_nao_enumera_contas(app, db, user, caixa):
    user.email_verified_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    client = app.test_client()
    client.get("/recuperar")
    existe = client.post("/recuperar", data={
        "csrf_token": _csrf(client), "email": user.email,
    }, follow_redirects=True)

    client2 = app.test_client()
    client2.get("/recuperar")
    nao_existe = client2.post("/recuperar", data={
        "csrf_token": _csrf(client2), "email": "ninguem@example.com",
    }, follow_redirects=True)

    assert existe.status_code == nao_existe.status_code
    # Só o csrf token e o nonce da CSP mudam entre respostas. Tirando esses,
    # a página tem de ser idêntica — byte a byte.
    limpar = lambda corpo: re.sub(
        rb'(value|content|nonce)="[A-Za-z0-9+/=._:-]{16,}"', b"", corpo
    )
    assert limpar(existe.data) == limpar(nao_existe.data)
    assert "Se existir uma conta" in existe.data.decode()


def test_email_nao_verificado_recebe_confirmacao_nao_recuperacao(app, db, user, caixa):
    """Sem prova de posse, o link de recuperação seria o caminho para tomar conta."""
    assert user.email_verified_at is None
    verification.request_password_reset(db, user.email, base_url="https://x.app")
    db.commit()

    assert caixa.mensagens, "deveria mandar o de confirmação"
    assert "Confirme" in caixa.mensagens[-1]["subject"]
    assert db.query(LinkToken).filter_by(purpose=verification.PROPOSITO_SENHA).count() == 0


def test_recuperacao_troca_senha_e_derruba_sessoes(app, db, user, caixa):
    user.email_verified_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    logado = _entrar(app, user.email, "segredo123")
    assert logado.get("/hoje").status_code == 200

    verification.request_password_reset(db, user.email, base_url="https://x.app")
    db.commit()
    token = caixa.ultimo_token()

    client = app.test_client()
    client.get(f"/recuperar/{token}")
    resposta = client.post(f"/recuperar/{token}", data={
        "csrf_token": _csrf(client), "password": "novasenhaboa9",
    })
    assert resposta.status_code == 302

    db.expire_all()
    quem = db.get(User, user.id)
    assert verify_password("novasenhaboa9", quem.password_hash)
    # A sessão antiga tem de estar morta: se a conta foi tomada, recuperar
    # senha precisa expulsar quem estava dentro.
    assert logado.get("/hoje").status_code in (302, 401)


def test_recuperacao_recusa_senha_fraca(app, db, user, caixa):
    user.email_verified_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    verification.request_password_reset(db, user.email, base_url="https://x.app")
    db.commit()
    token = caixa.ultimo_token()

    client = app.test_client()
    client.get(f"/recuperar/{token}")
    resposta = client.post(f"/recuperar/{token}", data={
        "csrf_token": _csrf(client), "password": "123",
    })
    assert resposta.status_code == 200  # continua no formulário
    db.expire_all()
    assert verify_password("segredo123", db.get(User, user.id).password_hash)


def test_link_de_recuperacao_serve_uma_vez(app, db, user, caixa):
    user.email_verified_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    verification.request_password_reset(db, user.email, base_url="https://x.app")
    db.commit()
    token = caixa.ultimo_token()

    assert verification.reset_password(db, token, "novasenhaboa9") is not None
    assert verification.reset_password(db, token, "outrasenharuim9") is None
    db.expire_all()
    assert verify_password("novasenhaboa9", db.get(User, user.id).password_hash)


def test_conta_apagada_nao_recupera(app, db, user, caixa):
    user.email_verified_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    verification.request_password_reset(db, user.email, base_url="https://x.app")
    db.commit()
    token = caixa.ultimo_token()

    user.deleted_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    assert verification.reset_password(db, token, "novasenhaboa9") is None


# --------------------------------------------------------------------------- #
# Telefone
# --------------------------------------------------------------------------- #
def test_codigo_de_telefone_confirma(app, db, user):
    user.phone_e164 = "+5551999990000"
    db.commit()
    codigo = verification.send_phone_code(db, user)
    db.commit()
    assert codigo and len(codigo) == 6 and codigo.isdigit()
    assert verification.confirm_phone(db, user, codigo)
    assert verification.telefone_verificado(user)
    assert user.phone_verified is True


def test_codigo_de_telefone_nao_serve_para_outra_conta(app, db, user):
    """O id do dono entra no hash: código vazado não vira conta de terceiro."""
    outro = User(name="Outro", email="outro@example.com",
                 password_hash=hash_password("segredo1234"),
                 phone_e164="+5551988880000", onboarding_done=True)
    db.add(outro)
    user.phone_e164 = "+5551999990000"
    db.commit()

    codigo = verification.send_phone_code(db, user)
    db.commit()
    assert not verification.confirm_phone(db, outro, codigo)
    assert not verification.telefone_verificado(outro)


def test_codigo_de_telefone_queima_apos_cinco_erros(app, db, user):
    user.phone_e164 = "+5551999990000"
    db.commit()
    codigo = verification.send_phone_code(db, user)
    db.commit()
    errado = "000000" if codigo != "000000" else "111111"

    for _ in range(verification.MAX_TENTATIVAS_CODIGO):
        assert not verification.confirm_phone(db, user, errado)
    db.commit()

    # O código certo já não vale: quem erra cinco vezes pede outro.
    assert not verification.confirm_phone(db, user, codigo)
    assert not verification.telefone_verificado(user)


def test_codigo_de_telefone_expira(app, db, user):
    user.phone_e164 = "+5551999990000"
    db.commit()
    codigo = verification.send_phone_code(db, user)
    linha = db.query(LinkToken).filter_by(purpose=verification.PROPOSITO_TELEFONE).one()
    linha.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    db.commit()
    assert not verification.confirm_phone(db, user, codigo)


def test_telefone_sem_numero_nao_gera_codigo(app, db, user):
    user.phone_e164 = None
    db.commit()
    assert verification.send_phone_code(db, user) is None


# --------------------------------------------------------------------------- #
# Antifraude de indicação
# --------------------------------------------------------------------------- #
def test_indicacao_de_email_nao_confirmado_e_recusada(app, db, user):
    """Pagamento é a barreira principal; e-mail confirmado corta o ruído antes."""
    indicado = User(name="Zé", email="ze@example.com",
                    password_hash=hash_password("segredo1234"), onboarding_done=True)
    db.add(indicado)
    db.flush()
    referrals.code_for(db, user)
    registro = referrals.attribute(db, indicado, user.referral_code)
    assert registro is not None
    db.add(Subscription(user_id=indicado.id, plan=PlanTier.STUDENT.value,
                        status=SubscriptionStatus.ACTIVE.value))
    referrals.mark_paid(db, indicado)
    registro.qualified_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    db.commit()

    referrals.run_qualification(db)
    db.commit()
    db.expire_all()
    assert registro.status == ReferralStatus.REJECTED.value
    assert "confirmado" in registro.rejection_reason


# --------------------------------------------------------------------------- #
# Provedores de e-mail
#
# A recuperação de conta depende inteiramente daqui. O que estes testes
# garantem: a escolha do provedor é previsível, a chave nunca vaza no log, e
# uma recusa do provedor NUNCA vira "enviado com sucesso" — um e-mail que
# "foi enviado" e nunca chega deixa a pessoa esperando para sempre.
# --------------------------------------------------------------------------- #
class _RespostaFalsa:
    def __init__(self, status, corpo):
        self.status_code = status
        self._corpo = corpo

    def json(self):
        return self._corpo


def test_chave_do_resend_vence_o_smtp(monkeypatch):
    """Ninguém coloca uma chave de API para continuar usando SMTP."""
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_teste")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.exemplo.com")
    email_channel.reset_provider()
    assert email_channel.provider().name == "resend"
    email_channel.reset_provider()


def test_sem_chave_nenhuma_o_provedor_e_honesto(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    email_channel.reset_provider()
    prov = email_channel.provider()
    assert prov.name == "none"
    assert prov.configured is False
    # E, o mais importante: não finge que enviou.
    assert email_channel.send(to="a@b.c", subject="x", text="y").ok is False
    email_channel.reset_provider()


def test_resend_envia_e_devolve_o_id(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_teste")
    monkeypatch.setattr(config, "EMAIL_FROM", "Grifo <oi@grifo.app>")
    enviados = []

    def falso_post(url, json=None, headers=None, timeout=None):
        enviados.append({"url": url, "json": json, "headers": headers})
        return _RespostaFalsa(200, {"id": "msg_abc123"})

    import requests

    monkeypatch.setattr(requests, "post", falso_post)
    email_channel.reset_provider()

    envio = email_channel.send(to="ana@example.com", subject="Confirme", text="link")
    assert envio.ok is True
    assert envio.detalhe == "msg_abc123"

    pedido = enviados[0]
    assert pedido["json"]["from"] == "Grifo <oi@grifo.app>"
    assert pedido["json"]["to"] == ["ana@example.com"]
    # A chave viaja no cabeçalho, nunca no corpo.
    assert "re_teste" not in json.dumps(pedido["json"])
    email_channel.reset_provider()


def test_recusa_do_resend_nao_vira_sucesso(monkeypatch, capsys):
    """Domínio não verificado é o erro mais comum no primeiro dia."""
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_teste")
    monkeypatch.setattr(config, "EMAIL_FROM", "Grifo <oi@grifo.app>")

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _RespostaFalsa(
        403, {"message": "The grifo.app domain is not verified"}))
    email_channel.reset_provider()

    envio = email_channel.send(to="ana@example.com", subject="x", text="y")
    assert envio.ok is False
    assert "not verified" in envio.detalhe, "o motivo tem de chegar a quem configura"

    registrado = capsys.readouterr().out
    assert "not verified" in registrado
    assert "re_teste" not in registrado, "a chave nunca pode entrar no log"
    email_channel.reset_provider()


def test_resend_fora_do_ar_nao_derruba_o_cadastro(monkeypatch):
    """Falha de rede vira envio falso, não exceção subindo pela rota."""
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_teste")
    monkeypatch.setattr(config, "EMAIL_FROM", "Grifo <oi@grifo.app>")

    import requests

    def explode(*_a, **_k):
        raise ConnectionError("sem rota para o host")

    monkeypatch.setattr(requests, "post", explode)
    email_channel.reset_provider()
    assert email_channel.send(to="a@b.c", subject="x", text="y").ok is False
    email_channel.reset_provider()


def test_email_nunca_aparece_inteiro_no_log(monkeypatch, capsys):
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    email_channel.reset_provider()

    email_channel.send(to="anabeatriz@example.com", subject="x", text="y")
    saida = capsys.readouterr().out
    assert "anabeatriz@example.com" not in saida
    assert "an***@example.com" in saida
    email_channel.reset_provider()
