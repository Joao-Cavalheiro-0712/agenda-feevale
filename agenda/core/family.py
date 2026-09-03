"""Conta família: responsáveis acompanhando estudantes (SPEC §59, §80).

Princípios:
  * o vínculo é sempre por convite explícito e revogável dos dois lados;
  * o responsável vê o que o estudante compartilha, com permissões separadas
    (ver agenda, adicionar compromisso, receber lembrete);
  * nada é visível antes do aceite;
  * a experiência do estudante continua sendo dele — o responsável entra como
    convidado, não como dono da conta.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from agenda.core import billing
from agenda.core.events import log
from agenda.models import GuardianLink, User
from agenda.security import hash_password, share_code

# Criar a conta de um filho menor é o único caminho legal para esse público
# (art. 14 da LGPD + capacidade civil). Por isso o primeiro estudante nunca é
# barrado por plano: o que é obrigação legal não pode virar upsell.
MINOR_ACCOUNT_FLOOR = 1


def invite(
    db: Session,
    student: User,
    *,
    email: str = "",
    relationship_label: str = "responsável",
) -> GuardianLink:
    """O estudante (ou o responsável dono do plano) gera um convite."""
    link = GuardianLink(
        student_id=student.id,
        invite_code=share_code(),
        invite_email=(email or "").strip().lower()[:200],
        relationship_label=relationship_label[:40],
        status="PENDING",
    )
    db.add(link)
    db.flush()
    log(db, user_id=student.id, actor="user", action="GUARDIAN_INVITE",
        object_type="guardian_link", object_id=link.id)
    return link


def accept(db: Session, guardian: User, code: str) -> GuardianLink | None:
    """O responsável aceita o convite. Só então enxerga qualquer coisa."""
    link = db.scalars(
        select(GuardianLink).where(GuardianLink.invite_code == code.strip().upper())
    ).first()
    if link is None or link.status != "PENDING":
        return None
    if link.student_id == guardian.id:
        return None  # ninguém é responsável por si mesmo

    student = db.get(User, link.student_id)
    if student is None:
        return None

    # A vaga vem do plano de quem tem direito a família.
    dono = student
    if not billing.allows(db, dono, billing.CAN_USE_FAMILY) and not billing.allows(
        db, guardian, billing.CAN_USE_FAMILY
    ):
        return None
    limite = max(
        billing.limit_of(db, dono, billing.MAX_STUDENTS),
        billing.limit_of(db, guardian, billing.MAX_STUDENTS),
    )
    if limite != billing.UNLIMITED and len(students_of(db, guardian)) >= limite:
        return None

    link.guardian_id = guardian.id
    link.status = "ACTIVE"
    link.accepted_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    log(db, user_id=student.id, actor="user", action="GUARDIAN_ACCEPT",
        object_type="guardian_link", object_id=link.id)
    return link


def revoke(db: Session, actor: User, link_id: str) -> bool:
    """Qualquer um dos dois lados pode encerrar o vínculo."""
    link = db.get(GuardianLink, link_id)
    if link is None or actor.id not in (link.student_id, link.guardian_id):
        return False
    link.status = "REVOKED"
    link.revoked_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    log(db, user_id=actor.id, actor="user", action="GUARDIAN_REVOKE",
        object_type="guardian_link", object_id=link.id)
    return True


def students_of(db: Session, guardian: User) -> list[GuardianLink]:
    return list(
        db.scalars(
            select(GuardianLink).where(
                GuardianLink.guardian_id == guardian.id, GuardianLink.status == "ACTIVE"
            )
        ).all()
    )


def guardians_of(db: Session, student: User) -> list[GuardianLink]:
    return list(
        db.scalars(
            select(GuardianLink).where(
                GuardianLink.student_id == student.id,
                GuardianLink.status.in_(["ACTIVE", "PENDING"]),
            )
        ).all()
    )


def link_between(db: Session, guardian_id: str, student_id: str) -> GuardianLink | None:
    return db.scalars(
        select(GuardianLink).where(
            GuardianLink.guardian_id == guardian_id,
            GuardianLink.student_id == student_id,
            GuardianLink.status == "ACTIVE",
        )
    ).first()


def can_view(db: Session, guardian: User, student_id: str) -> bool:
    link = link_between(db, guardian.id, student_id)
    return bool(link and link.can_view_agenda)


def can_add(db: Session, guardian: User, student_id: str) -> bool:
    link = link_between(db, guardian.id, student_id)
    return bool(link and link.can_add_events)


def reminder_recipients(db: Session, student: User) -> list[User]:
    """Responsáveis que optaram por receber os lembretes do estudante."""
    links = db.scalars(
        select(GuardianLink).where(
            GuardianLink.student_id == student.id,
            GuardianLink.status == "ACTIVE",
            GuardianLink.can_receive_reminders.is_(True),
        )
    ).all()
    usuarios = [db.get(User, link.guardian_id) for link in links]
    return [u for u in usuarios if u is not None and u.deleted_at is None]


def related_users(db: Session, user: User) -> list[GuardianLink]:
    """Todos os vínculos em que o usuário aparece, dos dois lados."""
    return list(
        db.scalars(
            select(GuardianLink).where(
                or_(GuardianLink.student_id == user.id, GuardianLink.guardian_id == user.id),
                GuardianLink.status != "REVOKED",
            )
        ).all()
    )


# --------------------------------------------------------------------------- #
# Conta criada pelo responsável (menores de idade)
# --------------------------------------------------------------------------- #
def can_create_student(db: Session, guardian: User) -> tuple[bool, str]:
    """Se este responsável ainda pode criar/adicionar um estudante."""
    atuais = len(students_of(db, guardian))
    limite = billing.limit_of(db, guardian, billing.MAX_STUDENTS)
    if limite == billing.UNLIMITED:
        return True, ""
    permitido = max(limite, MINOR_ACCOUNT_FLOOR)
    if atuais >= permitido:
        return False, (
            "Você já tem o número de estudantes do seu plano. "
            "O plano Família permite até 5."
        )
    return True, ""


def create_student_account(
    db: Session,
    guardian: User,
    *,
    name: str,
    email: str,
    password: str,
    birth_year: int | None,
    relationship_label: str = "responsável",
) -> User:
    """Cria a conta do estudante menor, já vinculada ao responsável.

    O estudante recebe login próprio — ele usa o app no celular dele, com a
    experiência dele. O responsável não entra "dentro" da conta: ele enxerga
    pelo vínculo, com as permissões do vínculo, exatamente como qualquer outro
    responsável. A diferença é a origem do consentimento: aqui quem autorizou
    está autenticado, o que é a melhor prova possível para o art. 14.
    """
    student = User(
        name=name.strip()[:160],
        email=email.strip().lower()[:200],
        password_hash=hash_password(password),
        timezone=guardian.timezone,
        birth_year=birth_year,
        is_minor=True,
        guardian_consent_at=dt.datetime.now(dt.timezone.utc),
        # Menor de idade começa sem automação silenciosa e sem IA opcional
        # ligada por padrão: o melhor interesse da criança pede o contrário
        # do padrão adulto (SPEC §80, LGPD art. 14 §1º).
        auto_create_enabled=False,
    )
    db.add(student)
    db.flush()

    link = GuardianLink(
        guardian_id=guardian.id,
        student_id=student.id,
        invite_code=share_code(),
        invite_email=(guardian.email or "")[:200],
        relationship_label=relationship_label[:40],
        status="ACTIVE",
        accepted_at=dt.datetime.now(dt.timezone.utc),
    )
    db.add(link)
    db.flush()
    log(db, user_id=guardian.id, actor="user", action="CREATE_STUDENT_ACCOUNT",
        object_type="user", object_id=student.id,
        after={"link_id": link.id, "relationship": link.relationship_label})
    return student
