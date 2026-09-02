"""API JSON consumida pelo PWA (SPEC §66).

Todas as escritas passam pelo motor de ações — a interface nunca fala direto
com o banco.
"""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from agenda import config
from agenda.ai.providers import ai_available, get_speech_provider, record_usage
from agenda.core import academic, actions as actions_core, assistant, notifications, planner
from agenda.core.actions import ActionProposal, Intent
from agenda.core.events import event_card
from agenda.ingest import pipeline
from agenda.models import Document, Event, EventType, Notification, PushSubscription, SourceType
from agenda.web.deps import current_user, db, limited, login_required

bp = Blueprint("api", __name__, url_prefix="/api")


def _context_id():
    context = academic.active_context(db(), current_user().id)
    return context.id if context else None


# --------------------------------------------------------------------------- #
# Captura rápida e assistente (SPEC §37, §53)
# --------------------------------------------------------------------------- #
@bp.post("/capture")
@login_required
@limited("assistant")
def capture():
    """Entrada multimodal: texto, áudio ou arquivo — um único endpoint."""
    user = current_user()

    uploaded = request.files.get("file")
    if uploaded and uploaded.filename:
        try:
            document = pipeline.ingest(
                db(), user, uploaded.filename, uploaded.read(),
                mime_type=uploaded.mimetype or "",
            )
        except pipeline.UploadError as exc:
            return jsonify({"status": "REJECTED", "message": str(exc)}), 400
        return jsonify(
            {
                "status": "DOCUMENT",
                "message": "Recebi. Já organizei o que consegui ler.",
                "document_id": document.id,
                "document_status": document.status,
                "summary": pipeline.summary(db(), document),
                "redirect": f"/documentos/{document.id}",
            }
        )

    audio = request.files.get("audio")
    if audio:
        data = audio.read()
        if len(data) > config.MAX_UPLOAD_BYTES:
            return jsonify({"status": "REJECTED", "message": "Áudio muito longo."}), 400
        if not ai_available():
            return jsonify(
                {
                    "status": "REJECTED",
                    "message": "Transcrição de áudio indisponível: configure a chave de IA.",
                }
            ), 503
        result = get_speech_provider().transcribe(data, audio.mimetype or "audio/webm")
        if not result.ok or not result.text.strip():
            return jsonify(
                {"status": "REJECTED", "message": "Não consegui entender o áudio. Tente de novo."}
            ), 200
        record_usage(db(), user_id=user.id, operation="transcribe", result=result)
        response = assistant.handle_message(
            db(), user, result.text, channel="web", source_type=SourceType.VOICE.value
        )
        response["transcript"] = result.text
        return jsonify(response)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or request.form.get("text") or "").strip()
    if not text:
        return jsonify({"status": "REJECTED", "message": "Escreva ou grave alguma coisa."}), 400
    return jsonify(
        assistant.handle_message(
            db(), user, text, channel="web", source_type=SourceType.WEB_CAPTURE.value
        )
    )


@bp.post("/assistant/message")
@login_required
@limited("assistant")
def assistant_message():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Mensagem vazia."}), 400
    return jsonify(
        assistant.handle_message(
            db(), current_user(), text, channel="web", source_type=SourceType.WEB_CAPTURE.value
        )
    )


@bp.post("/actions/<action_id>/confirm")
@login_required
def confirm_action(action_id: str):
    return jsonify(assistant.confirm(db(), current_user(), action_id))


@bp.post("/actions/<action_id>/reject")
@login_required
def reject_action(action_id: str):
    return jsonify(actions_core.reject_pending(db(), current_user(), action_id).as_dict())


@bp.post("/actions/<action_id>/undo")
@login_required
def undo_action(action_id: str):
    return jsonify(assistant.undo(db(), current_user(), action_id))


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #
@bp.get("/planner/today")
@login_required
def planner_today():
    user = current_user()
    view = planner.today_view(db(), user, context_id=_context_id())
    return jsonify(
        {
            "date": view["date"].isoformat(),
            "date_label": view["date_label"],
            "items": view["items"],
            "upcoming": view["upcoming"],
            "overdue": view["overdue"],
            "week": {
                "counts": view["week"]["counts"],
                "heaviest_day": view["week"]["heaviest_day"],
                "progress": view["week"]["progress"],
            },
        }
    )


@bp.get("/planner/week")
@login_required
def planner_week():
    user = current_user()
    start = None
    if request.args.get("start"):
        try:
            start = dt.date.fromisoformat(request.args["start"])
        except ValueError:
            start = None
    view = planner.week_view(db(), user, start, context_id=_context_id())
    return jsonify(
        {
            "start": view["start"].isoformat(),
            "days": [
                {"iso": day["iso"], "weekday": day["weekday"], "day": day["day"],
                 "is_today": day["is_today"], "items": day["items"]}
                for day in view["days"]
            ],
        }
    )


@bp.get("/planner/month")
@login_required
def planner_month():
    user = current_user()
    today = planner.today_of(user)
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    view = planner.month_view(db(), user, year, month, context_id=_context_id())
    return jsonify(
        {
            "year": year, "month": month, "label": view["month_label"],
            "weeks": [
                [{"iso": day["iso"], "day": day["day"], "in_month": day["in_month"],
                  "is_today": day["is_today"], "count": day["count"], "colors": day["colors"]}
                 for day in week]
                for week in view["weeks"]
            ],
        }
    )


