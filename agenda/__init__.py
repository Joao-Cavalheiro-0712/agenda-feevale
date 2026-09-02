"""Planner acadêmico multimodal — aplicação Flask.

Camadas:
    agenda/core      regras de negócio determinísticas (fonte de verdade)
    agenda/ai        interpretação: prompts, provedores, heurísticas
    agenda/ingest    pipeline documental
    agenda/channels  WhatsApp e Telegram
    agenda/jobs      workers em background
    agenda/web       HTTP: páginas, API e webhooks
"""
from __future__ import annotations

import os

from flask import Flask, g, request, session

from agenda import config
from agenda.db import init_db

__version__ = "1.0.0"


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_BYTES,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.IS_PRODUCTION,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=not config.IS_PRODUCTION,
    )

    from agenda.web import api, auth, deps, pages, webhooks

    deps.init_app(app)

    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(webhooks.bp)

    @app.after_request
    def security_headers(response):
        """CSP e cabeçalhos de segurança (SPEC §79)."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), camera=(self), microphone=(self)"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if config.IS_PRODUCTION:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    _register_jinja(app)

    @app.context_processor
    def inject_globals():
        return {
            "APP_NAME": config.APP_NAME,
            "flag": config.flag,
            "current_user": getattr(g, "user", None),
            "csrf_token": session.get("csrf"),
            "version": __version__,
        }

    @app.errorhandler(413)
    def too_large(_error):
        message = f"Arquivo maior que {config.MAX_UPLOAD_MB} MB."
        if request.path.startswith("/api/"):
            return {"error": message}, 413
        return message, 413

    init_db()
    _bootstrap_background()
    return app


def _register_jinja(app: Flask) -> None:
    """Filtros e globais usados pelos templates."""
    import datetime as _dt

    from agenda.core.dates import WEEKDAY_LABELS, format_date_pt

    def date_pt(value):
        if isinstance(value, str):
            try:
                value = _dt.date.fromisoformat(value[:10])
            except ValueError:
                return value
        return format_date_pt(value)

    def weekday_pt(value: int) -> str:
        return WEEKDAY_LABELS[int(value) % 7].capitalize()

    app.jinja_env.filters["date_pt"] = date_pt
    app.jinja_env.filters["weekday_pt"] = weekday_pt
    app.jinja_env.globals["one_week"] = _dt.timedelta(days=7)


def _bootstrap_background() -> None:  # pragma: no cover - infra
    """Sobe workers apenas no processo principal (evita duplicar no reloader)."""
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return
    if os.environ.get("DISABLE_BACKGROUND_JOBS", "").lower() in ("1", "true"):
        return
    from agenda.channels import telegram
    from agenda.jobs import scheduler

    scheduler.start()
    telegram.start_poller()
