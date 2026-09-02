"""Páginas do app (SPEC §28-§38, §56, §57, §97)."""
from __future__ import annotations

import datetime as dt

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import select

from agenda import config
from agenda.channels import whatsapp
from agenda.core import academic, notifications, planner
from agenda.core.academic import EDUCATION_LABELS
from agenda.core.events import event_card, refresh_statuses, type_label
from agenda.ingest import pipeline
from agenda.models import (
    AiUsage,
    Document,
    EducationContext,
    EducationType,
    Event,
    EventType,
    Location,
    Notification,
    SharedCollection,
    Subject,
    SubjectStatus,
    Teacher,
    User,
)
from agenda.security import share_code
from agenda.web.deps import (
    admin_required,
    current_user,
    db,
    limited,
    login_required,
    onboarding_required,
)

bp = Blueprint("pages", __name__)

# Tipos oferecidos na UI conforme o nível educacional (SPEC §4, §47).
TYPES_BY_EDUCATION: dict[str, list[str]] = {
    EducationType.ELEMENTARY.value: ["HOMEWORK", "MATERIAL", "EXAM", "PROJECT", "READING", "SCHOOL_EVENT", "REMINDER"],
    EducationType.MIDDLE_SCHOOL.value: ["HOMEWORK", "MATERIAL", "EXAM", "ASSIGNMENT", "PRESENTATION", "READING", "PROJECT", "SCHOOL_EVENT", "REMINDER"],
    EducationType.HIGH_SCHOOL.value: ["EXAM", "SIMULATION", "ASSIGNMENT", "HOMEWORK", "PAPER", "PRESENTATION", "PROJECT", "READING", "MATERIAL", "REMINDER"],
    EducationType.TECHNICAL.value: ["EXAM", "LAB", "ASSIGNMENT", "PROJECT", "PRESENTATION", "INTERNSHIP", "PAPER", "MATERIAL", "ADMINISTRATIVE", "REMINDER"],
    EducationType.UNDERGRAD.value: ["EXAM", "ASSIGNMENT", "PAPER", "SEMINAR", "PRESENTATION", "READING", "LAB", "PROJECT", "INTERNSHIP", "ADMINISTRATIVE", "REMINDER"],
    EducationType.POSTGRAD.value: ["PAPER", "SEMINAR", "PRESENTATION", "READING", "PROJECT", "EXAM", "ADMINISTRATIVE", "REMINDER"],
    EducationType.FREE_COURSE.value: ["ASSIGNMENT", "PROJECT", "PRESENTATION", "READING", "EXAM", "REMINDER"],
    EducationType.OTHER.value: ["EXAM", "ASSIGNMENT", "HOMEWORK", "MATERIAL", "READING", "REMINDER"],
}


def _greeting(user) -> str:
    """Saudação conforme a hora local do estudante."""
    from agenda.core.events import tz_of

    hour = dt.datetime.now(tz_of(user)).hour
    if hour < 12:
        return "Bom dia"
    if hour < 18:
        return "Boa tarde"
    return "Boa noite"


def _context():
    user = current_user()
    return academic.active_context(db(), user.id) if user else None


def _ui_types(context) -> list[tuple[str, str]]:
    keys = TYPES_BY_EDUCATION.get(
        context.type if context else EducationType.UNDERGRAD.value,
        TYPES_BY_EDUCATION[EducationType.UNDERGRAD.value],
    )
    return [(key, type_label(key, context.type if context else "")) for key in keys]


def _shell(**extra):
    user = current_user()
    context = _context()
    return {
        "context": context,
        "contexts": academic.list_contexts(db(), user.id) if user else [],
        "subjects": academic.list_subjects(db(), user.id, context_id=context.id if context else None),
        "ui_types": _ui_types(context),
        "unread": notifications.unread_count(db(), user.id) if user else 0,
        **extra,
    }


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #
@bp.route("/")
def home():
    user = current_user()
    if user is None:
        return render_template("landing.html", whatsapp_number=config.WHATSAPP_NUMBER)
    if not user.onboarding_done:
        return redirect(url_for("pages.onboarding"))
    return redirect(url_for("pages.today"))


