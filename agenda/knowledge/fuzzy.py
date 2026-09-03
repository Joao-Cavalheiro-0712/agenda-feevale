"""Quanto dois termos se parecem — por escrita e por som.

Devolve sempre um número entre 0 e 1, e quem chama decide o que fazer com ele.
Essa separação importa: o motor de ações já tem uma política de confiança
(≥0,90 executa, 0,70–0,89 confirma, abaixo pergunta), e a similaridade
alimenta essa política em vez de inventar outra.

A pontuação combina quatro sinais, do mais forte para o mais fraco:

1. igualdade exata depois de normalizar (1,00);
2. mesma chave fonética — "cauculo" e "cálculo" (0,94);
3. prefixo ou abreviação — "mat" para "matemática" (0,88 a 0,92);
4. distância de edição normalizada, com um empurrãozinho quando as chaves
   fonéticas também são próximas.

Para expressões de várias palavras, casa palavra a palavra e usa a melhor
combinação — "ed fis" encontra "Educação Física". E numeral romano vira
dígito, porque ninguém digita "Cálculo II" no celular: digita "calculo 2".

Números são comparados de forma estrita de propósito: "Cálculo I" e
"Cálculo II" **não** podem se parecer, ou o sistema marcaria a prova na
matéria errada — o tipo de erro que faz o aluno perder a confiança de vez.
"""
from __future__ import annotations

from agenda.core.text import norm
from agenda.knowledge.phonetics import phonetic_key

# Abaixo disto não é parecido, é coincidência.
LIMIAR_MINIMO = 0.72
# A partir daqui dá para resolver sozinho, sem perguntar.
LIMIAR_CONFIANTE = 0.88


def levenshtein(a: str, b: str) -> int:
    """Distância de edição clássica, iterativa (sem recursão, sem dependência)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        atual = [i]
        for j, cb in enumerate(b, start=1):
            atual.append(min(
                anterior[j] + 1,        # remoção
                atual[j - 1] + 1,       # inserção
                anterior[j - 1] + (ca != cb),  # substituição
            ))
        anterior = atual
    return anterior[-1]


def ratio(a: str, b: str) -> float:
    """Distância de edição normalizada pelo tamanho da maior string."""
    if not a and not b:
        return 1.0
    maior = max(len(a), len(b))
    if not maior:
        return 0.0
    return 1.0 - levenshtein(a, b) / maior


def is_abbreviation(curto: str, longo: str) -> bool:
    """"mat" para "matemática", "edfis" para "educação física"."""
    if len(curto) < 2 or len(curto) >= len(longo):
        return False
    if longo.startswith(curto):
        return True
    # Iniciais das palavras: "edf" para "educação física" ou "ed fis".
    iniciais = "".join(p[0] for p in longo.split() if p)
    if curto == iniciais and len(iniciais) >= 2:
        return True
    # Concatenação dos prefixos: "edfis" para "educação física".
    partes = longo.split()
    if len(partes) > 1:
        restante = curto
        for parte in partes:
            comum = 0
            while comum < len(restante) and comum < len(parte) and restante[comum] == parte[comum]:
                comum += 1
            if comum < 2:
                return False
            restante = restante[comum:]
        return not restante
    return False


_ROMANOS = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
}


def expand(texto: str) -> str:
    """Numeral romano vira dígito: "Cálculo II" e "cálculo 2" são a mesma coisa."""
    return " ".join(_ROMANOS.get(t, t) for t in norm(texto).split())


def _token_similarity(a: str, b: str) -> float:
    """Casamento palavra a palavra, para expressões de tamanhos diferentes."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 0.0
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)

    # Número só casa com número igual: "1" nunca é parecido com "2".
    def par(x: str, y: str) -> float:
        if x.isdigit() or y.isdigit():
            return 1.0 if x == y else 0.0
        return _similarity_simples(x, y)

    melhores = [max(par(t, outro) for outro in longo) for t in curto]
    media = sum(melhores) / len(melhores)
    # Palavra do lado longo que ficou sem par reduz um pouco a confiança:
    # "biologia" não é "biologia celular molecular".
    cobertura = len(curto) / len(longo)
    return round(media * (0.82 + 0.18 * cobertura), 4)


