"""Vocabulário acadêmico brasileiro — a base de conhecimento do produto.

Isto é o que faz o software entender sem pagar por token. Um LLM sabe que "p1"
é prova porque viu a internet inteira; nós sabemos porque está escrito aqui, e
consultar esta tabela custa microssegundos e zero centavo.

O conteúdo é deliberadamente **brasileiro e informal**: é o jeito que o
estudante escreve no WhatsApp às onze da noite, não o jeito que a secretaria
acadêmica escreve no edital. Inclui gíria ("trampo"), abreviação de celular
("tb", "pq"), o vocabulário de cada nível (o "tema de casa" do fundamental é a
"lista" do médio e o "trabalho" da faculdade) e o vocabulário regional.

Como crescer esta base: acrescente termo aqui e escreva o teste junto. Não
existe "termo pequeno demais" — cada um deles é uma vez que o usuário não vai
ouvir "não entendi".
"""
from __future__ import annotations

from agenda.core.text import norm
from agenda.knowledge import fuzzy
from agenda.models import EventType

# --------------------------------------------------------------------------- #
# Tipos de atividade → como as pessoas realmente chamam
# --------------------------------------------------------------------------- #
# A ordem da lista define a prioridade quando dois termos aparecem na mesma
# frase: "prova de trabalho de campo" é prova.
TYPE_TERMS: list[tuple[str, tuple[str, ...]]] = [
    (EventType.SIMULATION.value, (
        "simulado", "simuladao", "simulado enem", "mock", "prova modelo",
    )),
    (EventType.EXAM.value, (
        "prova", "provinha", "provao", "exame", "avaliacao", "avaliacao parcial",
        "teste", "testinho", "p1", "p2", "p3", "g1", "g2", "g3", "a1", "a2",
        "n1", "n2", "sub", "substitutiva", "segunda chamada", "recuperacao",
        "recup", "rec", "final", "prova final", "bimestral", "trimestral",
        "semestral", "vestibular", "enem", "concurso", "oral", "prova oral",
        "arguicao", "banca",
    )),
    (EventType.QUIZ.value, (
        "quiz", "questionario", "atividade online", "atividade do ava",
        "atividade no moodle", "formulario",
    )),
    (EventType.SEMINAR.value, (
        "seminario", "seminário", "mesa redonda", "roda de conversa", "colóquio",
        "coloquio", "palestra",
    )),
    (EventType.PRESENTATION.value, (
        "apresentacao", "apresentar", "apresentaçao", "slide", "slides",
        "pitch", "defesa", "banca de tcc", "expor", "exposicao",
    )),
    (EventType.PAPER.value, (
        "artigo", "paper", "fichamento", "resenha", "resumo", "redacao",
        "dissertacao", "ensaio", "abstract", "resumo expandido", "portfolio",
    )),
    (EventType.LAB.value, (
        "laboratorio", "lab", "pratica", "aula pratica", "relatorio de lab",
        "relatorio", "experimento", "pratica de campo", "aula de campo",
    )),
    (EventType.PROJECT.value, (
        "projeto", "tcc", "monografia", "trabalho de conclusao", "tese",
        "projeto integrador", "pi", "feira de ciencias", "maquete",
        "projeto final", "trabalho final",
    )),
    (EventType.INTERNSHIP.value, (
        "estagio", "estagio obrigatorio", "relatorio de estagio", "supervisao",
        "visita tecnica",
    )),
    (EventType.ASSIGNMENT.value, (
        "trabalho", "trampo", "tb", "entrega", "entregar", "atividade avaliativa",
        "atividade valendo nota", "valendo nota", "trabalho em grupo",
        "trabalho em dupla", "producao textual", "pesquisa",
    )),
    (EventType.HOMEWORK.value, (
        "tema", "tema de casa", "dever", "dever de casa", "licao", "licao de casa",
        "tarefa", "tarefa de casa", "exercicio", "exercicios", "lista",
        "lista de exercicios", "atividade", "atividades", "questoes",
        "caderno", "apostila", "para amanha",
    )),
    (EventType.READING.value, (
        "leitura", "ler", "capitulo", "capitulos", "cap", "livro", "texto",
        "obra literaria", "paradidatico", "resumir o capitulo",
    )),
    (EventType.MATERIAL.value, (
        "levar", "trazer", "comprar", "material", "cartolina", "cola", "tesoura",
        "regua", "jaleco", "uniforme", "tenis", "calculadora", "compasso",
        "transferidor", "pincel", "tinta", "eva", "isopor", "caderno novo",
        "canetinha", "lapis de cor", "garrafa", "lanche",
    )),
    (EventType.SCHOOL_EVENT.value, (
        "reuniao de pais", "reuniao", "festa junina", "gincana", "formatura",
        "excursao", "passeio", "olimpiada", "obmep", "sarau", "mostra cultural",
        "semana academica", "recesso", "feriado", "conselho de classe",
    )),
    (EventType.ADMINISTRATIVE.value, (
        "matricula", "rematricula", "prazo", "inscricao", "boleto", "mensalidade",
        "pagamento", "documento", "declaracao", "historico", "requerimento",
        "trancamento", "colacao de grau", "escolha de disciplinas",
    )),
    (EventType.CLASS.value, (
        "aula", "aulao", "revisao", "monitoria", "plantao de duvidas", "reforco",
        "orientacao", "encontro", "tutoria",
    )),
]

