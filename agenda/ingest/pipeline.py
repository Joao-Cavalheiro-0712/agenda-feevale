"""Pipeline de ingestão documental (SPEC §9, §90, §100, §113, §114).

Upload → validação → hash → extração nativa → (visão só onde precisa) →
extração estruturada por IA → validação determinística → score de confiança →
reconciliação → preview → confirmação → persistência.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.ai import prompts
from agenda.ai.context import build_context_block
from agenda.ai.providers import ai_available, get_provider, get_vision_provider, record_usage
from agenda.core import privacy
from agenda.core import academic, duplicates, events as events_core, planner
from agenda.core.dates import parse_explicit_date, resolve_expression
from agenda.core.text import norm
from agenda.ingest import text_extract
from agenda.models import (
    Document,
    DocumentExtraction,
    DocumentPage,
    DocumentStatus,
    Event,
    EventType,
    ScheduleException,
    SourceType,
    Subject,
    User,
)

_SAFE_ID = re.compile(r"[A-Za-z0-9-]{8,64}")

STEPS = [
    ("received", "Documento recebido"),
    ("extracted", "Texto extraído"),
    ("interpreted", "Datas e matérias identificadas"),
    ("checked", "Conflitos verificados"),
]


class UploadError(Exception):
    pass


# Assinatura esperada por extensão (SPEC §79): a extensão sozinha não vale nada.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
    ".doc": (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".heic": (b"\x00\x00\x00",),
}

# Cabeçalhos de executável/script que nunca podem entrar, em qualquer extensão.
_BLOQUEADOS = (
    b"MZ",           # PE (Windows)
    b"\x7fELF",      # ELF (Linux)
    b"\xca\xfe\xba\xbe",  # Mach-O / class Java
    b"#!",           # script com shebang
    b"<?php",
    b"<script",
)


def validate_upload(filename: str, data: bytes) -> None:
    """Validação de upload (SPEC §79).

    Três camadas: extensão na allowlist, conteúdo compatível com a extensão
    (magic bytes) e recusa de qualquer coisa com cara de executável ou script.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in config.ALLOWED_UPLOAD_EXTENSIONS:
        raise UploadError(
            f"Formato não suportado ({ext or 'sem extensão'}). "
            "Envie PDF, DOC, DOCX, XLSX, CSV, TXT ou imagem."
        )
    if not data:
        raise UploadError("Arquivo vazio.")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError(f"Arquivo maior que {config.MAX_UPLOAD_MB} MB.")

    cabecalho = data[:16]
    inicio_texto = data[:64].lstrip().lower()
    for assinatura in _BLOQUEADOS:
        if cabecalho.startswith(assinatura) or inicio_texto.startswith(assinatura):
            raise UploadError("Esse arquivo parece um executável ou script e não foi aceito.")

    esperados = _MAGIC.get(ext)
    if esperados and not any(cabecalho.startswith(m) for m in esperados):
        raise UploadError(
            f"O conteúdo do arquivo não corresponde a um {ext.lstrip('.').upper()} válido."
        )


def store_file(user_id: str, document_id: str, filename: str, data: bytes) -> str:
    """Grava o arquivo dentro da pasta do usuário.

    O nome no disco é derivado de identificadores gerados por nós (uuid do
    usuário e do documento) e de uma extensão da allowlist — nada que venha do
    cliente entra no caminho, então não há travessia de diretório possível.
    """
    if not _SAFE_ID.fullmatch(user_id) or not _SAFE_ID.fullmatch(document_id):
        raise UploadError("Identificador inválido.")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in config.ALLOWED_UPLOAD_EXTENSIONS:
        raise UploadError("Extensão não permitida.")

    folder = os.path.realpath(os.path.join(config.STORAGE_DIR, user_id))
    raiz = os.path.realpath(config.STORAGE_DIR)
    if not folder.startswith(raiz + os.sep):
        raise UploadError("Caminho de armazenamento inválido.")
    os.makedirs(folder, mode=0o700, exist_ok=True)

    path = os.path.join(folder, f"{document_id}{ext}")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as handle:
        handle.write(data)
    return path


