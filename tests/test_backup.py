"""Backup: o do usuário e o nosso.

O teste mais importante deste arquivo é o que garante que hash de senha, token
e hash de IP **não** saem no arquivo do usuário. Exportação de dados é a porta
mais fácil de esquecer aberta: ela existe justamente para entregar tudo, e um
campo a mais ali é um vazamento com a assinatura do próprio produto.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from agenda.core import academic, backup, events as events_core
from agenda.models import User
from agenda.security import hash_password


# --------------------------------------------------------------------------- #
# O que sai (e o que nunca pode sair)
# --------------------------------------------------------------------------- #
def _com_dados(db, user):
    contexto = academic.active_context(db, user.id)
    materia = academic.upsert_subject(db, user.id, contexto.id, "Cálculo I")
    events_core.create_event(
        db, user, title="Prova de integrais", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=3), subject=materia,
    )
    db.commit()
    return materia


def test_exportacao_traz_o_que_e_do_titular(db, user):
    _com_dados(db, user)
    dados = backup.export_user(db, user)

    assert dados["conta"]["email"] == user.email
    assert any(m["nome"] == "Cálculo I" for m in dados["materias"])
    assert any(c["titulo"] == "Prova de integrais" for c in dados["compromissos"])
    assert dados["consentimentos"], "o aceite tem de estar no arquivo (art. 8º §1º)"


def test_exportacao_nunca_carrega_segredo(db, user):
    _com_dados(db, user)
    bruto = backup.export_user_json(db, user)

    # Hash de senha é material de ataque offline; token abre a conta; hash de
    # IP é dado de outra natureza que não ajuda em nada quem exporta.
    assert user.password_hash not in bruto
    for proibido in ("password_hash", "token_hash", "ip_hash", "csrf", "secret"):
        assert proibido not in bruto, f"{proibido} vazou na exportação"


def test_exportacao_nao_atravessa_para_outra_conta(db, user):
    outro = User(name="Maria", email="maria@example.com",
                 password_hash=hash_password("segredo1234"), onboarding_done=True)
    db.add(outro)
    db.flush()
    from agenda.models import EducationContext

    contexto = EducationContext(user_id=outro.id, type="UNDERGRAD", institution="Outra")
    db.add(contexto)
    db.flush()
    materia = academic.upsert_subject(db, outro.id, contexto.id, "Segredo Alheio")
    events_core.create_event(
        db, outro, title="Prova secreta da Maria", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=2), subject=materia,
    )
    db.commit()

    bruto = backup.export_user_json(db, user)
    assert "Segredo Alheio" not in bruto
    assert "Prova secreta da Maria" not in bruto


def test_exportacao_e_json_valido_e_legivel(db, user):
    _com_dados(db, user)
    recarregado = json.loads(backup.export_user_json(db, user))
    assert recarregado["formato"] == "grifo/exportacao"
    assert recarregado["versao"] == 1


def test_rota_de_download_entrega_arquivo(app, db, user):
    _com_dados(db, user)
    client = app.test_client()
    client.get("/entrar")
    with client.session_transaction() as sessao:
        token = sessao.get("csrf", "")
    client.post("/entrar", data={"csrf_token": token, "email": user.email,
                                 "password": "segredo123"})

    resposta = client.get("/conta/meus-dados.json")
    assert resposta.status_code == 200
    assert "attachment" in resposta.headers["Content-Disposition"]
    # A agenda inteira da pessoa não pode ficar em cache de proxy.
    assert "no-store" in resposta.headers["Cache-Control"]
    assert json.loads(resposta.get_data(as_text=True))["conta"]["email"] == user.email


def test_download_exige_login(app):
    assert app.test_client().get("/conta/meus-dados.json").status_code in (302, 401)


# --------------------------------------------------------------------------- #
# Backup operacional
# --------------------------------------------------------------------------- #
def test_dump_do_sqlite_gera_arquivo_utilizavel(tmp_path, monkeypatch, db, user):
    _com_dados(db, user)
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path)
    resultado = backup.executar()

    assert resultado.ok, resultado.detalhe
    assert resultado.bytes > 0
    # A cópia tem de ser um banco de verdade, não um arquivo truncado.
    import sqlite3

    with sqlite3.connect(resultado.caminho) as copia:
        total = copia.execute("SELECT count(*) FROM users").fetchone()[0]
    assert total >= 1


def test_sem_backup_dir_a_falha_e_explicita(monkeypatch):
    """Falhar em silêncio é a pior falha possível num backup."""
    monkeypatch.setattr(backup, "BACKUP_DIR", None)
    resultado = backup.executar()
    assert not resultado.ok
    assert "BACKUP_DIR" in resultado.detalhe


def _fabricar(destino: Path, quando: dt.datetime) -> Path:
    caminho = destino / f"grifo-{quando:%Y%m%dT%H%M%SZ}.dump"
    caminho.write_bytes(b"x" * 16)
    return caminho


def test_retencao_em_escada_guarda_o_passado_distante(tmp_path):
    """O desastre mais comum não é o banco pegar fogo — é o dado corrompido
    descoberto três semanas depois. Só o backup de ontem não resolve isso."""
    agora = dt.datetime(2026, 9, 3, 3, 0, tzinfo=dt.timezone.utc)
    for dias in range(0, 200):
        _fabricar(tmp_path, agora - dt.timedelta(days=dias))

    backup.aplicar_retencao(tmp_path, agora=agora)
    restantes = sorted(p.name for p in tmp_path.iterdir())

    assert len(restantes) == len(set(restantes))
    # Sete diários no mínimo, e alguma coisa de meses atrás.
    assert len(restantes) >= backup.MANTER_DIARIOS
    antigos = [n for n in restantes if backup._carimbo_de(n) < agora - dt.timedelta(days=60)]
    assert antigos, "a escada tem de alcançar meses atrás"
    # E não pode guardar tudo: 200 arquivos viram um disco cheio.
    assert len(restantes) < 30


def test_retencao_nao_apaga_arquivo_estranho(tmp_path):
    """Um arquivo que não é nosso no diretório nunca pode ser apagado."""
    (tmp_path / "NAO-MEXER.txt").write_text("relatório do jurídico")
    _fabricar(tmp_path, dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))

    backup.aplicar_retencao(tmp_path, agora=dt.datetime(2026, 9, 3, tzinfo=dt.timezone.utc))
    assert (tmp_path / "NAO-MEXER.txt").exists()


def test_listagem_ordena_do_mais_novo_para_o_mais_velho(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path)
    base = dt.datetime(2026, 9, 3, tzinfo=dt.timezone.utc)
    for dias in (0, 5, 30):
        _fabricar(tmp_path, base - dt.timedelta(days=dias))

    linhas = backup.listar()
    assert [l["quando"] for l in linhas] == sorted(
        (l["quando"] for l in linhas), reverse=True
    )


def test_verificacao_exige_postgres(tmp_path, monkeypatch):
    """Em SQLite a verificação diz que não se aplica em vez de mentir 'ok'."""
    arquivo = _fabricar(tmp_path, dt.datetime(2026, 9, 3, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(backup, "_e_postgres", lambda: False)
    resultado = backup.verificar(arquivo)
    assert not resultado.ok
    assert "PostgreSQL" in resultado.detalhe


def test_verificacao_de_arquivo_inexistente_falha(tmp_path):
    assert not backup.verificar(tmp_path / "nao-existe.dump").ok


def test_url_do_postgres_perde_o_dialeto(monkeypatch):
    """pg_dump não entende `postgresql+psycopg://`."""
    from agenda import config

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg://u:s@h:5432/d")
    assert backup._url_para_pg() == "postgresql://u:s@h:5432/d"