# Índice invertido termo → tipo, montado uma vez.
_TERM_TO_TYPE: dict[str, str] = {}
for _tipo, _termos in TYPE_TERMS:
    for _t in _termos:
        _TERM_TO_TYPE.setdefault(norm(_t), _tipo)

# --------------------------------------------------------------------------- #
# Matérias comuns e como as pessoas as encurtam
# --------------------------------------------------------------------------- #
# Usado quando o usuário cita uma matéria que ainda não cadastrou: em vez de
# criar "mat" como nome de disciplina, o sistema sugere "Matemática".
SUBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "Matemática": ("mat", "mtm", "matema", "matematica", "calculo basico", "exatas"),
    "Português": ("port", "pt", "portuga", "lingua portuguesa", "gramatica", "literatura"),
    "História": ("hist", "hstoria"),
    "Geografia": ("geo", "geografia"),
    "Ciências": ("cien", "ciencias", "cie"),
    "Física": ("fis", "fisica"),
    "Química": ("quim", "qui", "quimica"),
    "Biologia": ("bio", "biologia"),
    "Filosofia": ("filo", "filosofia"),
    "Sociologia": ("socio", "sociologia", "soc"),
    "Inglês": ("ing", "ingles", "english"),
    "Espanhol": ("esp", "espanhol", "espanol"),
    "Educação Física": ("edfis", "ed fisica", "ed fis", "efi", "educacao fisica"),
    "Artes": ("arte", "artes", "art"),
    "Redação": ("red", "redacao"),
    "Ensino Religioso": ("religiao", "ensino religioso"),
    "Cálculo": ("calc", "calculo"),
    "Álgebra Linear": ("alglin", "algebra linear", "al"),
    "Estatística": ("estat", "estatistica"),
    "Programação": ("prog", "programacao", "codigo"),
    "Banco de Dados": ("bd", "banco de dados"),
    "Direito Constitucional": ("constitucional", "dconst"),
    "Direito Civil": ("civil", "dcivil"),
    "Direito Penal": ("penal", "dpenal"),
    "Anatomia": ("anato", "anatomia"),
    "Contabilidade": ("conta", "contabilidade", "cont"),
    "Administração": ("adm", "administracao"),
    "Metodologia Científica": ("metodologia", "metod", "metodologia cientifica"),
}

_ALIAS_TO_SUBJECT: dict[str, str] = {}
for _nome, _apelidos in SUBJECT_ALIASES.items():
    _ALIAS_TO_SUBJECT[norm(_nome)] = _nome
    for _a in _apelidos:
        _ALIAS_TO_SUBJECT.setdefault(norm(_a), _nome)

# --------------------------------------------------------------------------- #
# Palavras que mudam a leitura da frase
# --------------------------------------------------------------------------- #
# Negação: "não tem aula amanhã" não é criar aula, é cancelar/informar.
NEGATIONS = ("nao ", "nao,", "nao.", "n tem", "nao tem", "sem ", "cancelou",
             "cancelada", "cancelado", "adiou", "adiada", "adiado", "foi adiada")

# Urgência declarada — sobe a prioridade do lembrete.
URGENCY = ("urgente", "importante", "nao posso esquecer", "vale nota",
           "vale muito", "peso 2", "peso 3", "decisiva", "final")

# Marcas de dúvida: quem pergunta não quer criar nada.
QUESTION_MARKERS = ("o que", "oq", "qnd", "quando", "quais", "quanto", "tem algo",
                    "tem alguma", "sera que", "me lembra", "lembra o que",
                    "como esta", "como ta", "cade", "onde")

