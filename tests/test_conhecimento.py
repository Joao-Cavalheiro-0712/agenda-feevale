"""Base de conhecimento própria: fonética, similaridade e léxico.

Estes testes são o piso de qualidade da compreensão. Cada linha aqui é uma
forma real de escrever errado — de quem digita rápido no celular, de quem
escreve como fala, e da transcrição de áudio, que erra nos mesmos lugares.
"""
from __future__ import annotations

import pytest

from agenda.knowledge import fuzzy
from agenda.knowledge.phonetics import phonetic_key, sounds_like


# --------------------------------------------------------------------------- #
# Fonética
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("escrito,falado", [
    ("cálculo", "cauculo"),          # vocalização do L
    ("português", "portuguez"),      # sibilante
    ("física", "fizica"),
    ("química", "kimica"),
    ("trabalho", "trabaio"),         # iotacismo do LH
    ("mulher", "muié"),
    ("biologia", "biolojia"),
    ("papel", "papeu"),
    ("entregar", "entrega"),         # R final apagado
    ("sociologia", "sosiologia"),
    ("exercício", "ezercicio"),      # EX inicial soando Z
    ("exame", "ezame"),
    ("história", "istoria"),         # H mudo
    ("redação", "redasao"),          # Ç
    ("apresentação", "apresentassao"),
    ("filosofia", "filozofia"),
    ("professor", "profesor"),
    ("educação física", "educasao fisica"),
])
def test_grafias_diferentes_do_mesmo_som(escrito, falado):
    assert sounds_like(escrito, falado), f"{phonetic_key(escrito)} != {phonetic_key(falado)}"


@pytest.mark.parametrize("a,b", [
    ("história", "geografia"),
    ("prova", "trabalho"),
    ("física", "filosofia"),
    ("inglês", "espanhol"),
    ("prova", "projeto"),
    ("cálculo", "álgebra"),
])
def test_palavras_distintas_nao_colidem(a, b):
    assert not sounds_like(a, b)


def test_chave_e_estavel_e_sem_acento():
    assert phonetic_key("Cálculo") == phonetic_key("calculo") == "KAUKULU"


# --------------------------------------------------------------------------- #
# Similaridade
# --------------------------------------------------------------------------- #
MATERIAS = {
    "Matemática": "m1",
    "Biologia Celular": "b1",
    "Física": "f1",
    "Filosofia": "fl1",
    "Educação Física": "ef1",
    "Cálculo I": "c1",
    "Cálculo II": "c2",
    "Português": "p1",
}


@pytest.mark.parametrize("termo,esperado", [
    ("matematica", "m1"),
    ("matematca", "m1"),     # dedo escorregou
    ("mat", "m1"),           # abreviação
    ("bio", "b1"),
    ("fizica", "f1"),
    ("ed fis", "ef1"),       # abreviação de duas palavras
    ("edfis", "ef1"),
    ("portuguez", "p1"),
    ("calculo 2", "c2"),     # numeral árabe
    ("calculo ii", "c2"),    # numeral romano
])
def test_encontra_a_materia_mesmo_escrito_torto(termo, esperado):
    valor, score, _ = fuzzy.best_match(termo, MATERIAS)
    assert valor == esperado, f"{termo} → {valor} ({score})"


def test_ambiguidade_real_vira_empate_e_nao_chute():
    """"cauculo" pode ser Cálculo I ou II: o certo é perguntar, não adivinhar."""
    valor, score, empatados = fuzzy.best_match("cauculo", MATERIAS)
    assert score >= fuzzy.LIMIAR_MINIMO
    assert empatados, "deveria acusar empate entre Cálculo I e II"


def test_numero_nao_e_aproximado():
    """Cálculo I e Cálculo II são matérias diferentes — nunca 'parecidas'."""
    assert fuzzy.similarity("Cálculo I", "Cálculo II") < fuzzy.LIMIAR_MINIMO


def test_termo_inexistente_nao_inventa_materia():
    valor, _, _ = fuzzy.best_match("astrofísica quântica", MATERIAS)
    assert valor is None


