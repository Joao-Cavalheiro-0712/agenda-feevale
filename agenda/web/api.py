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
from agenda.core import (
    academic,
    actions as actions_core,
    assistant,
    billing,
    notifications,
    planner,
    privacy,
    scope,
    study,
)
from agenda.core.actions import ActionProposal, Intent
from agenda.core.events import event_card
from agenda.ingest import pipeline
from agenda.models import Document, Event, EventType, Notification, SourceType
from agenda.web.deps import current_user, db, limited, login_required

bp = Blueprint("api", __name__, url_prefix="/api")


def _http_status(result) -> int:
    """Código HTTP de um resultado do motor de ações.

    "Não é seu" e "não existe" viram o MESMO 404 com a MESMA mensagem: quem
    varre ids não consegue descobrir o que existe na conta de outra pessoa. A
    escolha vem do `reason` que o motor devolve, e não de inspecionar o texto
    da mensagem — texto muda, contrato não.
    """
    if result.ok:
        return 200
    return 404 if result.reason == "not_found" else 400


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
        # Interruptor de operação: se o custo de leitura de documento sair de
        # controle, isto desliga a entrada inteira sem deploy.
        if not config.flag("document_import_enabled"):
            return jsonify({
                "status": "REJECTED",
                "message": "Leitura de documentos está temporariamente indisponível.",
            }), 503
        try:
            document = pipeline.ingest(
                db(), user, uploaded.filename, uploaded.read(),
                mime_type=uploaded.mimetype or "",
            )
        except billing.QuotaExceeded as exc:
            return jsonify({"status": "QUOTA", "message": exc.message, "upgrade": "/planos"}), 402
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
        if not config.flag("voice_capture_enabled"):
            return jsonify({
                "status": "REJECTED",
                "message": "Captura por áudio está temporariamente indisponível.",
            }), 503
        pode, aviso = billing.check_quota(db(), user, billing.MAX_AI_MESSAGES, "ai_messages")
        if not pode:
            return jsonify({"status": "QUOTA", "message": aviso, "upgrade": "/planos"}), 402
        data = audio.read()
        if len(data) > config.MAX_UPLOAD_BYTES:
            return jsonify({"status": "REJECTED", "message": "Áudio muito longo."}), 400
        # Áudio é a operação mais cara por unidade: sem medição, um plano
        # qualquer viraria transcrição ilimitada e a quota não conteria nada.
        minutos = billing.estimate_audio_minutes(len(data))
        pode_audio, aviso_audio = billing.check_quota(
            db(), user, billing.MAX_AUDIO_MINUTES, "audio_minutes"
        )
        if not pode_audio:
            return jsonify({"status": "QUOTA", "message": aviso_audio, "upgrade": "/planos"}), 402
        if not privacy.ai_allowed(user):
            return jsonify(
                {
                    "status": "REJECTED",
                    "message": (
                        "A interpretação automática está desligada na sua conta. "
                        "Ligue em Perfil › Privacidade para enviar áudio."
                    ),
                }
            ), 403
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
        billing.consume(db(), user, "audio_minutes", amount=minutos)
        try:
            response = assistant.handle_message(
                db(), user, result.text, channel="web", source_type=SourceType.VOICE.value
            )
        except billing.QuotaExceeded as exc:
            return jsonify(
                {"status": "QUOTA", "message": exc.message, "upgrade": "/planos"}
            ), 402
        response["transcript"] = result.text
        return jsonify(response)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or request.form.get("text") or "").strip()[:4000]
    if not text:
        return jsonify({"status": "REJECTED", "message": "Escreva ou grave alguma coisa."}), 400

    try:
        return jsonify(
            assistant.handle_message(
                db(), user, text, channel="web", source_type=SourceType.WEB_CAPTURE.value
            )
        )
    except billing.QuotaExceeded as exc:
        return jsonify({"status": "QUOTA", "message": exc.message, "upgrade": "/planos"}), 402


@bp.post("/onboarding/voice")
@login_required
@limited("assistant")
def onboarding_voice():
    """Áudio do onboarding → estrutura revisável (SPEC §7)."""
    from agenda.ai import onboarding as onboarding_ai

    user = current_user()
    audio = request.files.get("audio")
    texto = (request.form.get("text") or "").strip()[:4000]

    if audio is not None:
        if not config.flag("voice_capture_enabled"):
            return jsonify({"ok": False, "reason": "Áudio temporariamente indisponível."}), 503
        if not privacy.ai_allowed(user):
            return jsonify(
                {"ok": False, "reason": "Interpretação automática desligada na sua conta."}
            ), 403
        dados = audio.read()
        if len(dados) > config.MAX_UPLOAD_BYTES:
            return jsonify({"ok": False, "reason": "Áudio muito longo."}), 400
        texto = onboarding_ai.transcribe(db(), user, dados, audio.mimetype or "audio/webm")
    if not texto:
        return jsonify({"ok": False, "reason": "Não consegui entender o áudio."}), 200

    try:
        return jsonify(onboarding_ai.interpret(db(), user, texto))
    except billing.QuotaExceeded as exc:
        return jsonify({"ok": False, "reason": exc.message, "upgrade": "/planos"}), 402