@bp.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    user = current_user()
    if request.method == "POST":
        education_type = request.form.get("type") or EducationType.UNDERGRAD.value
        if education_type not in {t.value for t in EducationType}:
            education_type = EducationType.OTHER.value
        context = EducationContext(
            user_id=user.id,
            type=education_type,
            institution=(request.form.get("institution") or "").strip()[:200],
            course_name=(request.form.get("course_name") or "").strip()[:200],
            grade_name=(request.form.get("grade_name") or "").strip()[:80],
            semester=(request.form.get("semester") or "").strip()[:40],
            module=(request.form.get("module") or "").strip()[:40],
            class_name=(request.form.get("class_name") or "").strip()[:80],
            shift=(request.form.get("shift") or "").strip()[:20],
            period_label=(request.form.get("period_label") or "").strip()[:40],
            is_active=True,
        )
        for field, attribute in (("starts_on", "starts_on"), ("ends_on", "ends_on")):
            raw = request.form.get(field)
            if raw:
                try:
                    setattr(context, attribute, dt.date.fromisoformat(raw))
                except ValueError:
                    pass
        db().add(context)
        academic.set_active_context(db(), user.id, context.id)
        user.onboarding_done = True
        db().flush()
        flash("Tudo certo! Agora me conta o que você precisa lembrar.", "success")
        return redirect(url_for("pages.today"))

    return render_template(
        "onboarding.html",
        education_options=[(t.value, EDUCATION_LABELS[t.value]) for t in EducationType],
    )


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #
@bp.route("/hoje")
@onboarding_required
def today():
    user = current_user()
    refresh_statuses(db(), user)
    context = _context()
    view = planner.today_view(db(), user, context_id=context.id if context else None)
    return render_template(
        "today.html", view=view, greeting=_greeting(user), **_shell(active="today")
    )


@bp.route("/semana")
@onboarding_required
def week():
    user = current_user()
    context = _context()
    start = None
    if request.args.get("inicio"):
        try:
            start = dt.date.fromisoformat(request.args["inicio"])
        except ValueError:
            start = None
    view = planner.week_view(db(), user, start, context_id=context.id if context else None)
    return render_template("week.html", view=view, **_shell(active="agenda"))


@bp.route("/mes")
@onboarding_required
def month():
    user = current_user()
    context = _context()
    today_date = planner.today_of(user)
    try:
        year = int(request.args.get("ano", today_date.year))
        month_number = int(request.args.get("mes", today_date.month))
        if not 1 <= month_number <= 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month_number = today_date.year, today_date.month
    view = planner.month_view(
        db(), user, year, month_number, context_id=context.id if context else None
    )
    return render_template("month.html", view=view, **_shell(active="agenda"))


@bp.route("/agenda")
@onboarding_required
def agenda():
    user = current_user()
    context = _context()
    groups = planner.agenda_view(db(), user, days=120, context_id=context.id if context else None)
    return render_template("agenda.html", groups=groups, **_shell(active="agenda"))


@bp.route("/linha-do-tempo")
@onboarding_required
def timeline():
    user = current_user()
    context = _context()
    view = planner.timeline_view(db(), user, context_id=context.id if context else None)
    return render_template("timeline.html", view=view, **_shell(active="agenda"))


@bp.route("/entregas")
@onboarding_required
def deadlines():
    user = current_user()
    context = _context()
    groups = planner.deadlines_view(db(), user, context_id=context.id if context else None)
    return render_template("deadlines.html", groups=groups, **_shell(active="agenda"))