def ingest(
    db: Session,
    user: User,
    filename: str,
    data: bytes,
    *,
    source_channel: str = SourceType.WEB_CAPTURE.value,
    mime_type: str = "",
) -> Document:
    """Cria o documento e processa de forma síncrona-curta.

    Em produção o processamento pesado roda em worker (``jobs.queue``); aqui a
    função é idempotente por hash: reenviar o mesmo arquivo reaproveita a
    extração (SPEC §114).

    A quota de documentos é cobrada AQUI, e não no chamador: a rota de API
    cobrava e a rota de formulário não, então o limite de 3 documentos do plano
    grátis era inaplicável por um caminho que existia o tempo todo. Regra
    geral: quem gasta é quem cobra.
    """
    from agenda.core import billing

    billing.enforce(db, user, billing.MAX_DOCUMENT_IMPORTS, "document_imports")
    validate_upload(filename, data)
    digest = hashlib.sha256(data).hexdigest()

    existing = db.scalars(
        select(Document).where(
            Document.user_id == user.id,
            Document.sha256 == digest,
            Document.status.in_([DocumentStatus.READY.value, DocumentStatus.NEEDS_REVIEW.value, DocumentStatus.IMPORTED.value]),
        )
    ).first()
    if existing is not None:
        # Reenviar o mesmo arquivo não custa quota de novo: é o mesmo trabalho,
        # já feito, e cobrar duas vezes puniria quem só tocou no botão errado.
        return existing

    billing.consume(db, user, "document_imports")
    document = Document(
        user_id=user.id,
        filename=filename[:300],
        mime_type=mime_type[:120],
        size_bytes=len(data),
        sha256=digest,
        source_channel=source_channel,
        status=DocumentStatus.QUEUED.value,
        progress=[{"key": key, "label": label, "done": False} for key, label in STEPS],
    )
    context = academic.active_context(db, user.id)
    if context:
        document.education_context_id = context.id
    db.add(document)
    db.flush()

    if config.DOCUMENT_RETENTION_DAYS != -1:
        try:
            document.storage_path = store_file(user.id, document.id, filename, data)
        except OSError:
            document.storage_path = ""

    process(db, user, document, data)
    return document


def _mark(document: Document, key: str) -> None:
    progress = document.progress or []
    for step in progress:
        if step["key"] == key:
            step["done"] = True
    document.progress = list(progress)


def process(db: Session, user: User, document: Document, data: bytes) -> Document:
    """Extrai, interpreta e gera os itens candidatos."""
    document.status = DocumentStatus.EXTRACTING.value
    _mark(document, "received")
    db.flush()

    try:
        pages = text_extract.extract_pages(document.filename, data)
    except ValueError as exc:
        document.status = DocumentStatus.FAILED.value
        document.error = str(exc)
        return document
    except Exception as exc:  # noqa: BLE001
        document.status = DocumentStatus.FAILED.value
        document.error = f"Não consegui ler o arquivo: {exc}"
        return document

    document.page_count = len(pages)
    for page in pages:
        db.add(
            DocumentPage(
                document_id=document.id,
                page_number=page["page"],
                text=page.get("text", "")[:200000],
                needs_vision=bool(page.get("needs_vision")),
            )
        )
    _mark(document, "extracted")
    document.status = DocumentStatus.INTERPRETING.value
    db.flush()

    text_blob = text_extract.pages_to_prompt_text(pages)
    vision_pages = [p for p in pages if p.get("needs_vision")]

    extracted = None
    # Sem consentimento de interpretação automática, nada sai daqui: cai na
    # heurística local, que roda inteiramente no nosso servidor.
    if ai_available() and privacy.ai_allowed(user):
        if text_blob.strip():
            extracted = _extract_with_ai(db, user, document, text_blob)
        elif vision_pages and text_extract.is_image(document.filename):
            extracted = _extract_with_vision(db, user, document, data)

    if extracted is None:
        if not text_blob.strip():
            document.status = DocumentStatus.FAILED.value
            document.error = (
                "Não consegui ler texto deste arquivo. "
                "Tente uma foto mais nítida ou envie o PDF original."
            )
            return document
        extracted = _extract_heuristic(db, user, text_blob)

    _persist_candidates(db, user, document, extracted)
    _mark(document, "interpreted")
    _mark(document, "checked")

    needs_review = any(item.needs_review for item in document.extractions)
    document.status = (
        DocumentStatus.NEEDS_REVIEW.value if needs_review else DocumentStatus.READY.value
    )
    db.flush()
    return document