@bp.post("/assistant/message")
@login_required
@limited("assistant")
def assistant_message():
    user = current_user()
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()[:4000]
    if not text:
        return jsonify({"error": "Mensagem vazia."}), 400
    try:
        return jsonify(
            assistant.handle_message(
                db(), user, text, channel="web", source_type=SourceType.WEB_CAPTURE.value
            )
        )
    except billing.QuotaExceeded as exc:
        return jsonify({"status": "QUOTA", "message": exc.message, "upgrade": "/planos"}), 402


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
    return jsonify(result.as_dict()), _http_status(result)


@bp.patch("/events/<event_id>")
@login_required
def update_event(event_id: str):
    payload = request.get_json(silent=True) or {}
    payload["event_id"] = event_id
    proposal = ActionProposal(
        action=Intent.UPDATE_EVENT.value, payload=payload, confidence=1.0, channel="web"
    )
    result = actions_core.execute(db(), current_user(), proposal, confirmed=True)
    return jsonify(result.as_dict()), _http_status(result)


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
    return jsonify(result.as_dict()), _http_status(result)


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
    return jsonify(result.as_dict()), _http_status(result)


@bp.put("/events/<event_id>/checklist")
@login_required
def update_checklist(event_id: str):
    """Substitui a lista inteira — operação atômica, sem estado intermediário."""
    from agenda.core import events as events_core

    user = current_user()
    event = scope.get(db(), Event, event_id, user.id)
    if event is None:
        return jsonify({"error": "Não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    itens = events_core.set_checklist(db(), event, payload.get("items"))
    feitos, total = events_core.checklist_progress(event)
    return jsonify({"items": itens, "done": feitos, "total": total})


@bp.get("/events/<event_id>")
@login_required
def get_event(event_id: str):
    user = current_user()
    event = scope.get(db(), Event, event_id, user.id)
    if event is None:
        return jsonify({"error": "Não encontrado."}), 404
    return jsonify(event_card(event, user))


# --------------------------------------------------------------------------- #
# Documentos, notificações e push
# --------------------------------------------------------------------------- #
@bp.get("/documents/<document_id>/status")
@login_required
def document_status(document_id: str):
    user = current_user()
    document = scope.get(db(), Document, document_id, user.id)
    if document is None:
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


@bp.get("/push/key")
@login_required
def push_key():
    """Chave pública VAPID para o navegador se inscrever."""
    from agenda.channels import push

    return jsonify({"enabled": push.is_configured(), "publicKey": config.VAPID_PUBLIC_KEY})


@bp.post("/push/subscribe")
@login_required
def push_subscribe():
    from agenda.channels import push

    payload = request.get_json(silent=True) or {}
    endpoint = (payload.get("endpoint") or "").strip()
    if not push.endpoint_permitido(endpoint):
        return jsonify({"error": "endpoint inválido"}), 400
    keys = payload.get("keys")
    if keys is not None and not isinstance(keys, dict):
        return jsonify({"error": "keys inválidas"}), 400
    push.register(db(), current_user(), endpoint, keys)
    return jsonify({"ok": True})


@bp.post("/push/unsubscribe")
@login_required
def push_unsubscribe():
    from agenda.channels import push

    payload = request.get_json(silent=True) or {}
    push.unregister(db(), current_user(), (payload.get("endpoint") or "").strip())
    return jsonify({"ok": True})


@bp.get("/stream")
@login_required
def stream():
    """SSE: o que chega pelo WhatsApp aparece na aba aberta (SPEC §141)."""
    from flask import Response

    from agenda.web import realtime

    user_id = current_user().id
    resposta = Response(realtime.stream(user_id), mimetype="text/event-stream")
    resposta.headers["Cache-Control"] = "no-cache, no-transform"
    resposta.headers["X-Accel-Buffering"] = "no"
    resposta.headers["Connection"] = "keep-alive"
    return resposta


@bp.post("/study/generate")
@login_required
def study_generate_api():
    user = current_user()
    if not billing.allows(db(), user, billing.CAN_USE_STUDY_PLANNER):
        return jsonify({"status": "QUOTA", "message": "Disponível nos planos pagos.", "upgrade": "/planos"}), 402
    propostas = study.propose(db(), user, today=planner.today_of(user))
    criados = study.save(db(), user, propostas)
    return jsonify({"status": "EXECUTED", "created": criados, "message": f"{criados} bloco(s) criado(s)."})


@bp.post("/study/<block_id>/complete")
@login_required
def study_complete(block_id: str):
    payload = request.get_json(silent=True) or {}
    ok = study.complete(db(), current_user(), block_id, done=bool(payload.get("done", True)))
    return jsonify({"ok": ok}), (200 if ok else 404)


@bp.get("/plan")
@login_required
def plan_status():
    resumo = billing.summary(db(), current_user())
    plano = resumo["plan"]
    return jsonify(
        {
            "tier": plano.tier,
            "name": plano.name,
            "status": resumo["subscription"].status,
            "trial_days_left": resumo["trial_days_left"],
            "usage": resumo["usage"],
        }
    )


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
