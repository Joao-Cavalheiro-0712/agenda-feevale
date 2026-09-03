"""Personalização por nível de ensino e períodos letivos (SPEC §4, §65, §132)."""
from __future__ import annotations

import datetime as dt

import pytest

from agenda.core import academic, periods, profiles
from agenda.core.events import create_event, type_label
from agenda.core.reminders import offsets_for
from agenda.models import AcademicPeriod, EducationContext, EducationType, PeriodKind


def _contexto(db, user, tipo, **kwargs):
    for antigo in academic.list_contexts(db, user.id, include_archived=True):
        antigo.is_active = False
        antigo.archived = True
    context = EducationContext(user_id=user.id, type=tipo, is_active=True, **kwargs)
    db.add(context)
    db.commit()
    return context


# --------------------------------------------------------------------------- #
# Perfis
# --------------------------------------------------------------------------- #
def test_todos_os_niveis_tem_perfil_completo():
    assert set(profiles.PROFILES) == {t.value for t in EducationType}
    for chave, perfil in profiles.PROFILES.items():
        assert perfil.label and perfil.event_types and perfil.home_blocks
        assert perfil.default_period_kind in {k.value for k in PeriodKind}
        assert perfil.default_type in perfil.event_types or perfil.default_type == "OTHER"
        assert perfil.reminder_offsets


@pytest.mark.parametrize(
    "nivel,esperado",
    [
        ("ELEMENTARY", "Tema de casa"),
        ("MIDDLE_SCHOOL", "Tarefa"),
        ("HIGH_SCHOOL", "Lista de exercícios"),
        ("UNDERGRAD", "Tarefa de casa"),
    ],
)
def test_vocabulario_muda_por_nivel(nivel, esperado):
    assert type_label("HOMEWORK", nivel) == esperado


def test_tipos_oferecidos_sao_diferentes_por_nivel():
    fundamental = [k for k, _ in profiles.offered_types("ELEMENTARY")]
    doutorado = [k for k, _ in profiles.offered_types("DOCTORATE")]
    assert "MATERIAL" in fundamental and "MATERIAL" not in doutorado
    assert "PAPER" in doutorado and "PAPER" not in fundamental
    # O fundamental abre pelo tema de casa; o doutorado, pelo artigo.
    assert fundamental[0] == "HOMEWORK" and doutorado[0] == "PAPER"


def test_home_do_fundamental_comeca_pelo_que_levar():
    assert profiles.profile_for("ELEMENTARY").home_blocks[0] == "levar"
    assert profiles.profile_for("UNDERGRAD").home_blocks[0] == "hoje"


def test_recursos_por_nivel():
    assert profiles.has_feature("ELEMENTARY", profiles.FEATURE_GUARDIAN)
    assert not profiles.has_feature("DOCTORATE", profiles.FEATURE_GUARDIAN)
    assert profiles.has_feature("TECHNICAL", profiles.FEATURE_LAB)
    assert profiles.has_feature("MASTERS", profiles.FEATURE_RESEARCH)


def test_perfil_de_menor_e_identificado():
    assert profiles.is_minor_profile("ELEMENTARY")
    assert profiles.is_minor_profile("EARLY_CHILDHOOD")
    assert not profiles.is_minor_profile("UNDERGRAD")


def test_lembretes_seguem_o_nivel(db, user):
    contexto = _contexto(db, user, "DOCTORATE")
    evento = create_event(
        db, user, title="Submissão do artigo", event_type="PAPER",
        date=dt.date.today() + dt.timedelta(days=60), context_id=contexto.id,
    )
    db.commit()
    # Doutorado tem prazo longo: o perfil pede 30/14/7/2 dias.
    assert 30 in offsets_for(evento, user, smart=False, education_type="DOCTORATE")
    assert offsets_for(evento, user, smart=False, education_type="ELEMENTARY") == [2, 1]


# --------------------------------------------------------------------------- #
# Períodos letivos
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "kind,quantidade",
    [("SEMESTER", 2), ("TRIMESTER", 3), ("BIMESTER", 4), ("QUADMESTER", 3), ("ANNUAL", 1)],
)
def test_quantidade_de_periodos_por_tipo(kind, quantidade):
    assert len(periods.plan_periods(kind, year=2026)) == quantidade


