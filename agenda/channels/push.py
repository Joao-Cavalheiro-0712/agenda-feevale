"""Web Push (SPEC §51, §60).

As chaves VAPID são geradas localmente (`python -m agenda.cli vapid`) e
guardadas em variáveis de ambiente — nenhuma chave de terceiro é necessária.
O envio usa o protocolo padrão de Web Push; sem chaves configuradas o módulo
degrada em silêncio e o app segue funcionando com notificação in-app.
"""
from __future__ import annotations

import base64
import json

import requests

from sqlalchemy.orm import Session

from agenda import config
from agenda.core import scope
from agenda.models import PushSubscription, User


def is_configured() -> bool:
    return bool(config.VAPID_PUBLIC_KEY and config.VAPID_PRIVATE_KEY)


def generate_keys() -> tuple[str, str]:
    """Gera um par VAPID (pública, privada) em base64url, pronto para o .env."""
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key()

    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    public_bytes = public.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    private_value = private.private_numbers().private_value
    private_bytes = private_value.to_bytes(32, "big")
    return _b64(public_bytes), _b64(private_bytes)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# Serviços de push dos navegadores. É uma lista curta e estável porque só
# existem quatro fabricantes de navegador — o que torna a allowlist a defesa
# certa aqui, e não uma manutenção eterna.
_PUSH_HOSTS_SUFIXOS = (
    ".googleapis.com",            # Chrome, Edge, Opera (FCM)
    ".push.services.mozilla.com",  # Firefox
    ".notify.windows.com",         # Windows / Edge legado
    ".push.apple.com",             # Safari
)


def endpoint_permitido(endpoint: str) -> bool:
    """Se este endereço de push pode ser aceito.

    O endereço vem do navegador do usuário, mas quem o envia para nós é código
    do cliente — ou seja, é dado não confiável que o SERVIDOR vai buscar depois.
    Sem allowlist, qualquer pessoa cadastra `https://algo.interno:8443/x` e
    passa a usar os nossos lembretes para bater em serviços internos.
    """
    from urllib.parse import urlparse

    if not endpoint or len(endpoint) > 800:
        return False
    partes = urlparse(endpoint)
    if partes.scheme != "https" or partes.port not in (None, 443):
        return False
    host = (partes.hostname or "").lower()
    if not host:
        return False
    return any(host == s.lstrip(".") or host.endswith(s) for s in _PUSH_HOSTS_SUFIXOS)


def subscriptions_of(db: Session, user: User) -> list[PushSubscription]:
    return list(db.scalars(scope.query(PushSubscription, user.id)).all())


def can_send(db: Session, user: User) -> bool:
    return is_configured() and bool(subscriptions_of(db, user))


def send(db: Session, user: User, *, title: str, body: str, url: str = "/hoje") -> int:
    """Envia para todos os dispositivos do usuário. Remove inscrições mortas."""
    if not is_configured():
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover - dependência opcional
        print("[push] pywebpush não instalado; pulando envio.")
        return 0

    enviados = 0
    payload = json.dumps({"title": title, "body": body, "url": url})
    # Sem redirecionamento: o endereço do push é dado do usuário, e seguir um
    # 307 para http://169.254.169.254 transformaria o nosso servidor em
    # ferramenta de varredura da rede interna.
    sessao = requests.Session()
    sessao.max_redirects = 0
    for inscricao in subscriptions_of(db, user):
        try:
            webpush(
                subscription_info={"endpoint": inscricao.endpoint, "keys": inscricao.keys or {}},
                data=payload,
                vapid_private_key=config.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{config.VAPID_CONTACT}"},
                timeout=10,
                requests_session=sessao,
            )
            enviados += 1
        except WebPushException as exc:  # pragma: no cover - depende de rede
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                # Inscrição expirada: o navegador não existe mais.
                db.delete(inscricao)
            else:
                print(f"[push] falha ao enviar: {exc}")
        except Exception as exc:  # noqa: BLE001 - push nunca derruba o fluxo
            print(f"[push] erro inesperado: {exc}")
    db.flush()
    return enviados


def register(db: Session, user: User, endpoint: str, keys: dict | None) -> PushSubscription:
    """Registra o dispositivo. Endpoint é único por usuário."""
    existente = next(
        (s for s in subscriptions_of(db, user) if s.endpoint == endpoint), None
    )
    if existente is not None:
        existente.keys = keys
        db.flush()
        return existente
    inscricao = PushSubscription(user_id=user.id, endpoint=endpoint, keys=keys)
    db.add(inscricao)
    db.flush()
    return inscricao


def unregister(db: Session, user: User, endpoint: str) -> bool:
    inscricao = next((s for s in subscriptions_of(db, user) if s.endpoint == endpoint), None)
    if inscricao is None:
        return False
    db.delete(inscricao)
    db.flush()
    return True