@bp.route("/evento/<event_id>")
@onboarding_required
def event_detail(event_id: str):
    user = current_user()
    event = db().get(Event, event_id)
    if event is None or event.user_id != user.id:
        abort(404)
    document = db().get(Document, event.source_id) if event.source_id else None
    return render_template(
        "event.html",
        event=event,
        card=event_card(event, user),
        document=document,
        **_shell(active="agenda"),
    )


# --------------------------------------------------------------------------- #
# Matérias
# --------------------------------------------------------------------------- #
@bp.route("/materias")
@onboarding_required
def subjects():
    user = current_user()
    context = _context()
    items = academic.list_subjects(
        db(), user.id, context_id=context.id if context else None, active_only=False
    )
    teachers = db().scalars(select(Teacher).where(Teacher.user_id == user.id)).all()
    return render_template(
        "subjects.html", items=items, teachers=teachers, **_shell(active="subjects")
    )


@bp.route("/materias", methods=["POST"])
@onboarding_required
def create_subject():
    user = current_user()
    context = _context()
    name = (request.form.get("name") or "").strip()
    if not name or context is None:
        flash("Informe o nome da matéria.", "error")
        return redirect(url_for("pages.subjects"))
    teacher = None
    if request.form.get("teacher_name"):
        teacher = academic.upsert_teacher(db(), user.id, request.form["teacher_name"])
    location = None
    if request.form.get("location_name") or request.form.get("room"):
        location = academic.upsert_location(
            db(), user.id, request.form.get("location_name", ""), room=request.form.get("room", "")
        )
    subject = academic.upsert_subject(
        db(), user.id, context.id, name,
        short_name=request.form.get("short_name", ""),
        color=request.form.get("color", ""),
        teacher_id=teacher.id if teacher else None,
        location_id=location.id if location else None,
    )
    for alias in (request.form.get("aliases") or "").split(","):
        if alias.strip():
            academic.add_alias(db(), subject, alias.strip())
    flash(f"{subject.name} cadastrada.", "success")
    return redirect(url_for("pages.subject_detail", subject_id=subject.id))


@bp.route("/materias/<subject_id>")
@onboarding_required
def subject_detail(subject_id: str):
    user = current_user()
    subject = db().get(Subject, subject_id)
    if subject is None or subject.user_id != user.id:
        abort(404)
    view = planner.subject_view(db(), user, subject)
    teachers = db().scalars(select(Teacher).where(Teacher.user_id == user.id)).all()
    locations = db().scalars(select(Location).where(Location.user_id == user.id)).all()
    return render_template(
        "subject.html", view=view, subject=subject, teachers=teachers, locations=locations,
        colors=academic.SUBJECT_COLORS, **_shell(active="subjects"),
    )


@bp.route("/materias/<subject_id>/editar", methods=["POST"])
@onboarding_required
def update_subject(subject_id: str):
    user = current_user()
    subject = db().get(Subject, subject_id)
    if subject is None or subject.user_id != user.id:
        abort(404)
    subject.name = (request.form.get("name") or subject.name).strip()[:200]
    subject.short_name = (request.form.get("short_name") or "").strip()[:60]
    subject.color = request.form.get("color") or subject.color
    subject.notes = (request.form.get("notes") or "").strip()
    status = request.form.get("status")
    if status in {s.value for s in SubjectStatus}:
        subject.status = status
    if request.form.get("teacher_name"):
        teacher = academic.upsert_teacher(db(), user.id, request.form["teacher_name"])
        subject.teacher_id = teacher.id
    for alias in (request.form.get("aliases") or "").split(","):
        if alias.strip():
            academic.add_alias(db(), subject, alias.strip())
    flash("Matéria atualizada.", "success")
    return redirect(url_for("pages.subject_detail", subject_id=subject.id))