# Abreviações de celular que a normalização precisa expandir antes de tudo.
CHAT_ABBREVIATIONS = {
    "vc": "voce", "vcs": "voces", "tb": "tambem", "tbm": "tambem", "pq": "porque",
    "q": "que", "qnd": "quando", "qdo": "quando", "hj": "hoje", "amanha": "amanha",
    "amnh": "amanha", "amh": "amanha", "ontem": "ontem", "dps": "depois",
    "msm": "mesmo", "blz": "beleza", "vlw": "valeu", "obg": "obrigado",
    "prof": "professor", "profa": "professora", "mt": "muito", "mto": "muito",
    "add": "adicionar", "add.": "adicionar", "n": "nao", "nao": "nao",
    "pfv": "por favor", "pfvr": "por favor", "sla": "sei la", "tá": "esta",
    "ta": "esta", "to": "estou", "tô": "estou", "vou": "vou", "eh": "e",
}


def expand_chat(texto: str) -> str:
    """Expande abreviação de celular preservando o resto da frase.

    "tenho q entregar o tb de bio amanha" → "tenho que entregar o trabalho de
    bio amanha". Roda antes de qualquer casamento, porque "q" isolado não casa
    com nada, mas "que" casa.
    """
    saida = []
    for palavra in norm(texto).split():
        limpa = palavra.strip(".,!?;:")
        sufixo = palavra[len(limpa):]
        saida.append(CHAT_ABBREVIATIONS.get(limpa, limpa) + sufixo)
    return " ".join(saida)


def event_type_of(termo: str, *, limiar: float = fuzzy.LIMIAR_MINIMO) -> tuple[str | None, float]:
    """Tipo de atividade a partir de uma palavra, tolerando erro de escrita."""
    chave = norm(termo)
    if not chave:
        return None, 0.0
    if chave in _TERM_TO_TYPE:
        return _TERM_TO_TYPE[chave], 1.0
    valor, score, _ = fuzzy.best_match(chave, _TERM_TO_TYPE, limiar=limiar)
    return valor, score


def find_event_type(frase: str) -> tuple[str | None, str, float]:
    """Varre a frase inteira e devolve `(tipo, termo_encontrado, score)`.

    Percorre na ordem de prioridade do catálogo, não na ordem da frase: se
    aparecem "prova" e "trabalho", prova vence, porque errar isso é marcar
    estudo de menos para a coisa que mais pesa.
    """
    texto = expand_chat(frase)
    for tipo, termos in TYPE_TERMS:
        for termo in termos:
            alvo = norm(termo)
            if _contem_termo(texto, alvo):
                return tipo, termo, 1.0

    # Nada exato: tenta som/escrita palavra a palavra ("provq", "trabaio").
    melhor: tuple[str | None, str, float] = (None, "", 0.0)
    for palavra in texto.split():
        if len(palavra) < 4:
            continue
        tipo, score = event_type_of(palavra, limiar=0.86)
        if tipo and score > melhor[2]:
            melhor = (tipo, palavra, score)
    return melhor


def canonical_subject(termo: str) -> tuple[str | None, float]:
    """Nome canônico de uma matéria conhecida — "bio" → "Biologia"."""
    chave = norm(termo)
    if not chave:
        return None, 0.0
    if chave in _ALIAS_TO_SUBJECT:
        return _ALIAS_TO_SUBJECT[chave], 1.0
    valor, score, _ = fuzzy.best_match(chave, _ALIAS_TO_SUBJECT, limiar=0.86)
    return valor, score


def has_negation(frase: str) -> bool:
    texto = " " + expand_chat(frase) + " "
    return any(marca in texto for marca in NEGATIONS)


def is_urgent(frase: str) -> bool:
    texto = expand_chat(frase)
    return any(marca in texto for marca in URGENCY)


def looks_like_question(frase: str) -> bool:
    texto = expand_chat(frase)
    return "?" in frase or any(texto.startswith(m) or f" {m}" in texto
                               for m in QUESTION_MARKERS)


def _contem_termo(texto: str, termo: str) -> bool:
    """Casamento respeitando fronteira de palavra, sem regex por termo."""
    if " " in termo:
        return termo in texto
    return termo in texto.split()


def size() -> dict[str, int]:
    """Tamanho da base — usado no painel e nos testes de regressão."""
    return {
        "termos_de_tipo": len(_TERM_TO_TYPE),
        "apelidos_de_materia": len(_ALIAS_TO_SUBJECT),
        "abreviacoes": len(CHAT_ABBREVIATIONS),
    }
