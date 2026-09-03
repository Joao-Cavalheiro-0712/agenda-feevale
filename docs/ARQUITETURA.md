# Arquitetura

## Regra número um

O web app e o WhatsApp **não têm duas lógicas diferentes** (SPEC §144). Todo
input — digitado, falado, fotografado ou recebido por webhook — percorre o
mesmo caminho:

```
Entrada (web · WhatsApp · Telegram · documento)
        │
        ▼
  normalização            agenda/core/phone.py, agenda/ingest/text_extract.py
        │
        ▼
  interpretação           agenda/ai/interpreter.py   (LLM ou heurística)
        │  produz ActionProposal — nunca escreve no banco
        ▼
  resolução determinística agenda/core/dates.py, agenda/core/academic.py
        │
        ▼
  motor de ações          agenda/core/actions.py
        │  schema → regras → permissão → confiança → executor
        ▼
  persistência + auditoria agenda/core/events.py, models.AuditLog, models.AiAction
        │
        ▼
  lembretes e notificação  agenda/core/reminders.py → notifications.py → canais
```

## Camadas

| Pasta | Responsabilidade |
|---|---|
| `agenda/core` | Regras determinísticas: datas, recorrência, lembretes, períodos letivos, perfis por nível, escopo de acesso, duplicados, ações, planner, planos, família, notas e estudo. **Fonte de verdade.** |
| `agenda/ai` | Interpretação: provedores plugáveis, prompts versionados, contexto seletivo, heurística de reserva. |
| `agenda/ingest` | Pipeline documental: extração nativa → visão só onde precisa → candidatos para revisão. |
| `agenda/channels` | WhatsApp Cloud API e Telegram. |
| `agenda/jobs` | Workers: entrega de lembretes, reconciliação de status, retenção de mídia. |
| `agenda/web` | HTTP: páginas server-rendered, API JSON e webhooks. |

## A IA interpreta, o software decide (SPEC §146)

O LLM é usado apenas para **compreender, extrair, classificar e relacionar**.
Datas, recorrências, permissões, lembretes, persistência e segurança são
código determinístico. Em particular:

* o modelo devolve a **expressão temporal** ("sexta que vem"), nunca a data;
  quem resolve é `core/dates.py`, com o fuso do usuário;
* quando não dá para resolver com segurança, o sistema **pergunta**
  (`needs_clarification`) em vez de inventar (SPEC §92);
* toda proposta passa por schema, regras de negócio, checagem de dono do
  objeto e limiar de confiança antes de virar escrita.

## Confiança e confirmação (SPEC §13, §129)

| Confiança | Comportamento |
|---|---|
| ≥ 0.90 | executa e oferece **Desfazer** |
| 0.70 – 0.89 | mostra prévia e pede confirmação |
| < 0.70 | não executa; pergunta |
| exclusão (qualquer confiança) | sempre exige confirmação explícita |

O usuário pode desligar a criação automática no perfil — aí tudo passa a
pedir confirmação.

## Desfazer (SPEC §27)

Cada execução grava um `AiAction` com `before_state`, `after_state`, origem,
modelo e versão de prompt. `actions.undo()` restaura o snapshot anterior (ou
remove o que foi criado) e registra a reversão na auditoria.

## Segurança de prompt (SPEC §72)

Todo conteúdo de terceiros (documento, transcrição, mensagem) entra no prompt
dentro de `<conteudo_nao_confiavel>`, precedido de regras explícitas: aquilo é
**dado**, não instrução. O modelo só pode devolver JSON validado por schema —
ele não tem como executar nada por conta própria.

## Custo (SPEC §112, §113)

* extração nativa antes de qualquer chamada de IA;
* visão apenas nas páginas sem texto aproveitável;
* deduplicação por SHA-256 do arquivo;
* cada operação grava `AiUsage` (modelo, tokens, custo estimado) para o painel
  interno calcular custo por usuário e por documento.


## Um núcleo, muitas experiências

`core/profiles.py` é o que faz o mesmo banco atender da educação infantil ao
doutorado. Cada nível declara vocabulário, tipos de atividade, campos do
cadastro, blocos da tela inicial, lembretes padrão e recursos ligados. Nenhuma
tela decide isso sozinha — se uma diferença entre níveis aparecer espalhada
pelo código, é bug de arquitetura.

`core/periods.py` cuida da outra metade: semestre, trimestre, bimestre,
quadrimestre, módulo, anual ou contínuo. Os períodos existem como entidade, o
que permite arquivar o passado sem apagar nada e responder "quando foi a prova
do semestre passado?".

## Isolamento como camada, não como disciplina

`core/scope.py` centraliza a regra de propriedade de cada modelo. Rotas e
serviços pedem objetos por lá; quem esquece de declarar um modelo novo recebe
negação, não vazamento. É o oposto de depender de lembrar do filtro em cada
consulta.

## Cobrança desacoplada

`core/billing.py` expõe `allows()`, `limit_of()` e `check_quota()`. O resto do
código nunca pergunta "qual é o plano?", pergunta "posso fazer isto?". Trocar
preço, limite ou provedor não toca em nenhuma regra de produto.
