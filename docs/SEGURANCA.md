# Segurança

Este documento descreve as decisões de segurança do produto e como verificá-las.
Ele existe para que a próxima pessoa que mexer no código saiba **por que** as
coisas estão como estão — e o que não pode ser afrouxado.

## Modelo de ameaça

O produto guarda agenda acadêmica de pessoas — incluindo adolescentes e
crianças. Os cenários que dirigem as decisões:

1. **Um usuário tentando ler dados de outro** (o mais provável e o mais grave).
2. **Roubo de sessão** por cookie vazado, XSS ou dispositivo perdido.
3. **Força bruta e enumeração de contas** para descobrir quem usa o produto.
4. **Upload malicioso** — arquivo que executa, que trava o servidor ou que
   escapa do diretório.
5. **SSRF** através da URL de mídia devolvida por um provedor externo.
6. **Prompt injection** vindo de documento ou mensagem de terceiro.

## Isolamento entre contas (multi-tenant)

O isolamento não depende de lembrar do `WHERE user_id = ?` em cada consulta.

* `agenda/core/scope.py` é o **único** lugar que sabe como cada modelo se
  amarra ao dono. `scope.get()` busca por id já validando propriedade e
  `scope.query()` devolve um `select` pré-filtrado.
* Modelo sem regra declarada **falha fechado**: `scope.get` devolve `None` e
  `scope.query` levanta `AccessDenied`. Esquecer de declarar nega acesso em vez
  de vazar.
* O motor de ações (`core/actions.owns`) usa o mesmo caminho — a IA não tem
  atalho.
* Resposta para objeto de outra conta é **404**, igual a "não existe": não
  confirmamos a existência de recursos alheios.

Verificação: `tests/test_security.py` faz varredura por todas as rotas que
recebem id, tenta ler e escrever em objetos de outra conta e confere que
nenhuma tela renderiza conteúdo de terceiro.

## Sessões

* O cookie guarda apenas um **token opaco**; a sessão vive em `user_sessions`.
* O banco guarda só o **SHA-256** do token — dump vazado não vira sessão.
* Toda entrada cria sessão nova (`session.clear()` antes) — sem fixação.
* Expiração absoluta de 30 dias e por inatividade de 14.
* Revogação por dispositivo, revogação em massa e revogação automática ao
  trocar a senha.
* Cookie com `HttpOnly`, `SameSite=Lax`, `Secure` e prefixo `__Host-` em
  produção (impede que subdomínio comprometido plante sessão).

## Login

* Mensagem idêntica para e-mail inexistente e senha errada.
* Tempo de resposta constante: conta inexistente passa por um hash descartável
  (`security.dummy_verify`), então não dá para enumerar pelo relógio.
* Bloqueio progressivo por **conta e por IP** (`core/login_guard.py`), com
  histórico em banco — rate limit por IP sozinho não protege contra ataque
  distribuído contra uma conta.
* Senha: mínimo de 10 caracteres, recusa de senhas comuns, teto de 256 bytes
  (senha gigante seria DoS de CPU no scrypt).
* IP nunca é guardado em claro: só o hash com sal do `SECRET_KEY`.

## Cabeçalhos e CSP

`script-src` **sem `'unsafe-inline'`**: cada resposta carrega um nonce novo, e
todo script inline referencia esse nonce. Um XSS refletido não executa.

`default-src 'none'` como base, mais `object-src 'none'`, `base-uri 'none'`,
`frame-ancestors 'none'`, COOP/CORP `same-origin`, `X-Frame-Options: DENY`,
`nosniff`, `Referrer-Policy` restrito, Permissions-Policy fechada e HSTS com
preload em produção.

`style-src` mantém `'unsafe-inline'` porque a interface usa atributos `style`.
O risco residual é de aparência, não de execução — e está registrado aqui para
não ser confundido com descuido.

## Upload de arquivos

Três camadas, em `ingest/pipeline.validate_upload`:

1. extensão na allowlist;
2. **magic bytes** compatíveis com a extensão (um `.pdf` precisa começar com
   `%PDF`);
3. recusa de qualquer cabeçalho de executável ou script (`MZ`, `ELF`, `#!`,
   `<?php`, `<script`).

O caminho no disco é montado só com identificadores gerados por nós (uuid) e
extensão da allowlist, dentro da pasta do usuário, com `realpath` conferido
contra a raiz — não há travessia possível. Arquivos são criados com permissão
`0600` e a pasta com `0700`.

Contra DoS: teto de tamanho (25 MB), de páginas processadas (80) e de texto
extraído (400 mil caracteres).

## SSRF

A URL da mídia do WhatsApp vem de um serviço externo e é tratada como não
confiável: só HTTPS, só hosts oficiais da Meta (allowlist com sufixos
validados corretamente, `lookaside.fbsbx.com.evil.com` é recusado), sem seguir
redirecionamento e com limite de tamanho durante o streaming.

## Prompt injection

Conteúdo de documento, transcrição e mensagem entra no prompt dentro de
`<conteudo_nao_confiavel>`, precedido de regras explícitas de que aquilo é
**dado**, não instrução. O modelo só devolve JSON validado por schema e não
tem caminho de escrita: quem grava é o motor de ações, depois de validar
schema, regras, propriedade e confiança.

## Webhooks

Assinatura HMAC-SHA256 comparada com `compare_digest`. Em produção, sem
`WHATSAPP_APP_SECRET` configurado a requisição é **recusada** — não existe
modo permissivo em produção. Idempotência por `provider_message_id`.

## Privacidade

* IP e token: apenas hash.
* Retenção configurável de áudio (padrão 7 dias) e documentos.
* Exportação completa em JSON e exclusão de conta com anonimização.
* Auditoria (`audit_logs`) registra quem, quando, de onde, com qual modelo e
  versão de prompt — sem guardar o conteúdo das mensagens.
* Logs de canal registram tamanho e telefone mascarado, nunca o conteúdo.
* Perfis de crianças começam com automação desligada.

## O que ainda depende de configuração

* `WHATSAPP_APP_SECRET` precisa existir em produção para o webhook funcionar.
* `SECRET_KEY` forte é obrigatório — `python -m agenda.cli check` recusa o
  valor de desenvolvimento em produção.
* Chaves VAPID (`python -m agenda.cli vapid`) para Web Push.
* Rate limit é por processo (memória). Com vários workers, o limite efetivo se
  multiplica; o bloqueio de login, que é o crítico, vive no banco e é global.
  Para limite global de tudo, plugue Redis mantendo a interface de
  `security.rate_limit`.

## Como verificar

```bash
pytest tests/test_security.py -q      # 48 testes adversariais
python -m agenda.cli check            # sanidade da configuração de produção
python -m pyflakes agenda tests       # análise estática
```
