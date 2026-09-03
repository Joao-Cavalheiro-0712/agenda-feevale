# Base de conhecimento — inteligência sem pagar por token

A regra do produto é: **o agente é o raciocínio, o conhecimento é nosso.**

Um LLM sabe que "p1" é prova porque leu a internet inteira. Nós sabemos porque
está escrito em `agenda/knowledge/lexicon.py`, e consultar isso custa
microssegundos e zero centavo. Toda vez que o software resolve sozinho, é uma
chamada de modelo que não aconteceu — e uma resposta que chega instantânea,
inclusive quando o provedor de IA está fora do ar.

## As cinco camadas, da mais barata para a mais cara

| # | Camada | O que resolve | Custo |
|---|---|---|---|
| 1 | `store` — memória do usuário | termos que **esta pessoa** já confirmou | grátis, uma consulta indexada |
| 2 | cadastro dele | nome, apelido e abreviação das matérias | grátis |
| 3 | `phonetics` + `fuzzy` | escrita torta e erro de transcrição de áudio | grátis, em memória |
| 4 | `lexicon` | vocabulário acadêmico brasileiro (198 termos) | grátis, tabela em código |
| 5 | modelo externo | só o que sobrou — e com prompt reduzido por `retrieval` | pago |

O interpretador tenta a camada local primeiro (`interpreter.LOCAL_SUFICIENTE`
= 0,85). Só quando ela não fecha com confiança é que o modelo é chamado. Esse
número é o botão que governa o custo de IA do produto: subir economiza e erra
mais, baixar paga por respostas que já tínhamos.

## Fonética do português brasileiro

O erro de escrita no Brasil não é aleatório: ele segue a fonologia da fala. E a
transcrição de áudio erra **nos mesmos lugares**, porque também está ouvindo.
`phonetics.py` normaliza esses eixos:

| Fenômeno | Exemplo | Chave |
|---|---|---|
| vocalização do L | cálculo ~ cauculo | `KAUKULU` |
| iotacismo do LH | trabalho ~ trabaio | `TRABAIU` |
| vogal átona final | livro ~ livru, noite ~ noiti | vogais em 3 classes: A, I, U |
| R final apagado | entregar ~ entregá | `INTRIGA` |
| sibilantes fundidas | sociologia ~ sosiologia, casa ~ caza | tudo vira `S` |
| EX inicial | exame ~ ezame | `ISAMI` |
| Ç, dígrafos, H mudo | redação ~ redasao, história ~ istoria | `RIDASAU`, `ISTURIA` |

Fundir sibilantes perde a diferença entre "caçar" e "casar". É deliberado:
essa distinção não existe no nosso domínio, e fundi-las salva todo mundo que
escreve "sosiologia" ou "presiso".

## O que a semelhança nunca pode fazer

Casamento aproximado serve para **entender mensagem**. Nunca para **decidir
identidade**. Três regras existem só por causa disso, cada uma com teste de
regressão:

1. **Número é exato.** "Cálculo I" e "Cálculo II" jamais são parecidos. Marcar
   a prova na matéria irmã é o erro que faz o estudante desinstalar o app.
2. **Prefixo, não "contém".** "astrofísica" contém "física" e é outra área.
3. **Criar matéria usa identidade, não semelhança.** `upsert_subject` compara
   só o nome próprio: os apelidos são gerados automaticamente e colidem
   ("Cálculo I" e "Cálculo II" geram o mesmo "calculo"). Usar apelido como
   critério de identidade faria a segunda matéria nunca ser criada.

E quando dois candidatos empatam, o sistema **pergunta** — com as opções na
tela, não com um "não entendi".

## Aprender e desaprender

Cada ação executada com sucesso ensina: o termo que a pessoa usou vira uma
linha em `knowledge_entries` apontando para a matéria ou o tipo. Aprender
acontece na **execução**, nunca na interpretação — proposta que o usuário
cancelou não pode virar conhecimento, senão o sistema decoraria o próprio erro
e o repetiria com mais convicção. Simétrico: **desfazer esquece**.

A memória é por usuário. "bio" é Biologia Celular para um e Bioquímica para
outro; misturar pioraria os dois. Termos genéricos ("prova", "de", "aula") são
recusados na entrada — aprendê-los envenenaria a base.

## Recuperação: prompt pequeno

`retrieval.py` monta o contexto **pela mensagem**, não pela conta inteira. Com
20 matérias cadastradas, o bloco cai ~40%; quanto mais matérias, maior o ganho.
E a precisão sobe junto: o modelo escolhe entre 6 matérias plausíveis em vez de
40. O contexto amplo (`ai/context.py`) segue existindo para leitura de
documento, onde não há uma frase para focar.

O prompt também leva o vocabulário do próprio usuário ("como este estudante
costuma falar"), o que ensina o modelo a língua dele sem exemplo fabricado.

## Nunca um "não entendi" seco

Devolver "não entendi" empurra o trabalho de volta para quem já escreveu uma
vez. Toda saída de falha agora diz **o que foi reconhecido** e pede **a peça
que falta**:

| Mensagem | Resposta |
|---|---|
| `p1 de istoria` | Anotei “Prova de História” em História. Para quando é? |
| `trampo de cauculo sexta` | É de Cálculo I ou Cálculo II? *(com os dois botões)* |
| `bio` (sem Biologia cadastrada) | Você quis dizer Biologia? Ainda não tenho essa matéria… |
| `asdkjhaskjdh` | Não peguei essa. Pode mandar assim: “…” *(exemplos do nível dele)* |

E, quando nada é reconhecido, o sistema **não inventa um compromisso**.
Fabricar uma linha sem sentido na agenda de alguém é pior que admitir a falha.

## Como crescer a base

Acrescente o termo em `lexicon.py` e escreva o teste junto, em
`tests/test_conhecimento.py`. Não existe "termo pequeno demais": cada um é uma
vez que alguém não vai ouvir "não entendi". Os testes de fonética são a rede —
cada linha ali é uma forma real de escrever errado.
