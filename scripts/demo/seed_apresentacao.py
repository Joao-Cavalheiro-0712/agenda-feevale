"""Popula uma conta de demonstração que parece de gente de verdade.

Nada de "Matéria 1" e "Prova A": a apresentação vende o produto, e o produto
só convence com dado plausível. Aqui é uma aluna de Direito na Feevale, 5º
semestre, com a semana cheia que qualquer estudante reconhece.
"""
import datetime as dt
import os
import sys

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
os.environ.update(
    DATABASE_URL="postgresql://grifo@127.0.0.1:5433/grifo",
    SECRET_KEY="apresentacao-grifo",
    APP_ENV="development",
    DISABLE_BACKGROUND_JOBS="1",
    STORAGE_DIR="/tmp/grifo-demo/storage",
    APP_NAME="Grifo",
)

from agenda.core import academic, events as ev, family, periods, privacy, study
from agenda.db import SessionLocal
from agenda.models import (
    AcademicPeriod, AssistantMessage, ClassSchedule, EducationContext,
    Location, Notification, StudyBlock, Subject, Teacher, User,
)
from agenda.security import hash_password

db = SessionLocal()
hoje = dt.date.today()


def segunda(base):
    return base - dt.timedelta(days=base.weekday())


inicio_semana = segunda(hoje)

# --------------------------------------------------------------------------- #
# A protagonista
# --------------------------------------------------------------------------- #
ana = User(
    name="Ana Beatriz Rocha", email="ana@exemplo.com",
    password_hash=hash_password("demonstracao123"),
    timezone="America/Sao_Paulo", onboarding_done=True, birth_year=2003,
    is_minor=False, phone_e164="+5551998877665",
    email_verified_at=dt.datetime.now(dt.timezone.utc),
    tour_done_at=dt.datetime.now(dt.timezone.utc),
)
db.add(ana); db.flush()
privacy.accept_documents(db, ana, ip="187.10.4.22", user_agent="demo", origin="web")

ctx = EducationContext(
    user_id=ana.id, type="UNDERGRAD", degree_kind="BACHELOR",
    institution="Universidade Feevale", course_name="Direito",
    semester="5º semestre", shift="noite", period_kind="SEMESTER",
    period_label="2026/2", is_active=True,
    starts_on=dt.date(2026, 8, 3), ends_on=dt.date(2026, 12, 12),
)
db.add(ctx); db.flush()
academic.set_active_context(db, ana.id, ctx.id)
periods.ensure_periods(db, ctx)

# --------------------------------------------------------------------------- #
# Professores, locais e disciplinas
# --------------------------------------------------------------------------- #
profs = {}
for nome, apelido in [
    ("Marcelo Antunes", "Marcelo"), ("Cláudia Reis", "Cláudia"),
    ("Rogério Baptista", "Rogério"), ("Helena Vasques", "Helena"),
    ("Paulo Sérgio Lima", "Paulo"),
]:
    t = Teacher(user_id=ana.id, name=nome, nickname=apelido)
    db.add(t); db.flush(); profs[apelido] = t

salas = {}
for nome, predio, sala in [("Sala 304", "Bloco C", "304"), ("Sala 211", "Bloco C", "211"),
                           ("Auditório", "Bloco A", "Auditório 1"),
                           ("Laboratório Jurídico", "Bloco D", "Lab 2")]:
    l = Location(user_id=ana.id, name=nome, campus="Campus II", building=predio, room=sala)
    db.add(l); db.flush(); salas[nome] = l

disciplinas = [
    ("Direito Penal II", "Penal", "#c6402f", "Marcelo", "Sala 304"),
    ("Direito Processual Civil", "Proc. Civil", "#3f7a52", "Cláudia", "Sala 211"),
    ("Direito do Trabalho", "Trabalho", "#9a6a15", "Rogério", "Sala 304"),
    ("Direito Empresarial", "Empresarial", "#4a6fa5", "Helena", "Sala 211"),
    ("Prática Jurídica II", "Prática", "#7a4a8f", "Paulo", "Laboratório Jurídico"),
]
mats = {}
for nome, curto, cor, prof, sala in disciplinas:
    s = academic.upsert_subject(
        db, ana.id, ctx.id, nome, short_name=curto, color=cor,
        teacher_id=profs[prof].id, location_id=salas[sala].id,
    )
    mats[curto] = s
