"""Onboarding por voz (SPEC §7).

O estudante conta como são os estudos dele; nós extraímos curso, período,
matérias e horários — e mostramos tudo para revisão antes de gravar. Nada de
baixa confiança entra em silêncio.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from agenda import config
from agenda.ai import prompts
from agenda.ai.providers import ai_available, get_provider, get_speech_provider, record_usage
from agenda.core import academic, periods, profiles
from agenda.core.dates import WEEKDAY_LABELS, parse_time
from agenda.models import EducationContext, EducationType, PeriodKind, User


def transcribe(db: Session, user: User, audio: bytes, mime_type: str) -> str:
    if not ai_available():
        return ""
    resultado = get_speech_provider().transcribe(audio, mime_type or "audio/webm")
    if not resultado.ok:
        return ""
    record_usage(db, user_id=user.id, operation="onboarding_transcribe", result=resultado)
    return resultado.text.strip()


def interpret(db: Session, user: User, transcript: str) -> dict:
    """Transcrição → estrutura revisável. Sem IA, devolve vazio com aviso."""
    if not transcript.strip():
        return {"ok": False, "reason": "Não entendi o áudio.", "subjects": []}
    if not ai_available():
        return {
            "ok": False,
            "reason": "A leitura por voz precisa da chave de IA configurada.",
            "transcript": transcript,
            "subjects": [],
        }

    prompt = prompts.onboarding_prompt(transcript=transcript, today=dt.date.today().isoformat())
    resultado = get_provider().structured(
        prompt, prompts.ONBOARDING_SCHEMA, model=config.AI_MODEL_STRONG
    )
    if not resultado.ok or not isinstance(resultado.data, dict):
        return {"ok": False, "reason": "Não consegui organizar o que você falou.", "subjects": []}
    record_usage(db, user_id=user.id, operation="onboarding_interpret", result=resultado)

    dados = resultado.data
    tipo = str(dados.get("education_type") or EducationType.UNDERGRAD.value)
    if tipo not in {t.value for t in EducationType}:
        tipo = EducationType.OTHER.value
    perfil = profiles.profile_for(tipo)
    turno = str(dados.get("shift") or "")

    materias = []
    for bruta in dados.get("subjects", []) or []:
        nome = str(bruta.get("name", "")).strip()
        if not nome:
            continue
        horarios = []
        for horario in bruta.get("schedules", []) or []:
            try:
                weekday = int(horario.get("weekday"))
            except (TypeError, ValueError):
                continue
            if not 0 <= weekday <= 6:
                continue
            inicio = parse_time(str(horario.get("start_time") or ""), shift=turno)
            if not inicio:
                continue
            fim = parse_time(str(horario.get("end_time") or ""), shift=turno) or inicio
            horarios.append(
                {
                    "weekday": weekday,
                    "weekday_label": WEEKDAY_LABELS[weekday].capitalize(),
                    "start_time": inicio,
                    "end_time": fim,
                }
            )
        materias.append(
            {
                "name": nome[:200],
                "teacher": str(bruta.get("teacher", "") or "")[:160],
                "location": str(bruta.get("location", "") or "")[:160],
                "schedules": horarios,
                "confidence": float(bruta.get("confidence", 0.8) or 0.8),
            }
        )

    return {
        "ok": bool(materias),
        "reason": "" if materias else "Não identifiquei nenhuma matéria no que você falou.",
        "transcript": transcript,
        "education_type": tipo,
        "education_label": perfil.label,
        "institution": str(dados.get("institution", "") or "")[:200],
        "course": str(dados.get("course", "") or "")[:200],
        "period": str(dados.get("period", "") or "")[:40],
        "period_kind": perfil.default_period_kind,
        "shift": turno[:20],
        "subjects": materias,
    }


def apply(db: Session, user: User, dados: dict) -> dict:
    """Cria contexto, matérias e horários já revisados pelo usuário."""
    tipo = dados.get("education_type") or EducationType.UNDERGRAD.value
    perfil = profiles.profile_for(tipo)
    period_kind = dados.get("period_kind") or perfil.default_period_kind
    if period_kind not in {k.value for k in PeriodKind}:
        period_kind = perfil.default_period_kind

    context = EducationContext(
        user_id=user.id,
        type=tipo,
        institution=str(dados.get("institution", ""))[:200],
        course_name=str(dados.get("course", ""))[:200],
        semester=str(dados.get("period", ""))[:40],
        shift=str(dados.get("shift", ""))[:20],
        period_kind=period_kind,
        is_active=True,
    )
    db.add(context)
    db.flush()
    academic.set_active_context(db, user.id, context.id)
    periodo = periods.current_period(db, context)

    criadas = 0
    horarios = 0
    for bruta in dados.get("subjects", []) or []:
        nome = str(bruta.get("name", "")).strip()
        if not nome:
            continue
        professor = None
        if bruta.get("teacher"):
            professor = academic.upsert_teacher(db, user.id, bruta["teacher"])
        local = None
        if bruta.get("location"):
            local = academic.upsert_location(db, user.id, bruta["location"])
        materia = academic.upsert_subject(
            db, user.id, context.id, nome,
            teacher_id=professor.id if professor else None,
            location_id=local.id if local else None,
        )
        if periodo is not None:
            materia.academic_period_id = periodo.id
        criadas += 1
        for horario in bruta.get("schedules", []) or []:
            academic.upsert_schedule(
                db, user.id, materia,
                weekday=int(horario["weekday"]),
                start_time=horario["start_time"],
                end_time=horario.get("end_time") or horario["start_time"],
                location_id=local.id if local else None,
                start_date=context.starts_on,
                end_date=context.ends_on,
            )
            horarios += 1

    user.onboarding_done = True
    if profiles.is_minor_profile(tipo):
        user.auto_create_enabled = False
    db.flush()
    return {"context": context, "subjects": criadas, "schedules": horarios}
