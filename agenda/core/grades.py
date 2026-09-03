"""Notas, pesos e média por disciplina (SPEC §137).

O modelo já guardava os campos; aqui eles viram cálculo e leitura. Regra:
nunca inventar nota nem projetar aprovação sem o usuário ter informado peso.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda.models import Event, Subject, User


def graded_events(db: Session, user: User, subject_id: str) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.user_id == user.id,
                Event.subject_id == subject_id,
                Event.max_grade.is_not(None),
            )
            .order_by(Event.local_date)
        ).all()
    )


def subject_summary(db: Session, user: User, subject: Subject) -> dict:
    """Média ponderada do que já foi lançado e quanto ainda está em aberto."""
    eventos = graded_events(db, user, subject.id)
    lancados = [e for e in eventos if e.grade_value is not None and e.max_grade]
    escala = subject.grade_scale or 10.0

    peso_total = sum((e.weight or 1) for e in eventos) or 0
    peso_lancado = sum((e.weight or 1) for e in lancados) or 0

    if lancados:
        soma = sum((e.grade_value / e.max_grade) * (e.weight or 1) for e in lancados)
        media = (soma / peso_lancado) * escala if peso_lancado else 0.0
    else:
        media = None

    restante = peso_total - peso_lancado
    necessario = None
    if subject.passing_grade and media is not None and restante > 0 and peso_total:
        alvo = subject.passing_grade * peso_total
        obtido = (media / escala) * peso_lancado * escala
        falta = (alvo - obtido) / restante
        necessario = max(0.0, min(round(falta, 2), escala))

    return {
        "media": round(media, 2) if media is not None else None,
        "escala": escala,
        "lancadas": len(lancados),
        "previstas": len(eventos),
        "peso_lancado": peso_lancado,
        "peso_total": peso_total,
        "passing_grade": subject.passing_grade,
        "necessario": necessario,
        "eventos": [
            {
                "id": e.id,
                "title": e.title,
                "date": e.local_date.isoformat(),
                "grade_value": e.grade_value,
                "max_grade": e.max_grade,
                "weight": e.weight,
            }
            for e in eventos
        ],
    }


def set_grade(
    db: Session,
    user: User,
    event: Event,
    *,
    grade_value: float | None,
    max_grade: float | None = None,
    weight: float | None = None,
) -> Event:
    if event.user_id != user.id:
        raise PermissionError("evento de outro usuário")
    if max_grade is not None:
        event.max_grade = max(0.0, float(max_grade))
    if weight is not None:
        event.weight = max(0.0, float(weight))
    if grade_value is None:
        event.grade_value = None
    else:
        teto = event.max_grade or 10.0
        event.grade_value = max(0.0, min(float(grade_value), teto))
    db.flush()
    return event
