"""Configuração central lida a partir de variáveis de ambiente.

No Railway, defina estas variáveis em Settings → Variables.
"""
import os
from zoneinfo import ZoneInfo

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# Opcional: se você já sabe seu chat_id, coloque aqui. Caso contrário, basta
# mandar /start para o bot que ele guarda automaticamente.
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# --- Google Gemini — extração inteligente dos cronogramas ---
# Gere a chave em https://aistudio.google.com/app/apikey (gratuito/barato).
# Se não houver chave, o sistema usa um extrator heurístico (regex) como reserva.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

# --- Banco de dados ---
# Railway injeta DATABASE_URL quando você adiciona um Postgres.
# Sem ele, usamos um SQLite local (persiste enquanto o container viver).
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///agenda.db").strip()
if DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy exige o prefixo postgresql://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# --- Fuso horário e horário do lembrete diário ---
TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "America/Sao_Paulo"))
# Hora do dia (0-23) em que o job diário roda e dispara os avisos.
DAILY_HOUR = int(os.environ.get("DAILY_HOUR", "8"))
DAILY_MINUTE = int(os.environ.get("DAILY_MINUTE", "0"))

# Quantos dias antes avisar. Padrão: 1 semana e 1 dia antes.
REMINDER_DAYS = [
    int(d) for d in os.environ.get("REMINDER_DAYS", "7,1").split(",") if d.strip()
]

# Senha simples opcional para proteger a interface web de upload.
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "").strip()

# Porta (Railway injeta PORT).
PORT = int(os.environ.get("PORT", "8080"))
