# Cobertura da especificação

Mapa honesto entre a SPEC v1.0 e o que existe no código. `✅` implementado,
`◑` implementado parcialmente (com nota), `⏳` deliberadamente fora deste
escopo (P1/P2/P3 ou dependente de contrato externo).

## Produto e experiência

| § | Item | Estado | Onde |
|---|---|---|---|
| 1–3 | Visão, princípios, zero atrito, IA invisível | ✅ | captura única multimodal em `web/api.py:capture` |
| 4 | Públicos (fundamental → pós) | ✅ | `EducationType`, `pages.TYPES_BY_EDUCATION`, rótulos em `core/events.py` |
| 5 | Hierarquia usuário → contexto → matéria | ✅ | `models.py` |
| 6 | Onboarding por etapas | ✅ | `templates/onboarding.html` |
| 7 | Onboarding por voz | ✅ | `ai/onboarding.py` + `/onboarding/voz` com tela de revisão item a item |
| 8–10 | Onboarding por documentos, pipeline, modelo intermediário | ✅ | `ingest/pipeline.py`, `ingest/text_extract.extract_pages` |
| 11 | Extração estruturada validada por schema | ✅ | `ai/prompts.DOCUMENT_SCHEMA` + validação em `pipeline._normalize_event` |
| 12 | Proveniência | ✅ | `Event.source_type/source_id/source_reference`, exibida em `templates/event.html` |
| 13 | Score de confiança | ✅ | `config.CONFIDENCE_*`, porteiro em `core/actions.execute` |
| 14 | Conflitos e remarcação | ✅ | `core/duplicates.find_reschedule_candidate`, `ai/interpreter._apply_conflict_rules` |
| 15–19 | WhatsApp como interface completa | ✅ | `channels/whatsapp.py`, `web/webhooks.py` |
| 20 | Memória contextual | ✅ | `ai/context.build_context_block` |
| 21 | Linguagem temporal determinística | ✅ | `core/dates.py` (+ 31 testes) |
| 22 | Cadastro em lote por áudio | ✅ | `core/assistant._handle_batch`, `actions.summarize_batch` |
| 23–24 | Documento e foto pelo WhatsApp | ✅ | `channels/whatsapp._handle_media` |
| 25–26 | Enum de intenções e modelo de ação | ✅ | `core/actions.Intent`, `ActionProposal` |
| 27 | Undo | ✅ | `actions.undo` + toast com "Desfazer" |
| 28–35 | Hoje, Semana, Mês, Agenda, Timeline, Disciplina, Deadlines | ✅ | `core/planner.py` + templates |
| 36–37 | Navegação e Quick Capture | ✅ | `partials/nav.html`, `partials/capture.html` |
| 38 | Desktop | ✅ | breakpoint 900px em `static/app.css` |
| 39–41 | Design system, temas, acessibilidade | ✅ | tokens, claro/escuro/sistema, foco visível, alvos de 44px, `prefers-reduced-motion` |
| 42–45 | Matérias, professores, locais, recorrências | ✅ | `core/academic.py`, `core/recurrence.py` |
| 46–48 | Eventos, tipos, status | ✅ | `models.Event` |
| 49–50 | Lembretes 7/1 dia e perfis por tipo | ✅ | `core/reminders.py` |
| 51–52 | Canais e central de notificações | ✅ | in-app, Web Push (VAPID gerado por `agenda.cli vapid`), WhatsApp, Telegram e cópia para responsáveis |
| 53–54 | Assistente no app e resumo da semana | ✅ | `templates/assistant.html`, `planner.week_summary` |
| 55 | Busca universal | ◑ | busca textual em eventos e matérias; linguagem natural passa pelo interpretador |
| 56 | Tela de documentos | ✅ | `templates/documents.html` |
| 57–58 | Compartilhamento de turma | ✅ | `SharedCollection`, `/join/<code>` |
| 59 | Conta família | ✅ | `core/family.py`, convite revogável, permissões separadas e cópia dos lembretes |
| 60 | PWA | ✅ | manifest, service worker, offline, instalável |

## Engenharia

