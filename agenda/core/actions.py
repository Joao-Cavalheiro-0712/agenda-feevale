"""Motor de ações (SPEC §26, §27, §104, §129, §144).

A IA nunca escreve no banco. Ela propõe uma ação; este módulo valida schema,
regras de negócio, permissão e confiança, executa e registra o estado anterior
para permitir desfazer. Web, WhatsApp e futuros clientes usam este mesmo
caminho — é a regra de arquitetura mais importante do produto (SPEC §144).
"""
from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.core import academic, duplicates, events as events_core, planner, recurrence
from agenda.core.dates import (
    WEEKDAY_LABELS,
    format_date_pt,
    resolve_expression,
)
from agenda.models import (
    AiAction,
    Event,
    EventStatus,
    EventType,
    SourceType,
    Subject,
    User,
)


class Intent(str, enum.Enum):
    CREATE_EVENT = "CREATE_EVENT"
    UPDATE_EVENT = "UPDATE_EVENT"
    DELETE_EVENT = "DELETE_EVENT"
    COMPLETE_EVENT = "COMPLETE_EVENT"

    CREATE_SUBJECT = "CREATE_SUBJECT"
    UPDATE_SUBJECT = "UPDATE_SUBJECT"

    CREATE_CLASS_SCHEDULE = "CREATE_CLASS_SCHEDULE"
    UPDATE_CLASS_SCHEDULE = "UPDATE_CLASS_SCHEDULE"

    CREATE_LOCATION = "CREATE_LOCATION"
    CREATE_TEACHER = "CREATE_TEACHER"

    IMPORT_DOCUMENT = "IMPORT_DOCUMENT"

    GET_TODAY = "GET_TODAY"
    GET_WEEK = "GET_WEEK"
    GET_MONTH = "GET_MONTH"
    GET_NEXT_EVENTS = "GET_NEXT_EVENTS"
    GET_SUBJECT_EVENTS = "GET_SUBJECT_EVENTS"
    GET_OVERDUE = "GET_OVERDUE"

    SET_REMINDER = "SET_REMINDER"
    RESCHEDULE = "RESCHEDULE"

    BULK = "BULK"
    UNKNOWN = "UNKNOWN"


READ_INTENTS = {
    Intent.GET_TODAY, Intent.GET_WEEK, Intent.GET_MONTH,
    Intent.GET_NEXT_EVENTS, Intent.GET_SUBJECT_EVENTS, Intent.GET_OVERDUE,
}

# Ações destrutivas amplas exigem confirmação explícita sempre (SPEC §104).
ALWAYS_CONFIRM = {Intent.DELETE_EVENT}

VALID_EVENT_TYPES = {t.value for t in EventType}


@dataclass
class ActionProposal:
    """Proposta produzida pela camada de interpretação."""

    action: str
    payload: dict = field(default_factory=dict)
    confidence: float = 0.0
    channel: str = "web"
    model: str = ""
    prompt_version: str = ""
    raw_text: str = ""
    question: str = ""
    options: list[dict] = field(default_factory=list)
    children: list["ActionProposal"] = field(default_factory=list)

    @property
    def intent(self) -> Intent:
        try:
            return Intent(self.action)
        except ValueError:
            return Intent.UNKNOWN


@dataclass
class ActionResult:
    status: str  # EXECUTED | NEEDS_CONFIRMATION | NEEDS_CLARIFICATION | REJECTED | FAILED | ANSWERED
    message: str = ""
    action_id: str | None = None
    cards: list[dict] = field(default_factory=list)
    question: str = ""
    options: list[dict] = field(default_factory=list)
    undoable: bool = False
    view: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("EXECUTED", "ANSWERED")

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "action_id": self.action_id,
            "cards": self.cards,
            "question": self.question,
            "options": self.options,
            "undoable": self.undoable,
            "view": self.view,
        }


class ValidationError(Exception):
    """Payload inválido — a IA propôs algo que não passa no schema."""


