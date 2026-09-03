"""Exportação ICS, planejador de estudos e tempo real."""
from __future__ import annotations

import datetime as dt


from agenda.core import academic, calendar_export, events as events_core, study
from agenda.models import StudyBlock, User
from agenda.security import hash_password


def _csrf(client) -> str:
    with client.session_transaction() as session:
        return session.get("csrf", "")


# --------------------------------------------------------------------------- #
# Calendário (.ics)
# --------------------------------------------------------------------------- #
def test_ics_tem_estrutura_valida(db, user):
    events_core.create_event(
        db, user, title="Prova de Penal", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=7),
    )
    db.commit()
    ics = calendar_export.build_calendar(db, user)

    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert ics.count("BEGIN:VEVENT") == ics.count("END:VEVENT") == 1
    assert "SUMMARY:Prova de Penal" in ics
    assert "\r\n" in ics  # CRLF exigido pelo RFC 5545


def test_ics_escapa_caracteres_especiais(db, user):
    events_core.create_event(
        db, user, title="Prova; com, vírgula", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=3),
        description="linha 1\nlinha 2",
    )
    db.commit()
    ics = calendar_export.build_calendar(db, user)
    assert r"Prova\; com\, vírgula" in ics
    assert "linha 1\\nlinha 2" in ics


def test_ics_so_traz_eventos_do_dono(db, user):
    outro = User(name="Maria", email="maria2@example.com", password_hash=hash_password("outrasenha123"))
    db.add(outro)
    db.flush()
    events_core.create_event(
        db, outro, title="Segredo da Maria", event_type="EXAM",
        date=dt.date.today() + dt.timedelta(days=2),
    )
    db.commit()
    assert "Segredo da Maria" not in calendar_export.build_calendar(db, user)


def test_feed_ics_exige_token_valido(client, db, user):
    assert client.get("/calendario/token-inventado.ics").status_code == 404

    client.post("/calendario/assinar", data={"csrf_token": _csrf(client)})
    db.expire_all()
    from agenda.models import LinkToken

    token = db.query(LinkToken).filter_by(user_id=user.id, purpose="calendar").first()
    resposta = client.get(f"/calendario/{token.token}.ics")
    assert resposta.status_code == 200
    assert resposta.headers["Content-Type"].startswith("text/calendar")
    assert resposta.headers["X-Robots-Tag"].startswith("noindex, nofollow")


def test_revogar_link_do_calendario(client, db, user):
    from agenda.models import LinkToken

    client.post("/calendario/assinar", data={"csrf_token": _csrf(client)})
    db.expire_all()
    token = db.query(LinkToken).filter_by(user_id=user.id, purpose="calendar").first().token

    client.post("/calendario/revogar", data={"csrf_token": _csrf(client)})
    db.expire_all()
    assert client.get(f"/calendario/{token}.ics").status_code == 404


# --------------------------------------------------------------------------- #
# Planejador de estudos
# --------------------------------------------------------------------------- #
def test_propostas_nascem_das_provas(db, user):
    hoje = dt.date.today()
    contexto = academic.active_context(db, user.id)
    materia = academic.upsert_subject(db, user.id, contexto.id, "Direito Penal")
    events_core.create_event(
        db, user, title="Prova 1", event_type="EXAM",
        date=hoje + dt.timedelta(days=10), subject=materia,
    )
    db.commit()

    propostas = study.propose(db, user, today=hoje)
    assert propostas
    assert all(p["local_date"] < hoje + dt.timedelta(days=10) for p in propostas)
    assert all(p["local_date"] >= hoje for p in propostas)
    # Respeita o teto diário de minutos.
    por_dia: dict[dt.date, int] = {}
    for p in propostas:
        por_dia[p["local_date"]] = por_dia.get(p["local_date"], 0) + p["minutes"]
    assert max(por_dia.values()) <= 90


def test_sem_prova_nao_ha_proposta(db, user):
    assert study.propose(db, user, today=dt.date.today()) == []


def test_salvar_nao_duplica(db, user):
    hoje = dt.date.today()
    events_core.create_event(
        db, user, title="Prova 1", event_type="EXAM", date=hoje + dt.timedelta(days=8)
    )
    db.commit()
    propostas = study.propose(db, user, today=hoje)
    criados = study.save(db, user, propostas)
    db.commit()
    assert criados > 0
    assert study.save(db, user, propostas) == 0
    db.commit()
    assert db.query(StudyBlock).filter_by(user_id=user.id).count() == criados


def test_bloco_de_estudo_de_outro_usuario_nao_e_concluido(db, user):
    outro = User(name="X", email="x2@example.com", password_hash=hash_password("outrasenha123"))
    db.add(outro)
    db.flush()
    bloco = StudyBlock(user_id=outro.id, local_date=dt.date.today(), minutes=45)
    db.add(bloco)
    db.commit()
    assert study.complete(db, user, bloco.id) is False


# --------------------------------------------------------------------------- #
# Tempo real
# --------------------------------------------------------------------------- #
def test_sse_entrega_apenas_ao_dono(db, user):
    from agenda.web import realtime

    canal = realtime.subscribe(user.id)
    assert realtime.publish(user.id, "agenda.changed", {"message": "oi"}) == 1
    assert realtime.publish("outro-usuario", "agenda.changed", {"message": "oi"}) == 0
    assert canal.get_nowait()["data"]["message"] == "oi"
    realtime.unsubscribe(user.id, canal)


def test_sse_limita_conexoes_por_usuario(db, user):
    from agenda.web import realtime

    canais = [realtime.subscribe(user.id) for _ in range(10)]
    assert realtime.publish(user.id, "x") <= 4
    for canal in canais:
        realtime.unsubscribe(user.id, canal)


# --------------------------------------------------------------------------- #
# Notas
# --------------------------------------------------------------------------- #
def test_media_ponderada_e_nota_necessaria(db, user):
    from agenda.core import grades

    contexto = academic.active_context(db, user.id)
    materia = academic.upsert_subject(db, user.id, contexto.id, "Cálculo")
    materia.passing_grade = 7.0
    hoje = dt.date.today()

    prova1 = events_core.create_event(
        db, user, title="P1", event_type="EXAM", date=hoje - dt.timedelta(days=10),
        subject=materia, max_grade=10.0, weight=1,
    )
    events_core.create_event(
        db, user, title="P2", event_type="EXAM", date=hoje + dt.timedelta(days=10),
        subject=materia, max_grade=10.0, weight=1,
    )
    grades.set_grade(db, user, prova1, grade_value=6.0)
    db.commit()

    resumo = grades.subject_summary(db, user, materia)
    assert resumo["media"] == 6.0
    assert resumo["lancadas"] == 1 and resumo["previstas"] == 2
    assert resumo["necessario"] == 8.0   # precisa de 8 na P2 para fechar em 7


def test_nota_nao_ultrapassa_o_maximo(db, user):
    from agenda.core import grades

    evento = events_core.create_event(
        db, user, title="P1", event_type="EXAM", date=dt.date.today(), max_grade=10.0
    )
    grades.set_grade(db, user, evento, grade_value=99.0)
    assert evento.grade_value == 10.0