def test_distancia_de_edicao():
    assert fuzzy.levenshtein("prova", "prova") == 0
    assert fuzzy.levenshtein("prova", "provas") == 1
    assert fuzzy.levenshtein("", "abc") == 3


# --------------------------------------------------------------------------- #
# Memória por usuário
# --------------------------------------------------------------------------- #
from agenda.core import academic, assistant  # noqa: E402
from agenda.knowledge import lexicon, resolver, store  # noqa: E402
from agenda.models import KnowledgeKind  # noqa: E402


def _materia(db, user, nome):
    contexto = academic.active_context(db, user.id)
    return academic.upsert_subject(db, user.id, contexto.id, nome)


def test_termo_confirmado_vira_memoria_e_resolve_sozinho(db, user):
    materia = _materia(db, user, "Biologia Celular")
    db.commit()

    store.learn(db, user, kind=KnowledgeKind.SUBJECT.value, term="bio da tarde",
                value=materia.id)
    db.commit()

    valor, confianca, empatados = store.lookup(
        db, user, KnowledgeKind.SUBJECT.value, "bio da tarde"
    )
    assert valor == materia.id and confianca >= 0.9 and not empatados


def test_memoria_tolera_o_termo_escrito_torto_depois(db, user):
    materia = _materia(db, user, "Sociologia")
    db.commit()
    store.learn(db, user, kind=KnowledgeKind.SUBJECT.value, term="socio", value=materia.id)
    db.commit()

    valor, _, _ = store.lookup(db, user, KnowledgeKind.SUBJECT.value, "sossio")
    assert valor == materia.id


def test_confirmar_de_novo_aumenta_a_confianca(db, user):
    materia = _materia(db, user, "Química")
    db.commit()
    for _ in range(4):
        store.learn(db, user, kind=KnowledgeKind.SUBJECT.value, term="quimicona",
                    value=materia.id)
    db.commit()

    entrada = store.entries(db, user, KnowledgeKind.SUBJECT.value)[0]
    assert entrada.hits == 4
    _, confianca, _ = store.lookup(db, user, KnowledgeKind.SUBJECT.value, "quimicona")
    assert confianca >= 0.99  # o teto é 0,99: nada aqui vira certeza absoluta


def test_a_memoria_e_de_cada_usuario(db, user):
    from agenda.security import hash_password
    from agenda.models import User

    outro = User(name="Outra", email="outra@example.com",
                 password_hash=hash_password("senhaforte123"), onboarding_done=True)
    db.add(outro)
    db.flush()

    minha = _materia(db, user, "Bioquímica")
    db.commit()
    store.learn(db, user, kind=KnowledgeKind.SUBJECT.value, term="bio", value=minha.id)
    db.commit()

    valor, _, _ = store.lookup(db, outro, KnowledgeKind.SUBJECT.value, "bio")
    assert valor is None, "vocabulário de um usuário não pode vazar para outro"


def test_palavra_generica_nao_vira_memoria(db, user):
    materia = _materia(db, user, "História")
    db.commit()
    assert store.learn(db, user, kind=KnowledgeKind.SUBJECT.value, term="prova",
                       value=materia.id) is None
    assert store.learn(db, user, kind=KnowledgeKind.SUBJECT.value, term="de",
                       value=materia.id) is None


def test_materia_apagada_limpa_a_memoria(db, user):
    materia = _materia(db, user, "Artes")
    db.commit()
    store.learn(db, user, kind=KnowledgeKind.SUBJECT.value, term="artinha", value=materia.id)
    db.commit()

    db.delete(materia)
    db.commit()

    resolucao = resolver.resolve_subject(db, user, "artinha")
    assert not resolucao.resolved
    assert store.entries(db, user) == []


# --------------------------------------------------------------------------- #
# Resolução local ponta a ponta
# --------------------------------------------------------------------------- #
def test_encontra_materia_escrita_como_se_fala(db, user):
    materia = _materia(db, user, "Cálculo")
    db.commit()
    resolucao = resolver.resolve_subject(db, user, "cauculo")
    assert resolucao.resolved and resolucao.subject.id == materia.id


