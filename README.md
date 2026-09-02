# Planno — planner acadêmico multimodal

> Você manda. Ele organiza.

Cronograma em PDF, foto do quadro, print do portal ou um áudio de dez segundos
saindo da aula: tudo vira uma agenda estruturada, com lembrete na hora certa.
O estudante não monta planner — ele conta o que recebeu.

Funciona como **PWA mobile-first** e como **bot de WhatsApp**, com o mesmo
núcleo de regras nos dois canais.

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

### Testes

```bash
pytest -q            # 125 testes
python -m pyflakes agenda wsgi.py tests
```

O golden dataset (`tests/golden/dataset.jsonl`) é versionado: **nunca** altere
um resultado esperado só para o teste passar — a mudança precisa de revisão
humana (SPEC §103).

---

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

## Como está organizado

```
agenda/
  core/       regras determinísticas — datas, recorrência, lembretes,
              duplicados, motor de ações, planner   (fonte de verdade)
  ai/         interpretação — provedores plugáveis, prompts versionados,
              contexto seletivo, heurística de reserva
  ingest/     pipeline documental — extração nativa → visão só onde precisa
  channels/   WhatsApp Cloud API e Telegram
  jobs/       workers: lembretes, reconciliação, retenção de mídia
  web/        páginas, API JSON e webhooks
docs/         ARQUITETURA.md e COBERTURA-SPEC.md
tests/        125 testes + golden dataset versionado
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
| `TIMEZONE`, `REMINDER_DAYS`, `REMINDER_HOUR` | comportamento dos lembretes |
| `FEATURE_*` | liga/desliga recursos gradualmente |
