"""Configuração central — lida de variáveis de ambiente.

Toda a configuração do produto vive aqui. Nenhum outro módulo deve ler
``os.environ`` diretamente, para que o comportamento seja previsível em
development / staging / production (SPEC §108).
"""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on", "sim")


# --------------------------------------------------------------------------- #
# Ambiente
# --------------------------------------------------------------------------- #
ENV = _env("APP_ENV", "development").lower()  # development | staging | production
IS_PRODUCTION = ENV == "production"
APP_NAME = _env("APP_NAME", "Grifo")
PUBLIC_URL = _env("PUBLIC_URL", "").rstrip("/")
PORT = int(_env("PORT", "8080"))

SECRET_KEY = _env("SECRET_KEY") or _env("WEB_PASSWORD") or "dev-secret-troque-em-producao"

# --------------------------------------------------------------------------- #
# Banco de dados
# --------------------------------------------------------------------------- #
DATABASE_URL = _env("DATABASE_URL", "sqlite:///agenda.db")
# Só para emergência: em produção o schema deve vir das migrations.
AUTO_CREATE_TABLES = _flag("AUTO_CREATE_TABLES", False)
if DATABASE_URL.startswith("postgres://"):  # Railway/Heroku legado
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# --------------------------------------------------------------------------- #
# Fuso horário e lembretes
# --------------------------------------------------------------------------- #
DEFAULT_TIMEZONE = _env("TIMEZONE", "America/Sao_Paulo")
TZ = ZoneInfo(DEFAULT_TIMEZONE)

# Lembretes padrão para atividades com prazo (SPEC §49): 7 dias e 1 dia antes.
DEFAULT_REMINDER_DAYS = [
    int(d) for d in _env("REMINDER_DAYS", "7,1").split(",") if d.strip().isdigit()
]
# Hora local em que os lembretes "de dias antes" são disparados.
REMINDER_HOUR = int(_env("REMINDER_HOUR", "8"))
REMINDER_MINUTE = int(_env("REMINDER_MINUTE", "0"))
# De quanto em quanto tempo o worker varre a fila de notificações (segundos).
NOTIFICATION_TICK_SECONDS = int(_env("NOTIFICATION_TICK_SECONDS", "60"))

# --------------------------------------------------------------------------- #
# IA (SPEC §69, §70, §115) — interface abstrata, provedor plugável
# --------------------------------------------------------------------------- #
AI_PROVIDER = _env("AI_PROVIDER", "gemini").lower()  # gemini | none
GEMINI_API_KEY = _env("GEMINI_API_KEY")
# Modelo barato para classificação/extração repetitiva.
AI_MODEL_FAST = _env("AI_MODEL_FAST", "gemini-2.5-flash")
# Modelo forte para interpretação complexa / baixa confiança.
AI_MODEL_STRONG = _env("AI_MODEL_STRONG", "gemini-2.5-pro")
AI_MODEL_VISION = _env("AI_MODEL_VISION", "gemini-2.5-flash")
SPEECH_MODEL = _env("SPEECH_MODEL", "gemini-2.5-flash")
PROMPT_VERSION = _env("PROMPT_VERSION", "2026-09-02.1")

# Limiares de confiança (SPEC §13).
CONFIDENCE_AUTO = float(_env("CONFIDENCE_AUTO", "0.90"))
CONFIDENCE_REVIEW = float(_env("CONFIDENCE_REVIEW", "0.70"))
# Campos críticos exigem barra mais alta.
CONFIDENCE_CRITICAL = float(_env("CONFIDENCE_CRITICAL", "0.95"))

