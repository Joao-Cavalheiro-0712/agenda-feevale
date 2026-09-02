"""Dependências compartilhadas das rotas: sessão de banco, usuário e CSRF."""
from __future__ import annotations

import functools

from flask import g, jsonify, redirect, request, session, url_for

from agenda import config
from agenda.db import SessionLocal
from agenda.models import User
from agenda.security import csrf_ok, new_csrf_token, rate_limit

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def db():
    if "db" not in g:
        g.db = SessionLocal()
    return g.db


def init_app(app) -> None:
    @app.before_request
    def _prepare():
        session.permanent = True
        if "csrf" not in session:
            session["csrf"] = new_csrf_token()
        g.user = _load_user()
        if request.method not in SAFE_METHODS and not _csrf_exempt():
            submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            if not csrf_ok(session.get("csrf"), submitted):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "CSRF inválido."}), 403
                return "Sessão expirada. Recarregue a página.", 403
        return None

    @app.teardown_appcontext
    def _close(exception):
        database = g.pop("db", None)
        if database is None:
            return
        try:
            if exception is None:
                database.commit()
            else:
                database.rollback()
        finally:
            database.close()


def _csrf_exempt() -> bool:
    return request.path.startswith("/webhooks/")


def _load_user() -> User | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = db().get(User, user_id)
    if user is None or user.deleted_at is not None:
        session.pop("user_id", None)
        return None
    return user


def login_user(user: User) -> None:
    session["user_id"] = user.id
    session["csrf"] = new_csrf_token()


def logout_user() -> None:
    session.clear()


def current_user() -> User | None:
    return getattr(g, "user", None)


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Faça login."}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def onboarding_required(view):
    """Rotas do app exigem um contexto educacional configurado."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("auth.login", next=request.path))
        if not user.onboarding_done:
            return redirect(url_for("pages.onboarding"))
        return view(*args, **kwargs)

    return wrapped


def limited(bucket: str):
    """Aplica rate limit por usuário/IP (SPEC §111)."""

    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            identity = user.id if user else (request.headers.get("X-Forwarded-For") or request.remote_addr or "anon")
            if not rate_limit(bucket, identity):
                message = "Muitas tentativas. Tente de novo em instantes."
                if request.path.startswith("/api/"):
                    return jsonify({"error": message}), 429
                return message, 429
            return view(*args, **kwargs)

        return wrapped

    return decorator


def wants_json() -> bool:
    return request.path.startswith("/api/") or request.headers.get("Accept", "").startswith(
        "application/json"
    )


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        is_admin = user is not None and (
            user.is_admin or (user.email or "").lower() in config.ADMIN_EMAILS
        )
        if not is_admin:
            return "Não encontrado.", 404
        return view(*args, **kwargs)

    return wrapped
