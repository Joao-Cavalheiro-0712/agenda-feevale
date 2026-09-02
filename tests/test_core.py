"""Núcleo determinístico: recorrência, lembretes, duplicados e permissões."""
from __future__ import annotations

import datetime as dt

from agenda.core import academic, duplicates, events as events_core, recurrence
from agenda.core.actions import ActionProposal, Intent, ValidationError, execute, undo, validate
from agenda.models import Event, EventStatus, EventType, ScheduleException, User


def _subject(db, user, name="Direito Penal"):
    context = academic.active_context(db, user.id)
    subject = academic.upsert_subject(db, user.id, context.id, name)
    db.flush()
    return subject


# --------------------------------------------------------------------------- #
# Recorrência
# --------------------------------------------------------------------------- #
def test_expande_aulas_semanais(db, user):
    subject = _subject(db, user)
    academic.upsert_schedule(db, user.id, subject, weekday=0, start_time="19:30", end_time="21:30")
    db.commit()

    occurrences = recurrence.expand_classes(
        db, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30)
    )
    assert [o.date for o in occurrences] == [
        dt.date(2026, 9, 7), dt.date(2026, 9, 14), dt.date(2026, 9, 21), dt.date(2026, 9, 28)
    ]


def test_feriado_cancela_a_aula(db, user):
    subject = _subject(db, user)
    academic.upsert_schedule(db, user.id, subject, weekday=0, start_time="19:30", end_time="21:30")
    db.add(
        ScheduleException(
            user_id=user.id, date=dt.date(2026, 9, 7), kind="HOLIDAY", label="Independência"
        )
    )
    db.commit()

    occurrences = recurrence.expand_classes(
        db, user.id, dt.date(2026, 9, 1), dt.date(2026, 9, 30), include_cancelled=False
    )
    assert dt.date(2026, 9, 7) not in [o.date for o in occurrences]
    assert len(occurrences) == 3


def test_proxima_aula_ignora_cancelamento(db, user):
    subject = _subject(db, user)
    academic.upsert_schedule(db, user.id, subject, weekday=2, start_time="19:30", end_time="21:30")
    db.add(ScheduleException(user_id=user.id, date=dt.date(2026, 9, 9), kind="CANCELLED"))
    db.commit()

    assert recurrence.next_class_date(db, user.id, subject.id, dt.date(2026, 9, 2)) == dt.date(2026, 9, 16)


# --------------------------------------------------------------------------- #
# Lembretes
# --------------------------------------------------------------------------- #
def test_lembretes_padrao_sao_7_e_1_dia_antes(db, user):
    event = events_core.create_event(
        db, user, title="Trabalho", event_type=EventType.ASSIGNMENT.value,
        date=dt.date.today() + dt.timedelta(days=20),
    )
    db.commit()
    offsets = sorted(r.offset_days for r in event.reminders)
    assert offsets == [1, 7]


def test_evento_proximo_recebe_apenas_lembretes_possiveis(db, user):
    event = events_core.create_event(
        db, user, title="Trabalho", event_type=EventType.ASSIGNMENT.value,
        date=dt.date.today() + dt.timedelta(days=3),
    )
    db.commit()
    offsets = sorted(r.offset_days for r in event.reminders)
    assert 7 not in offsets
    assert 1 in offsets


def test_prova_usa_perfil_inteligente(db, user):
    event = events_core.create_event(
        db, user, title="Prova", event_type=EventType.EXAM.value,
        date=dt.date.today() + dt.timedelta(days=30),
    )
    db.commit()
    assert sorted(r.offset_days for r in event.reminders) == [1, 3, 7]


def test_mudar_a_data_recalcula_os_lembretes(db, user):
    event = events_core.create_event(
        db, user, title="Trabalho", event_type=EventType.ASSIGNMENT.value,
        date=dt.date.today() + dt.timedelta(days=3),
    )
    db.commit()
    events_core.update_event(db, user, event, {"date": dt.date.today() + dt.timedelta(days=40)})
    db.commit()
    assert sorted(r.offset_days for r in event.reminders) == [1, 7]


# --------------------------------------------------------------------------- #
# Duplicados e remarcações
# --------------------------------------------------------------------------- #
def test_fingerprint_ignora_variacao_de_pontuacao():
    args = dict(user_id="u1", subject_id="s1", event_type="EXAM", date=dt.date(2026, 9, 18))
    assert duplicates.fingerprint(title="Prova 1", **args) == duplicates.fingerprint(title="prova 1!", **args)


def test_remarcacao_encontra_o_evento_anterior(db, user):
    subject = _subject(db, user, "Direito Civil")
    events_core.create_event(
        db, user, title="Prova 1", event_type=EventType.EXAM.value,
        date=dt.date(2026, 10, 20), subject=subject,
    )
    db.commit()
    candidate = duplicates.find_reschedule_candidate(
        db, user.id, subject_id=subject.id, event_type="EXAM",
        title="Prova", new_date=dt.date(2026, 10, 27),
    )
    assert candidate is not None and candidate.local_date == dt.date(2026, 10, 20)


