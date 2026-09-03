"""Interpretação de mensagens → propostas de ação (SPEC §20, §21, §25, §26).

A IA (ou a heurística) apenas **interpreta**. As datas são resolvidas aqui,
de forma determinística, e a execução acontece em ``core.actions``.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from agenda import config
from agenda.ai import heuristics, prompts
from agenda.ai.context import build_context_block
from agenda.ai.providers import ai_available, get_provider, record_usage
from agenda.core import academic, duplicates, planner, recurrence
from agenda.core.actions import ActionProposal, Intent
from agenda.core.dates import resolve_expression
from agenda.core.text import norm
from agenda.models import EventType, Subject, User


@dataclass
class InterpretResult:
    proposals: list[ActionProposal] = field(default_factory=list)
    reply: str = ""
    model: str = ""
    used_ai: bool = False
    transcript: str = ""


def interpret(
    db: Session,
    user: User,
    text: str,
    *,
    channel: str = "web",
    source_type: str = "WEB_CAPTURE",
    source_id: str | None = None,
) -> InterpretResult:
    text = (text or "").strip()
    if not text:
        return InterpretResult(reply="Não entendi. Pode repetir?")

    result = InterpretResult()
    if ai_available():
        result = _interpret_with_ai(db, user, text, channel=channel)
    if not result.proposals:
        result = _interpret_heuristic(db, user, text, channel=channel)

    for proposal in result.proposals:
        proposal.channel = channel
        proposal.raw_text = text
        proposal.payload.setdefault("source_type", source_type)
        if source_id:
            proposal.payload.setdefault("source_id", source_id)

    _apply_conflict_rules(db, user, text, result.proposals)
    return result


# --------------------------------------------------------------------------- #
# Caminho com LLM
# --------------------------------------------------------------------------- #
def _interpret_with_ai(db: Session, user: User, text: str, *, channel: str) -> InterpretResult:
    provider = get_provider()
    today = planner.today_of(user)
    prompt = prompts.interpret_prompt(
        message=text,
        context_block=build_context_block(db, user),
        today=today.isoformat(),
        timezone=user.timezone,
    )
    ai = provider.structured(prompt, prompts.INTERPRET_SCHEMA, model=config.AI_MODEL_FAST)
    if not ai.ok or not isinstance(ai.data, dict):
        return InterpretResult()
    record_usage(db, user_id=user.id, operation="interpret", result=ai)

    proposals: list[ActionProposal] = []
    for raw in ai.data.get("actions", []) or []:
        proposal = _proposal_from_ai(db, user, raw, model=ai.model)
        if proposal is not None:
            proposal.channel = channel
            proposals.append(proposal)
    return InterpretResult(
        proposals=proposals,
        reply=str(ai.data.get("reply", "") or ""),
        model=ai.model,
        used_ai=True,
    )


def _proposal_from_ai(db: Session, user: User, raw: dict, *, model: str) -> ActionProposal | None:
    action = str(raw.get("action", "")).strip().upper()
    try:
        intent = Intent(action)
    except ValueError:
        return None

    payload = {k: v for k, v in raw.items() if k not in ("action", "confidence", "question") and v not in (None, "")}
    confidence = float(raw.get("confidence", 0.5) or 0.5)
    question = str(raw.get("question", "") or "")
    options: list[dict] = []

    subject, subject_question, subject_options = _resolve_subject_reference(
        db, user, payload.get("subject_name", ""), payload.get("teacher_name", "")
    )
    if subject is not None:
        payload["subject_id"] = subject.id
    elif subject_question and not question:
        question, options = subject_question, subject_options
        confidence = min(confidence, 0.6)

    # Datas: sempre resolvidas aqui (SPEC §21).
    expression = payload.get("date_expression") or payload.get("date")
    if expression and intent not in (Intent.GET_TODAY, Intent.GET_WEEK, Intent.GET_MONTH):
        resolved, ask = _resolve_date_expression(db, user, str(expression), subject)
        if resolved:
            payload["date"] = resolved.isoformat()
        elif ask and not question:
            question = ask
            confidence = min(confidence, 0.5)

    if payload.get("start_time"):
        payload["start_time"] = _normalize_time(db, user, str(payload["start_time"]))
    if payload.get("end_time"):
        payload["end_time"] = _normalize_time(db, user, str(payload["end_time"]))

    return ActionProposal(
        action=intent.value,
        payload=payload,
        confidence=max(0.0, min(1.0, confidence)),
        model=model,
        prompt_version=prompts.VERSION,
        question=question,
        options=options,
    )


# --------------------------------------------------------------------------- #
# Caminho heurístico
# --------------------------------------------------------------------------- #
def _interpret_heuristic(db: Session, user: User, text: str, *, channel: str) -> InterpretResult:
    context = academic.active_context(db, user.id)
    shift = context.shift if context else ""

    query_intent = heuristics.is_query(text)
    if query_intent:
        payload: dict = {}
        subject, _, _ = _resolve_subject_reference(db, user, text, "")
        if subject:
            payload["subject_id"] = subject.id
            if query_intent == "GET_NEXT_EVENTS":
                payload["days"] = 120
        elif query_intent == "GET_SUBJECT_EVENTS":
            query_intent = "GET_NEXT_EVENTS"
        if re.search(r"\bprova|avaliacao|exame\b", norm(text)):
            payload["type"] = EventType.EXAM.value
            payload["days"] = 180
        return InterpretResult(
            proposals=[ActionProposal(action=query_intent, payload=payload, confidence=0.9, channel=channel)]
        )

    if heuristics.looks_like_schedule(text):
        return InterpretResult(proposals=_schedule_proposals(db, user, text, shift=shift, channel=channel))

    if heuristics.is_completion(text):
        subject, _, _ = _resolve_subject_reference(db, user, text, "")
        event = _find_event_by_text(db, user, text, subject)
        if event is not None:
            return InterpretResult(
                proposals=[
                    ActionProposal(
                        action=Intent.COMPLETE_EVENT.value,
                        payload={"event_id": event.id, "done": True},
                        confidence=0.9,
                        channel=channel,
                    )
                ]
            )

    event_type, type_confidence = heuristics.detect_type(text)
    subject, subject_question, subject_options = _resolve_subject_reference(db, user, text, text)
    expression = heuristics.find_time_expression(text)
    date, ask = _resolve_date_expression(db, user, expression or text, subject)
    start_time, end_time = heuristics.find_times(text, shift=shift)

    title = heuristics.guess_title(text, event_type, subject.display if subject else "")
    notes = heuristics.extract_notes(text)

    confidence = 0.55
    if date:
        confidence += 0.2
    if subject:
        confidence += 0.15
    if type_confidence >= 0.85:
        confidence += 0.08
    confidence = round(min(confidence, 0.95), 2)

    checklist = (
        heuristics.split_materials(text) if event_type == EventType.MATERIAL.value else []
    )
    payload = {
        "title": title,
        "type": event_type,
        "checklist": [{"text": item, "done": False} for item in checklist] or None,
        "description": (notes + ("\n" if notes and text else "") + text.strip())[:800],
        "date": date.isoformat() if date else None,
        "date_expression": expression,
        "start_time": start_time,
        "end_time": end_time,
    }
    if subject:
        payload["subject_id"] = subject.id
    payload = {k: v for k, v in payload.items() if v not in (None, "")}

    question = ""
    options: list[dict] = []
    if not date:
        question = ask or "Para quando é?"
        confidence = min(confidence, 0.5)
    elif subject is None and subject_question:
        question, options = subject_question, subject_options
        confidence = min(confidence, 0.65)

    return InterpretResult(
        proposals=[
            ActionProposal(
                action=Intent.CREATE_EVENT.value,
                payload=payload,
                confidence=confidence,
                channel=channel,
                question=question,
                options=options,
            )
        ]
    )


def _schedule_proposals(
    db: Session, user: User, text: str, *, shift: str, channel: str
) -> list[ActionProposal]:
    """"Tenho Penal segunda e quarta das 19:30 às 21:30" → aulas recorrentes."""
    weekdays = heuristics.find_weekdays(text)
    start_time, end_time = heuristics.find_times(text, shift=shift)
    subject, _, _ = _resolve_subject_reference(db, user, text, "")
    subject_name = subject.display if subject else _guess_subject_name(text)
    room = ""
    room_match = re.search(r"\bsala\s+([\w-]+)", norm(text))
    if room_match:
        room = room_match.group(1)
    building = ""
    building_match = re.search(r"\bpredio\s+([\w\s]{2,20})", norm(text))
    if building_match:
        building = building_match.group(1).strip()

    proposals = []
    for weekday in weekdays:
        payload = {
            "subject_name": subject_name,
            "weekday": weekday,
            "start_time": start_time,
            "end_time": end_time,
            "create_subject_if_missing": True,
        }
        if room:
            payload["room"] = room
        if building:
            payload["location_name"] = building
        if subject:
            payload["subject_id"] = subject.id
        proposals.append(
            ActionProposal(
                action=Intent.CREATE_CLASS_SCHEDULE.value,
                payload=payload,
                confidence=0.85 if start_time else 0.6,
                channel=channel,
            )
        )
    return proposals


def _guess_subject_name(text: str) -> str:
    match = re.search(r"\b(?:tenho|aula de|de)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wçãõáéíóú]+)", text)
    return match.group(1) if match else ""


# --------------------------------------------------------------------------- #
# Resolução de referências
# --------------------------------------------------------------------------- #
def _resolve_subject_reference(
    db: Session, user: User, subject_text: str, teacher_text: str
) -> tuple[Subject | None, str, list[dict]]:
    """Resolve a matéria por nome/apelido e, se preciso, pelo professor (§20)."""
    context = academic.active_context(db, user.id)
    context_id = context.id if context else None

    if subject_text:
        subject, ambiguous = academic.resolve_subject(db, user.id, subject_text, context_id=context_id)
        if subject:
            return subject, "", []
        if ambiguous:
            return (
                None,
                "De qual matéria você está falando?",
                [{"label": s.display, "value": s.id} for s in ambiguous],
            )

    if teacher_text:
        teacher_name = _extract_teacher_name(teacher_text)
        if teacher_name:
            teacher, candidates = academic.resolve_teacher(db, user.id, teacher_name)
            people = [teacher] if teacher else candidates
            subjects: list[Subject] = []
            for person in people:
                subjects.extend(academic.subjects_of_teacher(db, user.id, person.id))
            if len(subjects) == 1:
                return subjects[0], "", []
            if len(subjects) > 1:
                return (
                    None,
                    "Você está falando de qual matéria?",
                    [{"label": s.display, "value": s.id} for s in subjects],
                )
    return None, "", []


def _extract_teacher_name(text: str) -> str:
    match = re.search(
        r"\b(?:professora?|prof\.?|profa\.?)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wçãõáéíóú]+)", text
    )
    if match:
        return match.group(1)
    return text if len(text.split()) <= 3 else ""


def _resolve_date_expression(
    db: Session, user: User, expression: str, subject: Subject | None
) -> tuple[dt.date | None, str]:
    today = planner.today_of(user)
    resolution = resolve_expression(
        expression,
        today,
        next_class_date=(
            (lambda: recurrence.next_class_date(db, user.id, subject.id, today)) if subject else None
        ),
    )
    if resolution.ok:
        return resolution.date, ""
    if resolution.needs_clarification:
        return None, resolution.question
    return None, ""


def _normalize_time(db: Session, user: User, raw: str, *, shift: str = "") -> str | None:
    if not raw:
        return None
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        hour, minute = raw.split(":")
        return f"{int(hour):02d}:{minute}"
    if not shift:
        context = academic.active_context(db, user.id)
        shift = context.shift if context else ""
    from agenda.core.dates import parse_time

    return parse_time(raw, shift=shift)


def _find_event_by_text(db: Session, user: User, text: str, subject: Subject | None):
    from sqlalchemy import select

    from agenda.models import Event, EventStatus

    today = planner.today_of(user)
    stmt = select(Event).where(
        Event.user_id == user.id,
        Event.status.in_([EventStatus.UPCOMING.value, EventStatus.OVERDUE.value]),
        Event.local_date >= today - dt.timedelta(days=30),
    )
    if subject:
        stmt = stmt.where(Event.subject_id == subject.id)
    candidates = list(db.scalars(stmt.order_by(Event.local_date)).all())
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    target = norm(text)
    scored = [(sum(1 for w in norm(c.title).split() if w in target), c) for c in candidates]
    scored.sort(key=lambda pair: -pair[0])
    return scored[0][1] if scored[0][0] > 0 else None


# --------------------------------------------------------------------------- #
# Conflitos e remarcações (SPEC §14)
# --------------------------------------------------------------------------- #
def _apply_conflict_rules(
    db: Session, user: User, text: str, proposals: list[ActionProposal]
) -> None:
    explicit_reschedule = duplicates.looks_like_reschedule(text)
    for proposal in proposals:
        if proposal.intent is not Intent.CREATE_EVENT:
            continue
        date_raw = proposal.payload.get("date")
        if not date_raw:
            continue
        try:
            new_date = dt.date.fromisoformat(str(date_raw)[:10])
        except ValueError:
            continue

        candidate = duplicates.find_reschedule_candidate(
            db,
            user.id,
            subject_id=proposal.payload.get("subject_id"),
            event_type=proposal.payload.get("type", EventType.OTHER.value),
            title=proposal.payload.get("title", ""),
            new_date=new_date,
        )
        if candidate is None:
            continue

        if explicit_reschedule or proposal.payload.get("is_update"):
            # Contexto explícito ("passou para"): remarca e deixa desfazer.
            proposal.action = Intent.UPDATE_EVENT.value
            proposal.payload = {
                "event_id": candidate.id,
                "date": new_date.isoformat(),
                "source_type": proposal.payload.get("source_type"),
            }
            proposal.confidence = max(proposal.confidence, 0.92)
            proposal.question = ""
        else:
            # Pode ser remarcação ou um segundo evento — perguntamos (SPEC §14).
            proposal.question = (
                f"Encontrei “{candidate.title}” em "
                f"{candidate.local_date.strftime('%d/%m')}. É essa que mudou de data?"
            )
            proposal.options = [
                {"label": "Sim, remarcar", "value": f"reschedule:{candidate.id}"},
                {"label": "Criar outro", "value": "create"},
            ]
            proposal.confidence = min(proposal.confidence, 0.65)