db.flush()

# --------------------------------------------------------------------------- #
# Grade de aulas — curso noturno, segunda a sexta
# --------------------------------------------------------------------------- #
grade = [
    (0, "Penal", "19:00", "20:40"), (0, "Proc. Civil", "20:50", "22:30"),
    (1, "Trabalho", "19:00", "20:40"), (1, "Empresarial", "20:50", "22:30"),
    (2, "Penal", "19:00", "20:40"), (2, "Prática", "20:50", "22:30"),
    (3, "Proc. Civil", "19:00", "20:40"), (3, "Trabalho", "20:50", "22:30"),
    (4, "Empresarial", "19:00", "20:40"),
]
for dia, curto, ini, fim in grade:
    db.add(ClassSchedule(
        user_id=ana.id, subject_id=mats[curto].id, weekday=dia,
        start_time=ini, end_time=fim,
        location_id=mats[curto].default_location_id,
        start_date=ctx.starts_on, end_date=ctx.ends_on, active=True,
    ))
db.flush()

# --------------------------------------------------------------------------- #
# A agenda — o que vende o produto é a semana cheia e crível
# --------------------------------------------------------------------------- #
def dia(offset):
    return hoje + dt.timedelta(days=offset)

compromissos = [
    # (offset, título, tipo, matéria, hora, descrição, peso, nota_max)
    (0, "Prova 2 de Direito Penal II", "EXAM", "Penal", "19:00",
     "Crimes contra o patrimônio — arts. 155 a 183. Cai o que ele grifou na aula passada.", 4.0, 10.0),
    (0, "Ler acórdão do STJ para a Cláudia", "READING", "Proc. Civil", None,
     "REsp 1.879.503 — tutela de urgência.", None, None),
    (1, "Entregar petição inicial", "ASSIGNMENT", "Prática", "22:30",
     "Ação de cobrança. Modelo no portal, formatação ABNT.", 2.0, 10.0),
    (2, "Seminário de Direito do Trabalho", "SEMINAR", "Trabalho", "20:50",
     "Grupo da Duda, Nicolas e Rafa. Tema: terceirização depois da reforma.", 3.0, 10.0),
    (3, "Trabalho de Empresarial", "ASSIGNMENT", "Empresarial", None,
     "Estudo de caso — recuperação judicial da Oi. Mínimo 8 páginas.", 3.0, 10.0),
    (5, "Simulado da OAB", "SIMULATION", None, "09:00",
     "1ª fase. Feito pelo cursinho, no Campus II.", None, None),
    (7, "Prova de Processual Civil", "EXAM", "Proc. Civil", "19:00",
     "Tutelas provisórias e cumprimento de sentença.", 4.0, 10.0),
    (8, "Audiência simulada", "PRESENTATION", "Prática", "20:50",
     "Levar a peça impressa e a procuração.", 2.0, 10.0),
    (10, "Fichamento — Direito Penal II", "HOMEWORK", "Penal", None,
     "Capítulo 7 do Nucci.", 1.0, 10.0),
    (12, "Entrega do artigo de Empresarial", "PAPER", "Empresarial", None,
     "Coautoria com a Duda. Submissão no portal até 23h59.", 4.0, 10.0),
    (-2, "Prova 1 de Direito do Trabalho", "EXAM", "Trabalho", "19:00", "", 4.0, 10.0),
    (-5, "Resenha de Processual Civil", "ASSIGNMENT", "Proc. Civil", None, "", 2.0, 10.0),
]

