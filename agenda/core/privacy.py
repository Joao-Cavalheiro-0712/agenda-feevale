"""Consentimento, bases legais e direitos do titular (LGPD).

Este módulo concentra o que a lei exige do controlador em termos de prova e de
resposta ao titular. Ele não substitui parecer jurídico — descreve, em código,
o que o produto de fato faz, para que o texto jurídico possa ser escrito em
cima da realidade e não do contrário.

Referências principais:
  * art. 8º §1º — o ônus de provar o consentimento é do controlador;
  * art. 9º — o titular tem direito a saber a finalidade e com quem os dados
    são compartilhados;
  * art. 14 — dados de crianças exigem consentimento específico e em destaque
    de um dos pais ou responsável legal;
  * art. 18 — direitos de acesso, correção, portabilidade e eliminação;
  * art. 33/34 — transferência internacional;
  * art. 37 — registro das operações de tratamento.
"""
from __future__ import annotations

import datetime as dt
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenda import config
from agenda.models import ConsentKind, ConsentRecord, User
from agenda.security import hash_ip

# Versão dos documentos. Mudou o texto, sobe a versão: quem aceitou a anterior
# precisa aceitar de novo, e a prova antiga continua válida para o período dela.
TERMS_VERSION = "2026-09-03"
PRIVACY_VERSION = "2026-09-03"

# Idade a partir da qual o próprio titular consente. Abaixo disso, o
# tratamento depende de consentimento do responsável (art. 14).
AGE_OF_CONSENT = 16
CHILD_AGE_LIMIT = 12  # abaixo: "criança" na LGPD; acima até 18: "adolescente"

# Idade mínima para abrir a conta sozinho. A LGPD fala em 16 para consentir;
# o Código Civil fala em 18 para se obrigar por contrato. Como criar conta é
# aceitar termos, adotamos o critério mais alto: menor de 18 só entra por uma
# conta criada pelo responsável, que consente autenticado.
AGE_OF_MAJORITY = 18


# --------------------------------------------------------------------------- #
# Registro das operações de tratamento (art. 37)
# --------------------------------------------------------------------------- #
TREATMENT_RECORD = [
    {
        "finalidade": "Criar e manter a conta do estudante",
        "dados": "nome, e-mail, senha (hash), fuso horário",
        "base_legal": "Execução de contrato (art. 7º, V)",
        "retencao": "Enquanto a conta existir; excluída em até 30 dias após o pedido",
    },
    {
        "finalidade": "Organizar a agenda acadêmica",
        "dados": "matérias, professores, locais, horários, atividades, notas",
        "base_legal": "Execução de contrato (art. 7º, V)",
        "retencao": "Enquanto a conta existir",
    },
    {
        "finalidade": "Ler documentos e áudios enviados para extrair compromissos",
        "dados": "arquivos enviados, transcrições, texto extraído",
        "base_legal": "Consentimento (art. 7º, I) para o envio a provedor de IA",
        "retencao": f"Áudio: {config.AUDIO_RETENTION_DAYS} dias. Documento: até o usuário apagar",
    },
    {
        "finalidade": "Enviar lembretes",
        "dados": "e-mail, telefone (quando vinculado), inscrição de push",
        "base_legal": "Execução de contrato (art. 7º, V)",
        "retencao": "Enquanto a conta existir ou até revogar o canal",
    },
    {
        "finalidade": "Segurança, prevenção a fraude e auditoria",
        "dados": "hash do IP, agente do navegador, registro de acesso e de ações",
        "base_legal": "Legítimo interesse (art. 7º, IX) e obrigação legal",
        "retencao": "Tentativas de login: 30 dias. Auditoria: 12 meses",
    },
    {
        "finalidade": "Acompanhamento por responsável",
        "dados": "vínculo entre contas e permissões escolhidas pelo estudante",
        "base_legal": "Consentimento do titular e, para menores, do responsável (art. 14)",
        "retencao": "Enquanto o vínculo estiver ativo",
    },
]

