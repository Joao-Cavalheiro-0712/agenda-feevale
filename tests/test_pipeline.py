"""Pipeline documental sem IA: validação, extração e importação (SPEC §9)."""
from __future__ import annotations

import pytest

from agenda.core import academic
from agenda.ingest import pipeline
from agenda.ingest.text_extract import extract_pages
from agenda.models import Document, DocumentStatus, Event

CRONOGRAMA = """Cronograma - Direito Penal - 2026/2
Aula 1 - 10/09/2026 - Introducao
Prova G1 - 18/09/2026 - Capitulos 1 a 4
Entrega do trabalho sobre crimes - 02/10/2026
Seminario em grupo - 20/10/2026
""".encode("utf-8")


def test_rejeita_formato_nao_suportado():
    with pytest.raises(pipeline.UploadError):
        pipeline.validate_upload("virus.exe", b"MZ\x90\x00")


def test_rejeita_executavel_disfarcado():
    with pytest.raises(pipeline.UploadError):
        pipeline.validate_upload("cronograma.pdf", b"MZ\x90\x00mais bytes")


def test_rejeita_arquivo_gigante():
    with pytest.raises(pipeline.UploadError):
        pipeline.validate_upload("a.txt", b"x" * (26 * 1024 * 1024))


def test_extrai_paginas_de_texto():
    pages = extract_pages("cronograma.txt", CRONOGRAMA)
    assert len(pages) == 1
    assert "Prova G1" in pages[0]["text"]
    assert pages[0]["needs_vision"] is False


def test_ingestao_gera_candidatos_para_revisao(db, user):
    academic.upsert_subject(
        db, user.id, academic.active_context(db, user.id).id, "Direito Penal"
    )
    db.commit()

    document = pipeline.ingest(db, user, "cronograma.txt", CRONOGRAMA)
    db.commit()

    assert document.status in (DocumentStatus.READY.value, DocumentStatus.NEEDS_REVIEW.value)
    events = [item for item in document.extractions if item.kind == "event"]
    assert len(events) >= 3
    dates = {item.payload["date"] for item in events}
    assert "2026-09-18" in dates
    assert "2026-10-02" in dates
    # Nada é criado antes da confirmação (SPEC §9).
    assert db.query(Event).count() == 0


def test_importacao_cria_eventos_com_proveniencia(db, user):
    document = pipeline.ingest(db, user, "cronograma.txt", CRONOGRAMA)
    db.commit()
    ids = [item.id for item in document.extractions if item.kind == "event"]
    created = pipeline.confirm(db, user, document, selected_ids=ids)
    db.commit()

    assert created["events"] == len(ids)
    assert document.status == DocumentStatus.IMPORTED.value
    event = db.query(Event).first()
    assert event.source_type == "DOCUMENT"
    assert event.source_id == document.id
    assert event.source_reference["document"] == "cronograma.txt"
    # Lembretes já ficam programados na importação.
    assert event.reminders != []


def test_mesmo_arquivo_nao_e_processado_duas_vezes(db, user):
    first = pipeline.ingest(db, user, "cronograma.txt", CRONOGRAMA)
    db.commit()
    second = pipeline.ingest(db, user, "cronograma.txt", CRONOGRAMA)
    db.commit()
    assert first.id == second.id
    assert db.query(Document).count() == 1


def test_importar_duas_vezes_nao_duplica_eventos(db, user):
    document = pipeline.ingest(db, user, "cronograma.txt", CRONOGRAMA)
    db.commit()
    ids = [item.id for item in document.extractions if item.kind == "event"]
    pipeline.confirm(db, user, document, selected_ids=ids)
    db.commit()
    total = db.query(Event).count()

    pipeline.confirm(db, user, document, selected_ids=ids)
    db.commit()
    assert db.query(Event).count() == total


def test_arquivo_sem_texto_falha_com_mensagem_util(db, user):
    document = pipeline.ingest(db, user, "vazio.txt", b"   \n  ")
    db.commit()
    assert document.status == DocumentStatus.FAILED.value
    assert "não consegui" in document.error.lower()