# --------------------------------------------------------------------------- #
# Validação de schema (SPEC §26)
# --------------------------------------------------------------------------- #
REQUIRED_FIELDS: dict[Intent, tuple[str, ...]] = {
    Intent.CREATE_EVENT: ("title",),
    Intent.UPDATE_EVENT: ("event_id",),
    Intent.DELETE_EVENT: ("event_id",),
    Intent.COMPLETE_EVENT: ("event_id",),
    Intent.CREATE_SUBJECT: ("name",),
    Intent.CREATE_CLASS_SCHEDULE: ("subject_name", "weekday", "start_time"),
    Intent.CREATE_TEACHER: ("name",),
    Intent.CREATE_LOCATION: ("name",),
    Intent.RESCHEDULE: ("event_id",),
    Intent.SET_REMINDER: ("event_id",),
}


def validate(proposal: ActionProposal) -> None:
    intent = proposal.intent
    if intent is Intent.UNKNOWN:
        raise ValidationError("Ação desconhecida.")
    for required in REQUIRED_FIELDS.get(intent, ()):
        if proposal.payload.get(required) in (None, ""):
            raise ValidationError(f"Campo obrigatório ausente: {required}")
    event_type = proposal.payload.get("type")
    if event_type and event_type not in VALID_EVENT_TYPES:
        raise ValidationError(f"Tipo de evento inválido: {event_type}")
    weekday = proposal.payload.get("weekday")
    if weekday is not None and not (isinstance(weekday, int) and 0 <= weekday <= 6):
        raise ValidationError("Dia da semana inválido.")
    confidence = proposal.confidence
    if not 0.0 <= confidence <= 1.0:
        raise ValidationError("Confiança fora do intervalo.")


def owns(db: Session, user: User, model, object_id: str | None):
    """Checagem de permissão pelo escopo central (SPEC §26).

    Nunca comparamos o dono na mão: a regra de propriedade de cada modelo mora
    em ``core.scope`` e falha fechada para modelos não declarados.
    """
    from agenda.core import scope

    return scope.get(db, model, object_id, user.id)


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #
def execute(
    db: Session,
    user: User,
    proposal: ActionProposal,
    *,
    confirmed: bool = False,
) -> ActionResult:
    """Ponto único de execução. Nunca chame os writers diretamente pela IA."""
    try:
        validate(proposal)
    except ValidationError as exc:
        return ActionResult("REJECTED", message=str(exc))

    intent = proposal.intent

    if intent in READ_INTENTS:
        return _handle_read(db, user, proposal)

    # Perguntas de desambiguação vindas da interpretação (SPEC §3.3).
    if proposal.question and not confirmed:
        return ActionResult(
            "NEEDS_CLARIFICATION",
            message=proposal.question,
            question=proposal.question,
            options=proposal.options,
        )

    # Porteiro de confiança (SPEC §13, §129).
    auto_ok = user.auto_create_enabled and config.flag("auto_create_high_confidence")
    needs_confirmation = (
        intent in ALWAYS_CONFIRM
        or proposal.payload.get("force_confirm")
        or proposal.confidence < config.CONFIDENCE_AUTO
        or not auto_ok
    )
    if proposal.confidence < config.CONFIDENCE_REVIEW and not confirmed:
        return ActionResult(
            "NEEDS_CLARIFICATION",
            message=proposal.question or "Não tenho certeza do que você quis dizer. Pode detalhar?",
            question=proposal.question,
            options=proposal.options,
        )
    if needs_confirmation and not confirmed:
        record = _record(db, user, proposal, status="NEEDS_CONFIRMATION")
        db.flush()
        return ActionResult(
            "NEEDS_CONFIRMATION",
            message=_confirmation_text(db, user, proposal),
            action_id=record.id,
            cards=_preview_cards(db, user, proposal),
            options=[
                {"label": "Confirmar", "value": "confirm"},
                {"label": "Cancelar", "value": "cancel"},
            ],
        )

    handler = _HANDLERS.get(intent)
    if handler is None:
        return ActionResult("REJECTED", message="Ainda não sei fazer isso.")

    record = _record(db, user, proposal, status="PROPOSED")
    try:
        result = handler(db, user, proposal, record)
    except ValidationError as exc:
        record.status = "REJECTED"
        return ActionResult("REJECTED", message=str(exc))
    except Exception as exc:  # noqa: BLE001 - falha de execução vira erro amigável
        record.status = "FAILED"
        return ActionResult("FAILED", message=f"Não consegui concluir: {exc}")

    record.status = "EXECUTED"
    record.executed_at = dt.datetime.now(dt.timezone.utc)
    result.action_id = record.id
    result.undoable = record.undoable
    db.flush()
    return result


