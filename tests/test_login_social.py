"""Entrar com Google e com Apple.

O teste que mais importa aqui é `test_pre_hijack...`: o ataque em que alguém
cadastra o e-mail da vítima com senha, nunca confirma, e espera a vítima
aparecer pelo Google. Sem defesa, a vítima cai dentro da conta do atacante —
que continua com a senha e passa a ler tudo.

Os outros testes existem para que ninguém, num refactor, remova a verificação
de assinatura, de nonce ou de state achando que "o provedor já garante".
"""
from __future__ import annotations

import datetime as dt

import pytest

from agenda.core import oidc
from agenda.models import User
from agenda.security import hash_password, verify_password


# --------------------------------------------------------------------------- #
# Emissor de id_token de mentira, para poder testar a verificação de verdade
# --------------------------------------------------------------------------- #
@pytest.fixture
def provedor_falso(monkeypatch):
    """Um provedor com chave RSA nossa: dá para assinar tokens legítimos e
    forjados e ver a verificação separar os dois."""
    import json

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    publica = jwt.algorithms.RSAAlgorithm.to_jwk(chave.public_key(), as_dict=True)
    publica["kid"] = "teste-1"
    publica["alg"] = "RS256"

    prov = oidc.Google()
    monkeypatch.setattr(prov, "client_id", lambda: "cliente-de-teste")
    monkeypatch.setattr(prov, "client_secret", lambda: "segredo-de-teste")
    monkeypatch.setattr(oidc, "_jwks", lambda _p: {"keys": [json.loads(json.dumps(publica))]})

    def emitir(**extras):
        agora = int(dt.datetime.now(dt.timezone.utc).timestamp())
        corpo = {
            "iss": prov.issuer, "aud": "cliente-de-teste", "sub": "1234567890",
            "iat": agora, "exp": agora + 600,
            "email": "vitima@example.com", "email_verified": True,
            "name": "Vítima Feliz", "nonce": "nonce-certo",
        }
        corpo.update(extras)
        return jwt.encode(corpo, chave, algorithm="RS256", headers={"kid": "teste-1"})

    return {"prov": prov, "emitir": emitir, "chave": chave}


# --------------------------------------------------------------------------- #
# Verificação do id_token
# --------------------------------------------------------------------------- #
def test_id_token_legitimo_passa(provedor_falso):
    dados = oidc.verificar_id_token(
        provedor_falso["prov"], provedor_falso["emitir"](), nonce="nonce-certo"
    )
    assert dados["email"] == "vitima@example.com"


def test_nonce_errado_e_recusado(provedor_falso):
    """Sem isso, um id_token legítimo capturado em outro lugar seria aceito."""
    with pytest.raises(oidc.OidcError):
        oidc.verificar_id_token(
            provedor_falso["prov"], provedor_falso["emitir"](), nonce="outro-nonce"
        )