# Quem mais toca nos dados (art. 9º, II e art. 33).
SUBPROCESSORS = [
    {
        "nome": "Railway",
        "papel": "Hospedagem da aplicação e do banco de dados",
        "pais": "Estados Unidos",
        "dados": "Todos os dados da aplicação, em repouso e em trânsito",
    },
    {
        "nome": "Google (Gemini)",
        "papel": "Transcrição de áudio, leitura de imagem e extração de documentos",
        "pais": "Estados Unidos",
        "dados": "Conteúdo enviado pelo usuário para interpretação, sem identificadores de conta",
    },
    {
        "nome": "Meta (WhatsApp Business)",
        "papel": "Envio e recebimento de mensagens, quando o usuário conecta o número",
        "pais": "Estados Unidos",
        "dados": "Telefone e conteúdo das mensagens trocadas com o número oficial",
    },
]


def document_hash(texto: str) -> str:
    """Impressão digital do texto aceito — prova de qual versão o titular leu."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Consentimento
# --------------------------------------------------------------------------- #
def record(
    db: Session,
    user: User,
    kind: str,
    *,
    version: str,
    granted: bool = True,
    ip: str | None = None,
    user_agent: str = "",
    origin: str = "web",
    guardian_name: str = "",
    guardian_email: str = "",
    guardian_relationship: str = "",
    document_hash_value: str = "",
) -> ConsentRecord:
    """Grava um consentimento (ou a revogação dele). Nunca sobrescreve o anterior."""
    registro = ConsentRecord(
        user_id=user.id,
        kind=kind,
        version=version,
        granted=granted,
        document_hash=document_hash_value[:64],
        ip_hash=hash_ip(ip),
        user_agent=(user_agent or "")[:300],
        origin=origin,
        guardian_name=guardian_name.strip()[:160],
        guardian_email=guardian_email.strip().lower()[:200],
        guardian_relationship=guardian_relationship.strip()[:40],
    )
    db.add(registro)
    db.flush()
    return registro


def latest(db: Session, user: User, kind: str) -> ConsentRecord | None:
    return db.scalars(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user.id, ConsentRecord.kind == kind)
        .order_by(ConsentRecord.created_at.desc())
    ).first()


def has_consent(db: Session, user: User, kind: str, *, version: str | None = None) -> bool:
    """True se o consentimento vigente existe e é da versão pedida."""
    registro = latest(db, user, kind)
    if registro is None or not registro.granted:
        return False
    return version is None or registro.version == version


def revoke(db: Session, user: User, kind: str, *, ip: str | None = None) -> ConsentRecord | None:
    """Revoga um consentimento revogável.

    Termos e política não são revogáveis mantendo a conta: sem eles não há
    contrato. O caminho para "revogar" esses é excluir a conta.
    """
    if kind in (ConsentKind.TERMS.value, ConsentKind.PRIVACY.value):
        return None
    atual = latest(db, user, kind)
    versao = atual.version if atual else "—"
    return record(db, user, kind, version=versao, granted=False, ip=ip)


def history(db: Session, user: User) -> list[ConsentRecord]:
    return list(
        db.scalars(
            select(ConsentRecord)
            .where(ConsentRecord.user_id == user.id)
            .order_by(ConsentRecord.created_at.desc())
        ).all()
    )


def pending_documents(db: Session, user: User) -> list[str]:
    """Documentos que o usuário ainda não aceitou na versão vigente."""
    pendentes = []
    if not has_consent(db, user, ConsentKind.TERMS.value, version=TERMS_VERSION):
        pendentes.append(ConsentKind.TERMS.value)
    if not has_consent(db, user, ConsentKind.PRIVACY.value, version=PRIVACY_VERSION):
        pendentes.append(ConsentKind.PRIVACY.value)
    return pendentes


# --------------------------------------------------------------------------- #
# Menores de idade (art. 14)
# --------------------------------------------------------------------------- #
def age_from_year(birth_year: int | None, *, today: dt.date | None = None) -> int | None:
    if not birth_year:
        return None
    today = today or dt.date.today()
    idade = today.year - int(birth_year)
    return idade if 0 <= idade <= 120 else None


def requires_guardian(birth_year: int | None, *, education_type: str = "") -> bool:
    """Se o tratamento depende de consentimento de responsável.

    Dois caminhos levam à mesma conclusão: a idade informada, ou um perfil de
    ensino que só existe para crianças. Na dúvida, exigimos o responsável —
    errar para o lado protetivo é a única opção aceitável aqui.
    """
    idade = age_from_year(birth_year)
    if idade is not None and idade < AGE_OF_CONSENT:
        return True
    if education_type:
        from agenda.core.profiles import is_minor_profile

        return is_minor_profile(education_type)
    return False


def guardian_consent_ok(db: Session, user: User) -> bool:
    if not user.is_minor:
        return True
    registro = latest(db, user, ConsentKind.GUARDIAN_MINOR.value)
    return bool(registro and registro.granted)


def mark_minor(db: Session, user: User, *, is_minor: bool) -> None:
    user.is_minor = is_minor
    if not is_minor:
        user.guardian_consent_at = None
    db.flush()


def register_guardian_consent(
    db: Session,
    user: User,
    *,
    guardian_name: str,
    guardian_email: str,
    relationship: str,
    ip: str | None = None,
    user_agent: str = "",
) -> ConsentRecord:
    registro = record(
        db, user, ConsentKind.GUARDIAN_MINOR.value,
        version=PRIVACY_VERSION,
        ip=ip,
        user_agent=user_agent,
        guardian_name=guardian_name,
        guardian_email=guardian_email,
        guardian_relationship=relationship,
    )
    user.guardian_consent_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    return registro


def is_adult(birth_year: int | None, *, today: dt.date | None = None) -> bool:
    idade = age_from_year(birth_year, today=today)
    return idade is not None and idade >= AGE_OF_MAJORITY


def terms_text() -> str:
    from agenda.legal import documents

    return documents.plain_text(documents.TERMS_SECTIONS)


def privacy_text() -> str:
    from agenda.legal import documents

    return documents.plain_text(documents.PRIVACY_SECTIONS)


def accept_documents(
    db: Session,
    user: User,
    *,
    ip: str | None = None,
    user_agent: str = "",
    origin: str = "web",
    ai_processing: bool = True,
) -> None:
    """Registra o aceite dos dois documentos e carimba a versão no usuário.

    O ConsentRecord é a prova (art. 8º §1º); a versão no usuário é só o atalho
    que permite exigir novo aceite sem uma consulta por requisição.
    """
    record(db, user, ConsentKind.TERMS.value, version=TERMS_VERSION, ip=ip,
           user_agent=user_agent, origin=origin,
           document_hash_value=document_hash(terms_text()))
    record(db, user, ConsentKind.PRIVACY.value, version=PRIVACY_VERSION, ip=ip,
           user_agent=user_agent, origin=origin,
           document_hash_value=document_hash(privacy_text()))
    record(db, user, ConsentKind.AI_PROCESSING.value, version=PRIVACY_VERSION,
           granted=ai_processing, ip=ip, user_agent=user_agent, origin=origin)
    user.accepted_terms_version = TERMS_VERSION
    user.accepted_privacy_version = PRIVACY_VERSION
    user.ai_processing_enabled = ai_processing
    db.flush()


def documents_up_to_date(user: User) -> bool:
    return (
        user.accepted_terms_version == TERMS_VERSION
        and user.accepted_privacy_version == PRIVACY_VERSION
    )


def set_ai_processing(db: Session, user: User, *, enabled: bool, ip: str | None = None) -> None:
    """Liga/desliga o envio de conteúdo para interpretação automática."""
    user.ai_processing_enabled = enabled
    record(db, user, ConsentKind.AI_PROCESSING.value, version=PRIVACY_VERSION,
           granted=enabled, ip=ip)
    db.flush()


def ai_allowed(user: User | None) -> bool:
    """Chokepoint: nenhum conteúdo vai para provedor externo sem isto."""
    return bool(user is not None and user.ai_processing_enabled)


def blocked_reason(db: Session, user: User) -> str | None:
    """Por que este usuário não pode usar o app agora — ou None se pode.

    Fecha por padrão: qualquer inconsistência de consentimento trava o acesso
    em vez de deixar passar.
    """
    # A falta de responsável vem primeiro de propósito: um menor sem
    # autorização não deve ser mandado para uma tela de aceite que ele não tem
    # capacidade de dar — seria pedir a assinatura de quem não pode assinar.
    if user.is_minor and not user.guardian_consent_at:
        return "responsavel"
    if not documents_up_to_date(user):
        return "documentos"
    return None
