"""Interpretação sem LLM — determinística, em português (SPEC §70).

Serve para dois propósitos:
  1. o produto continua útil sem chave de IA (protótipo, staging, queda do
     fornecedor);
  2. é o piso de qualidade contra o qual medimos o modelo (SPEC §103).
"""
from __future__ import annotations

import re

from agenda.core.dates import WEEKDAYS, parse_time
from agenda.core.text import fold, norm
from agenda.models import EventType

# Palavra-chave → tipo de evento. A ordem importa: a primeira que casar vence.
TYPE_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("simulado",), EventType.SIMULATION.value),
    (("prova", "exame", "avaliacao", "g1", "g2", "teste"), EventType.EXAM.value),
    (("quiz",), EventType.QUIZ.value),
    (("seminario",), EventType.SEMINAR.value),
    (("apresentacao", "apresentar"), EventType.PRESENTATION.value),
    (("artigo", "fichamento", "resenha", "paper"), EventType.PAPER.value),
    (("relatorio", "laboratorio", "lab ", "pratica"), EventType.LAB.value),
    (("tcc", "projeto"), EventType.PROJECT.value),
    (("trabalho", "entrega", "entregar"), EventType.ASSIGNMENT.value),
    (("exercicio", "exercicios", "lista", "tema de casa", "dever", "tarefa"), EventType.HOMEWORK.value),
    (("leitura", "ler ", "capitulo", "capitulos"), EventType.READING.value),
    (("levar", "trazer", "material", "cartolina", "cola", "tesoura", "regua", "jaleco"),
     EventType.MATERIAL.value),
    (("estagio",), EventType.INTERNSHIP.value),
    (("matricula", "rematricula", "prazo"), EventType.ADMINISTRATIVE.value),
    (("aula",), EventType.CLASS.value),
]

MATERIAL_HINTS = ("levar", "trazer", "comprar")

# Expressões temporais reconhecidas, da mais específica para a mais genérica.
# A ordem é a prioridade: "dia 12/11" vale mais que "dia 12".
_MONTHS = (
    "janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|"
    "novembro|dezembro|jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez"
)
_DAY = r"(?:\d{1,2}|(?:vinte|trinta) e \w+|um|dois|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|catorze|quatorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta)"
_WEEKDAY = r"(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?"

TIME_EXPRESSIONS = [
    r"\d{4}-\d{2}-\d{2}",
    r"\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?",
    rf"{_DAY} de (?:{_MONTHS})(?: de \d{{4}})?",
    rf"dia {_DAY}(?: de (?:{_MONTHS}))?",
    r"depois de amanha",
    r"amanha",
    r"hoje",
    r"daqui a \w+ (?:dias?|semanas?|mes(?:es)?)",
    r"(?:dentro de|em) \w+ (?:dias?|semanas?)",
    r"(?:fim|final) do mes",
    r"(?:na )?proxima aula",
    r"(?:na )?aula seguinte",
    r"proximo encontro",
    rf"(?:proxim[ao]|prox\.?) {_WEEKDAY}",
    rf"{_WEEKDAY} que vem",
    _WEEKDAY,
    r"semana que vem",
    r"proxima semana",
]

QUERY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(atrasad|em atraso|pendente)", "GET_OVERDUE"),
    (r"\b(hoje)\b.*\b(tenho|tem|agenda|programad)|\b(o que|oque).*\bhoje\b", "GET_TODAY"),
    (r"\b(essa|esta|nessa|desta|dessa)\s+semana\b", "GET_WEEK"),
    (r"\b(semana)\b.*\b(tenho|tem|como esta|resumo)\b", "GET_WEEK"),
    (r"\b(esse|este|nesse|deste)\s+mes\b", "GET_MONTH"),
    (r"\b(proxima|proximo)\s+(prova|trabalho|entrega|aula|avaliacao)\b", "GET_NEXT_EVENTS"),
    (r"\b(quando)\b.*\b(prova|trabalho|entrega|aula)\b", "GET_NEXT_EVENTS"),
    (r"\b(me mostra|mostra|lista|listar)\b", "GET_SUBJECT_EVENTS"),
]

COMPLETE_PATTERNS = r"\b(ja )?(entreguei|terminei|conclui|finalizei|fiz|acabei)\b"

_STOPWORDS_TITLE = {
    "o", "a", "os", "as", "de", "da", "do", "das", "dos", "para", "pra", "pro",
    "em", "no", "na", "nos", "nas", "que", "e", "um", "uma", "com", "sobre",
    "professor", "professora", "prof", "marcou", "pediu", "passou", "tem", "vai",
    "ser", "eu", "meu", "minha", "nossa", "aula",
}


def detect_type(text: str) -> tuple[str, float]:
    """Tipo da atividade, consultando primeiro a base de conhecimento própria.

    O léxico conhece 200 formas de dizer a mesma coisa — "p1", "sub", "trampo",
    "lista", "tema" — e ainda tolera erro de escrita pelo som. A tabela local
    abaixo continua como rede de segurança para os casos que ela já resolvia.
    """
    from agenda.knowledge import lexicon

    tipo, _termo, score = lexicon.find_event_type(text)
    t = norm(text)
    if tipo:
        if tipo == EventType.CLASS.value and any(h in t for h in MATERIAL_HINTS):
            return EventType.MATERIAL.value, 0.85
        return tipo, round(min(0.95, 0.9 * score + 0.05), 2)

    for keywords, event_type in TYPE_KEYWORDS:
        for keyword in keywords:
            if keyword in t:
                if event_type == EventType.CLASS.value and any(h in t for h in MATERIAL_HINTS):
                    return EventType.MATERIAL.value, 0.85
                return event_type, 0.9
    return EventType.OTHER.value, 0.5