# --------------------------------------------------------------------------- #
# Extração
# --------------------------------------------------------------------------- #
def _extract_with_ai(db: Session, user: User, document: Document, text_blob: str) -> dict | None:
    provider = get_provider()
    prompt = prompts.document_prompt(
        document_text=text_blob,
        context_block=build_context_block(db, user),
        today=planner.today_of(user).isoformat(),
        filename=document.filename,
    )
    result = provider.structured(prompt, prompts.DOCUMENT_SCHEMA, model=config.AI_MODEL_FAST)
    if not result.ok or not isinstance(result.data, dict):
        return None
    record_usage(db, user_id=user.id, operation="document_extract", result=result)
    return result.data


def _extract_with_vision(db: Session, user: User, document: Document, data: bytes) -> dict | None:
    provider = get_vision_provider()
    result = provider.read_image(
        data,
        document.mime_type or "image/jpeg",
        prompts.vision_prompt(today=planner.today_of(user).isoformat()),
        prompts.DOCUMENT_SCHEMA,
    )
    if not result.ok or not isinstance(result.data, dict):
        return None
    record_usage(db, user_id=user.id, operation="document_vision", result=result, image_pages=1)
    return result.data


HEURISTIC_KEYWORDS = (
    "prova", "avaliacao", "trabalho", "entrega", "seminario", "apresentacao",
    "exame", "teste", "atividade", "relatorio", "projeto", "g1", "g2",
    "recuperacao", "simulado", "artigo", "leitura",
)


def _extract_heuristic(db: Session, user: User, text_blob: str) -> dict:
    """Reserva sem IA: linhas com palavra-chave + data explícita."""
    today = planner.today_of(user)
    subjects = academic.list_subjects(db, user.id)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    current_page = 1

    for line in text_blob.splitlines():
        clean = line.strip()
        if clean.startswith("--- Página"):
            try:
                current_page = int(clean.split()[2])
            except (IndexError, ValueError):
                pass
            continue
        if not clean:
            continue
        low = norm(clean)
        if not any(k in low for k in HEURISTIC_KEYWORDS):
            continue
        resolution = parse_explicit_date(clean, today)
        if not resolution.ok:
            continue
        subject_name = ""
        for subject in subjects:
            names = {norm(subject.name), norm(subject.short_name)} - {""}
            names |= {a.alias_norm for a in subject.aliases}
            if any(n in low for n in names):
                subject_name = subject.name
                break
        from agenda.ai.heuristics import detect_type, guess_title

        event_type, _ = detect_type(clean)
        title = guess_title(clean, event_type, subject_name)
        key = (norm(title), resolution.date.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "title": title,
                "type": event_type,
                "subject": subject_name,
                "date": resolution.date.isoformat(),
                "description": clean[:400],
                "confidence": round(min(0.85, 0.55 + resolution.confidence * 0.3), 2),
                "page": current_page,
                "excerpt": clean[:200],
            }
        )
    return {"events": out, "subjects": [], "schedules": [], "calendar_entries": []}