def test_assinatura_de_outra_chave_e_recusada(provedor_falso):
    """A falha clássica: ler o payload sem conferir quem assinou."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    intrusa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agora = int(dt.datetime.now(dt.timezone.utc).timestamp())
    forjado = jwt.encode(
        {"iss": provedor_falso["prov"].issuer, "aud": "cliente-de-teste",
         "sub": "9", "iat": agora, "exp": agora + 600,
         "email": "vitima@example.com", "email_verified": True,
         "nonce": "nonce-certo"},
        intrusa, algorithm="RS256", headers={"kid": "teste-1"},
    )
    with pytest.raises(Exception):
        oidc.verificar_id_token(provedor_falso["prov"], forjado, nonce="nonce-certo")


def test_token_expirado_e_recusado(provedor_falso):
    agora = int(dt.datetime.now(dt.timezone.utc).timestamp())
    with pytest.raises(Exception):
        oidc.verificar_id_token(
            provedor_falso["prov"],
            provedor_falso["emitir"](exp=agora - 10, iat=agora - 700),
            nonce="nonce-certo",
        )


def test_audiencia_de_outro_cliente_e_recusada(provedor_falso):
    """id_token emitido para OUTRO app não pode logar no nosso."""
    with pytest.raises(Exception):
        oidc.verificar_id_token(
            provedor_falso["prov"],
            provedor_falso["emitir"](aud="app-de-outra-empresa"),
            nonce="nonce-certo",
        )


def test_emissor_trocado_e_recusado(provedor_falso):
    with pytest.raises(Exception):
        oidc.verificar_id_token(
            provedor_falso["prov"],
            provedor_falso["emitir"](iss="https://provedor-do-atacante.example"),
            nonce="nonce-certo",
        )


# --------------------------------------------------------------------------- #
# State e PKCE
# --------------------------------------------------------------------------- #
def test_state_amarra_a_volta_ao_inicio():
    prov = oidc.Google()
    _url, estado = oidc.começar(prov, redirect_uri="https://x.app/entrar/google/retorno")
    selado = oidc.selar(estado)

    assert oidc.abrir(selado, state_recebido=estado["s"])["n"] == estado["n"]
    with pytest.raises(oidc.OidcError):
        oidc.abrir(selado, state_recebido="state-do-atacante")
    with pytest.raises(oidc.OidcError):
        oidc.abrir(None, state_recebido=estado["s"])


def test_cookie_de_estado_adulterado_nao_abre():
    prov = oidc.Google()
    _url, estado = oidc.começar(prov, redirect_uri="https://x.app/volta")
    selado = oidc.selar(estado)
    corpo, assinatura = selado.split(".", 1)
    with pytest.raises(oidc.OidcError):
        oidc.abrir(f"{corpo}x.{assinatura}", state_recebido=estado["s"])


def test_pkce_vai_no_pedido():
    prov = oidc.Google()
    url, estado = oidc.começar(prov, redirect_uri="https://x.app/volta")
    assert "code_challenge_method=S256" in url
    assert "code_challenge=" in url
    # O verifier NUNCA pode viajar na URL — é justamente o segredo do PKCE.
    assert estado["v"] not in url


def test_apple_pede_form_post():
    """É a razão do cookie de estado separado: Lax não acompanha POST cross-site."""
    assert oidc.provider("apple").response_mode == "form_post"
    url, _estado = oidc.começar(oidc.provider("apple"), redirect_uri="https://x.app/volta")
    assert "response_mode=form_post" in url


# --------------------------------------------------------------------------- #
# Vinculação de conta — o pre-hijack
# --------------------------------------------------------------------------- #
def _identidade(email="vitima@example.com"):
    return oidc.Identidade(provedor="google", email=email, nome="Vítima", sub="1")


def test_pre_hijack_conta_nao_confirmada_perde_para_o_provedor(db):
    """Atacante planta a conta; a vítima chega pelo Google e o atacante cai fora."""
    atacante = User(name="Atacante", email="vitima@example.com",
                    password_hash=hash_password("senhadoatacante1"),
                    onboarding_done=True)
    db.add(atacante)
    db.commit()
    assert atacante.email_verified_at is None

    conta = oidc.conta_existente(db, _identidade())
    db.commit()
    db.expire_all()

    conta = db.get(User, atacante.id)
    assert conta.email_verified_at is not None, "o provedor provou o e-mail"
    # A senha do atacante tem de deixar de funcionar.
    assert not verify_password("senhadoatacante1", conta.password_hash)


def test_conta_ja_confirmada_apenas_vincula(db, user):
    """Os dois lados provaram o mesmo e-mail: a senha continua valendo."""
    user.email_verified_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    conta = oidc.conta_existente(db, _identidade(email=user.email))
    db.commit()
    assert conta is not None and conta.id == user.id
    assert verify_password("segredo123", conta.password_hash), "não era para trocar a senha"


def test_email_novo_nao_cria_conta_sozinho(db):
    """Criar antes do aceite seria tratar dado sem base legal (LGPD art. 7º)."""
    assert oidc.conta_existente(db, _identidade("ninguem@example.com")) is None
    assert db.query(User).filter_by(email="ninguem@example.com").first() is None


def test_conta_criada_pelo_provedor_nao_tem_senha(db):
    conta = oidc.criar_conta(db, _identidade("nova@example.com"), birth_year=1998)
    db.commit()
    assert conta.email_verified_at is not None
    assert conta.password_hash == ""
    # E senha vazia não pode virar login.
    assert not verify_password("", conta.password_hash)


def test_conta_apagada_nao_e_reaproveitada(db, user):
    user.email_verified_at = dt.datetime.now(dt.timezone.utc)
    user.deleted_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    assert oidc.conta_existente(db, _identidade(email=user.email)) is None


# --------------------------------------------------------------------------- #
# Provedores desligados
# --------------------------------------------------------------------------- #
def test_sem_chave_o_botao_nao_aparece():
    """Botão que devolve erro é pior que botão nenhum."""
    assert oidc.disponiveis() == []


def test_rota_de_provedor_desligado_nao_explode(app):
    resposta = app.test_client().get("/entrar/google")
    assert resposta.status_code == 302
    assert "/entrar" in resposta.headers["Location"]


def test_provedor_inventado_nao_existe(app):
    assert oidc.provider("facebook") is None
    assert app.test_client().get("/entrar/facebook").status_code == 302
