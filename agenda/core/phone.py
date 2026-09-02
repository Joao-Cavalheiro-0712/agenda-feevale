"""Normalização de telefone para E.164 (SPEC §6.1, §16)."""
from __future__ import annotations

import re

DEFAULT_COUNTRY = "55"


def normalize(raw: str, *, country: str = DEFAULT_COUNTRY) -> str:
    """Devolve o número em E.164 ("+5551999998888") ou "" se implausível."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.strip().startswith("+"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in (10, 11):  # DDD + número, sem país
        digits = country + digits
    elif len(digits) in (8, 9):  # sem DDD: não dá para inferir com segurança
        return ""
    return f"+{digits}" if 10 <= len(digits) <= 15 else ""


def variants(e164: str) -> set[str]:
    """Variações aceitáveis do mesmo número brasileiro.

    O WhatsApp devolve números do Brasil às vezes sem o nono dígito
    ("+55519999-8888" vs "+5551999998888"); tratamos os dois como o mesmo.
    """
    out = {e164}
    digits = re.sub(r"\D", "", e164 or "")
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":
        out.add("+" + digits[:4] + digits[5:])
    elif digits.startswith("55") and len(digits) == 12:
        out.add("+" + digits[:4] + "9" + digits[4:])
    return {v for v in out if v}


def mask(e164: str) -> str:
    if not e164 or len(e164) < 6:
        return e164
    return f"{e164[:5]}…{e164[-4:]}"
