"""Tour de primeiro acesso — o roteiro, separado da animação.

O conteúdo vive aqui e não no template por um motivo: o tour muda com o nível
de ensino. Quem está no 3º ano não precisa ouvir falar de "artigo para
submissão", e quem faz doutorado não quer ver "levar cartolina". O mesmo
roteiro, com o vocabulário de cada um — a mesma regra que rege o resto do
produto (`core/profiles.py`).

## O que um tour bom faz, e o que um tour ruim faz

Tour ruim descreve a interface: "este é o botão de menu, aqui ficam suas
matérias". Ninguém lembra disso trinta segundos depois.

Tour bom mostra **uma transformação**: a pessoa vê a bagunça que ela vive
virando a agenda organizada, e entende em dois segundos o que ganha. Por isso
cada cena aqui tem um "antes" (o problema que ela reconhece) e um "depois" (o
que o app faz com aquilo), e a última cena não explica nada: ela pede a
primeira ação.

E o mais importante: **dá para pular a qualquer momento**, sem ficar escondido
num "x" de 8 pixels. Tour que prende é tour que gera desinstalação.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cena:
    """Uma cena do tour.

    `arte` nomeia a ilustração animada correspondente no template; o texto
    nunca depende dela para fazer sentido, porque quem usa leitor de tela ou
    pediu menos animação recebe só o texto.
    """

    chave: str
    kicker: str
    titulo: str
    corpo: str
    arte: str
    # O que a pessoa reconhece como problema dela. Aparece antes do "depois".
    antes: str = ""
    depois: str = ""
    dica: str = ""


# --------------------------------------------------------------------------- #
# Roteiro base — serve a todo mundo, e cada nível troca as palavras
# --------------------------------------------------------------------------- #
def roteiro(perfil, *, nome: str = "") -> list[Cena]:
    """Monta o roteiro no vocabulário do nível do estudante."""
    exemplos = list(perfil.capture_examples) or ["prova de matemática sexta"]
    atividade = perfil.type_labels.get(perfil.default_type, "compromisso").lower()
    materia = perfil.subject_label.lower()
    primeiro_nome = (nome or "").split(" ")[0]

    return [
        Cena(
            chave="promessa",
            kicker="Em um minuto",
            titulo=(
                f"{primeiro_nome}, você manda. Eu organizo."
                if primeiro_nome else "Você manda. Eu organizo."
            ),
            corpo=(
                "Não existe formulário para preencher aqui. Você fala, escreve ou "
                "fotografa do jeito que já faz — e a agenda se monta sozinha."
            ),
            arte="promessa",
            antes="A informação chega bagunçada: quadro, áudio do grupo, PDF do portal.",
            depois="Ela sai organizada: data, matéria e lembrete no lugar certo.",
        ),
        Cena(
            chave="captura",
            kicker="O botão do meio",
            titulo="Um toque para grifar",
            corpo=(
                f"O “+” abre uma caixa que aceita tudo: texto, áudio, foto do quadro "
                f"e arquivo. Escreva do seu jeito — “{exemplos[0]}” já é suficiente."
            ),
            arte="captura",
            dica="Escrito torto, com abreviação ou áudio ruim: eu entendo do mesmo jeito.",
        ),
        Cena(
            chave="confirmacao",
            kicker="Antes de salvar",
            titulo="Eu mostro o que entendi",
            corpo=(
                f"Cada {atividade} aparece como vai ficar na sua agenda — data, "
                f"{materia}, horário — para você conferir de olho batido. Se eu errar, "
                "é um toque em “Desfazer”."
            ),
            arte="confirmacao",
            dica="Quando tenho dúvida, eu pergunto em vez de chutar uma data.",
        ),
        Cena(
            chave="agenda",
            kicker="O resultado",
            titulo="Sua semana, sem susto",
            corpo=(
                "A tela inicial mostra o que é de hoje e o que está chegando, na "
                "ordem que faz sentido para quem estuda como você."
            ),
            arte="agenda",
            depois="E o lembrete chega antes — não no dia, quando já não dá tempo.",
        ),
        Cena(
            chave="whatsapp",
            kicker="Sem abrir o app",
            titulo="Também funciona no WhatsApp",
            corpo=(
                "Conecte seu número e mande o áudio direto de lá, no meio da aula. "
                "Aparece aqui na hora."
            ),
            arte="whatsapp",
        ),
        Cena(
            chave="comeco",
            kicker="Agora é com você",
            titulo="Manda a primeira",
            corpo=(
                f"Pode ser qualquer coisa que você precisa lembrar. Por exemplo: "
                f"“{exemplos[-1]}”."
            ),
            arte="comeco",
        ),
    ]


# Cenas extras por público. Aparecem só para quem elas servem — o tour tem de
# ser curto, e cada cena a mais é uma pessoa a menos chegando no fim.
def cenas_extras(perfil) -> list[Cena]:
    extras: list[Cena] = []
    from agenda.core.profiles import FEATURE_GUARDIAN

    if FEATURE_GUARDIAN in getattr(perfil, "features", frozenset()):
        extras.append(Cena(
            chave="familia",
            kicker="Para a família",
            titulo="Seu responsável pode acompanhar",
            corpo=(
                "Você decide o que ele vê e pode encerrar quando quiser. "
                "A conta continua sendo sua."
            ),
            arte="familia",
        ))
    return extras


def para(perfil, *, nome: str = "") -> list[Cena]:
    """Roteiro final: base + extras do público, com a última cena por último."""
    base = roteiro(perfil, nome=nome)
    extras = cenas_extras(perfil)
    if not extras:
        return base
    return base[:-1] + extras + base[-1:]
