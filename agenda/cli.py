"""Utilitários de linha de comando.

    python -m agenda.cli vapid        gera o par de chaves do Web Push
    python -m agenda.cli secret       gera um SECRET_KEY forte
    python -m agenda.cli migrate      aplica as migrations pendentes
    python -m agenda.cli check        verificação de sanidade da configuração
"""
from __future__ import annotations

import secrets
import sys


def _vapid() -> int:
    from agenda.channels.push import generate_keys

    publica, privada = generate_keys()
    print("VAPID_PUBLIC_KEY=" + publica)
    print("VAPID_PRIVATE_KEY=" + privada)
    print("\nGuarde a privada em variável de ambiente e nunca no repositório.")
    return 0


def _secret() -> int:
    print("SECRET_KEY=" + secrets.token_urlsafe(48))
    return 0


def _migrate() -> int:
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    print("migrations aplicadas.")
    return 0


def _check() -> int:
    from agenda import config

    problemas: list[str] = []
    if config.IS_PRODUCTION:
        if config.SECRET_KEY.startswith("dev-secret"):
            problemas.append("SECRET_KEY ainda é o valor de desenvolvimento.")
        if len(config.SECRET_KEY) < 32:
            problemas.append("SECRET_KEY curta demais (use 32+ caracteres).")
        if config.DATABASE_URL.startswith("sqlite"):
            problemas.append("DATABASE_URL aponta para SQLite em produção.")
        if config.flag("whatsapp_enabled") and not config.WHATSAPP_APP_SECRET:
            problemas.append("WHATSAPP_APP_SECRET ausente: webhook sem validação de assinatura.")
        if not config.PUBLIC_URL:
            problemas.append("PUBLIC_URL não definida: links enviados ficarão quebrados.")
    for problema in problemas:
        print(f"  ✗ {problema}")
    if problemas:
        print(f"\n{len(problemas)} problema(s) de configuração.")
        return 1
    print("configuração ok.")
    return 0


COMMANDS = {"vapid": _vapid, "secret": _secret, "migrate": _migrate, "check": _check}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    raise SystemExit(main())