@bp.get("/planner/deadlines")
@login_required
def planner_deadlines():
    return jsonify(planner.deadlines_view(db(), current_user(), context_id=_context_id()))


# --------------------------------------------------------------------------- #
# Eventos
# --------------------------------------------------------------------------- #
@bp.post("/events")
@login_required
def create_event():
    payload = request.get_json(silent=True) or request.form.to_dict()
    proposal = ActionProposal(
        action=Intent.CREATE_EVENT.value,
        payload={
            "title": (payload.get("title") or "").strip(),
            "type": payload.get("type") or EventType.OTHER.value,
            "date": payload.get("date"),
            "date_expression": payload.get("date_expression"),
            "subject_id": payload.get("subject_id") or None,
            "start_time": payload.get("start_time") or None,
            "end_time": payload.get("end_time") or None,
            "description": payload.get("description", ""),
            "location_name": payload.get("location_name", ""),
            "source_type": SourceType.MANUAL.value,
        },
        confidence=1.0,
        channel="web",
    )
    result = actions_core.execute(db(), current_user(), proposal, confirmed=True)
    return jsonify(result.as_dict()), (200 if result.ok else 400)


@bp.patch("/events/<event_id>")
@login_required
def update_event(event_id: str):
    payload = request.get_json(silent=True) or {}
    payload["event_id"] = event_id
    proposal = ActionProposal(
        action=Intent.UPDATE_EVENT.value, payload=payload, confidence=1.0, channel="web"
    )
    result = actions_core.execute(db(), current_user(), proposal, confirmed=True)
    return jsonify(result.as_dict()), (200 if result.ok else 400)


@bp.post("/events/<event_id>/complete")
@login_required
def complete_event(event_id: str):
    payload = request.get_json(silent=True) or {}
    proposal = ActionProposal(
        action=Intent.COMPLETE_EVENT.value,
        payload={"event_id": event_id, "done": payload.get("done", True)},
        confidence=1.0,
        channel="web",
    )
    result = actions_core.execute(db(), current_user(), proposal, confirmed=True)
    return jsonify(result.as_dict()), (200 if result.ok else 400)


@bp.delete("/events/<event_id>")
@login_required
def delete_event(event_id: str):
    proposal = ActionProposal(
        action=Intent.DELETE_EVENT.value,
        payload={"event_id": event_id},
        confidence=1.0,
        channel="web",
    )
    result = actions_core.execute(db(), current_user(), proposal, confirmed=True)
    return jsonify(result.as_dict()), (200 if result.ok else 400)


@bp.get("/events/<event_id>")
@login_required
def get_event(event_id: str):
    user = current_user()
    event = db().get(Event, event_id)
    if event is None or event.user_id != user.id:
        return jsonify({"error": "Não encontrado."}), 404
    return jsonify(event_card(event, user))


# --------------------------------------------------------------------------- #
# Documentos, notificações e push
# --------------------------------------------------------------------------- #
@bp.get("/documents/<document_id>/status")
@login_required
def document_status(document_id: str):
    user = current_user()
    document = db().get(Document, document_id)
    if document is None or document.user_id != user.id:
        return jsonify({"error": "Não encontrado."}), 404
    return jsonify(
        {
            "id": document.id,
            "status": document.status,
            "progress": document.progress or [],
            "error": document.error,
            "summary": pipeline.summary(db(), document),
        }
    )


@bp.get("/notifications")
@login_required
def list_notifications():
    user = current_user()
    items = db().scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    ).all()
    return jsonify(
        {
            "unread": notifications.unread_count(db(), user.id),
            "items": [
                {
                    "id": n.id, "title": n.title, "body": n.body, "event_id": n.event_id,
                    "read": n.read_at is not None, "created_at": n.created_at.isoformat(),
                }
                for n in items
            ],
        }
    )


@bp.patch("/notifications/<notification_id>/read")
@login_required
def read_notification(notification_id: str):
    count = notifications.mark_read(db(), current_user().id, notification_id)
    return jsonify({"updated": count})


@bp.post("/notifications/read-all")
@login_required
def read_all_notifications():
    return jsonify({"updated": notifications.mark_read(db(), current_user().id)})


@bp.post("/push/subscribe")
@login_required
def push_subscribe():
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if not endpoint:
        return jsonify({"error": "endpoint ausente"}), 400
    user = current_user()
    existing = db().scalars(
        select(PushSubscription).where(
            PushSubscription.user_id == user.id, PushSubscription.endpoint == endpoint
        )
    ).first()
    if existing is None:
        db().add(
            PushSubscription(user_id=user.id, endpoint=endpoint, keys=payload.get("keys"))
        )
    return jsonify({"ok": True})


@bp.get("/export")
@login_required
def export_data():
    """Exportação de dados do usuário (SPEC §80)."""
    user = current_user()
    events = db().scalars(select(Event).where(Event.user_id == user.id)).all()
    subjects = academic.list_subjects(db(), user.id, active_only=False)
    return jsonify(
        {
            "user": {"name": user.name, "email": user.email, "timezone": user.timezone},
            "contexts": [
                {"type": c.type, "institution": c.institution, "course": c.course_name,
                 "semester": c.semester, "class": c.class_name}
                for c in academic.list_contexts(db(), user.id, include_archived=True)
            ],
            "subjects": [
                {"name": s.name, "short_name": s.short_name, "color": s.color, "status": s.status}
                for s in subjects
            ],
            "events": [event_card(e, user) for e in events],
        }
    )
