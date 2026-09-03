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


def enumerate_pt(itens: list[str], *, conjuncao: str = "ou") -> str:
    """Lista no jeito que se lê em voz alta: "A, B ou C".

    Existe porque `" ou ".join(...)` produz "A ou B ou C", que soa a formulário
    e não a alguém falando com você.
    """
    itens = [i for i in itens if i]
    if not itens:
        return ""
    if len(itens) == 1:
        return itens[0]
    return f"{', '.join(itens[:-1])} {conjuncao} {itens[-1]}"


def plural_pt(quantidade: int, singular: str, plural: str = "") -> str:
    """"1 prova", "2 provas", "0 provas" — com o número na frente.

    Concordância errada ("1 provas") é pequena e é exatamente o tipo de coisa
    que faz um produto parecer inacabado.
    """
    palavra = singular if abs(quantidade) == 1 else (plural or f"{singular}s")
    return f"{quantidade} {palavra}"