def test_texto_de_remarcacao_e_reconhecido():
    assert duplicates.looks_like_reschedule("A prova de Civil passou para 27/10")
    assert not duplicates.looks_like_reschedule("Prova de Civil dia 27/10")


# --------------------------------------------------------------------------- #
# Motor de ações
# --------------------------------------------------------------------------- #
def test_schema_rejeita_payload_incompleto():
    proposal = ActionProposal(action=Intent.CREATE_EVENT.value, payload={}, confidence=0.99)
    try:
        validate(proposal)
    except ValidationError as exc:
        assert "title" in str(exc)
    else:
        raise AssertionError("deveria ter rejeitado")


def test_tipo_de_evento_invalido_e_rejeitado(db, user):
    proposal = ActionProposal(
        action=Intent.CREATE_EVENT.value,
        payload={"title": "X", "type": "NAO_EXISTE", "date": "2026-09-20"},
        confidence=0.99,
    )
    assert execute(db, user, proposal).status == "REJECTED"


def test_confianca_baixa_nao_executa(db, user):
    proposal = ActionProposal(
        action=Intent.CREATE_EVENT.value,
        payload={"title": "Prova", "type": "EXAM", "date": "2026-09-20"},
        confidence=0.4,
    )
    result = execute(db, user, proposal)
    assert result.status == "NEEDS_CLARIFICATION"
    assert db.query(Event).count() == 0


def test_confianca_media_pede_confirmacao(db, user):
    proposal = ActionProposal(
        action=Intent.CREATE_EVENT.value,
        payload={"title": "Prova", "type": "EXAM", "date": "2026-09-20"},
        confidence=0.8,
    )
    result = execute(db, user, proposal)
    assert result.status == "NEEDS_CONFIRMATION"
    assert result.action_id
    assert db.query(Event).count() == 0


def test_confianca_alta_executa_e_permite_desfazer(db, user):
    proposal = ActionProposal(
        action=Intent.CREATE_EVENT.value,
        payload={"title": "Prova de Penal", "type": "EXAM", "date": "2026-09-20"},
        confidence=0.97,
        model="teste",
    )
    result = execute(db, user, proposal)
    assert result.status == "EXECUTED"
    assert db.query(Event).count() == 1

    undone = undo(db, user, result.action_id)
    assert undone.status == "EXECUTED"
    assert db.query(Event).count() == 0


def test_undo_de_atualizacao_restaura_a_data(db, user):
    event = events_core.create_event(
        db, user, title="Prova", event_type=EventType.EXAM.value, date=dt.date(2026, 10, 20)
    )
    db.commit()
    result = execute(
        db, user,
        ActionProposal(
            action=Intent.UPDATE_EVENT.value,
            payload={"event_id": event.id, "date": "2026-10-27"},
            confidence=0.99,
        ),
    )
    assert result.status == "EXECUTED"
    assert db.get(Event, event.id).local_date == dt.date(2026, 10, 27)

    undo(db, user, result.action_id)
    assert db.get(Event, event.id).local_date == dt.date(2026, 10, 20)


def test_exclusao_sempre_exige_confirmacao(db, user):
    event = events_core.create_event(
        db, user, title="Prova", event_type=EventType.EXAM.value, date=dt.date(2026, 10, 20)
    )
    db.commit()
    proposal = ActionProposal(
        action=Intent.DELETE_EVENT.value, payload={"event_id": event.id}, confidence=1.0
    )
    assert execute(db, user, proposal).status == "NEEDS_CONFIRMATION"
    assert db.get(Event, event.id).status == EventStatus.UPCOMING.value


def test_nao_mexe_em_evento_de_outro_usuario(db, user):
    from agenda.security import hash_password

    other = User(name="Maria", email="maria@example.com", password_hash=hash_password("x" * 9))
    db.add(other)
    db.flush()
    event = events_core.create_event(
        db, other, title="Prova alheia", event_type=EventType.EXAM.value, date=dt.date(2026, 10, 20)
    )
    db.commit()

    result = execute(
        db, user,
        ActionProposal(
            action=Intent.UPDATE_EVENT.value,
            payload={"event_id": event.id, "date": "2026-11-01"},
            confidence=1.0,
        ),
        confirmed=True,
    )
    assert result.status == "REJECTED"
    assert db.get(Event, event.id).local_date == dt.date(2026, 10, 20)


def test_evento_duplicado_nao_e_criado_duas_vezes(db, user):
    payload = {"title": "Prova 1", "type": "EXAM", "date": "2026-09-18"}
    for _ in range(2):
        execute(db, user, ActionProposal(action=Intent.CREATE_EVENT.value, payload=dict(payload), confidence=0.97))
    assert db.query(Event).count() == 1
