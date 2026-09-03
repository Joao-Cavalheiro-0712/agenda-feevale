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

from agenda.core import login_guard, phone as phone_utils, privacy, sessions
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


def _parse_birth_year(raw: str | None) -> int | None:
    texto = (raw or "").strip()
    if not texto.isdigit() or len(texto) != 4:
        return None
    ano = int(texto)
    atual = dt.date.today().year
    return ano if 1900 <= ano <= atual else None


@bp.route("/criar-conta", methods=["GET", "POST"])
@limited("register")
def register():
    """Cadastro de quem tem 18 anos ou mais.

    Menor de idade não cria conta sozinho: nem para aceitar contrato (Código
    Civil), nem para consentir com o tratamento dos próprios dados (LGPD
    art. 14). O caminho dele é a conta criada pelo responsável, que consente
    autenticado — ver `pages.family_new_student`.
    """
    if current_user() is not None:
        return redirect(url_for("pages.today"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:160]
        email = (request.form.get("email") or "").strip().lower()[:200]
        password = request.form.get("password") or ""
        phone = phone_utils.normalize(request.form.get("phone") or "")
        birth_year = _parse_birth_year(request.form.get("birth_year"))
        aceitou = request.form.get("accept_terms") in ("on", "1", "true")

        problema = password_problems(password)
        if not email or "@" not in email or " " in email:
            problema = "Informe um e-mail válido."
        elif len(email) > 200:
            problema = "E-mail longo demais."
        elif birth_year is None:
            problema = "Informe o ano em que você nasceu."
        elif not privacy.is_adult(birth_year):
            # Não é "erro do usuário": é o caminho errado. Mandamos para o certo.
            return render_template(
                "auth/menor_de_idade.html",
                name=name,
                idade=privacy.age_from_year(birth_year),
            ), 200
        elif not aceitou:
            problema = "Para criar a conta é preciso aceitar os termos e a política."
        elif db().query(User).filter(User.email == email).first() is not None:
            problema = "Já existe uma conta com esse e-mail. Tente entrar."
        if problema:
            flash(problema, "error")
            return render_template(
                "auth/register.html", name=name, email=email,
                birth_year=request.form.get("birth_year", ""),
            )

        user = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            phone_e164=phone or None,
            timezone=(request.form.get("timezone") or "America/Sao_Paulo").strip()[:64],
            birth_year=birth_year,
            is_minor=False,
        )
        db().add(user)
        db().flush()
        # Prova do aceite: versão, hash do texto, IP embaralhado e agente.
        privacy.accept_documents(
            db(), user,
            ip=_client_ip(),
            user_agent=request.headers.get("User-Agent", ""),
            origin="web",
            ai_processing=request.form.get("ai_processing", "on") in ("on", "1", "true"),
        )
        # Indicação: o código chega num cookie assinado deixado por /i/<codigo>.
        # Falha aqui nunca pode impedir alguém de criar a conta, então tudo é
        # silencioso — quem perde é o programa de indicação, não o usuário.
        _atribuir_indicacao(user)

        log(db(), user_id=user.id, actor="user", action="REGISTER", object_type="user",
            object_id=user.id, origin="web",
            after={"terms": privacy.TERMS_VERSION, "privacy": privacy.PRIVACY_VERSION})
        login_user(user)
        return redirect(url_for("pages.onboarding"))

    return render_template("auth/register.html")


def _atribuir_indicacao(user: User) -> None:
    """Lê o cookie de indicação e registra quem trouxe quem."""
    from agenda.core import referrals
    from agenda.security import verify_payload
    from agenda.web.pages import REFERRAL_COOKIE

    bruto = request.cookies.get(REFERRAL_COOKIE)
    if not bruto:
        return
    dados = verify_payload(bruto)
    codigo = (dados or {}).get("c", "")
    if not codigo:
        return
    try:
        referrals.attribute(
            db(), user, codigo,
            ip=_client_ip(), user_agent=request.headers.get("User-Agent", ""),
        )
    except Exception:  # noqa: BLE001 - indicação nunca derruba o cadastro
        db().rollback()


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