@bp.route("/materias/<subject_id>/horario", methods=["POST"])
@onboarding_required
def create_schedule(subject_id: str):
    user = current_user()
    subject = db().get(Subject, subject_id)
    if subject is None or subject.user_id != user.id:
        abort(404)
    try:
        weekday = int(request.form["weekday"])
        start_time = request.form["start_time"]
        end_time = request.form.get("end_time") or start_time
    except (KeyError, ValueError):
        flash("Preencha dia e horário.", "error")
        return redirect(url_for("pages.subject_detail", subject_id=subject_id))
    location = None
    if request.form.get("room") or request.form.get("location_name"):
        location = academic.upsert_location(
            db(), user.id, request.form.get("location_name", ""), room=request.form.get("room", "")
        )
    context = _context()
    academic.upsert_schedule(
        db(), user.id, subject, weekday=weekday, start_time=start_time, end_time=end_time,
        location_id=location.id if location else None,
        start_date=context.starts_on if context else None,
        end_date=context.ends_on if context else None,
    )
    flash("Horário adicionado.", "success")
    return redirect(url_for("pages.subject_detail", subject_id=subject_id))


# --------------------------------------------------------------------------- #
# Documentos
# --------------------------------------------------------------------------- #
@bp.route("/documentos")
@onboarding_required
def documents():
    user = current_user()
    items = db().scalars(
        select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())
    ).all()
    return render_template(
        "documents.html",
        items=[(document, pipeline.summary(db(), document)) for document in items],
        **_shell(active="documents"),
    )


@bp.route("/documentos", methods=["POST"])
@onboarding_required
@limited("upload")
def upload_documents():
    user = current_user()
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Escolha ao menos um arquivo.", "error")
        return redirect(url_for("pages.documents"))
    last = None
    for uploaded in files:
        try:
            last = pipeline.ingest(
                db(), user, uploaded.filename, uploaded.read(),
                mime_type=uploaded.mimetype or "",
            )
        except pipeline.UploadError as exc:
            flash(str(exc), "error")
    if last is not None and len(files) == 1:
        return redirect(url_for("pages.document_review", document_id=last.id))
    return redirect(url_for("pages.documents"))


@bp.route("/documentos/<document_id>")
@onboarding_required
def document_review(document_id: str):
    user = current_user()
    document = db().get(Document, document_id)
    if document is None or document.user_id != user.id:
        abort(404)
    items = sorted(document.extractions, key=lambda i: (i.needs_review is False, i.kind))
    return render_template(
        "document_review.html",
        document=document,
        items=items,
        summary=pipeline.summary(db(), document),
        **_shell(active="documents"),
    )


@bp.route("/documentos/<document_id>/importar", methods=["POST"])
@onboarding_required
def import_document(document_id: str):
    user = current_user()
    document = db().get(Document, document_id)
    if document is None or document.user_id != user.id:
        abort(404)
    selected = request.form.getlist("selected")
    for item in document.extractions:
        if item.id in selected:
            for field in ("title", "date", "type", "subject_name"):
                value = request.form.get(f"{field}_{item.id}")
                if value:
                    payload = dict(item.payload or {})
                    payload[field] = value
                    if field == "subject_name":
                        payload.pop("subject_id", None)
                    item.payload = payload
    created = pipeline.confirm(db(), user, document, selected_ids=selected)
    flash(
        f"Importado: {created['events']} atividades, {created['subjects']} matérias, "
        f"{created['schedules']} aulas.",
        "success",
    )
    return redirect(url_for("pages.today"))


@bp.route("/documentos/<document_id>/excluir", methods=["POST"])
@onboarding_required
def delete_document(document_id: str):
    user = current_user()
    document = db().get(Document, document_id)
    if document is None or document.user_id != user.id:
        abort(404)
    db().delete(document)
    flash("Documento removido.", "success")
    return redirect(url_for("pages.documents"))


# --------------------------------------------------------------------------- #
# Assistente, notificações, busca
# --------------------------------------------------------------------------- #
@bp.route("/assistente")
@onboarding_required
def assistant_page():
    from agenda.models import AssistantMessage

    user = current_user()
    history = db().scalars(
        select(AssistantMessage)
        .where(AssistantMessage.user_id == user.id)
        .order_by(AssistantMessage.created_at.desc())
        .limit(30)
    ).all()
    return render_template(
        "assistant.html", history=list(reversed(history)), **_shell(active="assistant")
    )