def execute_pending(db: Session, user: User, action_id: str) -> ActionResult:
    """Confirma uma ação que estava aguardando o "sim" do usuário."""
    record = owns(db, user, AiAction, action_id)
    if record is None or record.status != "NEEDS_CONFIRMATION":
        return ActionResult("REJECTED", message="Ação não encontrada ou já processada.")
    proposal = ActionProposal(
        action=record.action,
        payload=record.payload or {},
        confidence=record.confidence,
        channel=record.channel,
        model=record.model,
        prompt_version=record.prompt_version,
    )
    record.status = "REPLACED"
    return execute(db, user, proposal, confirmed=True)


def reject_pending(db: Session, user: User, action_id: str) -> ActionResult:
    record = owns(db, user, AiAction, action_id)
    if record is None:
        return ActionResult("REJECTED", message="Ação não encontrada.")
    record.status = "REJECTED"
    return ActionResult("EXECUTED", message="Ok, não fiz nada.")


def undo(db: Session, user: User, action_id: str) -> ActionResult:
    """Desfaz uma ação executada, restaurando o estado anterior (SPEC §27)."""
    record = owns(db, user, AiAction, action_id)
    if record is None:
        return ActionResult("REJECTED", message="Ação não encontrada.")
    if record.status != "EXECUTED" or not record.undoable:
        return ActionResult("REJECTED", message="Essa ação não pode ser desfeita.")

    if record.target_type == "event":
        event = db.get(Event, record.target_id) if record.target_id else None
        if record.before_state is None:
            if event is not None:
                db.delete(event)
            message = "Desfeito. Removi o que eu tinha criado."
        else:
            events_core.restore(db, user, record.before_state)
            message = "Desfeito. Voltei ao estado anterior."
    elif record.target_type == "subject" and record.before_state is None:
        subject = db.get(Subject, record.target_id) if record.target_id else None
        if subject is not None:
            db.delete(subject)
        message = "Desfeito. Removi a matéria."
    else:
        return ActionResult("REJECTED", message="Essa ação não pode ser desfeita.")

    record.status = "UNDONE"
    events_core.log(
        db, user_id=user.id, actor="user", action="UNDO", object_type=record.target_type,
        object_id=record.target_id, before=record.after_state, after=record.before_state,
        origin=record.channel,
    )
    db.flush()
    return ActionResult("EXECUTED", message=message)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _record(db: Session, user: User, proposal: ActionProposal, *, status: str) -> AiAction:
    record = AiAction(
        user_id=user.id,
        action=proposal.action,
        payload=proposal.payload,
        confidence=proposal.confidence,
        status=status,
        channel=proposal.channel,
        model=proposal.model,
        prompt_version=proposal.prompt_version,
        fingerprint=proposal.payload.get("fingerprint", ""),
    )
    db.add(record)
    db.flush()
    return record


def _resolve_subject(db: Session, user: User, payload: dict) -> Subject | None:
    subject = owns(db, user, Subject, payload.get("subject_id"))
    if subject:
        return subject
    name = payload.get("subject_name") or payload.get("subject")
    if not name:
        return None
    context = academic.active_context(db, user.id)
    match, _ = academic.resolve_subject(
        db, user.id, name, context_id=context.id if context else None
    )
    if match:
        return match
    if payload.get("create_subject_if_missing") and context:
        return academic.upsert_subject(db, user.id, context.id, name)
    return None


def _resolve_date(db: Session, user: User, payload: dict, subject: Subject | None) -> dt.date | None:
    raw = payload.get("date")
    if raw:
        try:
            return dt.date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    expression = payload.get("date_expression")
    if expression:
        today = planner.today_of(user)
        resolution = resolve_expression(
            expression,
            today,
            next_class_date=(
                (lambda: recurrence.next_class_date(db, user.id, subject.id, today))
                if subject
                else None
            ),
        )
        if resolution.ok:
            return resolution.date
    return None


