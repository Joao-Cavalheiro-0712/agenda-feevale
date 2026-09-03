"""Configuração dos testes: banco temporário e usuário de exemplo."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

# Precisa vir antes de importar a aplicação: o engine é criado no import.
_TMP = tempfile.mkdtemp(prefix="planner-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("DISABLE_BACKGROUND_JOBS", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("STORAGE_DIR", os.path.join(_TMP, "storage"))
os.environ.setdefault("APP_ENV", "development")

from agenda import create_app  # noqa: E402
from agenda.db import Base, SessionLocal, engine  # noqa: E402
from agenda.core import privacy  # noqa: E402
from agenda.models import EducationContext, User  # noqa: E402
from agenda.security import _hits, hash_password  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest.fixture(autouse=True)
def clean_db(app):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _hits.clear()  # zera o rate limiter entre testes
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def user(db):
    person = User(
        name="João",
        email="joao@example.com",
        password_hash=hash_password("segredo123"),
        timezone="America/Sao_Paulo",
        onboarding_done=True,
        birth_year=2000,
    )
    db.add(person)
    db.flush()
    # Conta adulta com aceite vigente: é o estado normal de quem passou pelo
    # cadastro. Sem isto o app trava na tela de aceite, e é justamente isso
    # que `test_privacidade.py` verifica.
    privacy.accept_documents(db, person, ip="127.0.0.1", user_agent="pytest")
    context = EducationContext(
        user_id=person.id,
        type="UNDERGRAD",
        institution="Feevale",
        course_name="Direito",
        semester="1º semestre",
        shift="noite",
        starts_on=dt.date(2026, 8, 1),
        ends_on=dt.date(2026, 12, 20),
        is_active=True,
    )
    db.add(context)
    db.commit()
    return person


@pytest.fixture
def client(app, user):
    test_client = app.test_client()
    test_client.get("/entrar")  # inicializa a sessão e o token CSRF
    response = test_client.post(
        "/entrar",
        data={
            "csrf_token": csrf(test_client),
            "email": "joao@example.com",
            "password": "segredo123",
        },
    )
    assert response.status_code == 302, "login de teste falhou"
    return test_client


def csrf(test_client) -> str:
    with test_client.session_transaction() as session:
        return session.get("csrf", "")
