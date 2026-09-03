# Grifo — planner acadêmico multimodal

> Você manda. Ele grifa.

Cronograma em PDF, foto do quadro, print do portal ou um áudio de dez segundos
saindo da aula: tudo vira uma agenda estruturada, com lembrete na hora certa.
O estudante não monta planner — ele conta o que recebeu.

Funciona como **PWA mobile-first** e como **bot de WhatsApp**, com o mesmo
núcleo de regras nos dois canais — da educação infantil ao doutorado.

---

## O que já dá para fazer

* **Contar por texto ou voz**: “A professora de Civil pediu um trabalho sobre
  responsabilidade civil para dia 23, vale 2 pontos” → atividade criada, com
  matéria, data e lembretes, e um botão de desfazer.
* **Jogar os cronogramas**: PDF, DOC/DOCX, XLSX, CSV, TXT e imagens. O sistema
  extrai matérias, aulas recorrentes, provas, entregas e feriados, mostra tudo
  para revisão e só grava depois da sua confirmação.
* **Ver do jeito que faz sentido**: Hoje, Semana, Mês, Agenda, Linha do tempo
  do período (com heatmap de carga), por Disciplina e por Entregas.
* **Perguntar**: “o que eu tenho essa semana?”, “tenho algo atrasado?”,
  “quando é minha próxima prova?”.
* **Usar pelo WhatsApp**: texto, áudio, foto e documento, com identificação
  por número vinculado — nada de dado acadêmico antes da vinculação.
* **Ser lembrado**: 7 dias e 1 dia antes por padrão, com perfis mais espertos
  por tipo (prova: 7/3/1; material: véspera e no dia).
* **Compartilhar com a turma**: link `/join/CÓDIGO` que o colega adiciona à
  agenda dele — com confirmação, sem cópia silenciosa.
* **Acompanhar em família**: responsável entra por convite, com permissões
  separadas para ver agenda, adicionar compromisso e receber lembrete.
* **Levar para o calendário**: link `.ics` para Google, Apple e Outlook.
* **Estudar distribuído**: blocos de estudo antes das provas, sempre separados
  do que é obrigatório.
* **Notas e média**: peso por avaliação e quanto falta para fechar o período.

Sem chave de IA o produto continua funcionando: um interpretador heurístico em
português cobre os fluxos principais (é o piso de qualidade medido pelo golden
dataset).

---

## Rodando localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # ajuste o que quiser
python wsgi.py                  # http://localhost:8080
```

Crie a conta em `/criar-conta`, escolha o momento de estudos e comece a mandar
coisas. Com `GEMINI_API_KEY` definida, entram transcrição de áudio, leitura de
imagem e extração de documentos por IA.

### Testes e ferramentas

```bash
pytest -q                        # 229 testes
python -m pyflakes agenda tests  # análise estática
python -m agenda.cli check       # sanidade da configuração
python -m agenda.cli secret      # gera SECRET_KEY forte
python -m agenda.cli vapid       # gera as chaves do Web Push
python -m agenda.cli migrate     # aplica as migrations
```

O golden dataset (`tests/golden/dataset.jsonl`) é versionado: **nunca** altere
um resultado esperado só para o teste passar — a mudança precisa de revisão
humana (SPEC §103).

---

## No ar

O projeto **grifo** no Railway hospeda a aplicação:

* app: `https://grifo-web-production.up.railway.app`
* banco: PostgreSQL no mesmo projeto, com volume persistente
* schema aplicado por migration no start; healthcheck em `/healthz`

O que ainda está desligado por falta de credencial (e como ligar):

| Recurso | Variável | Efeito enquanto desligado |
|---|---|---|
| IA (áudio, visão, extração) | `GEMINI_API_KEY` | o interpretador heurístico assume; texto continua funcionando |
| WhatsApp | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `FEATURE_WHATSAPP=true` | o canal fica oculto na interface |
| Cobrança | gateway + `FEATURE_BILLING=true` | os planos aparecem, mas a troca paga é recusada |

`SECRET_KEY`, `DATABASE_URL` e as chaves VAPID já estão configuradas no ambiente
de produção.

## Deploy no Railway

1. Crie o projeto a partir deste repositório.
2. Adicione um **PostgreSQL** (o Railway injeta `DATABASE_URL`).
3. Em *Variables*, defina no mínimo:
   * `SECRET_KEY` — valor aleatório longo
   * `APP_ENV=production`
   * `PUBLIC_URL` — a URL pública gerada
   * `GEMINI_API_KEY` — opcional, mas é o que liga áudio, foto e documentos
4. Para o WhatsApp (Cloud API da Meta):
   * `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`,
     `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_NUMBER`
   * configure o webhook em `https://SEU-APP/webhooks/whatsapp`
5. `Procfile` e `nixpacks.toml` já cuidam do resto (o `antiword` entra para ler
   `.doc` antigo).

Em produção a assinatura do webhook é obrigatória: sem `WHATSAPP_APP_SECRET`,
requisições são recusadas.

---

## Um produto, cada nível do seu jeito

O banco é um só; o que muda é o **perfil do nível de ensino**
(`agenda/core/profiles.py`), que define vocabulário, tipos de atividade, campos
do cadastro, ordem da tela inicial, lembretes padrão e recursos ligados.