def _handle_create_event(db, user, proposal, record) -> ActionResult:
    payload = proposal.payload
    subject = _resolve_subject(db, user, payload)
    date = _resolve_date(db, user, payload, subject)
    if date is None:
        raise ValidationError("Não consegui identificar a data.")

    event_type = payload.get("type") or EventType.OTHER.value
    title = str(payload["title"]).strip()

    # Duplicado exato? Não cria de novo (SPEC §73, §74).
    fingerprint = duplicates.fingerprint(
        user_id=user.id, subject_id=subject.id if subject else None,
        event_type=event_type, date=date, title=title,
    )
    existing = db.scalars(
        select(Event).where(Event.user_id == user.id, Event.fingerprint == fingerprint)
    ).first()
    if existing is not None:
        record.undoable = False
        record.target_type = "event"
        record.target_id = existing.id
        return ActionResult(
            "EXECUTED",
            message="Isso já estava na sua agenda.",
            cards=[events_core.event_card(existing, user)],
        )

    context = academic.active_context(db, user.id)
    location = None
    if payload.get("location_name"):
        location = academic.upsert_location(
            db, user.id, payload["location_name"], room=payload.get("room", "")
        )

    event = events_core.create_event(
        db, user,
        title=title,
        event_type=event_type,
        date=date,
        subject=subject,
        context_id=context.id if context else None,
        description=payload.get("description", "") or "",
        start_time=payload.get("start_time"),
        end_time=payload.get("end_time"),
        location=location,
        confidence=proposal.confidence,
        source_type=payload.get("source_type", SourceType.WEB_CAPTURE.value),
        source_id=payload.get("source_id"),
        source_reference=payload.get("source_reference"),
        created_by="ai" if proposal.model else "user",
        checklist=events_core.normalize_checklist(payload.get("checklist")),
        weight=payload.get("weight"),
        max_grade=payload.get("max_grade"),
        group_work=bool(payload.get("group_work")),
    )
    record.target_type = "event"
    record.target_id = event.id
    record.before_state = None
    record.after_state = events_core.snapshot(event)
    events_core.log(
        db, user_id=user.id, actor="ai" if proposal.model else "user", action="CREATE_EVENT",
        object_type="event", object_id=event.id, after=record.after_state,
        origin=proposal.channel, confidence=proposal.confidence,
        ai_model=proposal.model, prompt_version=proposal.prompt_version,
    )
    return ActionResult(
        "EXECUTED",
        message=_created_message(event, user),
        cards=[events_core.event_card(event, user)],
        undoable=True,
    )


def _handle_update_event(db, user, proposal, record) -> ActionResult:
    payload = proposal.payload
    event = owns(db, user, Event, payload.get("event_id"))
    if event is None:
        raise ValidationError("Evento não encontrado.")
    record.before_state = events_core.snapshot(event)

    changes = {k: v for k, v in payload.items() if k in {
        "title", "description", "type", "status", "start_time", "end_time", "weight", "checklist",
    }}
    if payload.get("subject_name") or payload.get("subject_id"):
        subject = _resolve_subject(db, user, payload)
        if subject:
            changes["subject_id"] = subject.id
    if payload.get("location_name"):
        location = academic.upsert_location(
            db, user.id, payload["location_name"], room=payload.get("room", "")
        )
        changes["location_id"] = location.id
    date = _resolve_date(db, user, payload, event.subject)
    if date:
        changes["date"] = date

    events_core.update_event(db, user, event, changes)
    record.target_type = "event"
    record.target_id = event.id
    record.after_state = events_core.snapshot(event)
    events_core.log(
        db, user_id=user.id, actor="ai" if proposal.model else "user", action="UPDATE_EVENT",
        object_type="event", object_id=event.id, before=record.before_state,
        after=record.after_state, origin=proposal.channel, confidence=proposal.confidence,
        ai_model=proposal.model, prompt_version=proposal.prompt_version,
    )
    when = format_date_pt(event.local_date)
    return ActionResult(
        "EXECUTED",
        message=f"Pronto. {event.title} agora é {when}.",
        cards=[events_core.event_card(event, user)],
        undoable=True,
    )


