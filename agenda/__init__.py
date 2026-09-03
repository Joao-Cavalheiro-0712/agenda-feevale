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

from flask import Flask, g, render_template, request, session

from agenda import config
from agenda.core import oidc
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
        # Prefixo __Host- amarra o cookie a este host, sem subdomínio e só por
        # HTTPS: nem um subdomínio comprometido consegue plantar sessão.
        SESSION_COOKIE_NAME="__Host-grifo" if config.IS_PRODUCTION else "grifo_session",
        SESSION_COOKIE_PATH="/",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=not config.IS_PRODUCTION,
        MAX_FORM_MEMORY_SIZE=1024 * 1024,
        MAX_FORM_PARTS=200,
    )

    from agenda.web import api, auth, deps, pages, webhooks

    deps.init_app(app)

    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(webhooks.bp)

    @app.after_request
    def security_headers(response):
        """Cabeçalhos de segurança (SPEC §79).

        A CSP não usa 'unsafe-inline' em script-src: cada página recebe um
        nonce por requisição, então um XSS refletido não consegue executar
        script. Em style-src o 'unsafe-inline' permanece porque a interface usa
        atributos style; o risco residual é de aparência, não de execução.
        """
        nonce = getattr(g, "nonce", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(self), microphone=(self), payment=(), usb=(), "
            "accelerometer=(), gyroscope=(), magnetometer=(), interest-cohort=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "manifest-src 'self'; "
            "worker-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "object-src 'none'; "
            "upgrade-insecure-requests"
        )
        response.headers.pop("Server", None)
        # Páginas públicas com dado de turma não devem ser indexadas.
        if request.path.startswith(("/join/", "/calendario/")):
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        if config.IS_PRODUCTION:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
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
            "nonce": getattr(g, "nonce", ""),
            "version": __version__,
            "privacy_email": config.PRIVACY_EMAIL,
            # Só os provedores com chave configurada. Botão que devolve erro é
            # pior que botão nenhum.
            "social_providers": oidc.disponiveis(),
        }

    @app.errorhandler(413)
    def too_large(_error):
        message = f"Arquivo maior que {config.MAX_UPLOAD_MB} MB."
        if request.path.startswith("/api/"):
            return {"error": message}, 413
        return message, 413

    @app.errorhandler(404)
    def not_found(_error):
        """Mesma resposta para 'não existe' e 'não é seu' — sem enumeração."""
        if request.path.startswith("/api/"):
            return {"error": "Não encontrado."}, 404
        return render_template("erro.html", codigo=404,
                               titulo="Não encontrei essa página.",
                               detalhe="O endereço pode ter mudado ou o item não é seu."), 404

    @app.errorhandler(500)
    def server_error(error):
        """Nunca devolvemos stack trace: o detalhe fica no log do servidor."""
        app.logger.exception("erro nao tratado: %s", error)
        if request.path.startswith("/api/"):
            return {"error": "Algo deu errado do nosso lado."}, 500
        return render_template("erro.html", codigo=500,
                               titulo="Algo deu errado aqui.",
                               detalhe="Já registramos o problema. Tente de novo em instantes."), 500

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

    from agenda.core.academic import pigment
    from agenda.core.reminders import describe_offset

    app.jinja_env.filters["date_pt"] = date_pt
    app.jinja_env.filters["weekday_pt"] = weekday_pt
    app.jinja_env.filters["pigmento"] = pigment
    app.jinja_env.filters["offset_pt"] = describe_offset
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