criados = {}
for off, titulo, tipo, curto, hora, desc, peso, maxn in compromissos:
    e = ev.create_event(
        db, ana, title=titulo, event_type=tipo, date=dia(off),
        subject=mats[curto] if curto else None, context_id=ctx.id,
        description=desc, start_time=hora, weight=peso, max_grade=maxn,
        group_work=(tipo == "SEMINAR"),
        source_type="WHATSAPP_TEXT" if off in (0, 2, 7) else "MANUAL",
        confidence=0.94 if off in (0, 2, 7) else 1.0,
    )
    criados[titulo] = e
db.flush()

# Notas já lançadas no que passou — a tela de notas precisa mostrar algo.
criados["Prova 1 de Direito do Trabalho"].grade_value = 8.5
criados["Prova 1 de Direito do Trabalho"].status = "DONE"
criados["Prova 1 de Direito do Trabalho"].completed_at = dt.datetime.now(dt.timezone.utc)
criados["Resenha de Processual Civil"].grade_value = 9.0
criados["Resenha de Processual Civil"].status = "DONE"
criados["Resenha de Processual Civil"].completed_at = dt.datetime.now(dt.timezone.utc)

# Checklist no trabalho em grupo — mostra que o app desdobra a tarefa.
criados["Seminário de Direito do Trabalho"].checklist = [
    {"text": "Levantar a jurisprudência pós-reforma", "done": True},
    {"text": "Montar os slides", "done": True},
    {"text": "Ensaiar com o grupo na quarta", "done": False},
    {"text": "Imprimir o roteiro", "done": False},
]
db.flush()

# --------------------------------------------------------------------------- #
# Plano de estudo — blocos antes das provas
# --------------------------------------------------------------------------- #
prova_penal = criados["Prova 2 de Direito Penal II"]
prova_proc = criados["Prova de Processual Civil"]
for off, mins, topico, alvo, status in [
    (-1, 50, "Crimes contra o patrimônio — furto e roubo", prova_penal, "DONE"),
    (-1, 50, "Estelionato e apropriação indébita", prova_penal, "DONE"),
    (4, 50, "Tutela de urgência", prova_proc, "PENDING"),
    (5, 50, "Tutela de evidência", prova_proc, "PENDING"),
    (6, 50, "Cumprimento de sentença", prova_proc, "PENDING"),
]:
    db.add(StudyBlock(
        user_id=ana.id, event_id=alvo.id, subject_id=alvo.subject_id,
        local_date=dia(off), start_time="17:30", minutes=mins,
        topic=topico, status=status,
    ))

# --------------------------------------------------------------------------- #
# Conversa com o assistente — a captura como ela realmente acontece
# --------------------------------------------------------------------------- #
agora = dt.datetime.now(dt.timezone.utc)
conversa = [
    ("user", "prova de penal quinta que vem, crimes contra o patrimônio", "whatsapp", 52),
    ("assistant", "Anotei: Prova 2 de Direito Penal II, quinta às 19h, "
     "com Direito Penal II. Já separei dois blocos de estudo antes.", "whatsapp", 52),
    ("user", "manda foto do quadro depois", "whatsapp", 51),
    ("user", "entregar peticao ate terca 22:30 pratica juridica", "web", 40),
    ("assistant", "Pronto. Entregar petição inicial ficou para terça, "
     "22h30, em Prática Jurídica II.", "web", 40),
    ("user", "o q eu tenho essa semana?", "web", 12),
    ("assistant", "Quatro coisas: prova de Penal hoje às 19h, petição terça, "
     "seminário de Trabalho quarta e o trabalho de Empresarial quinta.", "web", 12),
]
for papel, texto, canal, minutos in conversa:
    db.add(AssistantMessage(
        user_id=ana.id, role=papel, text=texto, channel=canal,
        created_at=agora - dt.timedelta(minutes=minutos),
    ))