def _handle_delete_event(db, user, proposal, record) -> ActionResult:
    event = owns(db, user, Event, proposal.payload.get("event_id"))
    if event is None:
        raise ValidationError("Evento não encontrado.")
    record.before_state = events_core.snapshot(event)
    record.target_type = "event"
    record.target_id = event.id
    title = event.title
    event.status = EventStatus.CANCELLED.value
    events_core.log(
        db, user_id=user.id, actor="user", action="DELETE_EVENT", object_type="event",
        object_id=event.id, before=record.before_state, origin=proposal.channel,
    )
    return ActionResult("EXECUTED", message=f"Removi “{title}”.", undoable=True)


def _handle_complete_event(db, user, proposal, record) -> ActionResult:
    event = owns(db, user, Event, proposal.payload.get("event_id"))
    if event is None:
        raise ValidationError("Evento não encontrado.")
    record.before_state = events_core.snapshot(event)
    done = proposal.payload.get("done", True)
    events_core.complete_event(db, event, done=bool(done))
    record.target_type = "event"
    record.target_id = event.id
    record.after_state = events_core.snapshot(event)
    return ActionResult(
        "EXECUTED",
        message="Concluído. ✓" if done else "Reabri a atividade.",
        cards=[events_core.event_card(event, user)],
        undoable=True,
    )


def _handle_create_subject(db, user, proposal, record) -> ActionResult:
    context = academic.active_context(db, user.id)
    if context is None:
        raise ValidationError("Cadastre primeiro seu contexto de estudos.")
    payload = proposal.payload
    teacher = None
    if payload.get("teacher_name"):
        teacher = academic.upsert_teacher(db, user.id, payload["teacher_name"])
    location = None
    if payload.get("location_name"):
        location = academic.upsert_location(
            db, user.id, payload["location_name"], room=payload.get("room", "")
        )
    subject = academic.upsert_subject(
        db, user.id, context.id, payload["name"],
        short_name=payload.get("short_name", ""),
        color=payload.get("color", ""),
        teacher_id=teacher.id if teacher else None,
        location_id=location.id if location else None,
    )
    record.target_type = "subject"
    record.target_id = subject.id
    return ActionResult(
        "EXECUTED",
        message=f"Matéria {subject.name} cadastrada.",
        cards=[{"title": subject.name, "type_label": "Matéria", "color": subject.color}],
        undoable=True,
    )


def _handle_create_schedule(db, user, proposal, record) -> ActionResult:
    payload = proposal.payload
    subject = _resolve_subject(db, user, {**payload, "create_subject_if_missing": True})
    if subject is None:
        raise ValidationError("Não identifiquei a matéria dessa aula.")
    location = None
    if payload.get("location_name") or payload.get("room"):
        location = academic.upsert_location(
            db, user.id, payload.get("location_name", ""), room=payload.get("room", "")
        )
    context = academic.active_context(db, user.id)
    schedule = academic.upsert_schedule(
        db, user.id, subject,
        weekday=int(payload["weekday"]),
        start_time=payload["start_time"],
        end_time=payload.get("end_time") or payload["start_time"],
        location_id=location.id if location else None,
        start_date=context.starts_on if context else None,
        end_date=context.ends_on if context else None,
    )
    record.target_type = "class_schedule"
    record.target_id = schedule.id
    record.undoable = False
    weekday_label = WEEKDAY_LABELS[schedule.weekday]
    return ActionResult(
        "EXECUTED",
        message=f"Aula de {subject.display} toda {weekday_label} às {schedule.start_time}.",
        cards=[{
            "title": subject.display, "type_label": "Aula",
            "date_label": weekday_label.capitalize(), "time": schedule.start_time,
            "color": subject.color,
        }],
    )


