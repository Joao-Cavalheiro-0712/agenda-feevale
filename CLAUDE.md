# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O produto

Grifo é um planner acadêmico multimodal brasileiro (SaaS multi-tenant). O aluno
manda texto, áudio, foto do quadro ou PDF do portal — pelo app ou pelo WhatsApp
— e o sistema devolve a agenda montada, com lembrete antes da hora. Um núcleo
só atende **14 níveis de ensino**, da educação infantil ao doutorado, incluindo
EJA.

Idioma: **tudo em português do Brasil** — interface, docstrings, comentários,
mensagens de commit, nomes de função nos módulos mais novos. Os módulos
antigos (`events`, `academic`, `planner`) usam nomes em inglês; os novos
(`referrals`, `oidc`, `passkeys`, `backup`, `verification`) usam português.
Ao editar um arquivo, siga a língua que já está nele — não uniformize.

## Comandos

```bash
pytest -q                                   # 580 testes (~70s)
pytest tests/test_security.py -q            # um arquivo
pytest -k "passkey and not rota" -q         # por expressão
pytest tests/test_tour.py::test_o_tour_e_curto -q   # um teste
python -m pyflakes agenda tests             # análise estática (sem linter além disso)

python wsgi.py                              # sobe em http://localhost:8080 (SQLite)
python -m agenda.cli check                  # sanidade da configuração
python -m agenda.cli migrate                # aplica migrations
python -m agenda.cli secret | vapid         # gera SECRET_KEY / chaves Web Push
python -m agenda.cli backup | backup-list | backup-verify
```

**Migrations** são Alembic, aplicadas no start pelo `Procfile`. Em produção o
app **não** cria tabelas sozinho: divergência de schema tem que aparecer, não
ser silenciada. Ao adicionar coluna, crie a migration e teste `upgrade head`
**e** `downgrade -1`.

**Postgres local** (o dev roda SQLite, a produção roda Postgres — e a diferença
já escondeu bugs, ver "Armadilhas"):

```bash
su postgres -s /bin/bash -c "PATH=/usr/lib/postgresql/16/bin:\$PATH initdb -D /tmp/pgdata -U grifo --auth=trust"
su postgres -s /bin/bash -c "PATH=/usr/lib/postgresql/16/bin:\$PATH pg_ctl -D /tmp/pgdata \
  -o '-p 5433 -k /tmp/pgrun -c listen_addresses=127.0.0.1' start"
createdb -h 127.0.0.1 -p 5433 -U grifo grifo
DATABASE_URL="postgresql://grifo@127.0.0.1:5433/grifo" alembic upgrade head
```

`scripts/demo/` regenera a apresentação comercial (seed realista → 40 telas em
3× → PDF pelo Chromium). Ver `scripts/demo/README.md`.

## A regra de arquitetura número um

**A IA nunca escreve no banco.** Ela devolve uma `ActionProposal`; o motor
(`core/actions.py`) valida schema → regras de negócio → propriedade → confiança
→ executa → guarda o estado anterior para desfazer. Web, WhatsApp e Telegram
passam pelo **mesmo** caminho.

Nunca adicione uma escrita que contorne o motor, nem uma regra de negócio na
rota. Se uma rota precisa de algo novo, o lugar é `core/`.

Faixas de confiança (`config.CONFIDENCE_*`): ≥0.90 executa, 0.70–0.89 pede
confirmação, <0.70 pergunta. DELETE sempre confirma, independente da confiança.

## Camadas

```
agenda/web/       rotas HTTP (pages, api, auth, webhooks, realtime) — sem regra de negócio
agenda/core/      o núcleo: motor de ações, domínio, cobrança, privacidade, segurança de conta
agenda/knowledge/ base de conhecimento própria (fonética, léxico, fuzzy, memória do usuário)
agenda/ai/        provedores de modelo, prompts, heurísticas, interpretação
agenda/ingest/    upload → extração de texto → interpretação
agenda/channels/  WhatsApp, Telegram, Web Push, e-mail
agenda/payments/  interface de gateway + Stripe (o único que conhece o vocabulário do gateway)
agenda/legal/     texto dos termos e da política, versionados e com hash
agenda/jobs/      APScheduler: lembretes, limpeza, indicação, backup, vencimentos
```

