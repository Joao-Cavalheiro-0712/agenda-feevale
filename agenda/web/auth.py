"""Cadastro, login e conta (SPEC §6, §78, §80).

Postura de segurança desta camada:
  * mensagens de erro genéricas no login — não dizemos se o e-mail existe;
  * tempo de resposta constante, com hash descartável para conta inexistente;
  * bloqueio progressivo por conta e por IP, além do rate limit por rota;
  * sessão nova a cada login (sem fixação) e revogável dispositivo a dispositivo;
  * troca de senha encerra as outras sessões.
"""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from agenda.core import login_guard, phone as phone_utils, sessions
from agenda.core.events import log
from agenda.models import User
from agenda.security import dummy_verify, hash_password, password_problems, verify_password
from agenda.web.deps import (
    _client_ip,
    current_user,
    db,
    limited,
    login_required,
    login_user,
    logout_user,
)

bp = Blueprint("auth", __name__)

CREDENCIAL_INVALIDA = "E-mail ou senha incorretos."


def _safe_next(destino: str | None) -> str:
    """Só aceita caminho interno — bloqueia redirect aberto."""
    if not destino:
        return url_for("pages.today")
    if destino.startswith("//") or "://" in destino or "\\" in destino:
        return url_for("pages.today")
    if not destino.startswith("/"):
        return url_for("pages.today")
    return destino


@bp.route("/entrar", methods=["GET", "POST"])
@limited("login")
def login():
    if current_user() is not None:
        return redirect(url_for("pages.today"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:200]
        password = request.form.get("password") or ""
        ip = _client_ip()

        bloqueado, minutos = login_guard.blocked(db(), email, ip)
        if bloqueado:
            flash(
                f"Muitas tentativas. Tente de novo em {minutos} minuto(s).",
                "error",
            )
            return render_template("auth/login.html"), 429

        user = db().query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
        if user is None:
            # Gasta o mesmo tempo de um login real: sem canal por tempo.
            dummy_verify(password)
            login_guard.record(db(), email, ip, success=False)
            flash(CREDENCIAL_INVALIDA, "error")
        elif not verify_password(password, user.password_hash):
            login_guard.record(db(), email, ip, success=False)
            flash(CREDENCIAL_INVALIDA, "error")
        else:
            login_guard.record(db(), email, ip, success=True)
            login_user(user)
            log(db(), user_id=user.id, actor="user", action="LOGIN", object_type="user",
                object_id=user.id, origin="web")
            return redirect(_safe_next(request.args.get("next")))

    return render_template("auth/login.html")


@bp.route("/criar-conta", methods=["GET", "POST"])
@limited("register")
def register():
    if current_user() is not None:
        return redirect(url_for("pages.today"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:160]
        email = (request.form.get("email") or "").strip().lower()[:200]
        password = request.form.get("password") or ""
        phone = phone_utils.normalize(request.form.get("phone") or "")

        problema = password_problems(password)
        if not email or "@" not in email or " " in email:
            problema = "Informe um e-mail válido."
        elif len(email) > 200:
            problema = "E-mail longo demais."
        elif db().query(User).filter(User.email == email).first() is not None:
            problema = "Já existe uma conta com esse e-mail. Tente entrar."
        if problema:
            flash(problema, "error")
            return render_template("auth/register.html", name=name, email=email)

        user = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            phone_e164=phone or None,
            timezone=(request.form.get("timezone") or "America/Sao_Paulo").strip()[:64],
        )
        db().add(user)
        db().flush()
        log(db(), user_id=user.id, actor="user", action="REGISTER", object_type="user",
            object_id=user.id, origin="web")
        login_user(user)
        return redirect(url_for("pages.onboarding"))

    return render_template("auth/register.html")


@bp.post("/sair")
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.post("/conta/senha")
@login_required
def change_password():
    """Trocar a senha encerra as demais sessões — comportamento esperado."""
    user = current_user()
    atual = request.form.get("current_password") or ""
    nova = request.form.get("new_password") or ""

    if not verify_password(atual, user.password_hash):
        flash("Senha atual incorreta.", "error")
        return redirect(url_for("pages.security_page"))
    problema = password_problems(nova)
    if problema:
        flash(problema, "error")
        return redirect(url_for("pages.security_page"))

    user.password_hash = hash_password(nova)
    encerradas = sessions.revoke_all(db(), user, keep_token=session.get("sid"))
    log(db(), user_id=user.id, actor="user", action="CHANGE_PASSWORD", object_type="user",
        object_id=user.id, after={"sessions_revoked": encerradas})
    flash(
        f"Senha alterada. Encerrei {encerradas} sessão(ões) em outros dispositivos."
        if encerradas else "Senha alterada.",
        "success",
    )
    return redirect(url_for("pages.security_page"))


@bp.post("/conta/sessoes/<session_id>/revogar")
@login_required
def revoke_session(session_id: str):
    user = current_user()
    if sessions.revoke_id(db(), user, session_id):
        flash("Dispositivo desconectado.", "success")
    else:
        flash("Sessão não encontrada.", "error")
    return redirect(url_for("pages.security_page"))


@bp.post("/conta/sessoes/revogar-todas")
@login_required
def revoke_other_sessions():
    user = current_user()
    encerradas = sessions.revoke_all(db(), user, keep_token=session.get("sid"))
    flash(f"{encerradas} sessão(ões) encerrada(s).", "success")
    return redirect(url_for("pages.security_page"))


@bp.post("/conta/excluir")
@login_required
def delete_account():
    """Exclusão de conta (SPEC §80, §142): anonimiza e encerra tudo."""
    user = current_user()
    confirmacao = (request.form.get("confirm") or "").strip().lower()
    if confirmacao != "excluir":
        flash("Digite EXCLUIR para confirmar.", "error")
        return redirect(url_for("pages.security_page"))

    user.deleted_at = dt.datetime.now(dt.timezone.utc)
    user.email = f"deleted+{user.id}@invalid"
    user.password_hash = ""
    user.phone_e164 = None
    user.name = ""
    sessions.revoke_all(db(), user)
    log(db(), user_id=user.id, actor="user", action="DELETE_ACCOUNT", object_type="user",
        object_id=user.id)
    session.clear()
    flash("Conta excluída. Sentimos muito por não dar certo.", "success")
    return redirect(url_for("auth.login"))
