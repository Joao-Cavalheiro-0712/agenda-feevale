"""Chave fonética do português brasileiro.

Duas pessoas escrevem "cálculo" de quatro jeitos: `calculo`, `cauculo`,
`caucolo`, `calculu`. Um áudio transcrito erra nos mesmos lugares, porque o
erro não é aleatório — ele segue a fonologia do português falado no Brasil.
Esta chave normaliza justamente esses eixos:

* **vocalização do L** — "calculo" → "cauculo", "papel" → "papeu". L antes de
  consoante ou no fim da palavra vira U. É a regra que mais rende.
* **redução da vogal átona final** — "livro" → "livru", "noite" → "noiti".
  Por isso as vogais são reduzidas a três classes: A, I (e/i) e U (o/u).
* **R final apagado** — "entregar" → "entregá". Infinitivo falado.
* **iotacismo** — "trabalho" → "trabaio", "mulher" → "muié". O LH vira I.
* **sibilantes fundidas** — s, ss, ç, z, sc e o C de "ce/ci" caem todos no
  mesmo símbolo: quem escreve "sosiologia", "caza" ou "presiso" acerta o alvo.
* **dígrafos e alofones** — ch/x, nh, qu/k, g antes de e/i = j, h mudo,
  "ex" inicial soando como Z ("exame" ~ "ezame").
* **nasalização** — "ão/am" → ÃU, m/n final → N.

Não é um algoritmo publicado adaptado no chute: cada regra abaixo corresponde
a um fenômeno documentado do PB e está coberta por teste. O objetivo não é
linguística, é que o usuário nunca ouça "não entendi" por causa de ortografia.
"""
from __future__ import annotations

import re

from agenda.core.text import norm

# Ordem importa: dígrafos antes de letras isoladas.
_DIGRAFOS = (
    ("lh", "I"),
    ("nh", "N"),
    ("ch", "X"),
    ("ph", "F"),
    ("rr", "R"),
    ("ss", "S"),
    ("sc", "S"),
    ("sç", "S"),
    ("xc", "S"),
    ("qu", "K"),
    ("gu", "G"),
)


def phonetic_key(text: str) -> str:
    """Devolve a chave fonética de uma palavra ou expressão."""
    # O Ç precisa virar S aqui: `norm` remove acentos e o transformaria em C,
    # e aí "redação" viraria RIDAKAU em vez de RIDASAU.
    texto = (text or "").lower().replace("ç", "s")
    palavras = [p for p in re.split(r"[^a-z0-9]+", norm(texto)) if p]
    return " ".join(_palavra(p) for p in palavras if p)


def _palavra(p: str) -> str:
    if p.isdigit():
        return p

    # Nasais escritas de formas diferentes para o mesmo som.
    p = p.replace("ao", "AU") if p.endswith("ao") else p
    p = re.sub(r"am$", "AU", p)

    # "ex" inicial antes de vogal soa como Z: exame, exercício, exato.
    if len(p) > 2 and p.startswith("ex") and p[2] in _VOGAIS:
        p = p[0] + "S" + p[2:]

    for de, para in _DIGRAFOS:
        p = p.replace(de, para)

    # H mudo (já sem dígrafos).
    p = p.replace("h", "")

    saida: list[str] = []
    for i, c in enumerate(p):
        seguinte = p[i + 1] if i + 1 < len(p) else ""

        if c.isupper():           # já resolvido por dígrafo/nasal
            saida.append(c)
        elif c == "c":
            saida.append("S" if seguinte in "ei" else "K")
        elif c == "ç":
            saida.append("S")
        elif c == "g":
            saida.append("J" if seguinte in "ei" else "G")
        elif c == "j":
            saida.append("J")
        elif c == "q":
            saida.append("K")
        elif c == "w":
            saida.append("V")
        elif c == "y":
            saida.append("I")
        elif c == "x":
            saida.append("X")
        elif c in "sz":
            # Todas as sibilantes no mesmo símbolo: distinguir "caçar" de
            # "casar" não interessa aqui, e fundi-las salva quem escreve
            # "sosiologia" ou "caza".
            saida.append("S")
        elif c == "l":
            # Vocalização: L travando sílaba vira U.
            saida.append("L" if _e_vogal(seguinte) else "U")
        elif c == "r":
            # R final de infinitivo/substantivo é apagado na fala.
            if not seguinte and len(p) > 2:
                continue
            saida.append("R")
        elif c in "mn":
            # Nasal travando sílaba: um único som.
            saida.append(c.upper() if _e_vogal(seguinte) else "N")
        elif c in _VOGAIS:
            saida.append(_VOGAL_CLASSE[c])
        elif c.isalnum():
            saida.append(c.upper())

    chave = "".join(saida)
    chave = re.sub(r"(.)\1+", r"\1", chave)  # colapsa repetições
    return chave


_VOGAIS = "aeiou"
_VOGAL_CLASSE = {"a": "A", "e": "I", "i": "I", "o": "U", "u": "U"}


def _e_vogal(ch: str) -> bool:
    """Cuidado: `"" in "aeiou"` é True em Python. Aqui não pode ser."""
    return bool(ch) and ch in _VOGAIS


def sounds_like(a: str, b: str) -> bool:
    """True quando as duas grafias soam igual em português brasileiro."""
    if not a or not b:
        return False
    return phonetic_key(a) == phonetic_key(b)
