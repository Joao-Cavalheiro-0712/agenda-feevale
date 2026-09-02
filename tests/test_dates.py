"""Resolução temporal — o ponto mais crítico do produto (SPEC §21, §92)."""
import datetime as dt

import pytest

from agenda.core.dates import (
    human_delta,
    parse_time,
    resolve_expression,
    resolve_year,
)

WEDNESDAY = dt.date(2026, 9, 2)  # quarta-feira


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("hoje", dt.date(2026, 9, 2)),
        ("amanhã", dt.date(2026, 9, 3)),
        ("depois de amanhã", dt.date(2026, 9, 4)),
        ("sexta", dt.date(2026, 9, 4)),
        ("sexta que vem", dt.date(2026, 9, 11)),
        ("próxima segunda", dt.date(2026, 9, 7)),
        ("semana que vem", dt.date(2026, 9, 7)),
        ("dia 15", dt.date(2026, 9, 15)),
        ("dia vinte e três", dt.date(2026, 9, 23)),
        ("daqui a duas semanas", dt.date(2026, 9, 16)),
        ("em 3 dias", dt.date(2026, 9, 5)),
        ("fim do mês", dt.date(2026, 9, 30)),
        ("23/09", dt.date(2026, 9, 23)),
        ("23/09/2027", dt.date(2027, 9, 23)),
        ("23 de setembro", dt.date(2026, 9, 23)),
        ("2026-10-27", dt.date(2026, 10, 27)),
    ],
)
def test_resolve(expression, expected):
    assert resolve_expression(expression, WEDNESDAY).date == expected


def test_dia_do_mes_rola_para_o_mes_seguinte():
    # Dia 1 já passou em setembro: deve cair em outubro.
    assert resolve_expression("dia 1", WEDNESDAY).date == dt.date(2026, 10, 1)


def test_weekday_hoje_conta_como_hoje():
    assert resolve_expression("quarta", WEDNESDAY).date == WEDNESDAY


def test_ancora_sem_aula_pergunta_em_vez_de_inventar():
    resolution = resolve_expression("na próxima aula", WEDNESDAY)
    assert resolution.date is None
    assert resolution.needs_clarification
    assert resolution.question


def test_ancora_usa_o_horario_da_disciplina():
    resolution = resolve_expression(
        "na próxima aula", WEDNESDAY, next_class_date=lambda: dt.date(2026, 9, 9)
    )
    assert resolution.date == dt.date(2026, 9, 9)


def test_expressao_desconhecida_nao_vira_data():
    assert resolve_expression("quando der", WEDNESDAY).date is None


def test_ano_inferido_olha_para_frente():
    # Fevereiro já passou: assume o ano seguinte.
    assert resolve_year(10, 2, WEDNESDAY) == 2027
    # Setembro é o mês corrente.
    assert resolve_year(30, 9, WEDNESDAY) == 2026


def test_ano_explicito_no_passado_e_respeitado():
    assert resolve_expression("15/04/2024", WEDNESDAY).date == dt.date(2024, 4, 15)


@pytest.mark.parametrize(
    "text,shift,expected",
    [
        ("19:30", "", "19:30"),
        ("19h30", "", "19:30"),
        ("8h", "", "08:00"),
        ("sete e meia", "noite", "19:30"),
        ("sete e meia", "manha", "07:30"),
        ("nove", "noite", "21:00"),
        ("8 da noite", "", "20:00"),
    ],
)
def test_parse_time(text, shift, expected):
    assert parse_time(text, shift=shift) == expected


def test_human_delta():
    assert human_delta(WEDNESDAY, WEDNESDAY) == "hoje"
    assert human_delta(WEDNESDAY + dt.timedelta(days=1), WEDNESDAY) == "amanhã"
    assert human_delta(WEDNESDAY + dt.timedelta(days=3), WEDNESDAY) == "em 3 dias"
