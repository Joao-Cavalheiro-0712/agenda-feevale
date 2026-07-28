# 🎓 Agenda Feevale

Bot de Telegram que **lê seus cronogramas** (PDF, Word, Excel, CSV), identifica
**provas, trabalhos e avaliações** com data e te **avisa 1 semana antes e 1 dia
antes** de cada uma.

Roda inteiro no [Railway](https://railway.app) — uma interface web simples onde
você anexa os cronogramas, e o resto é automático.

---

## Como funciona

```
Você anexa o cronograma  ──►  Sistema lê o texto (PDF/Word/Excel)
                                        │
                                        ▼
                          Gemini identifica as avaliações + datas
                                        │
                                        ▼
                          Eventos salvos no banco de dados
                                        │
                    Job diário (08:00) verifica o que está próximo
                                        │
                                        ▼
                    📲 Telegram: "Prova G1 é amanhã!"
```

- **Extração inteligente** via Google Gemini (Flash — barato). Sem chave de IA,
  cai para um extrator por regex que reconhece datas e palavras‑chave.
- **Notificações** disparadas 1 semana antes e 1 dia antes (configurável).
- **Multi‑arquivo**: envie vários cronogramas de uma vez.

---

## Passo a passo para publicar no Railway

### 1. Crie o bot do Telegram
1. No Telegram, fale com o [@BotFather](https://t.me/BotFather).
2. `/newbot`, escolha um nome e um usuário. Guarde o **token**.

### 2. Pegue a chave do Gemini (recomendado)
- Acesse <https://aistudio.google.com/app/apikey> e gere uma **API key**.

### 3. Deploy no Railway
1. Crie um projeto no Railway a partir deste repositório (branch `main`).
2. Em **Variables**, defina:
   - `TELEGRAM_BOT_TOKEN` — token do BotFather
   - `GEMINI_API_KEY` — chave do Google AI Studio
   - (opcional) `WEB_PASSWORD` — protege a interface web
3. (Opcional, recomendado) adicione um **PostgreSQL** ao projeto — o Railway
   injeta `DATABASE_URL` automaticamente e seus dados persistem entre deploys.
   Sem isso, é usado SQLite (some se o container for recriado).
4. O Railway usa o `Procfile` e sobe o app. Abra a URL pública gerada.

### 4. Ative as notificações
1. Abra seu bot no Telegram e envie **/start** — pronto, você está inscrito.
2. Na interface web, clique em **Enviar teste** para confirmar.
3. Anexe seus cronogramas e deixe o resto por conta do bot. 🚀

---

## Comandos do bot no Telegram
- `/start` — inscrever para receber os avisos
- `/proximas` — listar as próximas avaliações
- `/sair` — parar de receber avisos

---

## Rodando localmente (opcional)

```bash
pip install -r requirements.txt
cp .env.example .env      # preencha as variáveis
export $(grep -v '^#' .env | xargs)   # carrega as variáveis
python app.py             # http://localhost:8080
```

---

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Token do bot (@BotFather) |
| `GEMINI_API_KEY` | recomendada | Chave do Google Gemini para extração |
| `GEMINI_MODEL` | ❌ | Modelo (padrão `gemini-2.5-flash`) |
| `DATABASE_URL` | ❌ | Postgres do Railway (senão SQLite) |
| `TELEGRAM_CHAT_ID` | ❌ | Chat fixo, se você já souber o id |
| `TIMEZONE` | ❌ | Padrão `America/Sao_Paulo` |
| `DAILY_HOUR` / `DAILY_MINUTE` | ❌ | Horário do lembrete (padrão 08:00) |
| `REMINDER_DAYS` | ❌ | Dias de antecedência (padrão `7,1`) |
| `WEB_PASSWORD` | ❌ | Senha da interface web |

---

## Formatos de cronograma suportados
PDF · Word (`.doc` e `.docx`) · Excel (`.xlsx`) · CSV · TXT

O melhor resultado vem de cronogramas com **datas explícitas** (ex.: `15/04`,
`15/04/2026`, `15 de abril`). A IA infere o ano pelo semestre corrente quando
ele não está escrito.