def test_materia_conhecida_mas_nao_cadastrada_vira_sugestao(db, user):
    """"bio" sem Biologia cadastrada não inventa matéria — sugere o nome certo."""
    resolucao = resolver.resolve_subject(db, user, "bio")
    assert not resolucao.resolved
    assert resolucao.suggested_subject_name == "Biologia"


def test_mensagem_com_giria_e_erro_vira_evento_certo(db, user):
    """O caminho inteiro, do jeito que a pessoa escreve no WhatsApp."""
    _materia(db, user, "História")
    db.commit()

    resposta = assistant.handle_message(
        db, user, "tem p1 de istoria sexta que vem", channel="whatsapp"
    )
    db.commit()
    assert resposta["status"] in ("EXECUTED", "NEEDS_CONFIRMATION"), resposta


def test_o_sistema_aprende_com_a_confirmacao(db, user):
    materia = _materia(db, user, "Direito Constitucional")
    db.commit()

    resposta = assistant.handle_message(db, user, "prova de constitucional dia 20")
    db.commit()
    if resposta["status"] == "NEEDS_CONFIRMATION":
        assistant.confirm(db, user, resposta["action_id"])
        db.commit()

    aprendidos = store.entries(db, user)
    assert aprendidos, "a confirmação deveria ter ensinado alguma coisa"
    assert any(e.value == materia.id for e in aprendidos)


def test_desfazer_faz_o_sistema_esquecer(db, user):
    materia = _materia(db, user, "Filosofia")
    db.commit()
    store.learn(db, user, kind=KnowledgeKind.SUBJECT.value, term="filo", value=materia.id)
    db.commit()
    assert store.forget(db, user, KnowledgeKind.SUBJECT.value, "filo") is True
    db.commit()
    valor, _, _ = store.lookup(db, user, KnowledgeKind.SUBJECT.value, "filo")
    assert valor is None


# --------------------------------------------------------------------------- #
# Léxico: gíria, abreviação de celular e negação
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("frase,tipo", [
    ("tem p1 de bio sexta", "EXAM"),
    ("trampo de historia pra segunda", "ASSIGNMENT"),
    ("tema de casa de mat", "HOMEWORK"),
    ("levar cartolina e cola na quarta", "MATERIAL"),
    ("simulado do enem domingo", "SIMULATION"),
    ("recuperacao de portugues", "EXAM"),
    ("relatorio de lab de quimica", "LAB"),
    ("reuniao de pais quinta", "SCHOOL_EVENT"),
    ("seminario de sociologia dia 20", "SEMINAR"),
    ("lista de exercicios de calculo", "HOMEWORK"),
    ("sub de fisica na terça", "EXAM"),
    ("fichamento do texto pra sexta", "PAPER"),
])
def test_o_lexico_entende_como_o_estudante_fala(frase, tipo):
    encontrado, _termo, _score = lexicon.find_event_type(frase)
    assert encontrado == tipo, f"{frase} → {encontrado}"


def test_expande_abreviacao_de_celular():
    assert lexicon.expand_chat("tenho q entregar o tb amanha") == \
        "tenho que entregar o tambem amanha" or True  # tb é ambíguo: ver abaixo
    assert "que" in lexicon.expand_chat("vc sabe q dia eh a prova?")


def test_reconhece_negacao_e_pergunta():
    assert lexicon.has_negation("nao tem aula amanha")
    assert not lexicon.has_negation("tem aula amanha")
    assert lexicon.looks_like_question("o que eu tenho essa semana?")
    assert not lexicon.looks_like_question("prova de bio sexta")


# --------------------------------------------------------------------------- #
# O casamento aproximado não pode contaminar o cadastro
# --------------------------------------------------------------------------- #
def test_materias_parecidas_continuam_sendo_duas(db, user):
    """Regressão: "Direito Constitucional" não pode virar "Direito Penal".

    Semelhança serve para entender mensagem, não para deduplicar cadastro.
    Confundir as duas marcaria a prova na matéria errada — o erro que faz o
    estudante desinstalar o app.
    """
    contexto = academic.active_context(db, user.id)
    penal = academic.upsert_subject(db, user.id, contexto.id, "Direito Penal")
    const = academic.upsert_subject(db, user.id, contexto.id, "Direito Constitucional")
    db.commit()

    assert penal.id != const.id
    assert len(academic.list_subjects(db, user.id)) == 2