def test_periodos_padrao_nao_se_sobrepoem():
    for kind in ("SEMESTER", "TRIMESTER", "BIMESTER", "QUADMESTER"):
        planos = periods.plan_periods(kind, year=2026)
        for anterior, seguinte in zip(planos, planos[1:]):
            assert anterior["ends_on"] < seguinte["starts_on"], kind


def test_datas_do_usuario_sao_respeitadas():
    planos = periods.plan_periods(
        "TRIMESTER", year=2026, starts_on=dt.date(2026, 2, 3), ends_on=dt.date(2026, 12, 18)
    )
    assert planos[0]["starts_on"] == dt.date(2026, 2, 3)
    assert planos[-1]["ends_on"] == dt.date(2026, 12, 18)


def test_contexto_tecnico_cria_trimestres(db, user):
    contexto = _contexto(
        db, user, "TECHNICAL",
        period_kind="TRIMESTER",
        starts_on=dt.date(2026, 2, 1), ends_on=dt.date(2026, 12, 15),
    )
    criados = periods.ensure_periods(db, contexto, today=dt.date(2026, 6, 10))
    db.commit()
    assert len(criados) == 3
    atual = periods.current_period(db, contexto, today=dt.date(2026, 6, 10))
    assert atual is not None and atual.sequence == 2


def test_virar_periodo_arquiva_e_copia_materias(db, user):
    contexto = _contexto(
        db, user, "UNDERGRAD", period_kind="SEMESTER",
        starts_on=dt.date(2026, 2, 1), ends_on=dt.date(2026, 12, 15),
    )
    periods.ensure_periods(db, contexto, today=dt.date(2026, 3, 1))
    atual = periods.current_period(db, contexto, today=dt.date(2026, 3, 1))
    materia = academic.upsert_subject(db, user.id, contexto.id, "Direito Penal")
    materia.academic_period_id = atual.id
    academic.upsert_schedule(db, user.id, materia, weekday=0, start_time="19:30", end_time="21:30")
    db.commit()

    novo = periods.start_next_period(
        db, contexto, copy_subjects=True, today=dt.date(2026, 3, 1)
    )
    db.commit()

    assert novo.is_current and novo.sequence == 2
    assert db.get(AcademicPeriod, atual.id).archived is True
    materias = academic.list_subjects(db, user.id, context_id=contexto.id, active_only=False)
    assert len(materias) == 2                      # a original e a cópia
    copia = next(m for m in materias if m.academic_period_id == novo.id)
    assert copia.name == "Direito Penal"
    # Nada foi apagado: o histórico do período anterior continua lá.
    assert any(m.status == "COMPLETED" for m in materias)


def test_onboarding_de_cada_nivel_grava_o_periodo_certo(client, db, user):
    with client.session_transaction() as session:
        token = session.get("csrf")
    resposta = client.post("/onboarding", data={
        "csrf_token": token, "type": "HIGH_SCHOOL", "institution": "Escola Nova",
        "grade_name": "2ª série", "period_kind": "TRIMESTER", "shift": "manha",
    })
    assert resposta.status_code == 302
    contexto = [c for c in academic.list_contexts(db, user.id) if c.type == "HIGH_SCHOOL"][0]
    assert contexto.period_kind == "TRIMESTER"
    assert len(periods.list_periods(db, user.id, context_id=contexto.id)) == 3


def test_onboarding_de_crianca_desliga_automacao(client, db, user):
    with client.session_transaction() as session:
        token = session.get("csrf")
    dados = {
        "csrf_token": token, "type": "ELEMENTARY", "institution": "Escola",
        "grade_name": "5º ano", "period_kind": "BIMESTER",
    }
    # Conta adulta escolhendo nível infantil primeiro responde de quem é a
    # agenda (ver test_privacidade.py); aqui interessa o efeito depois disso.
    assert client.post("/onboarding", data=dados).status_code == 200
    resposta = client.post("/onboarding", data={**dados, "confirmo_adulto": "1"})
    assert resposta.status_code == 302
    db.expire_all()
    assert db.get(type(user), user.id).auto_create_enabled is False
