"""Golden dataset versionado da interpretação (SPEC §102, §103).

Cada linha do dataset é um exemplo real anonimizado. A regra é: nunca
atualizar o resultado esperado só para o teste passar — a mudança precisa de
revisão humana. Rodar a cada mudança de prompt, modelo ou heurística.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from agenda.ai.interpreter import interpret
from agenda.core import academic

DATASET = pathlib.Path(__file__).parent / "golden" / "dataset.jsonl"
CASES = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def _prepare(db, user, case):
    context = academic.active_context(db, user.id)
    teachers = case.get("teachers", {})
    for name in case.get("subjects", []):
        teacher = None
        if name in teachers:
            teacher = academic.upsert_teacher(db, user.id, teachers[name])
        academic.upsert_subject(
            db, user.id, context.id, name, teacher_id=teacher.id if teacher else None
        )
    db.commit()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden(db, user, case):
    _prepare(db, user, case)
    result = interpret(db, user, case["input"])
    assert result.proposals, f"{case['id']}: nenhuma ação proposta"

    proposal = result.proposals[0]
    assert proposal.action == case["expected_intent"], case["id"]

    expected = case["expected"]
    payload = proposal.payload

    if expected.get("needs_clarification"):
        assert proposal.question, f"{case['id']}: deveria perguntar em vez de assumir"
        assert proposal.confidence < 0.7
        return

    if "type" in expected:
        assert payload.get("type") == expected["type"], case["id"]
    if "start_time" in expected:
        assert payload.get("start_time") == expected["start_time"], case["id"]
    if "end_time" in expected:
        assert payload.get("end_time") == expected["end_time"], case["id"]
    if "weekday" in expected and proposal.action == "CREATE_CLASS_SCHEDULE":
        assert expected["weekday"] in {p.payload["weekday"] for p in result.proposals}
    elif "weekday" in expected:
        date = dt.date.fromisoformat(payload["date"])
        assert date.weekday() == expected["weekday"], case["id"]
    if "day" in expected:
        assert dt.date.fromisoformat(payload["date"]).day == expected["day"], case["id"]
    if "subject" in expected:
        subject, _ = academic.resolve_subject(db, user.id, expected["subject"])
        assert payload.get("subject_id") == subject.id, case["id"]


def test_taxa_minima_de_acerto(db, user):
    """Métrica agregada: quantos casos o interpretador resolve sozinho."""
    resolved = 0
    for case in CASES:
        _prepare(db, user, case)
        result = interpret(db, user, case["input"])
        if result.proposals and result.proposals[0].action == case["expected_intent"]:
            resolved += 1
    assert resolved / len(CASES) >= 0.9, f"apenas {resolved}/{len(CASES)} casos resolvidos"