# --------------------------------------------------------------------------- #
# Persistência dos candidatos
# --------------------------------------------------------------------------- #
def _persist_candidates(db: Session, user: User, document: Document, data: dict) -> None:
    today = planner.today_of(user)
    context = academic.active_context(db, user.id)

    for raw in data.get("subjects", []) or []:
        name = str(raw.get("name", "")).strip()
        if not name:
            continue
        confidence = float(raw.get("confidence", 0.8) or 0.8)
        db.add(
            DocumentExtraction(
                document_id=document.id, user_id=user.id, kind="subject",
                payload={"name": name, "teacher": raw.get("teacher", "")},
                confidence=confidence,
                needs_review=confidence < config.CONFIDENCE_AUTO,
            )
        )

    for raw in data.get("schedules", []) or []:
        try:
            weekday = int(raw.get("weekday"))
        except (TypeError, ValueError):
            continue
        if not 0 <= weekday <= 6 or not raw.get("start_time"):
            continue
        confidence = float(raw.get("confidence", 0.8) or 0.8)
        db.add(
            DocumentExtraction(
                document_id=document.id, user_id=user.id, kind="schedule",
                payload={
                    "subject_name": raw.get("subject", ""),
                    "weekday": weekday,
                    "start_time": str(raw.get("start_time"))[:5],
                    "end_time": str(raw.get("end_time") or raw.get("start_time"))[:5],
                    "location_name": raw.get("location", ""),
                },
                confidence=confidence,
                needs_review=confidence < config.CONFIDENCE_AUTO,
            )
        )

    for raw in data.get("events", []) or []:
        item = _normalize_event(db, user, raw, today, context)
        if item is None:
            continue
        db.add(
            DocumentExtraction(
                document_id=document.id,
                user_id=user.id,
                kind="event",
                payload=item["payload"],
                confidence=item["confidence"],
                needs_review=item["needs_review"],
                review_reason=item["reason"],
                source_reference={
                    "document": document.filename,
                    "page": raw.get("page"),
                    "excerpt": (raw.get("excerpt") or "")[:300],
                },
            )
        )

    for raw in data.get("calendar_entries", []) or []:
        resolution = parse_explicit_date(str(raw.get("date", "")), today)
        if not resolution.ok:
            continue
        db.add(
            DocumentExtraction(
                document_id=document.id, user_id=user.id, kind="calendar",
                payload={
                    "label": str(raw.get("label", ""))[:160],
                    "kind": raw.get("kind", "HOLIDAY"),
                    "date": resolution.date.isoformat(),
                    "end_date": (parse_explicit_date(str(raw.get("end_date", "")), today).date or resolution.date).isoformat(),
                },
                confidence=0.9,
                needs_review=False,
            )
        )
    db.flush()


