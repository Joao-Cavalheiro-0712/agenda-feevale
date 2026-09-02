"""Interpretação heurística — os fluxos de MVP da especificação (§116-§119).

Estes testes rodam sem chave de IA: garantem o piso de qualidade do produto.
"""
from __future__ import annotations

import datetime as dt

from agenda.core import academic, assistant, events as events_core
from agenda.core.actions import Intent
from agenda.core.planner import today_of
from agenda.ai.interpreter import interpret
from agenda.models import Event, EventType


def _subject(db, user, name, teacher=None):
    context = academic.active_context(db, user.id)
    teacher_obj = academic.upsert_teacher(db, user.id, teacher) if teacher else None
    subject = academic.upsert_subject(
        db, user.id, context.id, name, teacher_id=teacher_obj.id if teacher_obj else None
    )
    db.commit()
    return subject


def test_trabalho_por_texto_com_data_relativa(db, user):
    _subject(db, user, "Direito Civil")
    result = interpret(
        db, user,
        "A professora de Civil pediu um trabalho sobre responsabilidade civil para dia 23. Vale 2 pontos.",
    )
    proposal = result.proposals[0]
    assert proposal.intent is Intent.CREATE_EVENT
    assert proposal.payload["type"] == EventType.ASSIGNMENT.value
    assert proposal.payload["date"].endswith("-23")
    assert "responsabilidade civil" in proposal.payload["title"].lower()
    assert "2 pontos" in proposal.payload["description"]


def test_material_para_o_fundamental(db, user):
    _subject(db, user, "Artes")
    result = interpret(db, user, "Preciso levar cartolina e cola para Artes na quarta")
    proposal = result.proposals[0]
    assert proposal.payload["type"] == EventType.MATERIAL.value
    assert proposal.payload["title"].lower().startswith("levar cartolina")
    assert proposal.payload["subject_id"]


def test_prova_do_ensino_medio(db, user):
    _subject(db, user, "Física")
    result = interpret(db, user, "Professor de física marcou prova sexta sobre cinemática")
    proposal = result.proposals[0]
    assert proposal.payload["type"] == EventType.EXAM.value
    assert "cinemática" in proposal.payload["title"].lower()
    assert dt.date.fromisoformat(proposal.payload["date"]).weekday() == 4


def test_entrega_tecnica(db, user):
    _subject(db, user, "Eletricidade")
    result = interpret(db, user, "Relatório do laboratório de elétrica é para dia 22")
    proposal = result.proposals[0]
    assert proposal.payload["type"] == EventType.LAB.value
    assert proposal.payload["date"].endswith("-22")


def test_professor_resolve_a_materia(db, user):
    _subject(db, user, "Direito Penal", teacher="Ricardo Silva")
    result = interpret(db, user, "O professor Ricardo marcou prova sexta")
    proposal = result.proposals[0]
    assert proposal.payload.get("subject_id")


def test_professor_ambiguo_gera_pergunta(db, user):
    context = academic.active_context(db, user.id)
    teacher = academic.upsert_teacher(db, user.id, "Ricardo Silva")
    academic.upsert_subject(db, user.id, context.id, "Direito Penal", teacher_id=teacher.id)
    academic.upsert_subject(db, user.id, context.id, "Direito Constitucional", teacher_id=teacher.id)
    db.commit()

    result = interpret(db, user, "O professor Ricardo marcou prova sexta")
    proposal = result.proposals[0]
    assert proposal.question
    assert len(proposal.options) == 2
    assert proposal.confidence <= 0.7


def test_sem_data_pergunta_em_vez_de_inventar(db, user):
    _subject(db, user, "História")
    result = interpret(db, user, "A professora de história passou um trabalho")
    proposal = result.proposals[0]
    assert "date" not in proposal.payload
    assert proposal.question
    assert proposal.confidence < 0.7


def test_pergunta_da_semana_vira_consulta(db, user):
    result = interpret(db, user, "O que eu tenho essa semana?")
    assert result.proposals[0].intent is Intent.GET_WEEK


def test_pergunta_de_atrasados(db, user):
    result = interpret(db, user, "Tenho alguma coisa atrasada?")
    assert result.proposals[0].intent is Intent.GET_OVERDUE


def test_aula_recorrente_por_texto(db, user):
    result = interpret(db, user, "Tenho Penal segunda e quarta das sete e meia às nove e meia")
    assert len(result.proposals) == 2
    assert {p.payload["weekday"] for p in result.proposals} == {0, 2}
    # Turno noturno: "sete e meia" é 19:30 (SPEC §7).
    assert result.proposals[0].payload["start_time"] == "19:30"
    assert result.proposals[0].payload["end_time"] == "21:30"


def test_remarcacao_explicita_vira_atualizacao(db, user):
    subject = _subject(db, user, "Direito Civil")
    today = today_of(user)
    events_core.create_event(
        db, user, title="Prova 1", event_type=EventType.EXAM.value,
        date=today + dt.timedelta(days=20), subject=subject,
    )
    db.commit()

    result = interpret(
        db, user,
        f"A prova de Civil passou para {(today + dt.timedelta(days=27)).strftime('%d/%m')}",
    )
    proposal = result.proposals[0]
    assert proposal.intent is Intent.UPDATE_EVENT
    assert proposal.confidence >= 0.9


def test_data_conflitante_sem_contexto_pergunta(db, user):
    subject = _subject(db, user, "Direito Civil")
    today = today_of(user)
    events_core.create_event(
        db, user, title="Prova 1", event_type=EventType.EXAM.value,
        date=today + dt.timedelta(days=20), subject=subject,
    )
    db.commit()

    result = interpret(
        db, user, f"Prova de Civil dia {(today + dt.timedelta(days=27)).strftime('%d/%m')}"
    )
    proposal = result.proposals[0]
    assert proposal.question
    assert any(option["value"].startswith("reschedule:") for option in proposal.options)


# --------------------------------------------------------------------------- #
# Assistente ponta a ponta (sem IA)
# --------------------------------------------------------------------------- #
def test_assistente_cria_e_responde(db, user):
    _subject(db, user, "Direito Civil")
    response = assistant.handle_message(db, user, "Prova de Civil dia 23 sobre contratos")
    assert response["status"] in ("EXECUTED", "NEEDS_CONFIRMATION")
    if response["status"] == "EXECUTED":
        assert db.query(Event).count() == 1
        assert "Vou te lembrar" in response["message"]


def test_assistente_responde_consulta_sem_criar_nada(db, user):
    response = assistant.handle_message(db, user, "O que eu tenho hoje?")
    assert response["status"] == "ANSWERED"
    assert db.query(Event).count() == 0


def test_assistente_confirma_acao_pendente(db, user):
    _subject(db, user, "História")
    response = assistant.handle_message(db, user, "trabalho de história dia 30")
    if response["status"] == "NEEDS_CONFIRMATION":
        confirmed = assistant.confirm(db, user, response["action_id"])
        assert confirmed["status"] == "EXECUTED"
    assert db.query(Event).count() == 1