# --------------------------------------------------------------------------- #
# WhatsApp Cloud API (SPEC §15-§19, §67, §68)
# --------------------------------------------------------------------------- #
WHATSAPP_TOKEN = _env("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = _env("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = _env("WHATSAPP_VERIFY_TOKEN", "agenda-verify")
WHATSAPP_APP_SECRET = _env("WHATSAPP_APP_SECRET")
WHATSAPP_NUMBER = _env("WHATSAPP_NUMBER")  # exibido na UI, formato E.164
WHATSAPP_API_VERSION = _env("WHATSAPP_API_VERSION", "v21.0")

# Telegram continua suportado como canal alternativo de notificação.
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")

# --------------------------------------------------------------------------- #
# Web Push (SPEC §51) — chaves geradas localmente com `python -m agenda.cli vapid`
# --------------------------------------------------------------------------- #
VAPID_PUBLIC_KEY = _env("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = _env("VAPID_PRIVATE_KEY")
VAPID_CONTACT = _env("VAPID_CONTACT", "suporte@grifo.app")

# --------------------------------------------------------------------------- #
# Uploads (SPEC §79)
# --------------------------------------------------------------------------- #
MAX_UPLOAD_MB = int(_env("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
# Travas de processamento: documento enorme não pode prender o worker.
MAX_DOCUMENT_PAGES = int(_env("MAX_DOCUMENT_PAGES", "80"))
MAX_DOCUMENT_CHARS = int(_env("MAX_DOCUMENT_CHARS", "400000"))
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".xlsx",
    ".jpg", ".jpeg", ".png", ".webp", ".heic",
}
# Retenção de mídia (SPEC §82, §83): 0 = manter até o usuário apagar.
AUDIO_RETENTION_DAYS = int(_env("AUDIO_RETENTION_DAYS", "7"))
DOCUMENT_RETENTION_DAYS = int(_env("DOCUMENT_RETENTION_DAYS", "0"))
STORAGE_DIR = _env("STORAGE_DIR", os.path.join(os.getcwd(), "storage"))

# --------------------------------------------------------------------------- #
# Feature flags (SPEC §98)
# --------------------------------------------------------------------------- #
# Só entra aqui flag que ALGUM código de fato lê. Flag decorativa é pior que
# nenhuma: o operador desliga `FEATURE_FAMILY=false` achando que desligou
# família, e ela continua ligada — um controle que mente é um risco, não um
# recurso. Família, planejador de estudo e calendário são governados pelo
# entitlement do plano (`core/billing.py`), que é o portão de verdade; ter dois
# portões para a mesma coisa é como se cria bug de permissão.
FEATURE_FLAGS: dict[str, bool] = {
    "whatsapp_enabled": _flag("FEATURE_WHATSAPP", True),
    "document_import_enabled": _flag("FEATURE_DOCUMENT_IMPORT", True),
    "auto_create_high_confidence": _flag("FEATURE_AUTO_CREATE", True),
    "voice_capture_enabled": _flag("FEATURE_VOICE", True),
    "sharing_enabled": _flag("FEATURE_SHARING", True),
    "billing_enabled": _flag("FEATURE_BILLING", False),
}


def flag(name: str) -> bool:
    return FEATURE_FLAGS.get(name, False)


# --------------------------------------------------------------------------- #
# Rate limits (SPEC §111) — requisições por janela de 60s
# --------------------------------------------------------------------------- #
# Limite por balde: (requisições, janela em segundos).
#
# O cadastro tem janela longa e teto alto de propósito. O público é estudante,
# e estudante entra em rajada: uma turma inteira sai pelo mesmo IP do wi‑fi da
# escola, ou pelo NAT da universidade. Um limite de 5 por minuto travaria a
# sexta pessoa da sala — e a sala é justamente onde o produto se espalha.
#
# O que barra conta falsa não é volume de cadastro, é o que a conta consegue
# fazer: as quotas do plano grátis (documentos, mensagens de IA) já limitam o
# custo por conta, e a recompensa de indicação só paga quando o indicado vira
# ASSINANTE — cartão de crédito é a prova de gente que nenhum limite de IP dá.
RATE_LIMITS = {
    "login": (int(_env("RATE_LIMIT_LOGIN", "10")), 60),
    "register": (int(_env("RATE_LIMIT_REGISTER", "40")), 600),
    "share": (int(_env("RATE_LIMIT_SHARE", "20")), 60),
    "export": (int(_env("RATE_LIMIT_EXPORT", "10")), 60),
    "assistant": (int(_env("RATE_LIMIT_ASSISTANT", "30")), 60),
    "upload": (int(_env("RATE_LIMIT_UPLOAD", "20")), 60),
    "webhook": (int(_env("RATE_LIMIT_WEBHOOK", "600")), 60),
    "referral": (int(_env("RATE_LIMIT_REFERRAL", "20")), 600),
    "checkout": (int(_env("RATE_LIMIT_CHECKOUT", "10")), 600),
}

ADMIN_EMAILS = {e.strip().lower() for e in _env("ADMIN_EMAILS").split(",") if e.strip()}

# --------------------------------------------------------------------------- #
# Pagamento (SPEC §96)
# --------------------------------------------------------------------------- #
# Provedor: stripe | none. Sem chave, o checkout diz que não está ligado em vez
# de fingir que funcionou.
PAYMENT_PROVIDER = _env("PAYMENT_PROVIDER", "none").lower()
STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")
# Janela de arrependimento do art. 49 do CDC.
REFUND_WINDOW_DAYS = int(_env("REFUND_WINDOW_DAYS", "7"))

# --------------------------------------------------------------------------- #
# Privacidade e contato do titular (LGPD art. 41)
# --------------------------------------------------------------------------- #
PRIVACY_EMAIL = _env("PRIVACY_EMAIL", "privacidade@grifo.app")
DPO_NAME = _env("DPO_NAME", "a definir antes do lançamento comercial")
COMPANY_NAME = _env("COMPANY_NAME", APP_NAME)
COMPANY_DOC = _env("COMPANY_DOC", "")  # CNPJ, quando houver
