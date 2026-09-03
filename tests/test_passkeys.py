"""Chaves de acesso: Face ID, Touch ID, Windows Hello.

Os testes usam um autenticador de software escrito aqui — chave EC de verdade,
assinatura de verdade — porque testar WebAuthn com resposta falsa não testa a
única coisa que importa: se a verificação criptográfica realmente acontece.

Três garantias que nenhum refactor pode remover:

* desafio de uso único (senão uma assinatura capturada vale para sempre);
* origem e `rp_id` conferidos (é o que faz passkey resistir a phishing);
* contador que anda para trás é recusado (sinal de credencial clonada).
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from agenda.core import passkeys
from agenda.models import Passkey, User
from agenda.security import hash_password


ORIGEM = "https://grifo.test"
RP_ID = "grifo.test"


@pytest.fixture(autouse=True)
def dominio(monkeypatch):
    monkeypatch.setattr(passkeys, "rp_id", lambda: RP_ID)
    monkeypatch.setattr(passkeys, "origens", lambda: [ORIGEM])


def _b64e(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


class Autenticador:
    """Um Face ID de mentira, com criptografia de verdade."""

    def __init__(self, *, rp_id: str = RP_ID, origem: str = ORIGEM):
        self.chave = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = b"credencial-de-teste-0001"
        self.rp_id = rp_id
        self.origem = origem
        self.contador = 0

    # -- dados do autenticador (rpIdHash | flags | contador | ...) ---------- #
    def _authdata(self, *, com_credencial: bool) -> bytes:
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        # UP (presença) | UV (verificação do usuário) | AT quando há credencial
        flags = 0x01 | 0x04 | (0x40 if com_credencial else 0x00)
        dados = rp_hash + bytes([flags]) + struct.pack(">I", self.contador)
        if com_credencial:
            numeros = self.chave.public_key().public_numbers()
            cose = _cbor_mapa({
                1: 2, 3: -7, -1: 1,
                -2: numeros.x.to_bytes(32, "big"),
                -3: numeros.y.to_bytes(32, "big"),
            })
            dados += (
                b"\x00" * 16
                + struct.pack(">H", len(self.credential_id))
                + self.credential_id
                + cose
            )
        return dados

    def _client_data(self, tipo: str, desafio: str) -> bytes:
        return json.dumps({
            "type": tipo, "challenge": desafio, "origin": self.origem,
            "crossOrigin": False,
        }, separators=(",", ":")).encode()

    def cadastrar(self, desafio: str) -> dict:
        client_data = self._client_data("webauthn.create", desafio)
        authdata = self._authdata(com_credencial=True)
        attestation = _cbor_mapa({"fmt": "none", "attStmt": {}, "authData": authdata})
        return {
            "id": _b64e(self.credential_id),
            "rawId": _b64e(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64e(client_data),
                "attestationObject": _b64e(attestation),
            },
            "clientExtensionResults": {},
        }

    def assinar(self, desafio: str) -> dict:
        self.contador += 1
        client_data = self._client_data("webauthn.get", desafio)
        authdata = self._authdata(com_credencial=False)
        assinatura = self.chave.sign(
            authdata + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": _b64e(self.credential_id),
            "rawId": _b64e(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64e(client_data),
                "authenticatorData": _b64e(authdata),
                "signature": _b64e(assinatura),
            },
            "clientExtensionResults": {},
        }


def _cbor_mapa(dados: dict) -> bytes:
    """CBOR canônico mínimo — só o que o WebAuthn usa."""
    saida = _cbor_cabecalho(5, len(dados))
    for chave, valor in dados.items():
        saida += _cbor(chave) + _cbor(valor)
    return saida


def _cbor_cabecalho(tipo: int, valor: int) -> bytes:
    if valor < 24:
        return bytes([(tipo << 5) | valor])
    if valor < 256:
        return bytes([(tipo << 5) | 24, valor])
    if valor < 65536:
        return bytes([(tipo << 5) | 25]) + struct.pack(">H", valor)
    return bytes([(tipo << 5) | 26]) + struct.pack(">I", valor)


def _cbor(valor) -> bytes:
    if isinstance(valor, bool):
        return b"\xf5" if valor else b"\xf4"
    if isinstance(valor, int):
        if valor >= 0:
            return _cbor_cabecalho(0, valor)
        return _cbor_cabecalho(1, -valor - 1)
    if isinstance(valor, bytes):
        return _cbor_cabecalho(2, len(valor)) + valor
    if isinstance(valor, str):
        bruto = valor.encode()
        return _cbor_cabecalho(3, len(bruto)) + bruto
    if isinstance(valor, dict):
        return _cbor_mapa(valor)
    raise TypeError(valor)


# --------------------------------------------------------------------------- #
def _cadastrar(db, user, autenticador=None):
    autenticador = autenticador or Autenticador()
    _opcoes, desafio = passkeys.opcoes_de_cadastro(db, user)
    chave = passkeys.concluir_cadastro(
        db, user, credencial=autenticador.cadastrar(desafio), desafio=desafio,
        rotulo="iPhone da Ana",
    )
    db.commit()
    return autenticador, chave


# --------------------------------------------------------------------------- #
# Cadastro
# --------------------------------------------------------------------------- #
def test_cadastro_guarda_so_a_chave_publica(db, user):
    autenticador, chave = _cadastrar(db, user)
    assert chave.user_id == user.id
    assert chave.label == "iPhone da Ana"
    assert chave.public_key, "sem chave pública não dá para verificar nada"
    # A privada não pode ter chegado até aqui de jeito nenhum.
    privada = autenticador.chave.private_numbers().private_value.to_bytes(32, "big")
    assert privada not in chave.public_key


def test_opcoes_exigem_verificacao_do_usuario(db, user):
    """Sem isso, um aparelho destravado na mesa entraria na conta."""
    opcoes, _desafio = passkeys.opcoes_de_cadastro(db, user)
    assert opcoes["authenticatorSelection"]["userVerification"] == "required"
    assert opcoes["rp"]["id"] == RP_ID


def test_chave_ja_cadastrada_entra_na_lista_de_exclusao(db, user):
    _cadastrar(db, user)
    opcoes, _desafio = passkeys.opcoes_de_cadastro(db, user)
    assert opcoes["excludeCredentials"], "senão dá para cadastrar duas vezes o mesmo aparelho"


def test_desafio_errado_recusa_o_cadastro(db, user):
    autenticador = Autenticador()
    _opcoes, desafio = passkeys.opcoes_de_cadastro(db, user)
    outro = _b64e(b"desafio-que-nao-foi-o-nosso-1234")
    with pytest.raises(passkeys.PasskeyError):
        passkeys.concluir_cadastro(
            db, user, credencial=autenticador.cadastrar(desafio), desafio=outro
        )


def test_origem_de_site_clonado_e_recusada(db, user):
    """É isto que faz passkey resistir a phishing."""
    clone = Autenticador(origem="https://gr1fo.test")
    _opcoes, desafio = passkeys.opcoes_de_cadastro(db, user)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.concluir_cadastro(
            db, user, credencial=clone.cadastrar(desafio), desafio=desafio
        )


def test_rp_id_de_outro_dominio_e_recusado(db, user):
    clone = Autenticador(rp_id="outro.test")
    _opcoes, desafio = passkeys.opcoes_de_cadastro(db, user)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.concluir_cadastro(
            db, user, credencial=clone.cadastrar(desafio), desafio=desafio
        )


def test_existe_teto_de_chaves_por_conta(db, user, monkeypatch):
    monkeypatch.setattr(passkeys, "MAX_POR_CONTA", 1)
    _cadastrar(db, user)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.opcoes_de_cadastro(db, user)


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def test_login_com_a_chave_funciona(db, user):
    autenticador, _chave = _cadastrar(db, user)
    _opcoes, desafio = passkeys.opcoes_de_login(db)
    quem = passkeys.autenticar(db, credencial=autenticador.assinar(desafio), desafio=desafio)
    assert quem.id == user.id


def test_assinatura_de_outra_chave_nao_entra(db, user):
    """Alguém com o credential_id — que é público — não consegue nada com ele."""
    _autenticador, chave = _cadastrar(db, user)
    impostor = Autenticador()
    impostor.credential_id = base64.urlsafe_b64decode(
        chave.credential_id + "=" * (-len(chave.credential_id) % 4)
    )
    _opcoes, desafio = passkeys.opcoes_de_login(db)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.autenticar(db, credencial=impostor.assinar(desafio), desafio=desafio)


def test_desafio_reaproveitado_nao_entra(db, user):
    """Replay: uma assinatura capturada não pode valer duas vezes."""
    autenticador, _chave = _cadastrar(db, user)
    _opcoes, desafio = passkeys.opcoes_de_login(db)
    resposta = autenticador.assinar(desafio)
    assert passkeys.autenticar(db, credencial=resposta, desafio=desafio)
    db.commit()

    # O mesmo par (desafio, assinatura) chega de novo: o contador já avançou.
    with pytest.raises(passkeys.PasskeyError):
        passkeys.autenticar(db, credencial=resposta, desafio=desafio)


def test_contador_que_anda_para_tras_e_recusado(db, user):
    """Sinal de credencial clonada."""
    autenticador, chave = _cadastrar(db, user)
    _opcoes, desafio = passkeys.opcoes_de_login(db)
    passkeys.autenticar(db, credencial=autenticador.assinar(desafio), desafio=desafio)
    db.commit()

    chave.sign_count = 99
    db.commit()
    _opcoes, novo_desafio = passkeys.opcoes_de_login(db)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.autenticar(
            db, credencial=autenticador.assinar(novo_desafio), desafio=novo_desafio
        )


def test_credencial_desconhecida_nao_entra(db, user):
    autenticador = Autenticador()
    _opcoes, desafio = passkeys.opcoes_de_login(db)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.autenticar(db, credencial=autenticador.assinar(desafio), desafio=desafio)


def test_conta_apagada_nao_entra_pela_chave(db, user):
    import datetime as dt

    autenticador, _chave = _cadastrar(db, user)
    user.deleted_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    _opcoes, desafio = passkeys.opcoes_de_login(db)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.autenticar(db, credencial=autenticador.assinar(desafio), desafio=desafio)


def test_opcoes_de_login_nao_revelam_quem_tem_conta(db, user):
    """A lista vazia é de propósito: senão a tela vira verificador de e-mails."""
    _cadastrar(db, user)
    opcoes, _desafio = passkeys.opcoes_de_login(db)
    assert not opcoes.get("allowCredentials")


# --------------------------------------------------------------------------- #
# Gestão
# --------------------------------------------------------------------------- #
def test_so_o_dono_remove_a_propria_chave(db, user):
    _autenticador, chave = _cadastrar(db, user)
    outro = User(name="Outro", email="outro@example.com",
                 password_hash=hash_password("segredo1234"), onboarding_done=True)
    db.add(outro)
    db.commit()

    assert passkeys.remover(db, outro, chave.id) is False
    assert db.get(Passkey, chave.id) is not None
    assert passkeys.remover(db, user, chave.id) is True
    db.commit()
    assert db.get(Passkey, chave.id) is None


def test_listagem_e_por_conta(db, user):
    _cadastrar(db, user)
    outro = User(name="Outro", email="outro2@example.com",
                 password_hash=hash_password("segredo1234"), onboarding_done=True)
    db.add(outro)
    db.commit()
    assert len(passkeys.listar(db, user)) == 1
    assert passkeys.listar(db, outro) == []


# --------------------------------------------------------------------------- #
# Rotas
# --------------------------------------------------------------------------- #
def test_rotas_de_cadastro_exigem_login(app):
    """Sem sessão nem CSRF, a porta nem abre — 403 é o gate de CSRF."""
    client = app.test_client()
    assert client.post("/api/passkey/cadastro/opcoes").status_code == 403

    # Com CSRF válido mas sem login, cai no login_required.
    client.get("/entrar")
    with client.session_transaction() as sessao:
        token = sessao.get("csrf", "")
    resposta = client.post("/api/passkey/cadastro/opcoes",
                           headers={"X-CSRF-Token": token})
    assert resposta.status_code in (302, 401)


def test_login_sem_desafio_na_sessao_falha(app):
    client = app.test_client()
    client.get("/entrar")
    with client.session_transaction() as sessao:
        token = sessao.get("csrf", "")
    resposta = client.post("/api/passkey/login", json={"credential": {}},
                           headers={"X-CSRF-Token": token})
    assert resposta.status_code == 400
