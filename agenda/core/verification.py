"""Verificação de contato e recuperação de senha.

## Por que verificar

Verificar e-mail não é burocracia: é o que sustenta três coisas.

1. **Recuperação de senha.** Sem verificação, quem cadastrou o e-mail de outra
   pessoa pode "recuperar" a conta dela. A recuperação é tão segura quanto a
   prova de que o e-mail é seu.
2. **Antifraude de indicação.** Recompensa só nasce com pagamento, mas exigir
   e-mail verificado corta a camada de ruído antes dela.
3. **Entregabilidade.** Lembrete que volta é lembrete que não chegou.

## Como o token é feito

Token opaco de 32 bytes, guardado **em hash** na tabela `link_tokens`, com
propósito, expiração e uso único. Não é payload assinado de propósito: payload
assinado não dá para revogar nem para invalidar depois do uso, e recuperação de
senha precisa das duas coisas.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.channels import email as email_channel
from agenda.core.events import log
from agenda.models import LinkToken, User

PROPOSITO_EMAIL = "email_verify"
PROPOSITO_SENHA = "password_reset"
PROPOSITO_TELEFONE = "phone_verify"

# Quantas tentativas de código de telefone antes de queimar o código.
MAX_TENTATIVAS_CODIGO = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _emitir(db: Session, user: User, *, purpose: str, ttl: dt.timedelta,
            valor: str = "") -> str:
    """Cria o token e devolve o valor CRU (que só existe neste instante).

    O banco guarda o hash: um dump vazado não vira link de recuperação.
    """
    bruto = valor or secrets.token_urlsafe(32)
    # Um token vivo por propósito: pedir de novo invalida o anterior, e assim
    # um link antigo que ficou no e-mail não serve mais.
    for antigo in db.scalars(
        select(LinkToken).where(
            LinkToken.user_id == user.id,
            LinkToken.purpose == purpose,
            LinkToken.used_at.is_(None),
        )
    ).all():
        antigo.used_at = dt.datetime.now(dt.timezone.utc)

    db.add(LinkToken(
        user_id=user.id,
        token=_hash(bruto)[:32],
        purpose=purpose,
        expires_at=dt.datetime.now(dt.timezone.utc) + ttl,
    ))
    db.flush()
    return bruto


def _token_vivo(db: Session, user: User, *, purpose: str) -> LinkToken | None:
    """O token ainda utilizável deste propósito, sem queimá-lo."""
    linha = db.scalars(
        select(LinkToken).where(
            LinkToken.user_id == user.id,
            LinkToken.purpose == purpose,
            LinkToken.used_at.is_(None),
        ).order_by(LinkToken.created_at.desc())
    ).first()
    if linha is None:
        return None
    expira = linha.expires_at
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=dt.timezone.utc)
    return linha if expira >= dt.datetime.now(dt.timezone.utc) else None


def _consumir(db: Session, bruto: str, *, purpose: str) -> User | None:
    """Valida e queima o token. Uso único, sempre."""
    if not bruto:
        return None
    linha = db.scalars(
        select(LinkToken).where(
            LinkToken.token == _hash(bruto)[:32],
            LinkToken.purpose == purpose,
        )
    ).first()
    if linha is None or linha.used_at is not None:
        return None
    expira = linha.expires_at
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=dt.timezone.utc)
    if expira < dt.datetime.now(dt.timezone.utc):
        return None
    user = db.get(User, linha.user_id)
    if user is None or user.deleted_at is not None:
        return None
    linha.used_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    return user


# --------------------------------------------------------------------------- #
# E-mail
# --------------------------------------------------------------------------- #
def send_email_verification(db: Session, user: User, *, base_url: str) -> bool:
    if not user.email or user.email_verified_at is not None:
        return False
    token = _emitir(db, user, purpose=PROPOSITO_EMAIL,
                    ttl=dt.timedelta(hours=config.EMAIL_VERIFY_TTL_HOURS))
    link = f"{base_url}/confirmar-email/{token}"
    nome = (user.name or "").split(" ")[0]
    envio = email_channel.send(
        to=user.email,
        subject=f"Confirme seu e-mail no {config.APP_NAME}",
        text=(
            f"{('Oi ' + nome + ', c') if nome else 'C'}onfirme seu e-mail para "
            f"garantir que você consegue recuperar a conta se esquecer a senha.\n\n"
            f"{link}\n\n"
            f"O link vale {config.EMAIL_VERIFY_TTL_HOURS} horas. "
            f"Se não foi você que criou a conta, ignore esta mensagem."
        ),
    )
    log(db, user_id=user.id, actor="system", action="EMAIL_VERIFY_SENT",
        object_type="user", object_id=user.id, after={"enviado": envio.ok})
    return envio.ok


def confirm_email(db: Session, token: str) -> User | None:
    user = _consumir(db, token, purpose=PROPOSITO_EMAIL)
    if user is None:
        return None
    user.email_verified_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    log(db, user_id=user.id, actor="user", action="EMAIL_VERIFIED",
        object_type="user", object_id=user.id)
    return user


def email_verificado(user: User) -> bool:
    return user.email_verified_at is not None


def telefone_verificado(user: User) -> bool:
    return user.phone_verified_at is not None


# --------------------------------------------------------------------------- #
# Recuperação de senha
# --------------------------------------------------------------------------- #
def request_password_reset(db: Session, email: str, *, base_url: str) -> None:
    """Sempre silenciosa: quem pede não descobre se o e-mail existe.

    A função não devolve nada de propósito. Qualquer diferença observável entre
    "existe" e "não existe" — mensagem, código HTTP, tempo — transforma a
    recuperação num enumerador de clientes.
    """
    endereco = (email or "").strip().lower()[:200]
    if not endereco:
        return
    user = db.scalars(
        select(User).where(User.email == endereco, User.deleted_at.is_(None))
    ).first()
    if user is None:
        return
    # E-mail não verificado não recebe link: sem a prova de posse, a
    # recuperação seria o caminho mais fácil para tomar a conta de alguém que
    # digitou o endereço errado no cadastro.
    if user.email_verified_at is None:
        send_email_verification(db, user, base_url=base_url)
        return

    token = _emitir(db, user, purpose=PROPOSITO_SENHA,
                    ttl=dt.timedelta(minutes=config.PASSWORD_RESET_TTL_MINUTES))
    link = f"{base_url}/recuperar/{token}"
    email_channel.send(
        to=user.email,
        subject=f"Recuperar sua senha do {config.APP_NAME}",
        text=(
            "Recebemos um pedido para redefinir sua senha.\n\n"
            f"{link}\n\n"
            f"O link vale {config.PASSWORD_RESET_TTL_MINUTES} minutos e só "
            "funciona uma vez. Se não foi você, ignore — sua senha continua a "
            "mesma, e ninguém consegue entrar sem este link."
        ),
    )
    log(db, user_id=user.id, actor="system", action="PASSWORD_RESET_SENT",
        object_type="user", object_id=user.id)


def reset_password(db: Session, token: str, nova_senha: str) -> User | None:
    """Troca a senha e derruba TODAS as sessões.

    Derrubar tudo é obrigatório: se a conta foi tomada, a recuperação tem de
    expulsar quem estava dentro — inclusive a sessão de quem está trocando,
    que faz login de novo com a senha nova.
    """
    from agenda.core import sessions
    from agenda.security import hash_password

    user = _consumir(db, token, purpose=PROPOSITO_SENHA)
    if user is None:
        return None
    user.password_hash = hash_password(nova_senha)
    encerradas = sessions.revoke_all(db, user)
    db.flush()
    log(db, user_id=user.id, actor="user", action="PASSWORD_RESET",
        object_type="user", object_id=user.id, after={"sessoes_encerradas": encerradas})
    return user


# --------------------------------------------------------------------------- #
# Telefone
# --------------------------------------------------------------------------- #
def send_phone_code(db: Session, user: User) -> str | None:
    """Código de 6 dígitos pelo WhatsApp. Devolve o código só em desenvolvimento."""
    from agenda.channels import whatsapp

    if not user.phone_e164:
        return None
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    _emitir(db, user, purpose=PROPOSITO_TELEFONE, ttl=dt.timedelta(minutes=10),
            valor=codigo + user.id)  # o id entra no hash: código só vale para esta conta

    enviado, _id, erro = whatsapp.send_text(
        db, user,
        f"Seu código de confirmação no {config.APP_NAME} é {codigo}. "
        "Ele vale 10 minutos. Nunca compartilhe com ninguém.",
    )
    log(db, user_id=user.id, actor="system", action="PHONE_CODE_SENT",
        object_type="user", object_id=user.id,
        after={"enviado": enviado, "erro": erro})
    return None if config.IS_PRODUCTION else codigo


def confirm_phone(db: Session, user: User, codigo: str) -> bool:
    limpo = "".join(c for c in (codigo or "") if c.isdigit())[:6]
    if len(limpo) != 6:
        return False

    vivo = _token_vivo(db, user, purpose=PROPOSITO_TELEFONE)
    if vivo is None:
        return False
    # Errar queima tentativa. Sem isso, seis dígitos caem em um milhão de
    # requisições — e o limite por IP não segura ataque distribuído.
    vivo.attempts += 1
    if vivo.attempts > MAX_TENTATIVAS_CODIGO:
        vivo.used_at = dt.datetime.now(dt.timezone.utc)
        db.flush()
        log(db, user_id=user.id, actor="system", action="PHONE_CODE_BURNED",
            object_type="user", object_id=user.id)
        return False
    db.flush()

    confirmado = _consumir(db, limpo + user.id, purpose=PROPOSITO_TELEFONE)
    if confirmado is None or confirmado.id != user.id:
        return False
    user.phone_verified = True
    user.phone_verified_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    log(db, user_id=user.id, actor="user", action="PHONE_VERIFIED",
        object_type="user", object_id=user.id)
    return True
