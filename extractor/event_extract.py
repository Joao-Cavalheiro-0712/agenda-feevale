"""Identifica avaliações, provas e trabalhos a partir do texto do cronograma.

Estratégia:
  1. Se houver GEMINI_API_KEY, usa o Google Gemini para extração estruturada
     (muito mais robusto com cronogramas variados).
  2. Sem chave (ou em caso de falha), cai num extrator heurístico por regex.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata

import config

# Palavras que indicam uma entrega/avaliação num cronograma.
KEYWORDS = [
    "prova", "avaliacao", "avaliação", "trabalho", "entrega", "seminario",
    "seminário", "apresentacao", "apresentação", "exame", "teste", "atividade",
    "relatorio", "relatório", "projeto", "g1", "g2", "grau", "recuperacao",
    "recuperação", "quiz", "prática", "pratica",
]

MONTHS_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def _norm(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def _classify(text: str) -> str:
    t = _norm(text)
    if "prova" in t or "exame" in t or "g1" in t or "g2" in t or "teste" in t:
        return "prova"
    if "trabalho" in t or "entrega" in t or "projeto" in t or "relatorio" in t:
        return "trabalho"
    return "avaliacao"


def extract_events(text: str, source_file: str = "") -> list[dict]:
    """Retorna lista de dicts: title, kind, date(YYYY-MM-DD), discipline, notes."""
    events: list[dict] = []
    if config.GEMINI_API_KEY:
        try:
            events = _extract_with_gemini(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[extractor] Gemini falhou ({exc}); usando heurística.")
            events = []
    if not events:
        events = _extract_heuristic(text)

    for e in events:
        e["source_file"] = source_file
    return events


# --------------------------------------------------------------------------- #
# Extração via Google Gemini
# --------------------------------------------------------------------------- #
def _extract_with_gemini(text: str) -> list[dict]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    today = dt.date.today().isoformat()

    # Limita o tamanho para não estourar o contexto em documentos enormes.
    snippet = text[:100000]

    prompt = f"""Você recebe o texto de um cronograma acadêmico da universidade Feevale.
A data de hoje é {today}.

Extraia TODAS as avaliações, provas, trabalhos, entregas, seminários e exames
que tenham uma DATA definida. Ignore aulas normais sem entrega.

Regras:
- "kind" deve ser exatamente um de: "prova", "trabalho" ou "avaliacao".
- "date" no formato YYYY-MM-DD. Se o ano não estiver explícito, infira pelo
  contexto do semestre corrente (use o ano de hoje, {today[:4]}, salvo indicação clara).
- "discipline" é o nome da matéria/disciplina, se identificável (senão "").
- "title" curto e descritivo (ex: "Prova G1", "Entrega do Trabalho Final").
- "notes" pode conter detalhes úteis (peso, capítulos), senão "".

Texto do cronograma:
\"\"\"
{snippet}
\"\"\""""

    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "kind": {"type": "STRING", "enum": ["prova", "trabalho", "avaliacao"]},
                "date": {"type": "STRING"},
                "discipline": {"type": "STRING"},
                "notes": {"type": "STRING"},
            },
            "required": ["title", "kind", "date"],
        },
    }

    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    return _parse_json_events(resp.text or "")


def _parse_json_events(raw: str) -> list[dict]:
    # Remove cercas de código, se houver.
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    # Recorta o primeiro array JSON.
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    data = json.loads(raw[start : end + 1])

    out: list[dict] = []
    for item in data:
        try:
            d = dt.date.fromisoformat(str(item["date"])[:10])
        except (KeyError, ValueError):
            continue
        title = str(item.get("title", "")).strip() or "Avaliação"
        kind = str(item.get("kind", "")).strip().lower()
        if kind not in ("prova", "trabalho", "avaliacao"):
            kind = _classify(title)
        out.append(
            {
                "title": title[:300],
                "kind": kind,
                "date": d.isoformat(),
                "discipline": str(item.get("discipline", "")).strip()[:200],
                "notes": str(item.get("notes", "")).strip(),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Extração heurística (sem IA)
# --------------------------------------------------------------------------- #
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_DATE_TEXTUAL = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-zçã]+)(?:\s+de\s+(\d{4}))?\b", re.IGNORECASE
)


def _resolve_year(year: str | None, month: int) -> int:
    if year:
        y = int(year)
        return y + 2000 if y < 100 else y
    today = dt.date.today()
    # Se o mês já passou faz muito tempo, assume o próximo ano.
    y = today.year
    if month < today.month - 1:
        y += 1
    return y


def _find_dates(line: str) -> list[dt.date]:
    found: list[dt.date] = []
    for m in _DATE_NUMERIC.finditer(line):
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= day <= 31 and 1 <= month <= 12:
            try:
                found.append(dt.date(_resolve_year(year, month), month, day))
            except ValueError:
                pass
    for m in _DATE_TEXTUAL.finditer(line):
        day = int(m.group(1))
        month = MONTHS_PT.get(_norm(m.group(2)))
        year = m.group(3)
        if month and 1 <= day <= 31:
            try:
                found.append(dt.date(_resolve_year(year, month), month, day))
            except ValueError:
                pass
    return found


def _extract_heuristic(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        low = _norm(clean)
        if not any(k in low for k in (_norm(k) for k in KEYWORDS)):
            continue
        for d in _find_dates(clean):
            title = re.sub(r"\s+", " ", clean)[:200]
            key = (title, d.isoformat())
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "title": title,
                    "kind": _classify(clean),
                    "date": d.isoformat(),
                    "discipline": "",
                    "notes": "Detectado automaticamente (heurística).",
                }
            )
    return out
