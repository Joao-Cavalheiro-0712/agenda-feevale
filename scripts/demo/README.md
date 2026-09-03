# Apresentação comercial — como regerar

Três passos, todos reproduzíveis. Não versionamos o PDF: ele sai do código, e
sair do código é o que garante que a apresentação nunca mostre uma tela que já
não existe.

```bash
# 1. Postgres local (o schema real, não SQLite)
su postgres -s /bin/bash -c \
  "PATH=/usr/lib/postgresql/16/bin:\$PATH initdb -D /tmp/pgdata -U grifo --auth=trust"
su postgres -s /bin/bash -c \
  "PATH=/usr/lib/postgresql/16/bin:\$PATH pg_ctl -D /tmp/pgdata -l /tmp/pgdata/log \
   -o '-p 5433 -k /tmp/pgrun -c listen_addresses=127.0.0.1' start"
createdb -h 127.0.0.1 -p 5433 -U grifo grifo
DATABASE_URL="postgresql://grifo@127.0.0.1:5433/grifo" .venv/bin/alembic upgrade head

# 2. Conta de demonstração + servidor
.venv/bin/python scripts/demo/seed_apresentacao.py
.venv/bin/python scripts/demo/servidor_demo.py &      # porta 5177

# 3. Telas e PDF
.venv/bin/python scripts/demo/capturar_telas.py       # 40 PNGs a 3x
.venv/bin/python scripts/demo/montar_apresentacao.py  # → Grifo-apresentacao.pdf
```

## Decisões que valem manter

**Postgres, não SQLite.** Rodar a demonstração no banco de produção de verdade
foi o que achou dois bugs que a suíte não achava: `varchar(5)` em horário de
aula (que quebrava a exportação) e a divisão indevida do período letivo.

**Dados plausíveis, não “Matéria 1”.** A apresentação vende o produto, e o
produto só convence com dado que parece de gente. O seed monta uma aluna de
Direito no 5º semestre, com grade noturna, provas com peso, seminário em grupo
com checklist e conversa de WhatsApp — mais a família (mãe responsável + filho
no 4º ano), pelo caminho real do produto.

**Captura em formato de celular.** Toda tela sai 402×874 @3x. Screenshot de
página inteira assa a barra de navegação fixa no meio da imagem, e uma tira de
1:6 fica ilegível no slide. Telas longas ganham uma segunda captura rolada, que
vira o próprio slide.

**PDF pelo Chromium, não por biblioteca.** O texto sai vetorial (selecionável,
nítido em qualquer zoom) e as imagens entram na resolução nativa — cerca de
4,6× de sobreamostragem no tamanho impresso. Montar o PDF a partir de imagens
de página inteira daria um arquivo pesado e borrado.