Há documentação de fundo em `docs/`: **ARQUITETURA.md** (decisões e o porquê),
**SEGURANCA.md** (modelo de ameaça, o que cada defesa cobre, configuração),
**LGPD.md**, **CONHECIMENTO.md** (as 5 camadas de entendimento),
**COBERTURA-SPEC.md** (o que da SPEC está feito). Leia o relevante antes de
mexer na área — cada arquivo explica decisões que não se deduzem do código.

## Invariantes que o código assume

Estes são os pontos onde uma mudança inocente quebra algo silenciosamente.

**Isolamento entre contas é uma camada, não disciplina.** `core/scope.py` é o
único lugar que sabe amarrar modelo a dono, e ele **falha fechado**:
`scope.query(Modelo, user_id)` levanta `AccessDenied` para modelo não
registrado em `_OWNER_FIELD`, e `scope.get` devolve `None`. Use `scope.query`
em vez de `select(Modelo)` sempre que a consulta for de um usuário.

> Sete modelos com `user_id` **ainda não estão registrados** (`Passkey`,
> `ConsentRecord`, `KnowledgeEntry`, `Reward`, `AuditLog`, `AiUsage`,
> `ChannelMessage`) e usam `.where(Modelo.user_id == ...)` explícito. Funciona,
> mas se você mexer em um deles, prefira registrar no `_OWNER_FIELD` e migrar
> para `scope.query`. `Referral` e `LoginAttempt` não têm dono único e ficam
> fora por desenho.

**"Não existe" e "não é seu" respondem igual** — mesmo 404, mesma mensagem.
Distinguir os dois é enumeração de recursos. Vale para rotas, para o motor de
ações (`reason == "not_found"`) e para a recuperação de senha.

**Quota mora dentro do caminho, não na rota.** `billing.enforce`/`consume`
ficam em `ingest/pipeline.ingest`, `core/assistant.handle_message` e
`ai/onboarding.interpret`. Já houve bypass quando a checagem estava na rota e
alguém adicionou uma segunda porta para o mesmo caminho. Se criar entrada nova
para IA, ela passa por uma dessas funções — não replique a checagem.

**O gate de consentimento (LGPD) falha fechado no `before_request`**
(`web/deps.py`), com `CONSENT_FREE_ENDPOINTS` como única saída. Webhooks não
passam pelo `before_request`, então **os canais repetem a checagem** — se
adicionar canal, repita.

**Tipo de evento é enum fechado, validado na porta de escrita**
(`core/events.create_event`), não em cada chamador. Foi assim que se fechou
injeção de ICS.

**O valor cobrado sai do catálogo, nunca do navegador** (`payments/service.py`).
O gateway só recebe centavos que o servidor calculou.

**Recompensa de indicação só nasce depois do pagamento E da janela de
reembolso** (`core/referrals.py`). Inverter essa ordem transforma o programa
numa torneira de dinheiro. A defesa é econômica, não técnica — não troque por
limite de IP (mãe e filho compartilham wi-fi, e esse é o caso de uso).

**Pix não renova sozinho.** `Subscription.renews=False` + `active_plan()`
devolvendo o grátis quando o período vence. Sem as duas coisas, um mês pago por
Pix vira plano vitalício.

**Sem chave, tudo falha fechado.** IA, e-mail, pagamento, login social e
WhatsApp recusam com mensagem honesta em vez de fingir sucesso. Nunca introduza
um caminho que conceda plano pago, ou diga "enviado", sem o provedor real.

## Testes

