"""Utilitários de normalização de texto em português."""
from __future__ import annotations

import re
import unicodedata


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def norm(s: str) -> str:
    """Minúsculas, sem acentos, espaços colapsados."""
    return re.sub(r"\s+", " ", strip_accents(s or "").lower()).strip()


def slug_key(s: str) -> str:
    """Chave alfanumérica para comparação de títulos (dedupe)."""
    return re.sub(r"[^a-z0-9]+", "", norm(s))


def fold(s: str) -> str:
    """Minúsculas e sem acentos, preservando o alinhamento de índices.

    Ao contrário de ``norm``, não colapsa espaços — assim dá para casar um
    padrão no texto normalizado e recortar o trecho no texto ORIGINAL,
    mantendo a acentuação para o usuário.
    """
    return strip_accents(s or "").lower()