def find_time_expression(text: str) -> str:
    """Primeira expressão temporal reconhecida, por ordem de especificidade."""
    t = norm(text)
    for pattern in TIME_EXPRESSIONS:
        match = re.search(rf"\b{pattern}\b", t)
        if match:
            return match.group(0)
    return ""


def find_times(text: str, *, shift: str = "") -> tuple[str | None, str | None]:
    """Detecta "das 19:30 às 21:30", "às 8h", "sete e meia".

    ``shift`` desambigua horas de 1 a 11 ditas sem período (SPEC §7).
    """
    t = norm(text)
    span = re.search(
        r"\b(?:das|de)\s+([\w:h]+(?:\s+e\s+\w+)?)\s+(?:as|ate|a)\s+([\w:h]+(?:\s+e\s+\w+)?)", t
    )
    if span:
        return parse_time(span.group(1), shift=shift), parse_time(span.group(2), shift=shift)
    single = re.search(r"\b(?:as|a partir das)\s+([\w:h]+(?:\s+e\s+\w+)?)", t)
    if single:
        return parse_time(single.group(1), shift=shift), None
    bare = re.search(r"\b(\d{1,2}[:h]\d{2})\b", t)
    if bare:
        return parse_time(bare.group(1), shift=shift), None
    return None, None


def find_weekdays(text: str) -> list[int]:
    t = norm(text)
    found: list[int] = []
    for word, weekday in WEEKDAYS.items():
        if len(word) < 3:
            continue
        if re.search(rf"\b{re.escape(word)}\b", t) and weekday not in found:
            found.append(weekday)
    return sorted(found)


def guess_title(text: str, event_type: str, subject_name: str) -> str:
    """Título curto e humano — nunca a frase inteira."""
    from agenda.core.events import TYPE_LABELS

    original = text.strip().rstrip(".")
    folded = fold(original)  # índices alinhados com o texto original
    label = TYPE_LABELS.get(event_type, "Compromisso")

    def slice_original(match, group: int = 1) -> str:
        start, end = match.span(group)
        return original[start:end]

    material = re.search(r"\b(?:levar|trazer|comprar)\s+(.{3,60})", folded)
    if event_type == EventType.MATERIAL.value and material:
        items = re.split(r"\b(?:para|pra|no|na|em|amanha|hoje)\b", slice_original(material))[0]
        items = items.strip(" ,.")
        if items:
            return f"Levar {items}"

    about = re.search(r"\bsobre\s+(.{3,60})", folded)
    if about:
        topic = re.split(
            r"\b(?:para|pra|no dia|dia|ate|até|vale)\b", slice_original(about), flags=re.IGNORECASE
        )[0].strip(" ,.")
        if topic:
            return f"{label} sobre {topic}".strip()

    chapters = re.search(r"\b(exercicios?|questoes|capitulos?|paginas?)\s+([\d\sa-]+)", folded)
    if chapters:
        return f"{slice_original(chapters).capitalize()} {slice_original(chapters, 2).strip()}"[:80]

    if subject_name:
        return f"{label} de {subject_name}"
    return label


def extract_notes(text: str) -> str:
    """Detalhes úteis: peso, valor, grupos."""
    notes = []
    t = norm(text)
    value = re.search(r"\bvale\s+([\d,.]+)\s*(pontos?|%)?", t)
    if value:
        notes.append(f"Vale {value.group(1)} {value.group(2) or 'pontos'}")
    groups = re.search(r"\bgrupos? de\s+(\d+)", t)
    if groups:
        notes.append(f"Grupos de {groups.group(1)}")
    return " · ".join(notes)


# Só tratamos como consulta o que tem cara de pergunta/pedido de listagem —
# "na próxima aula" no meio de uma frase é âncora de data, não consulta.
_QUESTION_OPENERS = (
    "o que", "oque", "quando", "qual", "quais", "quanto", "quantos",
    "me mostra", "mostra", "me lista", "lista", "listar", "tem algo", "tem alguma",
    "tenho algo", "tenho alguma", "como esta", "resumo",
)


def is_query(text: str) -> str | None:
    t = norm(text)
    asks = text.strip().endswith("?") or t.startswith(_QUESTION_OPENERS)
    if not asks:
        return None
    for pattern, intent in QUERY_PATTERNS:
        if re.search(pattern, t):
            return intent
    return None


def is_completion(text: str) -> bool:
    return bool(re.search(COMPLETE_PATTERNS, norm(text)))


def looks_like_schedule(text: str) -> bool:
    """Frases que descrevem aula recorrente, não um evento único."""
    t = norm(text)
    if not find_weekdays(t):
        return False
    if re.search(r"\b(toda|todas|todo|todos)\b", t):
        return True
    start, end = find_times(t)
    return bool(start and end and re.search(r"\b(tenho|aula|aulas)\b", t))


_SEPARADORES = re.compile(r"\s*(?:,|;|\se\s|\+)\s*")


def split_materials(text: str) -> list[str]:
    """Quebra "cartolina, cola e tesoura" em itens de checklist (SPEC §139)."""
    trecho = re.search(r"\b(?:levar|trazer|comprar)\s+(.{3,120})", fold(text))
    if not trecho:
        return []
    inicio, fim = trecho.span(1)
    bruto = text[inicio:fim]
    bruto = re.split(r"\b(?:para|pra|no|na|em|amanha|amanhã|hoje|ate|até)\b", bruto, flags=re.IGNORECASE)[0]
    itens = [i.strip(" .,;") for i in _SEPARADORES.split(bruto)]
    return [i for i in itens if len(i) > 1][:10]