def _similarity_simples(a: str, b: str) -> float:
    """Similaridade de uma palavra contra outra, sem quebrar em tokens."""
    if a == b:
        return 1.0
    fa, fb = phonetic_key(a), phonetic_key(b)
    if fa and fa == fb:
        return 0.94
    if is_abbreviation(a, b) or is_abbreviation(b, a):
        return 0.92 if min(len(a), len(b)) >= 3 else 0.88
    # Prefixo, e não "contém": "astrofísica" contém "física" e é outra área.
    # Confundir as duas marcaria a prova na matéria errada.
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return 0.90
    return round(min(0.93, 0.4 * ratio(a, b) + 0.6 * ratio(fa, fb)), 4)


def similarity(a: str, b: str) -> float:
    """Similaridade entre dois termos, de 0 a 1."""
    na, nb = expand(a), expand(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    # Fonética e abreviação valem para a expressão inteira, atravessando o
    # espaço: "edfis" é "educação física", e isso o caminho por palavra não vê.
    fa, fb = phonetic_key(na), phonetic_key(nb)
    if fa and fa == fb:
        return 0.94
    if is_abbreviation(na.replace(" ", ""), nb) or is_abbreviation(nb.replace(" ", ""), na):
        return 0.92 if min(len(na), len(nb)) >= 3 else 0.88

    if " " in na or " " in nb:
        # Só o caminho por palavra: comparar as frases inteiras como uma string
        # faria "Cálculo I" e "Cálculo II" parecerem 89% iguais.
        return _token_similarity(na, nb)
    return _similarity_simples(na, nb)


def _pares(candidatos) -> list[tuple[str, str]]:
    """Aceita dicionário ou lista de pares.

    A lista de pares importa: dois objetos diferentes podem ter o MESMO termo
    ("calculo" é apelido de Cálculo I e de Cálculo II). Num dicionário um
    apagaria o outro e o empate — que é o que manda perguntar — desapareceria
    silenciosamente, fazendo o sistema escolher uma das duas no escuro.
    """
    if isinstance(candidatos, dict):
        return list(candidatos.items())
    return [(termo, valor) for termo, valor in candidatos]


def best_match(alvo: str, candidatos, *, limiar: float = LIMIAR_MINIMO):
    """Melhor candidato para `alvo`.

    `candidatos` é um mapa termo → identificador, ou uma lista de pares quando
    o mesmo termo pode apontar para objetos diferentes.
    Devolve `(identificador, score, empatados)`. Quando dois candidatos ficam
    a menos de 0,04 um do outro, é empate — e empate vira pergunta, nunca
    escolha às cegas.
    """
    pontuados = sorted(
        ((similarity(alvo, termo), termo, valor) for termo, valor in _pares(candidatos)),
        key=lambda t: -t[0],
    )
    if not pontuados or pontuados[0][0] < limiar:
        return None, 0.0, []

    melhor_score, _, melhor_valor = pontuados[0]
    empatados = [
        valor for score, _, valor in pontuados
        if score >= melhor_score - 0.04 and valor != melhor_valor
    ]
    return melhor_valor, melhor_score, list(dict.fromkeys(empatados))


def rank(alvo: str, candidatos, *, limite: int = 5, limiar: float = LIMIAR_MINIMO):
    """Candidatos ordenados por similaridade — usado para sugerir opções."""
    pontuados = [
        (score, valor)
        for score, valor in (
            (similarity(alvo, termo), valor) for termo, valor in _pares(candidatos)
        )
        if score >= limiar
    ]
    pontuados.sort(key=lambda t: -t[0])
    vistos: dict[str, float] = {}
    for score, valor in pontuados:
        vistos.setdefault(valor, score)
    return list(vistos.items())[:limite]