def _handle_create_teacher(db, user, proposal, record) -> ActionResult:
    teacher = academic.upsert_teacher(db, user.id, proposal.payload["name"])
    record.target_type = "teacher"
    record.target_id = teacher.id
    record.undoable = False
    return ActionResult("EXECUTED", message=f"Professor(a) {teacher.name} cadastrado(a).")


def _handle_create_location(db, user, proposal, record) -> ActionResult:
    payload = proposal.payload
    location = academic.upsert_location(
        db, user.id, payload["name"], building=payload.get("building", ""),
        room=payload.get("room", ""), campus=payload.get("campus", ""),
    )
    record.target_type = "location"
    record.target_id = location.id
    record.undoable = False
    return ActionResult("EXECUTED", message=f"Local {location.label} cadastrado.")


def _handle_set_reminder(db, user, proposal, record) -> ActionResult:
    from agenda.core import reminders as reminders_core

    event = owns(db, user, Event, proposal.payload.get("event_id"))
    if event is None:
        raise ValidationError("Evento não encontrado.")
    offsets = proposal.payload.get("offsets")
    if offsets:
        user.reminder_days = ",".join(str(int(o)) for o in offsets)
    reminders_core.schedule_reminders(db, event, user)
    record.target_type = "event"
    record.target_id = event.id
    record.undoable = False
    return ActionResult("EXECUTED", message="Lembretes atualizados.")


_HANDLERS = {
    Intent.CREATE_EVENT: _handle_create_event,
    Intent.UPDATE_EVENT: _handle_update_event,
    Intent.RESCHEDULE: _handle_update_event,
    Intent.DELETE_EVENT: _handle_delete_event,
    Intent.COMPLETE_EVENT: _handle_complete_event,
    Intent.CREATE_SUBJECT: _handle_create_subject,
    Intent.CREATE_CLASS_SCHEDULE: _handle_create_schedule,
    Intent.UPDATE_CLASS_SCHEDULE: _handle_create_schedule,
    Intent.CREATE_TEACHER: _handle_create_teacher,
    Intent.CREATE_LOCATION: _handle_create_location,
    Intent.SET_REMINDER: _handle_set_reminder,
}


# --------------------------------------------------------------------------- #
# Consultas (mesmo caminho, sem escrita)
# --------------------------------------------------------------------------- #
def _handle_read(db: Session, user: User, proposal: ActionProposal) -> ActionResult:
    intent = proposal.intent
    today = planner.today_of(user)

    if intent is Intent.GET_TODAY:
        items = planner.day_items(db, user, today)
        if not items:
            return ActionResult("ANSWERED", message="Hoje você não tem nada marcado.", view="today")
        return ActionResult("ANSWERED", message="Seu dia:", cards=items, view="today")

    if intent is Intent.GET_WEEK:
        summary = planner.week_summary(db, user)
        cards = [
            card
            for day in planner.week_view(db, user)["days"]
            for card in day["items"]
        ]
        counts = summary["counts"]
        message = (
            f"Esta semana: {counts['aulas']} aulas, {counts['entregas']} entregas "
            f"e {counts['provas']} provas."
        )
        if summary["heaviest_day"]:
            message += f" O dia mais pesado é {summary['heaviest_day']}."
        return ActionResult("ANSWERED", message=message, cards=cards[:20], view="week")

    if intent is Intent.GET_MONTH:
        view = planner.month_view(db, user, today.year, today.month)
        cards = [item for week in view["weeks"] for day in week for item in day["items"]]
        return ActionResult(
            "ANSWERED", message=f"{view['month_label']}: {len(cards)} compromissos.",
            cards=cards[:30], view="month",
        )

    if intent is Intent.GET_OVERDUE:
        groups = planner.deadlines_view(db, user)
        overdue = next((g["items"] for g in groups if g["label"] == "Atrasados"), [])
        if not overdue:
            return ActionResult("ANSWERED", message="Nada atrasado. 👌", view="deadlines")
        return ActionResult(
            "ANSWERED", message=f"Você tem {len(overdue)} item(ns) atrasado(s).",
            cards=overdue, view="deadlines",
        )

    if intent is Intent.GET_SUBJECT_EVENTS:
        subject = _resolve_subject(db, user, proposal.payload)
        if subject is None:
            return ActionResult(
                "NEEDS_CLARIFICATION", message="Qual matéria?",
                question="Qual matéria?",
                options=[
                    {"label": s.display, "value": s.id}
                    for s in academic.list_subjects(db, user.id)
                ],
            )
        view = planner.subject_view(db, user, subject)
        return ActionResult(
            "ANSWERED",
            message=f"{subject.name}: {len(view['upcoming'])} atividade(s) pela frente.",
            cards=view["upcoming"], view=f"subject:{subject.id}",
        )

    # GET_NEXT_EVENTS
    payload = proposal.payload
    subject = _resolve_subject(db, user, payload)
    horizon = int(payload.get("days", 30))
    cards = []
    for group in planner.agenda_view(db, user, days=horizon):
        for item in group["items"]:
            if item.get("type") == "CLASS" and not payload.get("include_classes"):
                continue
            if subject and item.get("subject_id") != subject.id:
                continue
            cards.append(item)
    event_type = payload.get("type")
    if event_type:
        cards = [c for c in cards if c.get("type") == event_type]
    if not cards:
        return ActionResult("ANSWERED", message="Não encontrei nada nesse período.", view="agenda")
    return ActionResult("ANSWERED", message="Próximos compromissos:", cards=cards[:10], view="agenda")


