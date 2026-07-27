"""Interface web (Flask) para anexar cronogramas e ver/gerir os eventos."""
from __future__ import annotations

import datetime as dt
import functools

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import config
import scheduler
import telegram_bot
from database import Event, SessionLocal, Subscriber, init_db
from extractor.event_extract import extract_events
from extractor.text_extract import extract_text

app = Flask(__name__)
app.secret_key = config.WEB_PASSWORD or "agenda-feevale-dev-secret"
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB por upload


# --------------------------------------------------------------------------- #
# Proteção opcional por senha
# --------------------------------------------------------------------------- #
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if config.WEB_PASSWORD and not session.get("auth"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if not config.WEB_PASSWORD:
        return redirect(url_for("index"))
    if request.method == "POST":
        if request.form.get("password") == config.WEB_PASSWORD:
            session["auth"] = True
            return redirect(url_for("index"))
        flash("Senha incorreta.", "error")
    return render_template("login.html")


# --------------------------------------------------------------------------- #
# Páginas
# --------------------------------------------------------------------------- #
@app.route("/")
@login_required
def index():
    today = dt.date.today()
    with SessionLocal() as db:
        upcoming = (
            db.query(Event).filter(Event.date >= today).order_by(Event.date).all()
        )
        past = (
            db.query(Event)
            .filter(Event.date < today)
            .order_by(Event.date.desc())
            .limit(50)
            .all()
        )
        subs = db.query(Subscriber).filter_by(active=True).all()
    return render_template(
        "index.html",
        upcoming=upcoming,
        past=past,
        subscribers=subs,
        today=today,
        reminder_days=config.REMINDER_DAYS,
        has_ai=bool(config.GEMINI_API_KEY),
        has_bot=bool(config.TELEGRAM_BOT_TOKEN),
    )


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    files = request.files.getlist("cronograma")
    if not files or all(not f.filename for f in files):
        flash("Selecione ao menos um arquivo.", "error")
        return redirect(url_for("index"))

    total_new = 0
    total_found = 0
    for f in files:
        if not f.filename:
            continue
        data = f.read()
        try:
            text = extract_text(f.filename, data)
        except ValueError as exc:
            flash(str(exc), "error")
            continue
        if not text.strip():
            flash(f"Não consegui ler texto de {f.filename}.", "error")
            continue

        events = extract_events(text, source_file=f.filename)
        total_found += len(events)
        total_new += _save_events(events)

    if total_found == 0:
        flash(
            "Nenhuma avaliação/prova/trabalho com data foi identificada. "
            "Verifique se o cronograma tem datas explícitas.",
            "error",
        )
    else:
        flash(
            f"{total_found} item(ns) identificado(s), {total_new} novo(s) adicionado(s).",
            "success",
        )
    return redirect(url_for("index"))


def _save_events(events: list[dict]) -> int:
    added = 0
    with SessionLocal() as db:
        for e in events:
            date = dt.date.fromisoformat(e["date"])
            exists = (
                db.query(Event)
                .filter_by(title=e["title"], date=date, discipline=e.get("discipline", ""))
                .first()
            )
            if exists:
                continue
            db.add(
                Event(
                    title=e["title"],
                    kind=e.get("kind", "avaliacao"),
                    date=date,
                    discipline=e.get("discipline", ""),
                    notes=e.get("notes", ""),
                    source_file=e.get("source_file", ""),
                )
            )
            added += 1
        db.commit()
    return added


@app.route("/event/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_event(event_id: int):
    with SessionLocal() as db:
        ev = db.get(Event, event_id)
        if ev:
            db.delete(ev)
            db.commit()
    flash("Evento removido.", "success")
    return redirect(url_for("index"))


@app.route("/event/add", methods=["POST"])
@login_required
def add_event():
    try:
        date = dt.date.fromisoformat(request.form["date"])
    except (KeyError, ValueError):
        flash("Data inválida.", "error")
        return redirect(url_for("index"))
    title = request.form.get("title", "").strip()
    if not title:
        flash("Informe um título.", "error")
        return redirect(url_for("index"))
    _save_events(
        [
            {
                "title": title,
                "kind": request.form.get("kind", "avaliacao"),
                "date": date.isoformat(),
                "discipline": request.form.get("discipline", "").strip(),
                "notes": request.form.get("notes", "").strip(),
                "source_file": "manual",
            }
        ]
    )
    flash("Avaliação adicionada.", "success")
    return redirect(url_for("index"))


@app.route("/test-notification", methods=["POST"])
@login_required
def test_notification():
    n = telegram_bot.broadcast(
        "🔔 <b>Teste da Agenda Feevale</b>\nSe você recebeu isto, as notificações estão funcionando!"
    )
    if n:
        flash(f"Mensagem de teste enviada para {n} chat(s).", "success")
    else:
        flash(
            "Nenhum chat recebeu. Mande /start para o seu bot no Telegram primeiro.",
            "error",
        )
    return redirect(url_for("index"))


@app.route("/run-reminders", methods=["POST"])
@login_required
def run_reminders_now():
    n = scheduler.run_reminders()
    flash(f"Verificação executada: {n} lembrete(s) enviado(s).", "success")
    return redirect(url_for("index"))


@app.route("/healthz")
def healthz():
    return {"status": "ok"}, 200


# --------------------------------------------------------------------------- #
# Inicialização dos serviços em background
# --------------------------------------------------------------------------- #
def bootstrap() -> None:
    init_db()
    telegram_bot.start_poller()
    scheduler.start_scheduler()


bootstrap()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