@bp.route("/notificacoes")
@onboarding_required
def notifications_page():
    user = current_user()
    items = db().scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(60)
    ).all()
    return render_template("notifications.html", items=items, **_shell(active="profile"))


@bp.route("/buscar")
@onboarding_required
def search():
    user = current_user()
    query = (request.args.get("q") or "").strip()
    results = planner.search(db(), user, query) if query else {"events": [], "subjects": []}
    return render_template("search.html", query=query, results=results, **_shell(active="agenda"))


# --------------------------------------------------------------------------- #
# Perfil, WhatsApp, compartilhamento
# --------------------------------------------------------------------------- #
@bp.route("/perfil")
@login_required
def profile():
    user = current_user()
    from agenda.models import UserPhone

    links = db().scalars(select(UserPhone).where(UserPhone.user_id == user.id)).all()
    return render_template(
        "profile.html",
        links=[l for l in links if l.active],
        whatsapp_configured=whatsapp.is_configured(),
        whatsapp_number=config.WHATSAPP_NUMBER,
        **_shell(active="profile"),
    )


@bp.route("/perfil", methods=["POST"])
@login_required
def update_profile():
    user = current_user()
    user.name = (request.form.get("name") or user.name).strip()[:160]
    user.timezone = (request.form.get("timezone") or user.timezone).strip()[:64]
    user.theme = request.form.get("theme") or user.theme
    days = [d.strip() for d in (request.form.get("reminder_days") or "").split(",") if d.strip().isdigit()]
    if days:
        user.reminder_days = ",".join(days)
    user.auto_create_enabled = bool(request.form.get("auto_create_enabled"))
    flash("Preferências salvas.", "success")
    return redirect(url_for("pages.profile"))


@bp.route("/conectar")
@login_required
def connect():
    user = current_user()
    token = whatsapp.create_link_token(db(), user)
    return render_template(
        "connect.html",
        token=token,
        deep_link=whatsapp.deep_link(token),
        whatsapp_number=config.WHATSAPP_NUMBER,
        configured=whatsapp.is_configured(),
        **_shell(active="profile"),
    )


@bp.route("/conectar/desvincular", methods=["POST"])
@login_required
def disconnect():
    whatsapp.unlink(db(), current_user())
    flash("WhatsApp desvinculado.", "success")
    return redirect(url_for("pages.profile"))


@bp.route("/materias/<subject_id>/compartilhar", methods=["POST"])
@onboarding_required
def share_subject(subject_id: str):
    user = current_user()
    subject = db().get(Subject, subject_id)
    if subject is None or subject.user_id != user.id:
        abort(404)
    view = planner.subject_view(db(), user, subject)
    snapshot = {
        "subject": {"name": subject.name, "short_name": subject.short_name, "color": subject.color},
        "teacher": subject.teacher.name if subject.teacher else "",
        "schedules": view["schedules"],
        "events": [
            {"title": c["title"], "type": c["type"], "date": c["date"], "description": c["description"]}
            for c in view["upcoming"]
        ],
    }
    collection = SharedCollection(
        owner_id=user.id,
        code=share_code(),
        title=subject.name,
        description=f"{len(snapshot['events'])} atividades",
        subject_id=subject.id,
        snapshot=snapshot,
    )
    db().add(collection)
    db().flush()
    return redirect(url_for("pages.share_view", code=collection.code))


@bp.route("/join/<code>")
def share_view(code: str):
    collection = db().scalars(
        select(SharedCollection).where(SharedCollection.code == code.upper())
    ).first()
    if collection is None or not collection.active:
        abort(404)
    return render_template(
        "share.html",
        collection=collection,
        snapshot=collection.snapshot,
        logged_in=current_user() is not None,
    )