`tests/test_checklist_seguranca.py` é a lista das 20 vulnerabilidades mais
comuns de SaaS virada em teste executável (IDOR, mass assignment, XSS, SSRF,
webhook sem assinatura, JWT não validado, enumeração…). **Ao adicionar
superfície nova — rota, canal, provedor — estenda esse arquivo.** Ele tem
testes que leem a *árvore sintática* de funções críticas (ex.: garantir que
`verify_signature` nunca é desligado no OIDC).

`tests/test_cenarios.py` percorre jornadas reais ponta a ponta pelo HTTP —
conta própria, EJA, família, criança, estranho — e afirma tanto o que cada um
**vê** quanto o que **não pode ver**.

`tests/golden/dataset.jsonl` é o dataset de interpretação. **Nunca** altere um
resultado esperado só para o teste passar: a mudança precisa de revisão humana.

Testes novos vêm com docstring dizendo **por que o teste existe** — qual falha
real ele impede. É o padrão do repositório inteiro.

## Armadilhas conhecidas

**SQLite (dev/teste) aceita o que o Postgres (produção) recusa.** Dois bugs já
passaram por 568 testes: `varchar(5)` recebendo `dt.time`, e `.isoformat()`
chamado em campo que é string. Horário de aula (`ClassSchedule.start_time`) e
de bloco de estudo (`StudyBlock.start_time`) são **strings** `"19:00"`, não
objetos de tempo. Ao mexer em exportação, serialização ou seed, rode contra
Postgres de verdade.

**Testes dependentes de data quebram só em alguns dias.** Um teste marcava
evento em `hoje+2` e conferia a semana corrente — falhava de sexta a domingo.
Ao escrever teste com data, ancore na semana/mês, não em deslocamento cru.

**Screenshot de página inteira assa elementos `position: fixed` no meio da
imagem.** Para capturas, use viewport de celular e role, em vez de `full_page`.

**Flags mortas mentem.** Toda `FEATURE_*` em `config.FEATURE_FLAGS` precisa ser
lida em algum lugar; já se removeu três que não desligavam nada.

## Convenções de escrita

Comentários e docstrings explicam **por que**, não o quê — e nomeiam a falha
concreta que a decisão evita. Exemplo real do repositório:

> `# Cuidado: "" in "aeiou" é True em Python. Aqui não pode ser.`

Mensagens de commit: título curto em português no imperativo, corpo explicando
a decisão e o que ela evita. Bugs corrigidos no caminho entram no corpo, não em
commit separado.

Terminam com:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

## Estado e pendências

Publicado no Railway (branch `claude/academic-organization-ai-platform-3wewpw`,
deploy automático), Postgres gerenciado, volume `/data` para uploads e backups.

Tudo que depende de credencial está implementado e desligado: `GEMINI_API_KEY`,
`RESEND_API_KEY`+`EMAIL_FROM` (ou `SMTP_*`), `STRIPE_*`+`FEATURE_BILLING`,
`GOOGLE_*`, `APPLE_*`, `WHATSAPP_*`, `TELEGRAM_*`. Ver a tabela no README.

Pendente de decisão do dono, não de código:

- **Identidade jurídica em branco.** `DPO_NAME` ainda é "a definir antes do
  lançamento comercial" — e esse texto **sai impresso na política de
  privacidade**. `COMPANY_NAME`, `COMPANY_DOC` (CNPJ) e `PRIVACY_EMAIL` também.
  A LGPD art. 41 exige o encarregado identificado.
- **Backup só dentro do Railway.** O volume `/data` vive na mesma conta que o
  banco, e "alguém apagou a conta" é um dos cenários que o backup cobre. Falta
  cópia fora (S3/R2/Backblaze) e cifra em repouso.
- **Apple Pay exige registrar o domínio** em Payment Method Domains no Stripe;
  sem isso o botão some no Safari sem erro nenhum.
- **`backup-verify` precisa de agenda semanal** — está implementado (restaura o
  dump num banco descartável e confere as tabelas), mas ninguém o chama.
- **Redis para SSE com vários workers.** `web/realtime.py` usa barramento em
  memória; com mais de um worker, troque `_BUS` por pub/sub mantendo a
  interface.