| Nível | Vocabulário | Período letivo | Tela inicial começa por |
|---|---|---|---|
| Educação infantil | levar para a escola, evento | anual | o que levar |
| Fundamental I | tema de casa, material | bimestre | o que levar |
| Fundamental II | tarefa, trabalho | bimestre | hoje |
| Ensino médio | lista de exercícios, redação, simulado | trimestre | hoje |
| Cursinho | simulado, revisão | contínuo | hoje |
| Técnico | prática de laboratório, entrega técnica | trimestre / módulo | hoje |
| Graduação | trabalho, artigo, seminário | semestre | hoje |
| Pós / MBA | entrega, encontro, TCC | módulo | hoje |
| Mestrado | artigo, qualificação, orientação | semestre | hoje |
| Doutorado | tese, banca, submissão | semestre | hoje |
| Idiomas | homework, teste oral | módulo | hoje |

Também dá para ter **mais de um contexto ao mesmo tempo** (faculdade + curso de
inglês) e virar o período sem perder nada: o anterior fica arquivado e
pesquisável, e as matérias podem ser copiadas para o novo.

## Planos

| Plano | Preço | Inclui |
|---|---|---|
| Grátis | R$ 0 | agenda completa, 3 documentos e 30 capturas por mês |
| Estudante | R$ 19,90/mês | WhatsApp, 100 documentos, planejador de estudos, calendário |
| Família | R$ 34,90/mês | tudo do Estudante + até 5 estudantes com responsáveis |
| Institucional | sob contrato | uso ilimitado, turmas oficiais |

Os limites são **entitlements** (`agenda/core/billing.py`), não condicionais
espalhados: cada recurso pergunta "posso?" a um lugar só. A cobrança tem
provedor plugável — ligar o gateway é configuração, não reescrita.

## Segurança

Isolamento entre contas com escopo obrigatório e falha fechada, sessões
revogáveis com token só em hash, bloqueio progressivo de login, CSP com nonce
(sem `unsafe-inline` em scripts), validação de upload por magic bytes,
allowlist contra SSRF e separação explícita entre dado e instrução nos prompts.

Detalhes e como verificar: [`docs/SEGURANCA.md`](docs/SEGURANCA.md).

## Privacidade e LGPD

Termos de uso e política de privacidade versionados em código, com o hash do
texto gravado junto de cada aceite — a prova que o art. 8º §1º exige. Uma trava
`fail-closed` no `before_request` impede o uso do produto enquanto houver
pendência de consentimento, inclusive pelo WhatsApp.

**Menor de 18 anos não cria conta sozinho.** A conta é criada pelo responsável,
autenticado, que autoriza em tela destacada (art. 14). O estudante entra com o
login dele, no celular dele; o responsável acompanha do dele, pelo vínculo de
família. Para menores, automação e interpretação automática começam desligadas.

O que está implementado e o que ainda depende de decisão de negócio (DPO, CNPJ,
revisão jurídica das minutas): [`docs/LGPD.md`](docs/LGPD.md).

## Como está organizado

```
agenda/
  core/       regras determinísticas — datas, recorrência, lembretes, períodos,
              perfis por nível, escopo/isolamento, motor de ações, planner,
              planos, família, notas, estudo   (fonte de verdade)
  ai/         interpretação — provedores plugáveis, prompts versionados,
              contexto seletivo, heurística de reserva, onboarding por voz
  ingest/     pipeline documental — extração nativa → visão só onde precisa
  channels/   WhatsApp Cloud API, Telegram e Web Push
  jobs/       workers: lembretes, reconciliação, retenção de mídia
  web/        páginas, API JSON, webhooks e tempo real (SSE)
migrations/   Alembic — schema versionado
docs/         ARQUITETURA.md, SEGURANCA.md, LGPD.md e COBERTURA-SPEC.md
tests/        250 testes + golden dataset versionado
```

Duas regras guiam tudo:

* **A IA interpreta, o software decide.** O modelo compreende linguagem e
  extrai entidades; datas, permissões, recorrências, lembretes e persistência
  são código determinístico. Datas nunca são calculadas pelo LLM.
* **Web e WhatsApp usam o mesmo núcleo.** “Criar trabalho” é o mesmo serviço
  nos dois canais — e será no app nativo.

Detalhes em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md); o que está pronto,
parcial ou fora de escopo está em
[`docs/COBERTURA-SPEC.md`](docs/COBERTURA-SPEC.md).

---

## Confiança antes de automação

| Confiança da interpretação | O que acontece |
|---|---|
| ≥ 0.90 | cria e oferece **Desfazer** |
| 0.70 – 0.89 | mostra prévia e pergunta antes |
| < 0.70 | não cria; pede o que faltou |
| exclusão | sempre confirma, em qualquer confiança |

Quando o documento diz “entrega na próxima aula” e não há horário cadastrado,
o sistema **pergunta** em vez de chutar uma data. Toda ação automática guarda
estado anterior, origem, modelo e versão de prompt para auditoria.

---

## Privacidade

Dados acadêmicos — e, em alguns perfis, de adolescentes. Por isso:
minimização, retenção configurável de áudio e documentos, exportação em JSON,
exclusão de conta, auditoria de acessos e provedor de IA trocável sem
reescrever o produto. Mensagens proativas por WhatsApp devem seguir as
políticas e os templates vigentes da Meta.

---

## Variáveis de ambiente

Veja `.env.example`. As essenciais:

| Variável | Para quê |
|---|---|
| `SECRET_KEY` | sessões e links assinados |
| `DATABASE_URL` | Postgres (sem ela, SQLite local) |
| `GEMINI_API_KEY` | áudio, visão e extração por IA |
| `WHATSAPP_*` | canal oficial do WhatsApp |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | Web Push |
| `TIMEZONE`, `REMINDER_DAYS`, `REMINDER_HOUR` | comportamento dos lembretes |
| `FEATURE_*` | liga/desliga recursos gradualmente |

O schema é versionado com Alembic e aplicado no start (`Procfile`). Em produção
o app **não** cria tabelas sozinho: divergência de schema tem que aparecer, não
ser silenciada.