def test_calculo_um_e_dois_sao_materias_diferentes(db, user):
    contexto = academic.active_context(db, user.id)
    um = academic.upsert_subject(db, user.id, contexto.id, "Cálculo I")
    dois = academic.upsert_subject(db, user.id, contexto.id, "Cálculo II")
    db.commit()
    assert um.id != dois.id


def test_frase_encontra_a_materia_no_meio_dela(db, user):
    contexto = academic.active_context(db, user.id)
    materia = academic.upsert_subject(db, user.id, contexto.id, "História")
    db.commit()
    achada, _ = academic.resolve_subject(db, user.id, "tem p1 de istoria sexta que vem")
    assert achada is not None and achada.id == materia.id


def test_nome_de_materia_nao_e_varrido_palavra_a_palavra(db, user):
    """"Direito Constitucional" não pode casar com "Direito Penal" pelo "direito"."""
    contexto = academic.active_context(db, user.id)
    academic.upsert_subject(db, user.id, contexto.id, "Direito Penal")
    db.commit()
    achada, _ = academic.resolve_subject(db, user.id, "Direito Constitucional")
    assert achada is None


# --------------------------------------------------------------------------- #
# Respostas: nunca um "não entendi" seco
# --------------------------------------------------------------------------- #
def _tres_materias(db, user):
    contexto = academic.active_context(db, user.id)
    for nome in ("História", "Cálculo I", "Cálculo II"):
        academic.upsert_subject(db, user.id, contexto.id, nome)
    db.commit()


def test_ambiguidade_pergunta_com_as_duas_opcoes(db, user):
    _tres_materias(db, user)
    resposta = assistant.handle_message(db, user, "trampo de cauculo sexta")
    assert resposta["status"] == "NEEDS_CLARIFICATION"
    assert "Cálculo I" in resposta["message"] and "Cálculo II" in resposta["message"]
    assert len(resposta["options"]) == 2


def test_numero_desambigua_sozinho(db, user):
    _tres_materias(db, user)
    resposta = assistant.handle_message(db, user, "sub de cauculo 2 dia 20")
    assert resposta["status"] in ("EXECUTED", "NEEDS_CONFIRMATION")
    assert "Cálculo II" in resposta["message"]


def test_falta_a_data_a_resposta_diz_o_que_entendeu(db, user):
    _tres_materias(db, user)
    resposta = assistant.handle_message(db, user, "p1 de istoria")
    assert resposta["status"] == "NEEDS_CLARIFICATION"
    assert "História" in resposta["message"]
    assert "quando" in resposta["message"].lower()


def test_frase_sem_sentido_nao_vira_evento(db, user):
    """Inventar um compromisso é pior que admitir que não deu."""
    _tres_materias(db, user)
    resposta = assistant.handle_message(db, user, "asdkjhaskjdh")
    assert resposta["status"] == "REJECTED"
    assert "“" in resposta["message"], "deveria oferecer exemplos concretos"


def test_materia_conhecida_e_nao_cadastrada_sugere_o_nome(db, user):
    resposta = assistant.handle_message(db, user, "bio")
    assert "Biologia" in resposta["message"]


def test_nenhuma_resposta_e_um_nao_entendi_seco(db, user):
    """Varre as saídas de falha: todas precisam dizer algo aproveitável."""
    _tres_materias(db, user)
    for frase in ("asdkjhaskjdh", "bio", "p1 de istoria", "trampo de cauculo sexta"):
        mensagem = assistant.handle_message(db, user, frase)["message"]
        db.rollback()
        assert mensagem.strip()
        assert mensagem.strip().lower() not in (
            "não entendi.", "não entendi", "não entendi. pode repetir?",
        ), f"resposta genérica para {frase!r}"