# --------------------------------------------------------------------------- #
# Textos
# --------------------------------------------------------------------------- #
def _created_message(event: Event, user: User) -> str:
    """Tom direto, sem exagero (SPEC §128)."""
    when = format_date_pt(event.local_date)
    subject = f" · {event.subject.display}" if event.subject else ""
    return f"Pronto. {event.title}{subject} — {when}. Vou te lembrar antes."


def _confirmation_text(db: Session, user: User, proposal: ActionProposal) -> str:
    intent = proposal.intent
    payload = proposal.payload
    if intent is Intent.DELETE_EVENT:
        event = owns(db, user, Event, payload.get("event_id"))
        return f"Confirma remover “{event.title if event else 'esse item'}”?"
    if intent is Intent.CREATE_EVENT:
        subject = _resolve_subject(db, user, payload)
        date = _resolve_date(db, user, payload, subject)
        when = format_date_pt(date) if date else payload.get("date_expression", "sem data")
        return f"Confirma criar “{payload.get('title')}” para {when}?"
    return "Confirma essa alteração?"


def _preview_cards(db: Session, user: User, proposal: ActionProposal) -> list[dict]:
    payload = proposal.payload
    if proposal.intent is Intent.CREATE_EVENT:
        subject = _resolve_subject(db, user, payload)
        date = _resolve_date(db, user, payload, subject)
        return [
            {
                "title": payload.get("title", ""),
                "type": payload.get("type", EventType.OTHER.value),
                "type_label": events_core.type_label(payload.get("type", EventType.OTHER.value)),
                "date": date.isoformat() if date else "",
                "date_label": format_date_pt(date) if date else "",
                "subject": subject.display if subject else "",
                "color": subject.color if subject else "slate",
                "time": payload.get("start_time", ""),
                "preview": True,
            }
        ]
    if proposal.intent is Intent.DELETE_EVENT:
        event = owns(db, user, Event, payload.get("event_id"))
        return [events_core.event_card(event, user)] if event else []
    return []


def summarize_batch(proposals: list[ActionProposal]) -> str:
    """Resumo antes de executar operações em lote (SPEC §22)."""
    counts: dict[str, int] = {}
    for proposal in proposals:
        counts[proposal.action] = counts.get(proposal.action, 0) + 1
    labels = {
        Intent.CREATE_SUBJECT.value: "matéria(s)",
        Intent.CREATE_CLASS_SCHEDULE.value: "aula(s) recorrente(s)",
        Intent.CREATE_TEACHER.value: "professor(es)",
        Intent.CREATE_EVENT.value: "atividade(s)",
        Intent.CREATE_LOCATION.value: "local(is)",
    }
    parts = [f"{count} {labels.get(action, action)}" for action, count in counts.items()]
    return "Vou cadastrar " + ", ".join(parts) + ". Está certo?"
