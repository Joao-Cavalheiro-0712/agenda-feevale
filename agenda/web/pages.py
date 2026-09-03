"""Páginas do app (SPEC §28-§38, §56, §57, §97)."""
from __future__ import annotations

import datetime as dt
import json
import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from sqlalchemy import select

from agenda import config
from agenda.channels import whatsapp
from agenda.core import (
    academic,
    billing,
    calendar_export,
    family,
    grades,
    notifications,
    periods,
    planner,
    privacy,
    profiles,
    scope,
    sessions,
    study,
)
from agenda.core.events import event_card, refresh_statuses
from agenda.ingest import pipeline
from agenda.models import (
    AiUsage,
    DegreeKind,
    GuardianLink,
    LinkToken,
    Document,
    EducationContext,
    EducationType,
    Event,
    EventType,
    Location,
    Notification,
    PeriodKind,
    SharedCollection,
    Subject,
    SubjectStatus,
    Teacher,
    User,
    UserPhone,
)
from agenda.security import password_problems, share_code
from agenda.web.deps import (
    _client_ip,
    admin_required,
    current_user,
    db,
    limited,
    login_required,
    onboarding_required,
)

bp = Blueprint("pages", __name__)

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
    """Tipos de atividade oferecidos, no vocabulário do nível (SPEC §47)."""
    return profiles.offered_types(context.type if context else None)