def _normalize_event(db, user, raw: dict, today: dt.date, context) -> dict | None:
    title = str(raw.get("title", "")).strip()
    if not title:
        return None
    event_type = str(raw.get("type", EventType.OTHER.value))
    if event_type not in {t.value for t in EventType}:
        event_type = EventType.OTHER.value

    subject = None
    subject_name = str(raw.get("subject", "") or "")
    if subject_name:
        subject, _ = academic.resolve_subject(
            db, user.id, subject_name, context_id=context.id if context else None
        )

    confidence = float(raw.get("confidence", 0.7) or 0.7)
    reason = ""

    date = None
    raw_date = str(raw.get("date", "") or "")
    if raw_date:
        resolution = parse_explicit_date(raw_date, today)
        date = resolution.date
    if date is None and raw.get("date_expression"):
        resolution = resolve_expression(str(raw["date_expression"]), today)
        if resolution.ok:
            date = resolution.date
            confidence = min(confidence, 0.8)
        else:
            reason = resolution.question or "Data relativa não resolvida."
    if date is None:
        # Sem data não criamos evento automaticamente — vai para revisão (SPEC §92).
        reason = reason or "Sem data identificada."
        confidence = min(confidence, 0.4)

    payload = {
        "title": title[:300],
        "type": event_type,
        "subject_name": subject_name,
        "subject_id": subject.id if subject else None,
        "date": date.isoformat() if date else None,
        "date_expression": raw.get("date_expression", ""),
        "start_time": str(raw.get("start_time") or "")[:5] or None,
        "end_time": str(raw.get("end_time") or "")[:5] or None,
        "description": str(raw.get("description", "") or "")[:800],
        "location_name": str(raw.get("location", "") or ""),
    }

    needs_review = date is None or confidence < config.CONFIDENCE_AUTO
    if date is not None and subject_name and subject is None:
        needs_review = True
        reason = reason or f"Matéria “{subject_name}” ainda não existe."
    if date is not None and date < today - dt.timedelta(days=365):
        needs_review = True
        reason = reason or "Data muito antiga — confira o ano."
    return {
        "payload": {k: v for k, v in payload.items() if v not in (None, "")},
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "needs_review": needs_review,
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# Confirmação / importação
# --------------------------------------------------------------------------- #
def summary(db: Session, document: Document) -> dict:
    items = document.extractions
    events = [i for i in items if i.kind == "event"]
    return {
        "events": len(events),
        "subjects": len([i for i in items if i.kind == "subject"]),
        "schedules": len([i for i in items if i.kind == "schedule"]),
        "calendar": len([i for i in items if i.kind == "calendar"]),
        "needs_review": len([i for i in items if i.needs_review]),
        "auto": len([i for i in events if not i.needs_review]),
    }


def confirm(
    db: Session, user: User, document: Document, *, selected_ids: list[str] | None = None
) -> dict:
    """Importa os itens escolhidos. Nada é criado sem passar por aqui."""
    created_events = 0
    created_subjects = 0
    created_schedules = 0
    context = academic.active_context(db, user.id)

    def chosen(item: DocumentExtraction) -> bool:
        if selected_ids is None:
            return not item.needs_review
        return item.id in selected_ids

    for item in [i for i in document.extractions if i.kind == "subject" and chosen(i)]:
        if context is None:
            break
        teacher_name = item.payload.get("teacher") or ""
        teacher = academic.upsert_teacher(db, user.id, teacher_name) if teacher_name else None
        academic.upsert_subject(
            db, user.id, context.id, item.payload["name"],
            teacher_id=teacher.id if teacher else None,
        )
        item.accepted = True
        created_subjects += 1

    for item in [i for i in document.extractions if i.kind == "schedule" and chosen(i)]:
        if context is None:
            break
        name = item.payload.get("subject_name") or ""
        if not name:
            continue
        subject, _ = academic.resolve_subject(db, user.id, name, context_id=context.id)
        if subject is None:
            subject = academic.upsert_subject(db, user.id, context.id, name)
        location = None
        if item.payload.get("location_name"):
            location = academic.upsert_location(db, user.id, item.payload["location_name"])
        academic.upsert_schedule(
            db, user.id, subject,
            weekday=int(item.payload["weekday"]),
            start_time=item.payload["start_time"],
            end_time=item.payload.get("end_time") or item.payload["start_time"],
            location_id=location.id if location else None,
            start_date=context.starts_on,
            end_date=context.ends_on,
        )
        item.accepted = True
        created_schedules += 1

    for item in [i for i in document.extractions if i.kind == "calendar" and chosen(i)]:
        db.add(
            ScheduleException(
                user_id=user.id,
                education_context_id=context.id if context else None,
                date=dt.date.fromisoformat(item.payload["date"]),
                end_date=dt.date.fromisoformat(item.payload.get("end_date", item.payload["date"])),
                kind=item.payload.get("kind", "HOLIDAY") if item.payload.get("kind") in ("HOLIDAY", "BREAK") else "HOLIDAY",
                label=item.payload.get("label", ""),
            )
        )
        item.accepted = True

    for item in [i for i in document.extractions if i.kind == "event" and chosen(i)]:
        payload = item.payload
        if not payload.get("date"):
            continue
        subject = None
        if payload.get("subject_id"):
            subject = db.get(Subject, payload["subject_id"])
        elif payload.get("subject_name") and context:
            subject, _ = academic.resolve_subject(
                db, user.id, payload["subject_name"], context_id=context.id
            )
            if subject is None:
                subject = academic.upsert_subject(db, user.id, context.id, payload["subject_name"])

        date = dt.date.fromisoformat(payload["date"])
        fingerprint = duplicates.fingerprint(
            user_id=user.id, subject_id=subject.id if subject else None,
            event_type=payload["type"], date=date, title=payload["title"],
        )
        existing = db.scalars(
            select(Event).where(Event.user_id == user.id, Event.fingerprint == fingerprint)
        ).first()
        if existing is not None:
            item.accepted = True
            item.created_event_id = existing.id
            continue

        location = None
        if payload.get("location_name"):
            location = academic.upsert_location(db, user.id, payload["location_name"])
        event = events_core.create_event(
            db, user,
            title=payload["title"],
            event_type=payload["type"],
            date=date,
            subject=subject,
            context_id=context.id if context else None,
            description=payload.get("description", ""),
            start_time=payload.get("start_time"),
            end_time=payload.get("end_time"),
            location=location,
            confidence=item.confidence,
            source_type=SourceType.DOCUMENT.value,
            source_id=document.id,
            source_reference=item.source_reference,
            created_by="import",
        )
        item.accepted = True
        item.created_event_id = event.id
        created_events += 1

    document.status = DocumentStatus.IMPORTED.value
    document.imported_at = dt.datetime.now(dt.timezone.utc)
    events_core.log(
        db, user_id=user.id, actor="user", action="IMPORT_DOCUMENT",
        object_type="document", object_id=document.id,
        after={"events": created_events, "subjects": created_subjects, "schedules": created_schedules},
        origin=document.source_channel,
    )
    db.flush()
    return {
        "events": created_events,
        "subjects": created_subjects,
        "schedules": created_schedules,
    }
