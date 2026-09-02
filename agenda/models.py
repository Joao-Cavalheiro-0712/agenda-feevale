"""Modelo de dados (SPEC §46, §64, §65).

Um único núcleo atende do ensino fundamental à pós-graduação: o que muda é
``EducationContext.type`` e quais campos opcionais a UI mostra (SPEC §65).
"""
from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agenda.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Enums de domínio
# --------------------------------------------------------------------------- #
class EducationType(str, enum.Enum):
    ELEMENTARY = "ELEMENTARY"
    MIDDLE_SCHOOL = "MIDDLE_SCHOOL"
    HIGH_SCHOOL = "HIGH_SCHOOL"
    TECHNICAL = "TECHNICAL"
    UNDERGRAD = "UNDERGRAD"
    POSTGRAD = "POSTGRAD"
    FREE_COURSE = "FREE_COURSE"
    OTHER = "OTHER"


class EventType(str, enum.Enum):
    CLASS = "CLASS"
    EXAM = "EXAM"
    QUIZ = "QUIZ"
    ASSIGNMENT = "ASSIGNMENT"
    HOMEWORK = "HOMEWORK"
    PROJECT = "PROJECT"
    PRESENTATION = "PRESENTATION"
    READING = "READING"
    MATERIAL = "MATERIAL"
    LAB = "LAB"
    SIMULATION = "SIMULATION"
    SEMINAR = "SEMINAR"
    PAPER = "PAPER"
    INTERNSHIP = "INTERNSHIP"
    SCHOOL_EVENT = "SCHOOL_EVENT"
    ADMINISTRATIVE = "ADMINISTRATIVE"
    REMINDER = "REMINDER"
    OTHER = "OTHER"


class EventStatus(str, enum.Enum):
    UPCOMING = "UPCOMING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


class SubjectStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"
    DROPPED = "DROPPED"


class DocumentStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    EXTRACTING = "EXTRACTING"
    INTERPRETING = "INTERPRETING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    READY = "READY"
    IMPORTED = "IMPORTED"
    FAILED = "FAILED"


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SourceType(str, enum.Enum):
    MANUAL = "MANUAL"
    WEB_CAPTURE = "WEB_CAPTURE"
    VOICE = "VOICE"
    DOCUMENT = "DOCUMENT"
    WHATSAPP = "WHATSAPP"
    TELEGRAM = "TELEGRAM"
    SHARED = "SHARED"
    RECURRENCE = "RECURRENCE"


