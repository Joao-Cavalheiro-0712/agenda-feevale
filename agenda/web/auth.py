"""Cadastro, login e conta (SPEC §6, §78, §80)."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from agenda.core import phone as phone_utils
from agenda.core.events import log
from agenda.models import User
from agenda.security import hash_password, password_problems, verify_password
from agenda.web.deps import current_user, db, limited, login_required, login_user, logout_user

bp = Blueprint("auth", __name__)


@bp.route("/entrar", methods=["GET", "POST"])
@limited("login")
def login():
    if current_user() is not None:
        return redirect(url_for("pages.today"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = db().query(User).filter(User.email == email).first()
        if user is None or not verify_password(password, user.password_hash):
            flash("E-mail ou senha incorretos.", "error")
        else:
            login_user(user)
            destination = request.args.get("next") or url_for("pages.today")
            return redirect(destination if destination.startswith("/") else url_for("pages.today"))
    return render_template("auth/login.html")


@bp.route("/criar-conta", methods=["GET", "POST"])
@limited("login")
def register():
    if current_user() is not None:
        return redirect(url_for("pages.today"))
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        phone = phone_utils.normalize(request.form.get("phone") or "")

        problem = password_problems(password)
        if not email or "@" not in email:
            problem = "Informe um e-mail válido."
        elif db().query(User).filter(User.email == email).first() is not None:
            problem = "Já existe uma conta com esse e-mail."
        if problem:
            flash(problem, "error")
            return render_template("auth/register.html", name=name, email=email)

        user = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            phone_e164=phone or None,
            timezone=request.form.get("timezone") or "America/Sao_Paulo",
        )
        db().add(user)
        db().flush()
        log(db(), user_id=user.id, actor="user", action="REGISTER", object_type="user", object_id=user.id)
        login_user(user)
        return redirect(url_for("pages.onboarding"))
    return render_template("auth/register.html")


@bp.route("/sair", methods=["POST", "GET"])
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/conta/excluir", methods=["POST"])
@login_required
def delete_account():
    """Exclusão de conta (SPEC §80, §142)."""
    user = current_user()
    user.deleted_at = dt.datetime.now(dt.timezone.utc)
    user.email = f"deleted+{user.id}@invalid"
    user.password_hash = ""
    log(db(), user_id=user.id, actor="user", action="DELETE_ACCOUNT", object_type="user", object_id=user.id)
    session.clear()
    flash("Conta excluída.", "success")
    return redirect(url_for("auth.login"))
