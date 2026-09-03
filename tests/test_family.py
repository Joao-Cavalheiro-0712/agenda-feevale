"""Conta família: convites, permissões e isolamento (SPEC §59)."""
from __future__ import annotations

import pytest

from agenda.core import billing, family
from agenda.models import PlanTier, User
from agenda.security import hash_password


@pytest.fixture
def responsavel(db):
    pessoa = User(
        name="Ana (mãe)",
        email="ana@example.com",
        password_hash=hash_password("outrasenha123"),
        onboarding_done=True,
    )
    db.add(pessoa)
    db.commit()
    return pessoa


def _com_plano_familia(db, user):
    billing.change_plan(db, user, PlanTier.FAMILY.value)
    db.commit()


def test_convite_so_vira_vinculo_depois_do_aceite(db, user, responsavel):
    _com_plano_familia(db, user)
    convite = family.invite(db, user, email="ana@example.com")
    db.commit()

    assert convite.status == "PENDING"
    assert family.students_of(db, responsavel) == []

    vinculo = family.accept(db, responsavel, convite.invite_code)
    db.commit()
    assert vinculo is not None and vinculo.status == "ACTIVE"
    assert len(family.students_of(db, responsavel)) == 1


def test_codigo_invalido_nao_cria_vinculo(db, responsavel):
    assert family.accept(db, responsavel, "NAOEXISTE") is None


def test_convite_nao_pode_ser_aceito_duas_vezes(db, user, responsavel):
    _com_plano_familia(db, user)
    convite = family.invite(db, user)
    db.commit()
    assert family.accept(db, responsavel, convite.invite_code) is not None
    db.commit()
    assert family.accept(db, responsavel, convite.invite_code) is None


def test_sem_plano_familia_o_vinculo_e_recusado(db, user, responsavel):
    convite = family.invite(db, user)
    db.commit()
    assert family.accept(db, responsavel, convite.invite_code) is None


def test_ninguem_e_responsavel_por_si_mesmo(db, user):
    _com_plano_familia(db, user)
    convite = family.invite(db, user)
    db.commit()
    assert family.accept(db, user, convite.invite_code) is None


def test_permissoes_controlam_o_que_o_responsavel_ve(db, user, responsavel):
    _com_plano_familia(db, user)
    convite = family.invite(db, user)
    db.commit()
    vinculo = family.accept(db, responsavel, convite.invite_code)
    db.commit()

    assert family.can_view(db, responsavel, user.id)
    vinculo.can_view_agenda = False
    db.commit()
    assert not family.can_view(db, responsavel, user.id)


def test_revogar_encerra_o_acesso(db, user, responsavel):
    _com_plano_familia(db, user)
    convite = family.invite(db, user)
    db.commit()
    vinculo = family.accept(db, responsavel, convite.invite_code)
    db.commit()

    assert family.revoke(db, user, vinculo.id) is True
    db.commit()
    assert not family.can_view(db, responsavel, user.id)
    assert family.students_of(db, responsavel) == []


def test_estranho_nao_revoga_vinculo_alheio(db, user, responsavel):
    _com_plano_familia(db, user)
    convite = family.invite(db, user)
    db.commit()
    vinculo = family.accept(db, responsavel, convite.invite_code)
    db.commit()

    estranho = User(name="X", email="x@example.com", password_hash=hash_password("outrasenha123"))
    db.add(estranho)
    db.commit()
    assert family.revoke(db, estranho, vinculo.id) is False


def test_agenda_do_estudante_exige_vinculo_ativo(app, db, user, responsavel):
    _com_plano_familia(db, user)
    convite = family.invite(db, user)
    db.commit()

    client = app.test_client()
    client.get("/entrar")
    with client.session_transaction() as session:
        token = session.get("csrf")
    client.post("/entrar", data={
        "csrf_token": token, "email": "ana@example.com", "password": "outrasenha123",
    })

    # Sem vínculo: 404 (não confirmamos nem que o estudante existe).
    assert client.get(f"/familia/{user.id}/agenda").status_code == 404

    family.accept(db, responsavel, convite.invite_code)
    db.commit()
    assert client.get(f"/familia/{user.id}/agenda").status_code == 200


def test_responsavel_recebe_copia_do_lembrete(db, user, responsavel):
    import datetime as dt

    from agenda.core import events as events_core, notifications
    from agenda.models import Notification

    _com_plano_familia(db, user)
    convite = family.invite(db, user)
    db.commit()
    family.accept(db, responsavel, convite.invite_code)
    db.commit()

    evento = events_core.create_event(
        db, user, title="Prova de matemática", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=30),
    )
    db.commit()
    futuro = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=29)
    notifications.run_due_reminders(db, now=futuro)
    db.commit()

    do_responsavel = db.query(Notification).filter_by(user_id=responsavel.id).all()
    assert do_responsavel and "Prova de matemática" in do_responsavel[0].body
    assert evento.id is not None
