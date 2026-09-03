"""Entrar com Google e com Apple.

## O fluxo, e por que cada peça existe

Authorization Code + PKCE. Nunca implicit: token que volta na URL fica no
histórico do navegador, no Referer e no log do servidor.

* **`state`** — amarra a volta ao início. Sem ele, alguém induz a vítima a
  completar um login que o atacante começou (CSRF de login) e a vítima acaba
  logada na conta do atacante, entregando o que digitar depois.
* **`nonce`** — vai no pedido e volta DENTRO do id_token assinado. É o que
  impede reaproveitar um id_token legítimo capturado em outro lugar.
* **PKCE** — o code só é trocável por quem tem o `code_verifier`. Protege se o
  código vazar no caminho de volta.
* **Verificação de assinatura pela JWKS do provedor.** O id_token só vale
  depois de conferir assinatura, `iss`, `aud`, `exp` e `nonce`. Ler o payload
  sem verificar a assinatura é aceitar qualquer identidade que o atacante
  digitar — é a falha clássica de "JWT não validado".

## O estado não cabe na sessão (por causa da Apple)

A Apple responde com `response_mode=form_post`: um POST cross-site de volta
para a gente. O cookie de sessão é `SameSite=Lax`, e Lax **não acompanha POST
cross-site** — a sessão chegaria vazia e o `state` não teria com o que ser
comparado. Por isso `state`, `nonce` e `code_verifier` viajam num cookie
próprio, curto, `SameSite=None; Secure; HttpOnly`, com o conteúdo assinado.
Afrouxar o cookie de sessão principal para resolver isso seria trocar um
problema de integração por um buraco de CSRF em todo o resto do app.

## Vinculação de conta: onde mora o ataque

O ataque é o "pre-hijack": o atacante cadastra `vitima@gmail.com` com senha e
nunca confirma o e-mail. Meses depois a vítima clica em "Entrar com Google" e,
se a gente vincular ingenuamente, ela cai dentro da conta do atacante — que
continua com a senha dele e passa a ler tudo.

A regra aqui: o Google/Apple afirmando `email_verified` **prova** posse do
e-mail; uma conta local com e-mail não confirmado **não prova nada**. Então,
quando a conta local existe e não está confirmada, a identidade do provedor
vence: confirmamos o e-mail, **derrubamos todas as sessões e invalidamos a
senha** — quem plantou a conta perde o acesso, e quem provou o e-mail entra.
Se a conta local já estava confirmada, os dois lados provaram o mesmo e-mail e
a vinculação é direta.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets
import time
import urllib.parse

from dataclasses import dataclass

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.core.events import log
from agenda.models import User

STATE_COOKIE = "grifo_oidc"
STATE_TTL_SEGUNDOS = 600  # 10 minutos: tempo de fazer login, não mais que isso
TIMEOUT = 8

# Cache das chaves públicas do provedor. Buscar a JWKS a cada login é um
# round-trip a mais no caminho crítico e uma dependência de rede a mais para
# falhar; as chaves giram em dias, não em segundos.
_JWKS_CACHE: dict[str, tuple[float, dict]] = {}
_JWKS_TTL = 3600


class OidcError(Exception):
    """Erro de fluxo. A mensagem é para o log, nunca para a tela."""


# --------------------------------------------------------------------------- #
# Provedores
# --------------------------------------------------------------------------- #
class Provider:
    slug = ""
    nome = ""
    issuer = ""
    authorize_url = ""
    token_url = ""
    jwks_url = ""
    scope = "openid email profile"
    response_mode = ""

    def client_id(self) -> str:
        raise NotImplementedError

    def client_secret(self) -> str:
        raise NotImplementedError

    def configurado(self) -> bool:
        try:
            return bool(self.client_id() and self.client_secret())
        except Exception:  # noqa: BLE001 - chave malformada é "não configurado"
            return False


class Google(Provider):
    slug = "google"
    nome = "Google"
    issuer = "https://accounts.google.com"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    jwks_url = "https://www.googleapis.com/oauth2/v3/certs"

    def client_id(self) -> str:
        return config.GOOGLE_CLIENT_ID

    def client_secret(self) -> str:
        return config.GOOGLE_CLIENT_SECRET


class Apple(Provider):
    slug = "apple"
    nome = "Apple"
    issuer = "https://appleid.apple.com"
    authorize_url = "https://appleid.apple.com/auth/authorize"
    token_url = "https://appleid.apple.com/auth/token"
    jwks_url = "https://appleid.apple.com/auth/keys"
    scope = "openid email name"
    # A Apple exige form_post quando o escopo pede email/name. É a razão do
    # cookie de estado separado — ver o docstring do módulo.
    response_mode = "form_post"

    def client_id(self) -> str:
        return config.APPLE_CLIENT_ID

    def client_secret(self) -> str:
        """A Apple não dá segredo: a gente assina um JWT ES256 com a chave .p8.

        O segredo vale no máximo 6 meses pela regra dela; usamos 1 hora e
        geramos a cada login, que evita guardar segredo de longa validade.
        """
        import jwt

        if not (config.APPLE_TEAM_ID and config.APPLE_KEY_ID and config.APPLE_PRIVATE_KEY):
            return ""
        agora = int(time.time())
        return jwt.encode(
            {
                "iss": config.APPLE_TEAM_ID,
                "iat": agora,
                "exp": agora + 3600,
                "aud": self.issuer,
                "sub": config.APPLE_CLIENT_ID,
            },
            config.APPLE_PRIVATE_KEY.replace("\\n", "\n"),
            algorithm="ES256",
            headers={"kid": config.APPLE_KEY_ID},
        )

    def configurado(self) -> bool:
        return bool(
            config.APPLE_CLIENT_ID and config.APPLE_TEAM_ID
            and config.APPLE_KEY_ID and config.APPLE_PRIVATE_KEY
        )


PROVEDORES: dict[str, Provider] = {p.slug: p for p in (Google(), Apple())}


def provider(slug: str) -> Provider | None:
    return PROVEDORES.get((slug or "").lower())


def disponiveis() -> list[Provider]:
    """Só entram no botão os que estão realmente configurados.

    Mostrar um botão que devolve erro é pior que não mostrar botão nenhum.
    """
    return [p for p in PROVEDORES.values() if p.configurado()]


# --------------------------------------------------------------------------- #
# Início do fluxo
# --------------------------------------------------------------------------- #
def _b64url(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def começar(prov: Provider, *, redirect_uri: str, proximo: str = "") -> tuple[str, dict]:
    """Devolve (url para onde mandar o usuário, estado a guardar no cookie)."""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    estado = {
        "p": prov.slug,
        "s": secrets.token_urlsafe(24),
        "n": secrets.token_urlsafe(24),
        "v": verifier,
        "r": redirect_uri,
        "next": proximo[:200],
    }
    parametros = {
        "client_id": prov.client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": prov.scope,
        "state": estado["s"],
        "nonce": estado["n"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if prov.response_mode:
        parametros["response_mode"] = prov.response_mode
    url = f"{prov.authorize_url}?{urllib.parse.urlencode(parametros)}"
    return url, estado


# --------------------------------------------------------------------------- #
# Volta do provedor
# --------------------------------------------------------------------------- #
def _jwks(prov: Provider) -> dict:
    cache = _JWKS_CACHE.get(prov.slug)
    if cache and cache[0] > time.time():
        return cache[1]
    resposta = requests.get(prov.jwks_url, timeout=TIMEOUT)
    resposta.raise_for_status()
    chaves = resposta.json()
    _JWKS_CACHE[prov.slug] = (time.time() + _JWKS_TTL, chaves)
    return chaves


def _trocar_codigo(prov: Provider, code: str, *, redirect_uri: str, verifier: str) -> dict:
    resposta = requests.post(
        prov.token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": prov.client_id(),
            "client_secret": prov.client_secret(),
            "code_verifier": verifier,
        },
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if resposta.status_code != 200:
        raise OidcError(f"{prov.slug}: troca de código falhou ({resposta.status_code})")
    return resposta.json()


def verificar_id_token(prov: Provider, id_token: str, *, nonce: str) -> dict:
    """Assinatura, emissor, público, validade e nonce. Nesta ordem, sem pular.

    `jwt.decode` com `options={"verify_signature": False}` seria a linha que
    transforma isto num formulário de "digite quem você quer ser".
    """
    import jwt

    cabecalho = jwt.get_unverified_header(id_token)
    kid = cabecalho.get("kid")
    chave = None
    for candidata in _jwks(prov).get("keys", []):
        if candidata.get("kid") == kid:
            chave = jwt.PyJWK(candidata).key
            break
    if chave is None:
        # Chave desconhecida pode ser rotação: derruba o cache e tenta uma vez.
        _JWKS_CACHE.pop(prov.slug, None)
        for candidata in _jwks(prov).get("keys", []):
            if candidata.get("kid") == kid:
                chave = jwt.PyJWK(candidata).key
                break
    if chave is None:
        raise OidcError(f"{prov.slug}: id_token assinado por chave desconhecida")

    dados = jwt.decode(
        id_token,
        chave,
        algorithms=[cabecalho.get("alg", "RS256")],
        audience=prov.client_id(),
        issuer=prov.issuer,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )
    if not secrets.compare_digest(str(dados.get("nonce", "")), nonce):
        raise OidcError(f"{prov.slug}: nonce não confere")
    return dados


def _email_confirmado_pelo_provedor(dados: dict) -> bool:
    """A Apple manda `email_verified` como string "true" em algumas respostas."""
    valor = dados.get("email_verified", dados.get("is_private_email") is not None)
    return valor in (True, "true", "True", 1, "1")


@dataclass
class Identidade:
    """O que o provedor provou: este e-mail é desta pessoa, agora."""

    provedor: str
    email: str
    nome: str
    sub: str


def concluir(prov: Provider, *, code: str, estado: dict,
             nome_do_formulario: str = "") -> Identidade:
    """Troca o código e verifica o id_token. Não toca no banco.

    Separar "provar a identidade" de "criar conta" é o que permite exigir ano
    de nascimento e aceite dos termos ANTES de existir usuário — conta criada
    sem consentimento seria tratamento sem base legal, e conta de menor criada
    sozinha é exatamente o que o art. 14 proíbe.
    """
    tokens = _trocar_codigo(prov, code, redirect_uri=estado["r"], verifier=estado["v"])
    id_token = tokens.get("id_token")
    if not id_token:
        raise OidcError(f"{prov.slug}: resposta sem id_token")

    dados = verificar_id_token(prov, id_token, nonce=estado["n"])
    email = (dados.get("email") or "").strip().lower()[:200]
    if not email:
        raise OidcError(f"{prov.slug}: id_token sem e-mail")
    if not _email_confirmado_pelo_provedor(dados):
        # Sem essa afirmação, o e-mail é só um texto que o provedor repassou —
        # e vincular por texto é entregar conta alheia.
        raise OidcError(f"{prov.slug}: e-mail não confirmado no provedor")

    nome = (
        nome_do_formulario
        or dados.get("name")
        or dados.get("given_name")
        or email.split("@")[0]
    )[:160]
    return Identidade(provedor=prov.slug, email=email, nome=nome,
                      sub=str(dados.get("sub", ""))[:120])


def conta_existente(db: Session, identidade: Identidade) -> User | None:
    """A conta já existente para este e-mail, se houver — e o pre-hijack.

    O ataque: alguém cadastra `vitima@gmail.com` com senha e nunca confirma.
    A vítima entra com Google meses depois e, se a gente vincular ingenuamente,
    cai dentro da conta do atacante — que continua com a senha.

    O provedor afirmando `email_verified` prova posse do e-mail; a conta local
    não confirmada não prova nada. Então a identidade do provedor vence: o
    e-mail passa a confirmado, a senha é invalidada e todas as sessões caem.
    """
    from agenda.core import sessions

    existente = db.scalars(
        select(User).where(User.email == identidade.email, User.deleted_at.is_(None))
    ).first()
    if existente is None:
        return None

    if existente.email_verified_at is None:
        existente.email_verified_at = dt.datetime.now(dt.timezone.utc)
        existente.password_hash = ""
        derrubadas = sessions.revoke_all(db, existente)
        log(db, user_id=existente.id, actor="system", action="OIDC_TOOK_OVER_UNVERIFIED",
            object_type="user", object_id=existente.id, origin=identidade.provedor,
            after={"sessoes_encerradas": derrubadas})
    else:
        log(db, user_id=existente.id, actor="user", action="LOGIN_OIDC",
            object_type="user", object_id=existente.id, origin=identidade.provedor)
    db.flush()
    return existente


def criar_conta(db: Session, identidade: Identidade, *, birth_year: int) -> User:
    """Cria a conta depois do aceite. Sem senha: não há senha para vazar."""
    user = User(
        name=identidade.nome,
        email=identidade.email,
        password_hash="",
        email_verified_at=dt.datetime.now(dt.timezone.utc),
        timezone="America/Sao_Paulo",
        birth_year=birth_year,
        is_minor=False,
    )
    db.add(user)
    db.flush()
    log(db, user_id=user.id, actor="user", action="REGISTER_OIDC",
        object_type="user", object_id=user.id, origin=identidade.provedor)
    return user


# --------------------------------------------------------------------------- #
# Cookie de estado
# --------------------------------------------------------------------------- #
def selar(estado: dict) -> str:
    from agenda.security import sign_payload

    return sign_payload(estado, ttl_seconds=STATE_TTL_SEGUNDOS)


def abrir(selado: str | None, *, state_recebido: str) -> dict:
    from agenda.security import verify_payload

    if not selado:
        raise OidcError("cookie de estado ausente (login demorou demais?)")
    estado = verify_payload(selado)
    if estado is None:
        raise OidcError("cookie de estado inválido ou expirado")
    if not secrets.compare_digest(str(estado.get("s", "")), state_recebido or ""):
        raise OidcError("state não confere — pedido não começou aqui")
    return estado