| § | Item | Estado | Onde |
|---|---|---|---|
| 61–63 | Stack e serviços lógicos | ◑ | monólito Python organizado nas mesmas fronteiras (`core`, `ai`, `ingest`, `channels`, `jobs`, `web`) para extração futura |
| 64–65 | Níveis de ensino e períodos | ✅ | 14 níveis em `EducationType` (inclusive EJA), 7 divisões de ano em `PeriodKind`, `core/periods.py` e `core/profiles.py` |
| 64–65 | Entidades e educação flexível | ✅ | `models.py` |
| 66 | Endpoints | ✅ | `web/api.py`, `web/pages.py`, `web/webhooks.py` |
| 67–68 | Webhook e mídia | ✅ | assinatura HMAC, idempotência, 200 rápido, processamento fora do request |
| 69–71 | Orquestrador, estratégia de modelos, contexto seletivo | ✅ | `ai/providers.py`, `ai/prompts.py`, `ai/context.py` |
| 72 | Anti prompt-injection | ✅ | bloco `<conteudo_nao_confiavel>` + `GUARD` |
| 73–74 | Idempotência e duplicados | ✅ | `provider_message_id`, `Event.fingerprint` |
| 75 | Timezone | ✅ | data local + instantes em UTC |
| 76–77 | Notificações agendadas e status de entrega | ✅ | `EventReminder`, `NotificationDelivery` |
| 78–79 | Autenticação e segurança | ✅ | scrypt, cookies HttpOnly/SameSite/Secure, CSRF, CSP, rate limit, validação de upload |
| 80–83 | LGPD, idade, retenção | ◑ | exportação, exclusão anonimizada, retenção configurável, minimização (IP e token só em hash), automação desligada em perfil de criança e conta de responsável; a revisão jurídica formal fica para o pré-lançamento |
| 84 | Auditoria | ✅ | `AuditLog` com antes/depois, modelo e versão de prompt |
| 85–88 | Observabilidade e métricas | ◑ | `AiUsage`, painel interno e logs estruturados; Sentry/tracing dependem do ambiente |
| 89–91 | Estados vazios, processamento, erro | ✅ | telas de vazio com ação, progresso por etapas, mensagens de erro acionáveis |
| 92 | Não inventar | ✅ | âncoras não resolvidas viram pergunta (teste `test_ancora_sem_aula_pergunta_em_vez_de_inventar`) |
| 93–95 | Sugestões proativas, plano de estudos, calendários externos | ✅ | `core/study.py` (blocos separados do obrigatório) e `core/calendar_export.py` (.ics para Google/Apple/Outlook); sincronização bidirecional continua fora |
| 96 | Billing | ◑ | planos, entitlements, quotas mensais e paywall em `core/billing.py`; a chamada ao gateway depende de chave |
| 97 | Admin | ✅ | `/admin`, invisível para aluno (404) |
| 98 | Feature flags | ✅ | `config.FEATURE_FLAGS` |
| 99–100 | Jobs e estados de processamento | ✅ | `jobs/scheduler.py`, `DocumentStatus` |
| 101–103 | Testes e golden dataset | ✅ | 125 testes; `tests/golden/dataset.jsonl` versionado |
| 104 | Ações destrutivas | ✅ | exclusão sempre confirma |
| 105–107 | Performance, cache, backup | ◑ | updates otimistas e skeletons no cliente; tetos de processamento por documento; cache/backup são configuração de infraestrutura |
| 108–110 | Ambientes, CI/CD, migrations | ✅ | `APP_ENV`, CI no GitHub Actions e Alembic aplicado no start (produção não cria tabela sozinha) |
| 111 | Rate limits | ✅ | `security.rate_limit` |
| 112–115 | Custo de IA, hash, privacidade | ✅ | `AiUsage`, SHA-256, provedores plugáveis |
| 116–119 | Fluxos de MVP | ✅ | cobertos em `tests/test_interpreter.py` e no golden dataset |
| 120 | Escopo P0 | ✅ | completo |
| 121 | Escopo P1 | ✅ | WhatsApp, PWA, LGPD, Web Push, analytics interno e planos com quotas |
| 122 | Escopo P2 | ◑ | família, notas e pesos, checklists, planejador de estudo e exportação de calendário entregues; sincronização bidirecional e apps nativos continuam fora |
| 123 | P3 / B2B | ⏳ | plano institucional existe como entitlement; painel da instituição e SSO ficam para depois |
| — | EJA / supletivo | ✅ | nível de primeira classe, com vocabulário e tom de adulto: quem voltou a estudar no básico não recebe a tela de uma criança |
| 137 | Notas e pesos | ✅ | `core/grades.py` com média ponderada e quanto falta para passar |
| 139–140 | Materiais e checklists | ✅ | checklist no evento, com extração automática de "levar cartolina e cola" |
| 141 | Tempo real | ✅ | SSE em `web/realtime.py`: o que chega pelo WhatsApp aparece na aba aberta |

| 70–71 | Compreensão sem depender de IA | ✅ | base própria em `knowledge/`: fonética PT-BR, léxico de 198 termos, memória por usuário e recuperação — resolve local antes de gastar modelo, ver [`CONHECIMENTO.md`](CONHECIMENTO.md) |
| — | Consentimento e LGPD | ✅ | termos e política versionados, prova de aceite com hash, trava fail-closed, menor de 18 só por conta criada pelo responsável — ver [`LGPD.md`](LGPD.md) |

## O que ainda depende de terceiros

Tudo que falta agora depende de credencial ou de decisão externa — não de código:

1. **Chaves de IA** (`GEMINI_API_KEY`): sem elas o produto funciona com o
   interpretador heurístico, mas áudio, visão e extração inteligente ficam
   desligados.
2. **WhatsApp Business**: número aprovado, token e templates de mensagem
   proativa aprovados pela Meta.
3. **Gateway de pagamento**: a estrutura de planos e quotas está pronta; falta
   plugar o provedor e ligar `FEATURE_BILLING`.
4. **Revisão jurídica** das minutas de termos e política, e do fluxo para
   menores, antes do go-live — mais o nome do encarregado (DPO) e o CNPJ do
   controlador, que hoje entram por variável de ambiente. Detalhes na seção
   "O que ainda depende de decisão de negócio" de [`LGPD.md`](LGPD.md).
5. **Fila externa** (Redis/Celery) no lugar das threads, quando o volume pedir —
   a fronteira já está desenhada em `jobs/`.
