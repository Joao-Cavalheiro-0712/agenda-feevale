"""Escopo obrigatório por usuário — a espinha dorsal do isolamento.

Multi-tenant de verdade não depende de lembrar do `where user_id = ...` em
cada consulta. Aqui existe um único lugar que sabe como amarrar qualquer
modelo ao dono, e o resto do código pede o objeto por aqui. Se um modelo novo
não declarar como é amarrado, a busca falha fechada (nega) em vez de vazar.
"""
from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from agenda import models

T = TypeVar("T")

# Como cada modelo se liga ao dono. "user_id" direto, ou (modelo_pai, campo).
_OWNER_FIELD: dict[type, str] = {
    models.User: "id",
    models.UserPhone: "user_id",
    models.UserSession: "user_id",
    models.LinkToken: "user_id",
    models.EducationContext: "user_id",
    models.AcademicPeriod: "user_id",
    models.Subject: "user_id",
    models.Teacher: "user_id",
    models.Location: "user_id",
    models.ClassSchedule: "user_id",
    models.ScheduleException: "user_id",
    models.Event: "user_id",
    models.EventReminder: "user_id",
    models.Document: "user_id",
    models.DocumentExtraction: "user_id",
    models.AssistantMessage: "user_id",
    models.AiAction: "user_id",
    models.Notification: "user_id",
    models.PushSubscription: "user_id",
    models.SharedCollection: "owner_id",
    models.Subscription: "user_id",
    models.UsageCounter: "user_id",
    models.StudyBlock: "user_id",
    models.GuardianLink: "student_id",
}

# Modelos filhos: chegam ao dono por um pai.
_PARENT: dict[type, tuple[type, str]] = {
    models.SubjectAlias: (models.Subject, "subject_id"),
    models.DocumentPage: (models.Document, "document_id"),
    models.NotificationDelivery: (models.Notification, "notification_id"),
}


class AccessDenied(Exception):
    """O objeto não existe ou não pertence a quem pediu. Nunca distinguimos os
    dois casos para fora: revelar a diferença é enumeração de recursos."""


def owner_of(instance: Any) -> str | None:
    modelo = type(instance)
    campo = _OWNER_FIELD.get(modelo)
    if campo:
        return getattr(instance, campo, None)
    return None


def get(db: Session, model: type[T], object_id: str | None, user_id: str) -> T | None:
    """Busca por id garantindo o dono. Devolve None quando não é do usuário."""
    if not object_id or not user_id:
        return None

    if model in _PARENT:
        parent_model, fk = _PARENT[model]
        child = db.get(model, object_id)
        if child is None:
            return None
        parent = get(db, parent_model, getattr(child, fk, None), user_id)
        return child if parent is not None else None

    campo = _OWNER_FIELD.get(model)
    if campo is None:
        # Falha fechada: modelo sem regra declarada não é acessível por id.
        return None

    instance = db.get(model, object_id)
    if instance is None:
        return None
    return instance if getattr(instance, campo, None) == user_id else None


def require(db: Session, model: type[T], object_id: str | None, user_id: str) -> T:
    """Igual a `get`, mas levanta AccessDenied — para rotas que precisam falhar."""
    instance = get(db, model, object_id, user_id)
    if instance is None:
        raise AccessDenied(f"{model.__name__} inacessível")
    return instance


def query(model: type[T], user_id: str) -> Select:
    """Select já filtrado pelo dono. Use SEMPRE em vez de `select(Modelo)`."""
    campo = _OWNER_FIELD.get(model)
    if campo is None:
        raise AccessDenied(f"{model.__name__} não declara dono em scope._OWNER_FIELD")
    return select(model).where(getattr(model, campo) == user_id)


def assert_owned(instance: Any, user_id: str) -> None:
    if owner_of(instance) != user_id:
        raise AccessDenied("objeto de outro usuário")
