"""Entrar com biometria: Face ID, Touch ID, Windows Hello, chave física.

## O que sai do aparelho, e o que não sai

A biometria **nunca** chega aqui. Ela destrava o aparelho; o aparelho assina um
desafio com uma chave privada que vive no Secure Enclave / TPM e não é
exportável. O servidor guarda só a chave pública, que sozinha não abre nada —
um dump da tabela `passkeys` não dá acesso a conta nenhuma. Isso é o oposto de
uma senha, cujo hash guardado é sempre material de ataque offline.

## Por que isso é mais seguro que senha, não só mais cômodo

Passkey resiste a phishing por construção: a assinatura é amarrada ao domínio
(`rp_id`) e à origem. Um site clonado em `gr1fo.app` não consegue produzir uma
assinatura que a gente aceite, mesmo que a pessoa caia no golpe — e é
exatamente aí que senha e código de SMS falham.

## As duas verificações que ninguém pode remover

1. **Desafio de uso único**, guardado no servidor. Sem isso, uma assinatura
   capturada uma vez vale para sempre (replay).
2. **Contador do autenticador (`sign_count`).** Se ele voltar para trás, a
   credencial pode ter sido clonada. Recusamos e marcamos. Autenticadores com
   sincronização em nuvem (iCloud Keychain) mandam 0 sempre — aí o contador não
   diz nada e a regra só vale quando ele existe.
"""
from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.core.events import log
from agenda.models import Passkey, User

DESAFIO_TTL_SEGUNDOS = 300
MAX_POR_CONTA = 10


class PasskeyError(Exception):
    """Falha de fluxo. A mensagem é para o log, não para a tela."""


def rp_id() -> str:
    """O domínio ao qual a credencial fica amarrada.

    Tem de ser o domínio registrável, sem esquema e sem porta. Errar isso é o
    motivo número um de "funciona no meu computador e não em produção".
    """
    if config.WEBAUTHN_RP_ID:
        return config.WEBAUTHN_RP_ID
    base = config.PUBLIC_URL or "http://localhost:8000"
    sem_esquema = base.split("://", 1)[-1]
    return sem_esquema.split("/")[0].split(":")[0]


def origens() -> list[str]:
    if config.WEBAUTHN_ORIGIN:
        return [o.strip() for o in config.WEBAUTHN_ORIGIN.split(",") if o.strip()]
    return [config.PUBLIC_URL or "http://localhost:8000"]


def disponivel() -> bool:
    """Passkey exige contexto seguro. Em localhost o navegador abre exceção."""
    return bool(rp_id())


# --------------------------------------------------------------------------- #
# Cadastro de uma chave
# --------------------------------------------------------------------------- #
def opcoes_de_cadastro(db: Session, user: User) -> tuple[dict, str]:
    """Devolve (opções para o navegador, desafio a guardar na sessão)."""
    import webauthn
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    if len(listar(db, user)) >= MAX_POR_CONTA:
        raise PasskeyError("limite de chaves por conta atingido")

    desafio = secrets.token_bytes(32)
    opcoes = webauthn.generate_registration_options(
        rp_id=rp_id(),
        rp_name=config.APP_NAME,
        # O id do usuário no autenticador é o nosso uuid, não o e-mail: e-mail
        # muda, e a credencial não pode ficar órfã por causa disso.
        user_id=user.id.encode(),
        user_name=user.email or user.id,
        user_display_name=user.name or user.email or "Estudante",
        challenge=desafio,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Resident key = a pessoa entra sem digitar o e-mail primeiro, que
            # é a experiência que faz passkey valer a pena no celular.
            resident_key=ResidentKeyRequirement.PREFERRED,
            # Exigir verificação do usuário é o que garante que houve biometria
            # ou PIN, e não só um aparelho destravado na mesa.
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        # Impede cadastrar duas vezes a mesma chave no mesmo aparelho.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=_b64d(chave.credential_id))
            for chave in listar(db, user)
        ],
    )
    import json

    return json.loads(webauthn.options_to_json(opcoes)), _b64e(desafio)


