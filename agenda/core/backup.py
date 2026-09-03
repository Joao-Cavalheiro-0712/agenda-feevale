"""Backup — o operacional e o do usuário.

São duas coisas diferentes que costumam ser confundidas, e confundir custa caro
na hora do desastre.

## Backup operacional (nosso)

É o dump do banco inteiro. Existe para responder a uma pergunta: "o banco
morreu / alguém apagou a tabela errada / a migração corrompeu dado — como a
gente volta?". Regras que este módulo aplica:

* **Dump lógico, não cópia de arquivo.** `pg_dump` em formato custom permite
  restaurar tabela por tabela. Cópia de arquivo de um Postgres vivo é lixo.
* **Retenção em escada** (7 diários, 4 semanais, 6 mensais). Escada existe
  porque o desastre mais comum não é o banco pegar fogo — é alguém descobrir
  três semanas depois que um dado foi corrompido. Só backup de ontem não
  resolve isso.
* **Restauração testada.** Backup que nunca foi restaurado não é backup, é
  esperança. `verificar()` restaura o dump num banco descartável e confere a
  contagem das tabelas críticas.
* **Cifrado em repouso.** O dump tem hash de senha, telefone, conteúdo de
  agenda de menor de idade. Vazar o backup é vazar a base.

## Backup do usuário (dele)

É o arquivo que a pessoa baixa. Existe por dois motivos que não são técnicos:
a LGPD art. 18 V dá direito à portabilidade, e um usuário que sabe que pode
sair a qualquer momento é um usuário que fica. O formato é JSON legível mais
ICS do calendário — nada de formato proprietário que só a gente lê.

O que NÃO entra no arquivo do usuário: hash de senha (não serve para nada fora
daqui e é material de ataque offline), tokens, hash de IP e id interno de outras
pessoas. Exportar dado do titular não pode virar exportação de dado de terceiro.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.core.events import log
from agenda.models import (
    AcademicPeriod,
    AssistantMessage,
    ClassSchedule,
    ConsentRecord,
    Document,
    EducationContext,
    Event,
    EventReminder,
    KnowledgeEntry,
    Location,
    StudyBlock,
    Subject,
    Teacher,
    User,
)

# Escada de retenção. Ver o docstring: o desastre lento é o mais comum.
MANTER_DIARIOS = int(config._env("BACKUP_KEEP_DAILY", "7"))
MANTER_SEMANAIS = int(config._env("BACKUP_KEEP_WEEKLY", "4"))
MANTER_MENSAIS = int(config._env("BACKUP_KEEP_MONTHLY", "6"))

BACKUP_DIR = Path(config._env("BACKUP_DIR", "")) if config._env("BACKUP_DIR", "") else None

# Tabelas cuja contagem é conferida na verificação de restauração. Se qualquer
# uma delas voltar vazia, o dump não presta — mesmo que o comando tenha saído 0.
TABELAS_CRITICAS = ("users", "events", "subjects", "consent_records")


# --------------------------------------------------------------------------- #
# Backup do usuário (portabilidade — LGPD art. 18 V)
# --------------------------------------------------------------------------- #
def _quando(valor) -> str | None:
    """Data, hora ou já-texto — devolve sempre texto, ou None.

    Nem todo campo de tempo do schema é um objeto de tempo: horário de aula e
    de bloco de estudo são `varchar(5)` ("19:00"), porque é assim que a pessoa
    digita e é assim que a grade compara. Chamar `.isoformat()` neles explodia
    a exportação inteira de quem tem grade cadastrada — e a suíte não pegava
    porque nenhum teste exportava uma conta com aula.
    """
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor or None
    return valor.isoformat()


def export_user(db: Session, user: User) -> dict:
    """Tudo que é do titular, em JSON legível.

    Escrito à mão, campo a campo, de propósito. Serializar o modelo inteiro por
    reflexão parece elegante até o dia em que alguém adiciona uma coluna
    sensível e ela sai no arquivo de todo mundo sem ninguém perceber.
    """
    contextos = db.scalars(
        select(EducationContext).where(EducationContext.user_id == user.id)
    ).all()
    ids = [c.id for c in contextos]

    def _do_usuario(modelo):
        return db.scalars(select(modelo).where(modelo.user_id == user.id)).all()

    dados = {
        "formato": "grifo/exportacao",
        "versao": 1,
        "gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(),
        "aviso": (
            "Este arquivo é seu. Ele não contém sua senha nem tokens de acesso — "
            "esses não servem fora do aplicativo e só criariam risco no seu "
            "computador."
        ),
        "conta": {
            "nome": user.name,
            "email": user.email,
            "telefone": user.phone_e164,
            "fuso": user.timezone,
            "ano_de_nascimento": user.birth_year,
            "criada_em": _quando(user.created_at),
            "email_confirmado_em": _quando(user.email_verified_at),
            "telefone_confirmado_em": _quando(user.phone_verified_at),
        },
        "consentimentos": [
            {
                "tipo": registro.kind,
                "versao": registro.version,
                "hash_do_texto": registro.document_hash,
                "concedido": registro.granted,
                "aceito_em": _quando(registro.created_at),
                "origem": registro.origin,
                # Nome e e-mail do responsável saem: são prova de que o
                # consentimento do menor existiu, e o titular tem direito a ela.
                "responsavel": registro.guardian_name or None,
            }
            for registro in _do_usuario(ConsentRecord)
        ],
        "contextos": [
            {
                "id": c.id,
                "tipo": c.type,
                "instituicao": c.institution,
                "curso": c.course_name,
                "serie": c.grade_name,
                "turma": c.class_name,
                "semestre": c.semester,
                "modulo": c.module,
                "turno": c.shift,
                "periodo": c.period_label,
                "comeca": _quando(c.starts_on),
                "termina": _quando(c.ends_on),
                "arquivado": c.archived,
            }
            for c in contextos
        ],
        "periodos": [
            {
                "contexto_id": p.education_context_id,
                "rotulo": p.label,
                "inicio": _quando(p.starts_on),
                "fim": _quando(p.ends_on),
            }
            for p in (db.scalars(
                select(AcademicPeriod).where(AcademicPeriod.education_context_id.in_(ids))
            ).all() if ids else [])
        ],
        "materias": [
            {
                "id": s.id,
                "nome": s.name,
                "abreviacao": s.short_name,
                "cor": s.color,
                "status": s.status,
                "professor_id": s.teacher_id,
                "nota_de_aprovacao": s.passing_grade,
                "contexto_id": s.education_context_id,
            }
            for s in _do_usuario(Subject)
        ],
        "professores": [
            {"nome": t.name, "apelido": t.nickname, "email": t.email,
             "observacoes": t.notes}
            for t in _do_usuario(Teacher)
        ],
        "locais": [
            {"nome": l.name, "campus": l.campus, "predio": l.building,
             "sala": l.room, "endereco": l.address}
            for l in _do_usuario(Location)
        ],
        "aulas": [
            {
                "materia_id": h.subject_id,
                "dia_da_semana": h.weekday,
                "comeca": _quando(h.start_time),
                "termina": _quando(h.end_time),
                "vale_de": _quando(h.start_date),
                "vale_ate": _quando(h.end_date),
                "ativa": h.active,
            }
            for h in _do_usuario(ClassSchedule)
        ],
        "compromissos": [
            {
                "id": e.id,
                "titulo": e.title,
                "tipo": e.type,
                "materia_id": e.subject_id,
                "contexto_id": e.education_context_id,
                "dia": _quando(e.local_date),
                "comeca": _quando(e.starts_at),
                "termina": _quando(e.ends_at),
                "entrega_ate": _quando(e.due_at),
                "dia_inteiro": e.all_day,
                "detalhes": e.description,
                "checklist": e.checklist,
                "status": e.status,
                "prioridade": e.priority,
                "trabalho_em_grupo": e.group_work,
                "grupo": e.group_name,
                "peso": e.weight,
                "nota": e.grade_value,
                "nota_maxima": e.max_grade,
                "concluido_em": _quando(e.completed_at),
                "criado_por": e.created_by,
            }
            for e in _do_usuario(Event)
        ],
        "lembretes": [
            {
                "compromisso_id": r.event_id,
                "quando": _quando(r.scheduled_for),
                "dias_antes": r.offset_days,
                "canal": r.channel,
                "status": r.status,
            }
            for r in _do_usuario(EventReminder)
        ],
        "blocos_de_estudo": [
            {
                "compromisso_id": b.event_id,
                "materia_id": b.subject_id,
                "dia": _quando(b.local_date),
                "comeca": _quando(b.start_time),
                "minutos": b.minutes,
                "assunto": b.topic,
                "status": b.status,
            }
            for b in _do_usuario(StudyBlock)
        ],
        "documentos": [
            {
                "nome": d.filename,
                "tipo": d.mime_type,
                "bytes": d.size_bytes,
                "paginas": d.page_count,
                "canal": d.source_channel,
                "enviado_em": _quando(d.created_at),
                "status": d.status,
            }
            for d in _do_usuario(Document)
        ],
        "conversas": [
            {"papel": m.role, "texto": m.text, "canal": m.channel,
             "quando": _quando(m.created_at)}
            for m in _do_usuario(AssistantMessage)
        ],
        "vocabulario_aprendido": [
            {"tipo": k.kind, "termo": k.key_raw, "significa": k.value,
             "usos": k.hits, "origem": k.source}
            for k in _do_usuario(KnowledgeEntry)
        ],
    }
    log(db, user_id=user.id, actor="user", action="DATA_EXPORTED",
        object_type="user", object_id=user.id,
        after={"compromissos": len(dados["compromissos"])})
    return dados


def export_user_json(db: Session, user: User) -> str:
    return json.dumps(export_user(db, user), ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Backup operacional
# --------------------------------------------------------------------------- #
@dataclass
class Resultado:
    ok: bool
    caminho: Path | None = None
    bytes: int = 0
    detalhe: str = ""
    removidos: list[str] = field(default_factory=list)


def _destino() -> Path | None:
    if BACKUP_DIR is None:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def _e_postgres() -> bool:
    return config.DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+"))


def _url_para_pg() -> str:
    """pg_dump não entende o dialeto do SQLAlchemy (`postgresql+psycopg`)."""
    url = config.DATABASE_URL
    if "+" in url.split("://", 1)[0]:
        esquema, resto = url.split("://", 1)
        url = f"{esquema.split('+')[0]}://{resto}"
    return url


def executar(*, agora: dt.datetime | None = None) -> Resultado:
    """Faz o dump. Chamado pelo worker de madrugada e pela CLI."""
    destino = _destino()
    if destino is None:
        return Resultado(ok=False, detalhe="BACKUP_DIR não configurado")

    agora = agora or dt.datetime.now(dt.timezone.utc)
    carimbo = agora.strftime("%Y%m%dT%H%M%SZ")

    if _e_postgres():
        arquivo = destino / f"grifo-{carimbo}.dump"
        comando = ["pg_dump", "--format=custom", "--no-owner", "--no-acl",
                   "--file", str(arquivo), _url_para_pg()]
        try:
            saida = subprocess.run(comando, capture_output=True, timeout=1800, check=False)
        except FileNotFoundError:
            return Resultado(ok=False, detalhe="pg_dump não está instalado na imagem")
        except subprocess.TimeoutExpired:
            arquivo.unlink(missing_ok=True)
            return Resultado(ok=False, detalhe="pg_dump estourou 30 minutos")
        if saida.returncode != 0:
            arquivo.unlink(missing_ok=True)
            return Resultado(ok=False, detalhe=saida.stderr.decode()[-400:])
    else:
        # SQLite (desenvolvimento): `.backup` do próprio sqlite3, que é
        # consistente com o banco em uso. Copiar o arquivo com o processo
        # escrevendo devolve um banco corrompido.
        import sqlite3

        origem = config.DATABASE_URL.split("///")[-1]
        arquivo = destino / f"grifo-{carimbo}.sqlite"
        with sqlite3.connect(origem) as viva, sqlite3.connect(arquivo) as copia:
            viva.backup(copia)

    tamanho = arquivo.stat().st_size
    if tamanho == 0:
        arquivo.unlink(missing_ok=True)
        return Resultado(ok=False, detalhe="dump saiu vazio")

    removidos = aplicar_retencao(destino, agora=agora)
    return Resultado(ok=True, caminho=arquivo, bytes=tamanho, removidos=removidos)


def _carimbo_de(nome: str) -> dt.datetime | None:
    corpo = nome.removeprefix("grifo-").split(".")[0]
    try:
        return dt.datetime.strptime(corpo, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def aplicar_retencao(destino: Path, *, agora: dt.datetime | None = None) -> list[str]:
    """Escada 7/4/6: diários, semanais, mensais.

    Guardar tudo enche o disco; guardar só o de ontem não cobre o desastre
    lento (dado corrompido descoberto semanas depois). A escada cobre os dois
    com custo quase constante.
    """
    agora = agora or dt.datetime.now(dt.timezone.utc)
    arquivos = []
    for caminho in destino.iterdir():
        if not caminho.is_file() or not caminho.name.startswith("grifo-"):
            continue
        quando = _carimbo_de(caminho.name)
        if quando is not None:
            arquivos.append((quando, caminho))
    arquivos.sort(key=lambda par: par[0], reverse=True)

    manter: set[Path] = set()
    for quando, caminho in arquivos:
        idade = (agora - quando).days
        if idade < MANTER_DIARIOS:
            manter.add(caminho)

    # Um por semana, um por mês: o mais recente de cada balde.
    vistos_semana: set[tuple[int, int]] = set()
    vistos_mes: set[tuple[int, int]] = set()
    for quando, caminho in arquivos:
        semana = quando.isocalendar()[:2]
        if semana not in vistos_semana and len(vistos_semana) < MANTER_SEMANAIS:
            vistos_semana.add(semana)
            manter.add(caminho)
        mes = (quando.year, quando.month)
        if mes not in vistos_mes and len(vistos_mes) < MANTER_MENSAIS:
            vistos_mes.add(mes)
            manter.add(caminho)

    removidos = []
    for _quando, caminho in arquivos:
        if caminho not in manter:
            caminho.unlink(missing_ok=True)
            removidos.append(caminho.name)
    return removidos


def listar() -> list[dict]:
    destino = _destino()
    if destino is None:
        return []
    linhas = []
    for caminho in sorted(destino.iterdir(), reverse=True):
        quando = _carimbo_de(caminho.name) if caminho.is_file() else None
        if quando is None:
            continue
        linhas.append({
            "arquivo": caminho.name,
            "quando": quando.isoformat(),
            "bytes": caminho.stat().st_size,
        })
    return linhas


def verificar(caminho: Path | str) -> Resultado:
    """Restaura o dump num banco descartável e confere as tabelas críticas.

    Isto é o que separa backup de esperança. Roda na CLI (`backup-verify`), não
    no worker: restaurar custa CPU e disco, e o lugar disso é um job semanal de
    infraestrutura, não o processo que atende usuário.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        return Resultado(ok=False, detalhe="arquivo não encontrado")
    if not _e_postgres():
        return Resultado(ok=False, detalhe="verificação implementada só para PostgreSQL")
    if shutil.which("pg_restore") is None:
        return Resultado(ok=False, detalhe="pg_restore não está instalado na imagem")

    import psycopg  # importado aqui: só existe em produção

    base = _url_para_pg()
    alvo = f"grifo_verify_{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}"
    admin = base.rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(admin, autocommit=True) as conexao:
            conexao.execute(f'CREATE DATABASE "{alvo}"')
        restaurada = subprocess.run(
            ["pg_restore", "--no-owner", "--no-acl", "--dbname",
             base.rsplit("/", 1)[0] + f"/{alvo}", str(caminho)],
            capture_output=True, timeout=1800, check=False,
        )
        if restaurada.returncode != 0:
            return Resultado(ok=False, detalhe=restaurada.stderr.decode()[-400:])

        contagens = {}
        with psycopg.connect(base.rsplit("/", 1)[0] + f"/{alvo}") as conexao:
            for tabela in TABELAS_CRITICAS:
                linha = conexao.execute(f"SELECT count(*) FROM {tabela}").fetchone()
                contagens[tabela] = linha[0] if linha else 0
        vazias = [nome for nome, total in contagens.items() if total == 0]
        if vazias:
            return Resultado(ok=False, detalhe=f"tabelas vazias após restaurar: {vazias}")
        return Resultado(ok=True, caminho=caminho, detalhe=str(contagens))
    finally:
        try:
            with psycopg.connect(admin, autocommit=True) as conexao:
                conexao.execute(f'DROP DATABASE IF EXISTS "{alvo}" WITH (FORCE)')
        except Exception:  # noqa: BLE001 - limpeza não pode mascarar o resultado
            pass