@bp.route("/join/<code>", methods=["POST"])
@onboarding_required
def share_accept(code: str):
    from agenda.core import events as events_core

    user = current_user()
    collection = db().scalars(
        select(SharedCollection).where(SharedCollection.code == code.upper())
    ).first()
    if collection is None or not collection.active:
        abort(404)
    context = _context()
    if context is None:
        abort(400)
    snapshot = collection.snapshot or {}
    subject_data = snapshot.get("subject", {})
    teacher = None
    if snapshot.get("teacher"):
        teacher = academic.upsert_teacher(db(), user.id, snapshot["teacher"])
    subject = academic.upsert_subject(
        db(), user.id, context.id, subject_data.get("name", collection.title),
        short_name=subject_data.get("short_name", ""),
        color=subject_data.get("color", ""),
        teacher_id=teacher.id if teacher else None,
    )
    for schedule in snapshot.get("schedules", []):
        from agenda.core.dates import WEEKDAY_LABELS

        weekday_label = (schedule.get("weekday") or "").lower()
        weekday = next((i for i, label in enumerate(WEEKDAY_LABELS) if label == weekday_label), None)
        if weekday is None:
            continue
        academic.upsert_schedule(
            db(), user.id, subject,
            weekday=weekday,
            start_time=schedule.get("start_time", "08:00"),
            end_time=schedule.get("end_time", "09:00"),
        )
    imported = 0
    for item in snapshot.get("events", []):
        try:
            date = dt.date.fromisoformat(item["date"])
        except (KeyError, ValueError):
            continue
        events_core.create_event(
            db(), user,
            title=item.get("title", "Atividade"),
            event_type=item.get("type", EventType.OTHER.value),
            date=date,
            subject=subject,
            context_id=context.id,
            description=item.get("description", ""),
            source_type="SHARED",
            source_id=collection.id,
            created_by="share",
        )
        imported += 1
    collection.uses += 1
    flash(f"{subject.name} adicionada com {imported} atividade(s).", "success")
    return redirect(url_for("pages.subject_detail", subject_id=subject.id))


# --------------------------------------------------------------------------- #
# Admin (SPEC §97) — separado da experiência do aluno
# --------------------------------------------------------------------------- #
@bp.route("/admin")
@login_required
@admin_required
def admin():
    users = db().scalars(select(User).where(User.deleted_at.is_(None))).all()
    documents = db().scalars(select(Document)).all()
    usage = db().scalars(select(AiUsage)).all()
    total_cost = round(sum(u.estimated_cost for u in usage), 4)
    return render_template(
        "admin.html",
        users=users,
        documents=documents,
        usage_count=len(usage),
        total_cost=total_cost,
        cost_per_user=round(total_cost / len(users), 4) if users else 0,
        failures=[d for d in documents if d.status == "FAILED"],
        flags=config.FEATURE_FLAGS,
    )


# --------------------------------------------------------------------------- #
# PWA (SPEC §60)
# --------------------------------------------------------------------------- #
@bp.route("/manifest.webmanifest")
def manifest():
    response = jsonify(
        {
            "name": config.APP_NAME,
            "short_name": config.APP_NAME,
            "description": "Seu planner acadêmico que se organiza sozinho.",
            "start_url": "/hoje",
            "scope": "/",
            "display": "standalone",
            "background_color": "#0b0d12",
            "theme_color": "#0b0d12",
            "lang": "pt-BR",
            "icons": [
                {"src": url_for("static", filename="icon.svg"), "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
            ],
            "shortcuts": [
                {"name": "Hoje", "url": "/hoje"},
                {"name": "Capturar", "url": "/assistente"},
            ],
        }
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@bp.route("/sw.js")
def service_worker():
    response = make_response(render_template("sw.js"))
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@bp.route("/offline")
def offline():
    return render_template("offline.html")


@bp.route("/healthz")
def healthz():
    return {"status": "ok", "env": config.ENV}, 200