def concluir_cadastro(
    db: Session, user: User, *, credencial: dict, desafio: str, rotulo: str = ""
) -> Passkey:
    import webauthn

    try:
        verificada = webauthn.verify_registration_response(
            credential=credencial,
            expected_challenge=_b64d(desafio),
            expected_rp_id=rp_id(),
            expected_origin=origens(),
            require_user_verification=True,
        )
    except Exception as erro:  # noqa: BLE001 - a lib levanta vários tipos
        raise PasskeyError(f"cadastro de passkey recusado: {erro}") from erro

    credential_id = _b64e(verificada.credential_id)
    if db.scalars(select(Passkey).where(Passkey.credential_id == credential_id)).first():
        raise PasskeyError("essa chave já está cadastrada")

    chave = Passkey(
        user_id=user.id,
        credential_id=credential_id,
        public_key=verificada.credential_public_key,
        sign_count=verificada.sign_count or 0,
        label=(rotulo or "Este aparelho")[:80],
        backed_up=bool(getattr(verificada, "credential_backed_up", False)),
    )
    db.add(chave)
    db.flush()
    log(db, user_id=user.id, actor="user", action="PASSKEY_ADDED",
        object_type="passkey", object_id=chave.id, after={"rotulo": chave.label})
    return chave


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def opcoes_de_login(db: Session, user: User | None = None) -> tuple[dict, str]:
    """Sem `user`, o login é "descoberto": a pessoa escolhe a conta no aparelho.

    É de propósito que a lista de credenciais fique vazia nesse caso — devolver
    as credenciais de um e-mail transformaria a tela de login num verificador de
    quem tem conta aqui.
    """
    import json

    import webauthn
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor,
        UserVerificationRequirement,
    )

    desafio = secrets.token_bytes(32)
    permitidas = None
    if user is not None:
        permitidas = [
            PublicKeyCredentialDescriptor(id=_b64d(c.credential_id))
            for c in listar(db, user)
        ]
    opcoes = webauthn.generate_authentication_options(
        rp_id=rp_id(),
        challenge=desafio,
        allow_credentials=permitidas,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return json.loads(webauthn.options_to_json(opcoes)), _b64e(desafio)


def autenticar(db: Session, *, credencial: dict, desafio: str) -> User:
    import webauthn

    credential_id = (credencial.get("id") or "").strip()
    if not credential_id:
        raise PasskeyError("credencial sem id")

    chave = db.scalars(
        select(Passkey).where(Passkey.credential_id == credential_id)
    ).first()
    if chave is None:
        raise PasskeyError("credencial desconhecida")
    user = db.get(User, chave.user_id)
    if user is None or user.deleted_at is not None:
        raise PasskeyError("conta inexistente ou apagada")

    try:
        verificada = webauthn.verify_authentication_response(
            credential=credencial,
            expected_challenge=_b64d(desafio),
            expected_rp_id=rp_id(),
            expected_origin=origens(),
            credential_public_key=chave.public_key,
            credential_current_sign_count=chave.sign_count,
            require_user_verification=True,
        )
    except Exception as erro:  # noqa: BLE001 - a lib levanta vários tipos
        raise PasskeyError(f"assinatura recusada: {erro}") from erro

    novo = verificada.new_sign_count or 0
    # Contador que anda para trás é sinal de clonagem. Autenticador com
    # sincronização em nuvem manda 0 sempre — aí o contador não diz nada.
    if novo and chave.sign_count and novo <= chave.sign_count:
        log(db, user_id=user.id, actor="system", action="PASSKEY_COUNTER_REGRESSION",
            object_type="passkey", object_id=chave.id,
            after={"guardado": chave.sign_count, "recebido": novo})
        raise PasskeyError("contador do autenticador regrediu — possível clonagem")

    chave.sign_count = max(novo, chave.sign_count)
    chave.last_used_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    log(db, user_id=user.id, actor="user", action="LOGIN_PASSKEY",
        object_type="passkey", object_id=chave.id)
    return user


# --------------------------------------------------------------------------- #
# Gestão
# --------------------------------------------------------------------------- #
def listar(db: Session, user: User) -> list[Passkey]:
    return list(db.scalars(
        select(Passkey).where(Passkey.user_id == user.id).order_by(Passkey.created_at)
    ).all())


def remover(db: Session, user: User, passkey_id: str) -> bool:
    """Apagar a chave é do dono, e só dele.

    Não impedimos apagar a última: obrigar alguém a manter uma credencial que
    ele quer tirar é pior que o risco de ficar sem — a senha e a recuperação
    por e-mail continuam existindo.
    """
    chave = db.scalars(
        select(Passkey).where(Passkey.id == passkey_id, Passkey.user_id == user.id)
    ).first()
    if chave is None:
        return False
    db.delete(chave)
    db.flush()
    log(db, user_id=user.id, actor="user", action="PASSKEY_REMOVED",
        object_type="passkey", object_id=passkey_id)
    return True


# --------------------------------------------------------------------------- #
def _b64e(dados: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _b64d(texto: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))