def _shell(**extra):
    user = current_user()
    context = _context()
    perfil = profiles.profile_of_context(context) if context else profiles.profile_for(None)
    return {
        "context": context,
        "contexts": academic.list_contexts(db(), user.id) if user else [],
        "subjects": academic.list_subjects(db(), user.id, context_id=context.id if context else None),
        "ui_types": _ui_types(context),
        "perfil": perfil,
        "periodo": periods.current_period(db(), context) if context else None,
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
    """Escolha do nível + apenas os campos que fazem sentido para ele (SPEC §6)."""
    user = current_user()
    if request.method == "POST":
        education_type = request.form.get("type") or EducationType.UNDERGRAD.value
        if education_type not in {t.value for t in EducationType}:
            education_type = EducationType.OTHER.value
        perfil = profiles.profile_for(education_type)

        # Uma conta adulta escolhendo um nível de criança tem três explicações,
        # e a mais comum no Brasil não é a fraude: é adulto no EJA. Depois vem
        # o pai montando a agenda do filho na conta errada e, só então, a
        # criança que informou um ano falso. Perguntar custa um toque e acerta
        # os três; bloquear erraria justamente com quem voltou a estudar.
        if (
            profiles.is_child_only_profile(education_type)
            and not user.is_minor
            and request.form.get("confirmo_adulto") != "1"
        ):
            return render_template(
                "legal/de_quem_e_a_agenda.html",
                nivel=education_type,
                nivel_label=perfil.label,
                eja_label=profiles.PROFILES[EducationType.EJA.value].label,
                dados=request.form,
            ), 200

        period_kind = request.form.get("period_kind") or perfil.default_period_kind
        if period_kind not in {k.value for k in PeriodKind}:
            period_kind = perfil.default_period_kind

        degree_kind = request.form.get("degree_kind") or ""
        if degree_kind not in {d.value for d in DegreeKind}:
            degree_kind = ""

        context = EducationContext(
            user_id=user.id,
            type=education_type,
            degree_kind=degree_kind,
            institution=(request.form.get("institution") or "").strip()[:200],
            course_name=(request.form.get("course_name") or "").strip()[:200],
            grade_name=(request.form.get("grade_name") or "").strip()[:80],
            semester=(request.form.get("semester") or "").strip()[:40],
            module=(request.form.get("module") or "").strip()[:40],
            class_name=(request.form.get("class_name") or "").strip()[:80],
            shift=(request.form.get("shift") or "").strip()[:20],
            period_kind=period_kind,
            is_active=True,
        )
        for field in ("starts_on", "ends_on"):
            raw = request.form.get(field)
            if raw:
                try:
                    setattr(context, field, dt.date.fromisoformat(raw))
                except ValueError:
                    pass
        db().add(context)
        db().flush()
        academic.set_active_context(db(), user.id, context.id)
        periods.ensure_periods(db(), context)
        user.onboarding_done = True
        # Automação silenciosa desligada por padrão para menores (SPEC §80). O
        # critério é a pessoa, não o nível: um adulto no EJA ou no fundamental
        # é um adulto, e não perde recurso por causa da série em que está.
        if user.is_minor:
            user.auto_create_enabled = False
        db().flush()
        flash("Tudo certo. Agora me conta o que você precisa lembrar.", "success")
        return redirect(url_for("pages.today"))

    return render_template(
        "onboarding.html",
        education_options=[(key, profiles.PROFILES[key]) for key in profiles.ONBOARDING_ORDER],
        period_labels=periods.PERIOD_LABELS,
        degree_labels=profiles.DEGREE_LABELS,
    )


@bp.route("/onboarding/voz")
@login_required
def onboarding_voice():
    from agenda.ai.providers import ai_available

    return render_template(
        "onboarding_voice.html",
        ia_disponivel=ai_available(),
        exemplos=profiles.profile_for(None).capture_examples,
    )


@bp.post("/onboarding/voz/confirmar")
@login_required
def onboarding_voice_confirm():
    """Aplica o que o usuário revisou na tela — nunca o que a IA supôs sozinha."""
    from agenda.ai import onboarding as onboarding_ai

    user = current_user()
    bruto = request.form.get("payload") or ""
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        flash("Não consegui ler a revisão. Tente de novo.", "error")
        return redirect(url_for("pages.onboarding_voice"))
    if not isinstance(dados, dict):
        abort(400)

    # Só entram as matérias que o usuário deixou marcadas.
    marcadas = set(request.form.getlist("keep"))
    dados["subjects"] = [
        materia
        for indice, materia in enumerate(dados.get("subjects", []) or [])
        if str(indice) in marcadas
    ]
    if not dados["subjects"]:
        flash("Escolha ao menos uma matéria.", "error")
        return redirect(url_for("pages.onboarding_voice"))

    resultado = onboarding_ai.apply(db(), user, dados)
    flash(
        f"Pronto: {resultado['subjects']} matéria(s) e {resultado['schedules']} horário(s).",
        "success",
    )
    return redirect(url_for("pages.today"))


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
    shell = _shell(active="today")
    periodo = shell.get("periodo")
    dias_restantes = None
    if periodo is not None and periodo.ends_on:
        restante = (periodo.ends_on - planner.today_of(user)).days
        dias_restantes = restante if restante >= 0 else None
    return render_template(
        "today.html",
        view=view,
        greeting=_greeting(user),
        primeiro_nome=(user.name or "").split(" ")[0],
        dias_restantes=dias_restantes,
        **shell,
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
    event = scope.get(db(), Event, event_id, user.id)
    if event is None:
        abort(404)
    document = scope.get(db(), Document, event.source_id, user.id) if event.source_id else None
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
    subject = scope.get(db(), Subject, subject_id, user.id)
    if subject is None:
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
    subject = scope.get(db(), Subject, subject_id, user.id)
    if subject is None:
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
    subject = scope.get(db(), Subject, subject_id, user.id)
    if subject is None:
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
    document = scope.get(db(), Document, document_id, user.id)
    if document is None:
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
    document = scope.get(db(), Document, document_id, user.id)
    if document is None:
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
    document = scope.get(db(), Document, document_id, user.id)
    if document is None:
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
    links = db().scalars(scope.query(UserPhone, user.id)).all()
    calendario = db().scalars(
        scope.query(LinkToken, user.id).where(LinkToken.purpose == "calendar")
    ).first()
    base = config.PUBLIC_URL or request.url_root.rstrip("/")
    return render_template(
        "profile.html",
        links=[l for l in links if l.active],
        whatsapp_configured=whatsapp.is_configured(),
        whatsapp_number=config.WHATSAPP_NUMBER,
        plano=billing.active_plan(db(), user),
        calendar_url=f"{base}/calendario/{calendario.token}.ics" if calendario else "",
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
    subject = scope.get(db(), Subject, subject_id, user.id)
    if subject is None:
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
@limited("share")
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
# Conta: segurança, dispositivos e privacidade (SPEC §78, §80)
# --------------------------------------------------------------------------- #
@bp.route("/conta/seguranca")
@login_required
def security_page():
    user = current_user()
    ativos = sessions.list_active(db(), user)
    atual = flask_session.get("sid")
    from agenda.security import hash_token

    atual_hash = hash_token(atual) if atual else ""
    return render_template(
        "security.html",
        devices=[
            {
                "id": row.id,
                "label": sessions.describe(row),
                "created_at": row.created_at,
                "last_seen_at": row.last_seen_at,
                "is_current": row.token_hash == atual_hash,
            }
            for row in ativos
        ],
        **_shell(active="profile"),
    )


# --------------------------------------------------------------------------- #
# Documentos legais e privacidade (LGPD)
# --------------------------------------------------------------------------- #
def _legal(secoes, *, titulo, titulo_marcado, versao, resumo):
    from agenda.legal import documents

    return render_template(
        "legal/documento.html",
        secoes=secoes,
        titulo=titulo,
        titulo_marcado=titulo_marcado,
        versao=versao,
        resumo=resumo,
        tratamento=privacy.TREATMENT_RECORD,
        subprocessadores=privacy.SUBPROCESSORS,
        contato=config.PRIVACY_EMAIL,
        documentos=documents,
    )


@bp.route("/termos")
def terms_page():
    from agenda.legal import documents

    return _legal(
        documents.TERMS_SECTIONS,
        titulo="Termos de uso",
        titulo_marcado='O combinado, em <span class="grifo-sob">português</span>',
        versao=privacy.TERMS_VERSION,
        resumo="Sem letra miúda: o que o serviço faz, o que é seu e o que a gente promete.",
    )


@bp.route("/privacidade")
def privacy_page():
    from agenda.legal import documents

    return _legal(
        documents.PRIVACY_SECTIONS,
        titulo="Política de privacidade",
        titulo_marcado='Seus dados, <span class="grifo-sob">explicados</span>',
        versao=privacy.PRIVACY_VERSION,
        resumo="Que dados tratamos, por quê, com quem compartilhamos e como você tira tudo daqui.",
    )


@bp.route("/aceite", methods=["GET", "POST"])
@login_required
def accept_documents_page():
    """Novo aceite quando a versão dos documentos muda — ou quando falta.

    Também é onde uma conta antiga, criada antes da checagem de idade, informa
    o ano de nascimento. Se a resposta indicar menor de idade, a conta não
    segue: o caminho passa a ser o responsável.
    """
    user = current_user()
    if privacy.documents_up_to_date(user) and request.method == "GET":
        return redirect(url_for("pages.today"))

    if request.method == "POST":
        ano = (request.form.get("birth_year") or "").strip()
        birth_year = int(ano) if ano.isdigit() and len(ano) == 4 else user.birth_year
        if not request.form.get("accept_terms"):
            flash("Para continuar é preciso aceitar os documentos.", "error")
            return redirect(url_for("pages.accept_documents_page"))
        if birth_year is None:
            flash("Informe o ano em que você nasceu.", "error")
            return redirect(url_for("pages.accept_documents_page"))
        user.birth_year = birth_year
        if not privacy.is_adult(birth_year) and not user.is_minor:
            # Conta adulta que se revelou de menor: trava e explica o caminho.
            user.is_minor = True
            db().flush()
            return render_template("legal/precisa_responsavel.html", user=user), 200
        privacy.accept_documents(
            db(), user,
            ip=_client_ip(),
            user_agent=request.headers.get("User-Agent", ""),
            ai_processing=request.form.get("ai_processing") in ("on", "1", "true"),
        )
        flash("Obrigado. Registro do aceite guardado.", "success")
        return redirect(url_for("pages.today"))

    return render_template(
        "legal/aceite.html",
        precisa_idade=user.birth_year is None,
        terms_version=privacy.TERMS_VERSION,
        privacy_version=privacy.PRIVACY_VERSION,
    )


@bp.route("/conta/privacidade")
@login_required
def privacy_center():
    """Central de privacidade: o que tratamos, o que você já consentiu, o que dá para desligar."""
    user = current_user()
    rotulos = {
        "TERMS": "Termos de uso",
        "PRIVACY": "Política de privacidade",
        "GUARDIAN_MINOR": "Consentimento do responsável",
        "AI_PROCESSING": "Interpretação automática",
        "MARKETING": "Comunicações opcionais",
    }
    return render_template(
        "legal/central.html",
        historico=privacy.history(db(), user),
        rotulos=rotulos,
        tratamento=privacy.TREATMENT_RECORD,
        subprocessadores=privacy.SUBPROCESSORS,
        contato=config.PRIVACY_EMAIL,
        encarregado=config.DPO_NAME,
        ia_ligada=user.ai_processing_enabled,
        responsaveis=[
            db().get(User, link.guardian_id)
            for link in family.guardians_of(db(), user)
            if link.guardian_id
        ],
        **_shell(active="profile"),
    )


@bp.post("/conta/privacidade/ia")
@login_required
def privacy_toggle_ai():
    user = current_user()
    ligar = request.form.get("enabled") in ("on", "1", "true")
    privacy.set_ai_processing(db(), user, enabled=ligar, ip=_client_ip())
    flash(
        "Interpretação automática ligada." if ligar else
        "Desliguei. Nada mais é enviado para leitura automática — o cadastro manual continua.",
        "success",
    )
    return redirect(url_for("pages.privacy_center"))


# --------------------------------------------------------------------------- #
# Planos (SPEC §96)
# --------------------------------------------------------------------------- #
@bp.route("/planos")
@login_required
def plans_page():
    user = current_user()
    return render_template(
        "plans.html",
        planos=[billing.PLANS[key] for key in ("FREE", "STUDENT", "FAMILY")],
        resumo=billing.summary(db(), user),
        **_shell(active="profile"),
    )


@bp.post("/planos/testar")
@login_required
def start_trial():
    billing.start_trial(db(), current_user())
    flash(f"Teste de {billing.TRIAL_DAYS} dias liberado. Aproveite.", "success")
    return redirect(url_for("pages.plans_page"))


@bp.post("/planos/assinar")
@login_required
def subscribe_plan():
    """Troca de plano. A cobrança real depende do gateway configurado."""
    plano = request.form.get("plan", "")
    if plano not in billing.PLANS:
        flash("Plano inválido.", "error")
        return redirect(url_for("pages.plans_page"))
    if not config.flag("billing_enabled") and plano != "FREE":
        flash(
            "A cobrança ainda não está ligada neste ambiente. "
            "Configure o gateway para aceitar pagamentos.",
            "error",
        )
        return redirect(url_for("pages.plans_page"))
    billing.change_plan(db(), current_user(), plano)
    flash("Plano atualizado.", "success")
    return redirect(url_for("pages.plans_page"))


@bp.post("/planos/cancelar")
@login_required
def cancel_plan():
    billing.cancel(db(), current_user())
    flash("Assinatura cancelada. Você continua com acesso até o fim do período.", "success")
    return redirect(url_for("pages.plans_page"))


# --------------------------------------------------------------------------- #
# Família (SPEC §59)
# --------------------------------------------------------------------------- #
@bp.route("/familia")
@login_required
def family_page():
    user = current_user()
    estudantes = []
    for link in family.students_of(db(), user):
        estudante = db().get(User, link.student_id)
        if estudante is None:
            continue
        estudantes.append({"link": link, "user": estudante})
    return render_template(
        "family.html",
        estudantes=estudantes,
        responsaveis=[
            {"link": link, "user": db().get(User, link.guardian_id) if link.guardian_id else None}
            for link in family.guardians_of(db(), user)
        ],
        pode_usar=billing.allows(db(), user, billing.CAN_USE_FAMILY),
        **_shell(active="profile"),
    )


@bp.route("/familia/novo-estudante", methods=["GET", "POST"])
@login_required
def family_new_student():
    """Responsável cria a conta do filho menor e consente por ele (art. 14).

    O consentimento aqui vale mais que um checkbox anônimo: quem autoriza está
    autenticado, o registro guarda nome, e-mail, vínculo declarado, versão do
    documento, hash do texto, IP embaralhado e agente. É a prova que o art. 8º
    §1º exige do controlador.
    """
    user = current_user()
    if user.is_minor:
        abort(404)  # menor não cria conta para ninguém
    pode, motivo = family.can_create_student(db(), user)

    if request.method == "POST":
        if not pode:
            flash(motivo, "error")
            return redirect(url_for("pages.plans_page"))

        nome = (request.form.get("name") or "").strip()[:160]
        email = (request.form.get("email") or "").strip().lower()[:200]
        senha = request.form.get("password") or ""
        ano = (request.form.get("birth_year") or "").strip()
        parentesco = (request.form.get("relationship") or "responsável").strip()[:40]
        declarou = request.form.get("guardian_consent") in ("on", "1", "true")

        birth_year = int(ano) if ano.isdigit() and len(ano) == 4 else None
        problema = password_problems(senha)
        if not nome:
            problema = "Diga o nome do estudante."
        elif not email or "@" not in email or " " in email:
            problema = "Informe um e-mail válido para o estudante entrar."
        elif birth_year is None or not (1900 <= birth_year <= dt.date.today().year):
            problema = "Informe o ano de nascimento do estudante."
        elif not declarou:
            problema = "É preciso declarar que você é o responsável e autorizar o uso."
        elif db().query(User).filter(User.email == email).first() is not None:
            problema = "Já existe uma conta com esse e-mail."
        if problema:
            flash(problema, "error")
            return render_template(
                "family_new_student.html", pode=pode, motivo=motivo,
                dados=request.form, **_shell(active="profile"),
            )

        estudante = family.create_student_account(
            db(), user,
            name=nome, email=email, password=senha,
            birth_year=birth_year, relationship_label=parentesco,
        )
        # O responsável aceita os documentos em nome do menor e registra o
        # consentimento específico do art. 14 — em duas linhas separadas,
        # porque são consentimentos distintos.
        privacy.accept_documents(
            db(), estudante,
            ip=_client_ip(),
            user_agent=request.headers.get("User-Agent", ""),
            origin="guardian",
            ai_processing=request.form.get("ai_processing") in ("on", "1", "true"),
        )
        privacy.register_guardian_consent(
            db(), estudante,
            guardian_name=user.name or "",
            guardian_email=user.email or "",
            relationship=parentesco,
            ip=_client_ip(),
            user_agent=request.headers.get("User-Agent", ""),
        )
        flash(
            f"Conta de {estudante.name} criada. Entregue o e-mail e a senha para "
            "ele entrar no celular dele.",
            "success",
        )
        return redirect(url_for("pages.family_page"))

    return render_template(
        "family_new_student.html", pode=pode, motivo=motivo, dados={},
        **_shell(active="profile"),
    )


@bp.post("/familia/convidar")
@login_required
def family_invite():
    user = current_user()
    link = family.invite(
        db(), user,
        email=request.form.get("email", ""),
        relationship_label=request.form.get("relationship", "responsável"),
    )
    flash(f"Convite criado. Código: {link.invite_code}", "success")
    return redirect(url_for("pages.family_page"))


@bp.post("/familia/aceitar")
@limited("share")
@login_required
def family_accept():
    codigo = (request.form.get("code") or "").strip()
    link = family.accept(db(), current_user(), codigo)
    if link is None:
        flash("Convite inválido, expirado ou sem vaga no plano.", "error")
    else:
        flash("Pronto. Agora você acompanha a agenda desse estudante.", "success")
    return redirect(url_for("pages.family_page"))


@bp.post("/familia/<link_id>/permissoes")
@login_required
def family_permissions(link_id: str):
    user = current_user()
    link = scope.get(db(), GuardianLink, link_id, user.id)
    if link is None or link.student_id != user.id:
        abort(404)
    link.can_view_agenda = bool(request.form.get("can_view_agenda"))
    link.can_add_events = bool(request.form.get("can_add_events"))
    link.can_receive_reminders = bool(request.form.get("can_receive_reminders"))
    flash("Permissões atualizadas.", "success")
    return redirect(url_for("pages.family_page"))


@bp.post("/familia/<link_id>/encerrar")
@login_required
def family_revoke(link_id: str):
    if family.revoke(db(), current_user(), link_id):
        flash("Vínculo encerrado.", "success")
    else:
        flash("Vínculo não encontrado.", "error")
    return redirect(url_for("pages.family_page"))


@bp.route("/familia/<student_id>/agenda")
@login_required
def family_student_agenda(student_id: str):
    """Responsável vê a agenda do estudante — só se houver vínculo ativo."""
    user = current_user()
    if not family.can_view(db(), user, student_id):
        abort(404)
    estudante = db().get(User, student_id)
    if estudante is None or estudante.deleted_at is not None:
        abort(404)
    grupos = planner.agenda_view(db(), estudante, days=45)
    return render_template(
        "family_agenda.html",
        estudante=estudante,
        grupos=grupos,
        pode_adicionar=family.can_add(db(), user, student_id),
        **_shell(active="profile"),
    )


# --------------------------------------------------------------------------- #
# Notas (SPEC §137)
# --------------------------------------------------------------------------- #
@bp.route("/materias/<subject_id>/notas")
@onboarding_required
def subject_grades(subject_id: str):
    user = current_user()
    subject = scope.get(db(), Subject, subject_id, user.id)
    if subject is None:
        abort(404)
    return render_template(
        "grades.html",
        subject=subject,
        resumo=grades.subject_summary(db(), user, subject),
        **_shell(active="subjects"),
    )


@bp.post("/materias/<subject_id>/notas")
@onboarding_required
def save_subject_grades(subject_id: str):
    user = current_user()
    subject = scope.get(db(), Subject, subject_id, user.id)
    if subject is None:
        abort(404)

    escala = request.form.get("grade_scale")
    if escala:
        try:
            subject.grade_scale = max(1.0, float(escala.replace(",", ".")))
        except ValueError:
            pass
    aprovacao = request.form.get("passing_grade")
    if aprovacao:
        try:
            subject.passing_grade = float(aprovacao.replace(",", "."))
        except ValueError:
            subject.passing_grade = None
    else:
        subject.passing_grade = None

    for evento in grades.graded_events(db(), user, subject.id):
        nota = request.form.get(f"grade_{evento.id}")
        peso = request.form.get(f"weight_{evento.id}")
        try:
            valor = float(nota.replace(",", ".")) if nota else None
        except ValueError:
            valor = None
        try:
            peso_valor = float(peso.replace(",", ".")) if peso else None
        except ValueError:
            peso_valor = None
        grades.set_grade(db(), user, evento, grade_value=valor, weight=peso_valor)

    flash("Notas salvas.", "success")
    return redirect(url_for("pages.subject_grades", subject_id=subject.id))


# --------------------------------------------------------------------------- #
# Plano de estudos (SPEC §93, §94)
# --------------------------------------------------------------------------- #
@bp.route("/plano-de-estudo")
@onboarding_required
def study_plan():
    user = current_user()
    hoje = planner.today_of(user)
    return render_template(
        "study.html",
        blocos=planner.study_blocks_view(db(), user, days=21),
        propostas=study.propose(db(), user, today=hoje),
        liberado=billing.allows(db(), user, billing.CAN_USE_STUDY_PLANNER),
        **_shell(active="agenda"),
    )


@bp.post("/plano-de-estudo/gerar")
@onboarding_required
def study_generate():
    user = current_user()
    if not billing.allows(db(), user, billing.CAN_USE_STUDY_PLANNER):
        flash("O planejador de estudos faz parte dos planos pagos.", "error")
        return redirect(url_for("pages.plans_page"))
    try:
        minutos = max(30, min(int(request.form.get("minutes_per_day", 90)), 360))
    except ValueError:
        minutos = 90
    dias = tuple(
        int(d) for d in request.form.getlist("weekdays") if d.isdigit() and 0 <= int(d) <= 6
    ) or (0, 1, 2, 3, 4, 5, 6)
    propostas = study.propose(
        db(), user, today=planner.today_of(user), minutes_per_day=minutos, weekdays=dias
    )
    criados = study.save(db(), user, propostas)
    flash(f"{criados} bloco(s) de estudo no seu plano.", "success")
    return redirect(url_for("pages.study_plan"))


@bp.post("/plano-de-estudo/limpar")
@onboarding_required
def study_clear():
    removidos = study.clear(db(), current_user())
    flash(f"{removidos} bloco(s) removido(s).", "success")
    return redirect(url_for("pages.study_plan"))


# --------------------------------------------------------------------------- #
# Períodos letivos (SPEC §132)
# --------------------------------------------------------------------------- #
@bp.route("/periodos")
@onboarding_required
def periods_page():
    user = current_user()
    context = _context()
    return render_template(
        "periods.html",
        lista=periods.list_periods(db(), user.id, context_id=context.id if context else None),
        kind_label=periods.kind_label,
        **_shell(active="profile"),
    )


@bp.post("/periodos/virar")
@onboarding_required
def next_period():
    context = _context()
    if context is None:
        abort(400)
    copiar = bool(request.form.get("copy_subjects"))
    novo = periods.start_next_period(db(), context, copy_subjects=copiar)
    flash(f"{novo.label} começou. O período anterior ficou arquivado.", "success")
    return redirect(url_for("pages.periods_page"))


# --------------------------------------------------------------------------- #
# Exportação de calendário (SPEC §95)
# --------------------------------------------------------------------------- #
@bp.post("/calendario/assinar")
@login_required
def calendar_subscribe():
    """Gera (ou renova) o link secreto do calendário .ics."""
    user = current_user()
    for antigo in db().scalars(
        scope.query(LinkToken, user.id).where(LinkToken.purpose == "calendar")
    ).all():
        db().delete(antigo)
    token = LinkToken(
        user_id=user.id,
        token=secrets.token_urlsafe(24)[:32],
        purpose="calendar",
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=730),
    )
    db().add(token)
    db().flush()
    flash("Link do calendário gerado.", "success")
    return redirect(url_for("pages.profile"))


@bp.post("/calendario/revogar")
@login_required
def calendar_revoke():
    user = current_user()
    for antigo in db().scalars(
        scope.query(LinkToken, user.id).where(LinkToken.purpose == "calendar")
    ).all():
        db().delete(antigo)
    flash("Link do calendário revogado.", "success")
    return redirect(url_for("pages.profile"))


@bp.route("/calendario/<token>.ics")
@limited("export")
def calendar_feed(token: str):
    """Feed .ics para Google/Apple/Outlook. Autenticado só pelo token secreto."""
    linha = db().scalars(
        select(LinkToken).where(LinkToken.token == token, LinkToken.purpose == "calendar")
    ).first()
    if linha is None:
        abort(404)
    expira = linha.expires_at
    if expira is not None and expira.tzinfo is None:
        expira = expira.replace(tzinfo=dt.timezone.utc)
    if expira is not None and expira < dt.datetime.now(dt.timezone.utc):
        abort(404)
    dono = db().get(User, linha.user_id)
    if dono is None or dono.deleted_at is not None:
        abort(404)

    corpo = calendar_export.build_calendar(db(), dono, app_name=config.APP_NAME)
    resposta = make_response(corpo)
    resposta.headers["Content-Type"] = "text/calendar; charset=utf-8"
    resposta.headers["Content-Disposition"] = 'inline; filename="grifo.ics"'
    resposta.headers["Cache-Control"] = "private, max-age=900"
    return resposta


# --------------------------------------------------------------------------- #
# Admin (SPEC §97) — separado da experiência do aluno
# --------------------------------------------------------------------------- #
@bp.route("/admin")
@login_required
@admin_required
def admin():
    # O painel interno vê agregados e falhas — nunca o conteúdo dos alunos.
    users = db().scalars(select(User).where(User.deleted_at.is_(None))).all()
    documents = db().scalars(
        select(Document).order_by(Document.created_at.desc()).limit(200)
    ).all()
    usage = db().scalars(select(AiUsage).order_by(AiUsage.created_at.desc()).limit(2000)).all()
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
        menores=[u for u in users if u.is_minor],
    )


@bp.post("/admin/usuarios/<user_id>/menor")
@login_required
@admin_required
def admin_flag_minor(user_id: str):
    """Marca (ou desmarca) uma conta como de menor sem autorização.

    É o fim da linha de um aviso recebido pelo canal de privacidade: alguém
    informou que a conta é de uma criança. Marcar aqui derruba a conta na mesma
    hora pela trava de consentimento — o acesso só volta quando um responsável
    autorizar. Não apagamos nada: o titular (e o responsável) continuam podendo
    exportar e pedir exclusão.
    """
    alvo = db().get(User, user_id)
    if alvo is None or alvo.deleted_at is not None:
        abort(404)

    marcar = request.form.get("acao") != "adulto"
    alvo.is_minor = marcar
    if marcar:
        alvo.guardian_consent_at = None
        alvo.auto_create_enabled = False
        alvo.ai_processing_enabled = False
        sessions.revoke_all(db(), alvo)
    from agenda.core.events import log as log_event

    log_event(db(), user_id=alvo.id, actor="admin", action="FLAG_MINOR",
              object_type="user", object_id=alvo.id,
              after={"is_minor": marcar, "by": current_user().id})
    flash(
        "Conta marcada como de menor e pausada até a autorização do responsável."
        if marcar else "Conta marcada como adulta.",
        "success",
    )
    return redirect(url_for("pages.admin"))


# --------------------------------------------------------------------------- #
# PWA (SPEC §60)
# --------------------------------------------------------------------------- #
@bp.route("/manifest.webmanifest")
def manifest():
    response = jsonify(
        {
            "name": config.APP_NAME,
            "short_name": config.APP_NAME,
            "description": "Manda o cronograma, a foto do quadro ou um áudio. O Grifo organiza.",
            "start_url": "/hoje",
            "scope": "/",
            "display": "standalone",
            "background_color": "#faf6ef",
            "theme_color": "#faf6ef",
            "lang": "pt-BR",
            "categories": ["education", "productivity"],
            "icons": [
                {
                    "src": url_for("static", filename="icon.svg"),
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any",
                },
                {
                    "src": url_for("static", filename="icon-maskable.svg"),
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "maskable",
                },
            ],
            "shortcuts": [
                {"name": "Hoje", "url": "/hoje"},
                {"name": "Grifar", "url": "/assistente"},
                {"name": "Entregas", "url": "/entregas"},
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
