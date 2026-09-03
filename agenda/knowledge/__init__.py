"""Base de conhecimento própria — a inteligência que não depende de API.

A ideia é simples e é o que barateia o produto: **o agente é o raciocínio, o
conhecimento é nosso**. Em vez de mandar tudo para um LLM e pagar por token
para ele descobrir que "p1" é prova e que "cauculo" é Cálculo, o software já
sabe. O modelo externo só entra quando sobra ambiguidade real.

Camadas, da mais barata para a mais cara:

1. `phonetics` — como a palavra soa em português brasileiro. Resolve quem
   escreve como fala e o que a transcrição de áudio erra.
2. `fuzzy` — quanto dois termos se parecem, combinando escrita e som.
3. `lexicon` — vocabulário acadêmico curado: gírias, abreviações, sinônimos.
4. `store` — o que este usuário específico já ensinou ao sistema.
5. `retrieval` — recupera só o que interessa para a frase, para o prompt ficar
   pequeno quando o LLM realmente for necessário.
"""