# --------------------------------------------------------------------------- #
# Usuários e identidades
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    phone_e164: Mapped[str | None] = mapped_column(String(24), index=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    locale: Mapped[str] = mapped_column(String(10), default="pt-BR")
    theme: Mapped[str] = mapped_column(String(10), default="system")  # system|light|dark
    onboarding_done: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_create_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_days: Mapped[str] = mapped_column(String(60), default="7,1")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    contexts: Mapped[list["EducationContext"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def reminder_offsets(self) -> list[int]:
        return sorted(
            {int(x) for x in self.reminder_days.split(",") if x.strip().lstrip("-").isdigit()},
            reverse=True,
        )


class UserPhone(Base):
    """Telefones vinculados (SPEC §17). O vínculo é revogável."""

    __tablename__ = "user_phones"
    __table_args__ = (UniqueConstraint("phone_e164", "channel", name="uq_phone_channel"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    phone_e164: Mapped[str] = mapped_column(String(24), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")
    external_id: Mapped[str] = mapped_column(String(64), default="")  # wa_id / chat_id
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    linked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LinkToken(Base):
    """Token curto e de uso único para vincular WhatsApp (SPEC §17, §131)."""

    __tablename__ = "link_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    purpose: Mapped[str] = mapped_column(String(40), default="whatsapp_link")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
# Contexto acadêmico
# --------------------------------------------------------------------------- #
class EducationContext(Base):
    __tablename__ = "education_contexts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20), default=EducationType.UNDERGRAD.value)
    institution: Mapped[str] = mapped_column(String(200), default="")
    course_name: Mapped[str] = mapped_column(String(200), default="")
    grade_name: Mapped[str] = mapped_column(String(80), default="")
    semester: Mapped[str] = mapped_column(String(40), default="")
    module: Mapped[str] = mapped_column(String(40), default="")
    class_name: Mapped[str] = mapped_column(String(80), default="")
    shift: Mapped[str] = mapped_column(String(20), default="")  # manha|tarde|noite|integral
    period_label: Mapped[str] = mapped_column(String(40), default="")  # ex.: 2026/2
    starts_on: Mapped[dt.date | None] = mapped_column(Date)
    ends_on: Mapped[dt.date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="contexts")
    subjects: Mapped[list["Subject"]] = relationship(
        back_populates="context", cascade="all, delete-orphan"
    )

    @property
    def title(self) -> str:
        return self.course_name or self.grade_name or self.institution or "Meus estudos"

    @property
    def subtitle(self) -> str:
        bits = [b for b in (self.institution, self.semester or self.module or self.class_name) if b]
        return " · ".join(bits)


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    nickname: Mapped[str] = mapped_column(String(80), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    campus: Mapped[str] = mapped_column(String(120), default="")
    building: Mapped[str] = mapped_column(String(120), default="")
    room: Mapped[str] = mapped_column(String(60), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def label(self) -> str:
        bits = [b for b in (self.building or self.name, self.room) if b]
        return " · ".join(bits) or self.name


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    education_context_id: Mapped[str] = mapped_column(
        ForeignKey("education_contexts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str] = mapped_column(String(60), default="")
    color: Mapped[str] = mapped_column(String(20), default="violet")
    teacher_id: Mapped[str | None] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    default_location_id: Mapped[str | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL")
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=SubjectStatus.ACTIVE.value)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    context: Mapped[EducationContext] = relationship(back_populates="subjects")
    aliases: Mapped[list["SubjectAlias"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )
    teacher: Mapped[Teacher | None] = relationship()
    default_location: Mapped[Location | None] = relationship()

    @property
    def display(self) -> str:
        return self.short_name or self.name


class SubjectAlias(Base):
    """Apelidos usados pela IA para resolver a disciplina (SPEC §42)."""

    __tablename__ = "subject_aliases"
    __table_args__ = (UniqueConstraint("subject_id", "alias_norm", name="uq_subject_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(120))
    alias_norm: Mapped[str] = mapped_column(String(120), index=True)

    subject: Mapped[Subject] = relationship(back_populates="aliases")


class ClassSchedule(Base):
    """Aula recorrente (SPEC §45)."""

    __tablename__ = "class_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0=segunda ... 6=domingo
    start_time: Mapped[str] = mapped_column(String(5))  # "19:30"
    end_time: Mapped[str] = mapped_column(String(5))
    start_date: Mapped[dt.date | None] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    recurrence_rule: Mapped[str] = mapped_column(String(120), default="FREQ=WEEKLY")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subject: Mapped[Subject] = relationship()
    location: Mapped[Location | None] = relationship()


class ScheduleException(Base):
    """Feriado, recesso, aula cancelada ou remarcada (SPEC §45, §135)."""

    __tablename__ = "schedule_exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    education_context_id: Mapped[str | None] = mapped_column(
        ForeignKey("education_contexts.id", ondelete="CASCADE")
    )
    schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("class_schedules.id", ondelete="CASCADE")
    )
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    end_date: Mapped[dt.date | None] = mapped_column(Date)  # intervalos (recesso)
    kind: Mapped[str] = mapped_column(String(30), default="HOLIDAY")  # HOLIDAY|BREAK|CANCELLED|MOVED
    label: Mapped[str] = mapped_column(String(160), default="")
    moved_to: Mapped[dt.date | None] = mapped_column(Date)


# --------------------------------------------------------------------------- #
# Eventos
# --------------------------------------------------------------------------- #
class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_user_date", "user_id", "local_date"),
        Index("ix_events_fingerprint", "user_id", "fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    education_context_id: Mapped[str | None] = mapped_column(
        ForeignKey("education_contexts.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str | None] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(24), default=EventType.OTHER.value)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")

    # Data local (fonte de verdade para date-only, SPEC §75) + instantes em UTC.
    local_date: Mapped[dt.date] = mapped_column(Date, index=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default=EventStatus.UPCOMING.value)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    checklist: Mapped[list | None] = mapped_column(JSON)  # SPEC §139/§140

    # Notas e pesos preparados para o futuro (SPEC §137).
    grade_value: Mapped[float | None] = mapped_column(Float)
    max_grade: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float)
    group_work: Mapped[bool] = mapped_column(Boolean, default=False)
    group_name: Mapped[str] = mapped_column(String(120), default="")

    # Proveniência (SPEC §12).
    source_type: Mapped[str] = mapped_column(String(20), default=SourceType.MANUAL.value)
    source_id: Mapped[str | None] = mapped_column(String(36))
    source_reference: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")

    created_by: Mapped[str] = mapped_column(String(20), default="user")  # user|ai|import|share
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    subject: Mapped[Subject | None] = relationship()
    location: Mapped[Location | None] = relationship()
    reminders: Mapped[list["EventReminder"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    DEADLINE_TYPES = {
        EventType.ASSIGNMENT.value, EventType.HOMEWORK.value, EventType.PROJECT.value,
        EventType.PAPER.value, EventType.READING.value, EventType.MATERIAL.value,
        EventType.PRESENTATION.value, EventType.SEMINAR.value, EventType.EXAM.value,
        EventType.QUIZ.value, EventType.SIMULATION.value, EventType.LAB.value,
        EventType.INTERNSHIP.value, EventType.ADMINISTRATIVE.value, EventType.REMINDER.value,
    }

    @property
    def is_deadline(self) -> bool:
        return self.type in self.DEADLINE_TYPES

    @property
    def time_label(self) -> str:
        if self.all_day or not self.starts_at:
            return ""
        return ""  # preenchido pela camada de apresentação (depende do fuso do usuário)


class EventReminder(Base):
    __tablename__ = "event_reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    offset_days: Mapped[int] = mapped_column(Integer, default=1)
    scheduled_for: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="inapp")
    status: Mapped[str] = mapped_column(String(20), default=DeliveryStatus.PENDING.value)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[Event] = relationship(back_populates="reminders")


# --------------------------------------------------------------------------- #
# Documentos
# --------------------------------------------------------------------------- #
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    education_context_id: Mapped[str | None] = mapped_column(
        ForeignKey("education_contexts.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    storage_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.UPLOADED.value)
    progress: Mapped[list | None] = mapped_column(JSON)  # etapas para a UX (SPEC §90)
    error: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    source_channel: Mapped[str] = mapped_column(String(20), default=SourceType.WEB_CAPTURE.value)
    imported_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    extractions: Mapped[list["DocumentExtraction"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text, default="")
    needs_vision: Mapped[bool] = mapped_column(Boolean, default=False)


class DocumentExtraction(Base):
    """Item candidato extraído de um documento, antes da confirmação (SPEC §9)."""

    __tablename__ = "document_extractions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="event")  # event|subject|schedule|context
    payload: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str] = mapped_column(String(255), default="")
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    created_event_id: Mapped[str | None] = mapped_column(String(36))
    source_reference: Mapped[dict | None] = mapped_column(JSON)

    document: Mapped[Document] = relationship(back_populates="extractions")


# --------------------------------------------------------------------------- #
# Assistente, ações de IA e auditoria
# --------------------------------------------------------------------------- #
class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12), default="user")  # user|assistant
    channel: Mapped[str] = mapped_column(String(20), default="web")
    text: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AiAction(Base):
    """Proposta de ação da IA e seu resultado — base do undo (SPEC §26, §27)."""

    __tablename__ = "ai_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")
    # PROPOSED | NEEDS_CONFIRMATION | EXECUTED | REJECTED | UNDONE | FAILED
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str | None] = mapped_column(String(36))
    before_state: Mapped[dict | None] = mapped_column(JSON)
    after_state: Mapped[dict | None] = mapped_column(JSON)
    channel: Mapped[str] = mapped_column(String(20), default="web")
    model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(40), default="")
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    undoable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    executed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(20), default="user")  # user|ai|system
    action: Mapped[str] = mapped_column(String(60))
    object_type: Mapped[str] = mapped_column(String(40), default="")
    object_id: Mapped[str | None] = mapped_column(String(36))
    before_state: Mapped[dict | None] = mapped_column(JSON)
    after_state: Mapped[dict | None] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(20), default="web")
    confidence: Mapped[float | None] = mapped_column(Float)
    ai_model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AiUsage(Base):
    """Custo por operação de IA (SPEC §112)."""

    __tablename__ = "ai_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(30), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    input_units: Mapped[int] = mapped_column(Integer, default=0)
    output_units: Mapped[int] = mapped_column(Integer, default=0)
    audio_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    image_pages: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# --------------------------------------------------------------------------- #
# Mensagens de canais externos e notificações
# --------------------------------------------------------------------------- #
class ChannelMessage(Base):
    """Mensagem recebida por WhatsApp/Telegram — idempotência por id do provedor."""

    __tablename__ = "channel_messages"
    __table_args__ = (
        UniqueConstraint("channel", "provider_message_id", name="uq_channel_message"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")
    provider_message_id: Mapped[str] = mapped_column(String(120), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    from_phone: Mapped[str] = mapped_column(String(24), default="")
    kind: Mapped[str] = mapped_column(String(20), default="text")  # text|audio|image|document
    text: Mapped[str] = mapped_column(Text, default="")
    media_id: Mapped[str] = mapped_column(String(160), default="")
    transcript: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="RECEIVED")
    error: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    event_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(30), default="reminder")
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="inapp")
    status: Mapped[str] = mapped_column(String(20), default=DeliveryStatus.PENDING.value)
    provider_message_id: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --------------------------------------------------------------------------- #
# Compartilhamento de turma (SPEC §57, §58)
# --------------------------------------------------------------------------- #
class SharedCollection(Base):
    __tablename__ = "shared_collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    subject_id: Mapped[str | None] = mapped_column(String(36))
    snapshot: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    keys: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