# --------------------------------------------------------------------------- #
# Notificações
# --------------------------------------------------------------------------- #
for titulo, corpo, minutos in [
    ("Prova de Penal é hoje", "Direito Penal II, 19h, Sala 304. Você já estudou 100 minutos.", 180),
    ("Faltam 2 dias: petição inicial", "Prática Jurídica II, terça às 22h30.", 1500),
    ("Seminário de Trabalho quarta", "Ainda faltam duas coisas na sua lista.", 2900),
]:
    db.add(Notification(user_id=ana.id, title=titulo, body=corpo, kind="reminder",
                        created_at=agora - dt.timedelta(minutes=minutos)))

db.commit()

# --------------------------------------------------------------------------- #
# A família: mãe responsável + filho no fundamental
# --------------------------------------------------------------------------- #
mae = User(
    name="Regina Rocha", email="regina@exemplo.com",
    password_hash=hash_password("demonstracao123"), timezone="America/Sao_Paulo",
    onboarding_done=True, birth_year=1979,
    email_verified_at=dt.datetime.now(dt.timezone.utc),
    tour_done_at=dt.datetime.now(dt.timezone.utc),
)
db.add(mae); db.flush()
privacy.accept_documents(db, mae, ip="187.10.4.22", user_agent="demo", origin="web")
ctx_mae = EducationContext(user_id=mae.id, type="UNDERGRAD", institution="UFRGS",
                           course_name="Pedagogia", period_kind="SEMESTER", is_active=True)
db.add(ctx_mae); db.flush()
academic.set_active_context(db, mae.id, ctx_mae.id)

# Pelo caminho real do produto: é a mãe autenticada quem cria e consente.
filho = family.create_student_account(
    db, mae, name="Téo Rocha", email="teo@exemplo.com",
    password="demonstracao123", birth_year=hoje.year - 9,
    relationship_label="mãe",
)
# Dois consentimentos distintos, como a rota web faz: o aceite dos documentos
# em nome do menor e o consentimento específico do art. 14.
privacy.accept_documents(db, filho, ip="187.10.4.22", user_agent="demo",
                         origin="guardian", ai_processing=True)
privacy.register_guardian_consent(
    db, filho, guardian_name=mae.name, guardian_email=mae.email,
    relationship="mãe", ip="187.10.4.22", user_agent="demo",
)
filho.onboarding_done = True
filho.tour_done_at = dt.datetime.now(dt.timezone.utc)
db.flush()

ctx_filho = EducationContext(
    user_id=filho.id, type="ELEMENTARY", institution="Escola Estadual Érico Veríssimo",
    grade_name="4º ano", class_name="B", shift="manha", period_kind="BIMESTER",
    period_label="3º bimestre", is_active=True,
)
db.add(ctx_filho); db.flush()
academic.set_active_context(db, filho.id, ctx_filho.id)
periods.ensure_periods(db, ctx_filho)

for nome, cor in [("Português", "#c6402f"), ("Matemática", "#3f7a52"),
                  ("Ciências", "#4a6fa5"), ("História", "#9a6a15")]:
    academic.upsert_subject(db, filho.id, ctx_filho.id, nome, color=cor)
db.flush()

mats_filho = {s.name: s for s in academic.list_subjects(db, filho.id)}
for off, titulo, tipo, materia, desc in [
    (0, "Tema de casa de Matemática", "HOMEWORK", "Matemática", "Página 42, exercícios 1 a 8."),
    (1, "Levar a cartolina", "MATERIAL", "Ciências", "Trabalho sobre o sistema solar."),
    (2, "Prova de Português", "EXAM", "Português", "Substantivo e adjetivo."),
    (4, "Trabalho de História", "ASSIGNMENT", "História", "Desenhar a linha do tempo da família."),
    (6, "Reunião de pais", "SCHOOL_EVENT", None, "19h, na sala da professora Ivone."),
]:
    ev.create_event(db, filho, title=titulo, event_type=tipo, date=dia(off),
                    subject=mats_filho.get(materia), context_id=ctx_filho.id,
                    description=desc)

db.commit()
print(f"OK — Ana ({len(compromissos)} compromissos, {len(disciplinas)} disciplinas, "
      f"{len(grade)} aulas), Regina (responsável) e Téo (4º ano).")
db.close()
