"""Utilitários de linha de comando.

    python -m agenda.cli vapid        gera o par de chaves do Web Push
    python -m agenda.cli secret       gera um SECRET_KEY forte
    python -m agenda.cli migrate      aplica as migrations pendentes
    python -m agenda.cli check        verificação de sanidade da configuração
    python -m agenda.cli reset-schema apaga o schema (exige ALLOW_SCHEMA_RESET=1)
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


# Chave arbitrária, porém estável, do advisory lock de migration no Postgres.
_MIGRATION_LOCK = 728_411_003


def _migrate() -> int:
    """Aplica as migrations com trava, seguro para vários processos ao mesmo tempo.

    O Railway (como qualquer PaaS) pode subir mais de uma instância em paralelo
    num deploy. Sem trava, duas rodam `upgrade head` juntas e a segunda quebra
    com "relation already exists". Aqui só um processo migra por vez; os outros
    esperam e encontram o banco já em dia.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    from agenda.db import engine

    cfg = Config("alembic.ini")
    usa_postgres = engine.url.get_backend_name().startswith("postgres")
    if usa_postgres:
        _ensure_database(engine)

    with engine.connect() as conexao:
        if usa_postgres:
            conexao.execute(text("SELECT pg_advisory_lock(:chave)"), {"chave": _MIGRATION_LOCK})
            conexao.commit()
        try:
            _adopt_baseline(conexao, cfg, inspect(engine))
            command.upgrade(cfg, "head")
            print("migrations aplicadas.")
        finally:
            if usa_postgres:
                conexao.execute(
                    text("SELECT pg_advisory_unlock(:chave)"), {"chave": _MIGRATION_LOCK}
                )
                conexao.commit()
    return 0


def _ensure_database(engine) -> None:
    """Cria o banco informado na URL, se ele ainda não existir.

    Operação puramente aditiva: se o banco existe, não fazemos nada. Isso deixa
    o primeiro deploy funcionar sozinho quando o servidor Postgres já está de
    pé mas o banco da aplicação ainda não foi criado.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    try:
        with engine.connect():
            return  # o banco existe e aceita conexão
    except OperationalError as erro:
        if "does not exist" not in str(erro):
            raise

    nome = engine.url.database
    admin_url = engine.url.set(database="postgres")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conexao:
            existe = conexao.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :nome"), {"nome": nome}
            ).first()
            if existe is None:
                # O nome vem da nossa própria configuração, não do usuário.
                conexao.execute(text(f'CREATE DATABASE "{nome}"'))
                print(f"banco {nome} criado.")
    finally:
        admin.dispose()


def _adopt_baseline(conexao, cfg, inspetor) -> None:
    """Adota um banco que já tem TODAS as tabelas, mas nunca foi versionado.

    Só age no caso inequívoco: o schema está completo e não existe
    `alembic_version`. Se estiver pela metade (uma migration que morreu no
    meio, por exemplo), paramos e explicamos — marcar como aplicado um schema
    incompleto seria esconder o problema até ele aparecer em produção.
    """
    import importlib

    from alembic import command

    from agenda.db import Base

    importlib.import_module("agenda.models")  # sem isso o metadata vem vazio
    tabelas = set(inspetor.get_table_names())
    if "alembic_version" in tabelas or not tabelas:
        return

    esperadas = set(Base.metadata.tables)
    faltando = esperadas - tabelas

    if not faltando:
        print("banco completo e sem versão: marcando a revisão base.")
        command.stamp(cfg, "head")
        return

    if not (tabelas & esperadas):
        return  # banco de outra aplicação: não é problema nosso

    # Schema pela metade — tipicamente uma migration que morreu no meio.
    # Completar só é seguro se ainda não existe NENHUM dado; com dados, paramos
    # e explicamos, porque aí a divergência precisa de olho humano.
    if _sem_dados(conexao, tabelas & esperadas):
        print(
            "schema incompleto e banco vazio: criando as tabelas que faltam "
            f"({len(faltando)}) e marcando a revisão base."
        )
        Base.metadata.create_all(conexao.engine)
        command.stamp(cfg, "head")
        return

    raise SystemExit(
        "Schema incompleto, sem versão do Alembic e COM dados. Faltam: "
        + ", ".join(sorted(faltando)[:8])
        + ". Corrija o schema à mão antes de subir — não vou adivinhar."
    )


def _sem_dados(conexao, tabelas) -> bool:
    """True se todas as tabelas informadas estão vazias."""
    from sqlalchemy import text

    for tabela in tabelas:
        if not tabela.replace("_", "").isalnum():
            return False  # nome inesperado: por segurança, tratamos como "tem dados"
        try:
            existe = conexao.execute(text(f'SELECT 1 FROM "{tabela}" LIMIT 1')).first()
        except Exception:  # noqa: BLE001 - tabela ilegível conta como não vazia
            return False
        if existe is not None:
            return False
    return True


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


def _reset_schema() -> int:
    """Apaga o schema inteiro. Só para banco descartável.

    Exige `ALLOW_SCHEMA_RESET=1` no ambiente — nunca roda por acidente, e nunca
    faz parte do start normal da aplicação.
    """
    import importlib
    import os

    from sqlalchemy import text

    from agenda.db import engine

    if os.environ.get("ALLOW_SCHEMA_RESET") != "1":
        print("recusado: defina ALLOW_SCHEMA_RESET=1 para confirmar que o banco é descartável.")
        return 1

    with engine.connect() as conexao:
        if engine.url.get_backend_name().startswith("postgres"):
            conexao.execute(text("DROP SCHEMA public CASCADE"))
            conexao.execute(text("CREATE SCHEMA public"))
            conexao.commit()
            print("schema public recriado do zero.")
        else:
            from agenda.db import Base

            importlib.import_module("agenda.models")  # sem isso o metadata vem vazio
            Base.metadata.drop_all(engine)
            conexao.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conexao.commit()
            print("tabelas removidas.")
    return 0


COMMANDS = {
    "vapid": _vapid,
    "secret": _secret,
    "migrate": _migrate,
    "check": _check,
    "reset-schema": _reset_schema,
}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    raise SystemExit(main())
