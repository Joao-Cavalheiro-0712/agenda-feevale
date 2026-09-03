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

## Dinheiro: cobrança e indicação

O endpoint de webhook de pagamento é o ponto onde dinheiro vira permissão. Se
ele cair, qualquer pessoa com um `curl` vira assinante Família de graça. Quatro
regras o protegem, e cada uma tem teste de ataque em `tests/test_pagamento.py`:

1. **O preço nunca vem do cliente.** O navegador escolhe plano e ciclo; quanto
   isso custa sai de `billing.PLANS`, no servidor. O desconto de indicação
   também sai do registro de indicação, nunca de um campo do formulário.
2. **Plano só muda por confirmação do servidor.** Com gateway ligado,
   `/planos/assinar` não altera nada: redireciona para o checkout. Quem muda o
   plano é o webhook, depois de o dinheiro entrar.
3. **Assinatura, data e idempotência.** HMAC-SHA256 com `compare_digest` —
   comparar com `==` vaza o prefixo correto por diferença de tempo. Janela de
   5 minutos contra replay de evento capturado. Chave única no id do evento no
   banco: o gateway reenvia quando não recebe 200, e reprocessar um
   `subscription.paid` concederia dois períodos.
4. **Falha fechado e cala a boca.** Sem chave, o checkout diz que a cobrança
   não está ligada em vez de fingir sucesso. E a recusa nunca explica o motivo:
   quem forja webhook não pode aprender onde errou.

Reembolso e contestação derrubam o acesso **e revogam a recompensa de
indicação** — é assim que o programa se protege do golpe "pago, ganho a
recompensa, peço o dinheiro de volta".

### Fraude de indicação

A defesa central é econômica, não técnica: a recompensa só nasce quando o
indicado **paga e passa da janela de reembolso**. Para ganhar um mês de
R$ 19,90 o fraudador precisaria pagar três assinaturas de verdade e não pedir
reembolso. Ninguém faz isso.

Bloqueio por IP foi **deliberadamente evitado**: mãe e filho dividem o wi-fi de
casa, e a mãe indicando o filho é o caso de uso que queremos. Punir cliente
legítimo sem parar o fraudador — que troca de IP em dois toques — é o pior dos
dois mundos. O sinal fica registrado para auditoria, nunca para recusar.

Outras travas: atribuição imutável (uma pessoa é indicada uma vez e para
sempre, senão a mesma indicação se revende), cookie de atribuição assinado com
HMAC e expiração, autoindicação recusada e teto anual de meses grátis por
conta — para que nem um caso extremo nem um bug virem assinatura eterna.

### Quotas

Quota é controle de custo E de abuso. Toda operação que gasta IA passa por
`check_quota` + `consume`: texto, documento e **áudio nos três caminhos** (web,
WhatsApp e onboarding). O áudio é medido pelo tamanho do arquivo, arredondando
para cima — errar a favor da fatura é a escolha certa quando o alternativo é
transcrição ilimitada. `billing.margin_report()` mostra o pior caso por plano,
que é o teto de prejuízo de uma conta abusiva.

## Privacidade

* IP e token: apenas hash.
* Retenção configurável de áudio (padrão 7 dias) e documentos.
* Exportação completa em JSON e exclusão de conta com anonimização.
* Auditoria (`audit_logs`) registra quem, quando, de onde, com qual modelo e
  versão de prompt — sem guardar o conteúdo das mensagens.
* Logs de canal registram tamanho e telefone mascarado, nunca o conteúdo.
* Perfis de crianças começam com automação desligada.

## Backup e recuperação

São dois backups diferentes, e confundi-los custa caro no dia do desastre.

### O nosso (operacional)

Responde a "o banco morreu / alguém apagou a tabela errada / a migração
corrompeu dado". Roda às 02h30 pelo worker (`backup_tick`) e também na mão:

```
python -m agenda.cli backup          # dump + retenção
python -m agenda.cli backup-list     # o que existe hoje
python -m agenda.cli backup-verify   # RESTAURA o mais recente e confere
```

Decisões e o porquê:

* **`pg_dump --format=custom`**, não cópia de arquivo. Cópia de um Postgres
  vivo devolve um banco corrompido; o formato custom permite restaurar tabela
  por tabela, que é o que se precisa quando o problema foi *uma* tabela.
* **Retenção em escada 7/4/6** (diários/semanais/mensais). O desastre mais
  comum não é o banco pegar fogo — é alguém descobrir três semanas depois que
  um dado foi corrompido. Só o backup de ontem não cobre isso, e guardar tudo
  enche o disco.
* **`backup-verify` restaura de verdade** num banco descartável e confere a
  contagem de `users`, `events`, `subjects` e `consent_records`. Backup que
  nunca foi restaurado não é backup, é esperança. Roda semanalmente, fora do
  processo que atende usuário (restaurar custa CPU e disco).
* **Falha nunca é silenciosa.** `BACKUP_FALHOU` sai no log com o motivo, e a
  CLI devolve código de saída diferente de zero. Backup que falha em silêncio
  só é descoberto no dia em que era necessário.

Configuração (`BACKUP_DIR` vazio desliga tudo):

| Variável | Padrão | O que faz |
|---|---|---|
| `BACKUP_DIR` | *(vazio)* | Onde os dumps ficam. **Precisa ser um volume persistente**, não o disco efêmero do contêiner |
| `BACKUP_KEEP_DAILY` | `7` | Diários mantidos |
| `BACKUP_KEEP_WEEKLY` | `4` | Semanais mantidos |
| `BACKUP_KEEP_MONTHLY` | `6` | Mensais mantidos |

**O que ainda depende de operação, e é importante:** o dump fica cifrado em
repouso só se o volume for cifrado. O arquivo tem hash de senha, telefone e
agenda de menor de idade — vazar o backup é vazar a base inteira. Em Railway,
isso significa um volume dedicado; num provedor de objeto (S3/R2), SSE ligado e
bucket privado. O backup gerenciado do próprio Postgres do Railway **não
substitui** este: ele vive na mesma conta, e "alguém apagou a conta" é um dos
cenários que o backup existe para cobrir. Guarde uma cópia fora.

### O do usuário (portabilidade)

`GET /conta/meus-dados.json` (e `GET /api/export`, a mesma função). JSON legível
com conta, consentimentos, contextos, matérias, aulas, compromissos, lembretes,
blocos de estudo, documentos, conversas e vocabulário aprendido.

O que **não** entra, e por quê: hash de senha (material de ataque offline que
não serve para nada fora daqui), tokens, hash de IP, e id interno de outras
pessoas. Exportar dado do titular não pode virar exportação de dado de
terceiro. O `export_user` é escrito campo a campo de propósito — serializar o
modelo por reflexão parece elegante até o dia em que alguém adiciona uma coluna
sensível e ela sai no arquivo de todo mundo sem ninguém perceber.

A resposta vai com `Cache-Control: no-store, private`: é a agenda inteira de
uma pessoa, e não pode encostar em cache de proxy.

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
pytest tests/test_security.py -q      # testes adversariais de isolamento
pytest tests/test_cenarios.py -q      # jornadas reais: quem vê o quê
pytest tests/test_pagamento.py -q     # ataques ao webhook de cobrança
pytest tests/test_indicacao.py -q     # fraude de indicação
python -m agenda.cli check            # sanidade da configuração de produção
python -m pyflakes agenda tests       # análise estática
```

## Entrar com Google e com Apple

Authorization Code + PKCE, com `state`, `nonce` e verificação de assinatura
pela JWKS do provedor. Nunca implicit: token na URL fica no histórico, no
`Referer` e no log do servidor.

O id_token só vale depois de conferir, nesta ordem: assinatura (pela chave do
`kid`), `iss`, `aud`, `exp` e `nonce`. `jwt.decode(..., verify_signature=False)`
seria a linha que transforma o login num formulário de "digite quem você quer
ser" — é a vulnerabilidade "JWT não validado" da lista, e há teste afirmando
que uma assinatura de outra chave é recusada.

**O cookie de estado é separado do de sessão, de propósito.** A Apple responde
com `response_mode=form_post` (POST cross-site), e o cookie de sessão é
`SameSite=Lax`, que não acompanha POST cross-site. Em vez de afrouxar o cookie
principal — trocando uma integração por um buraco de CSRF em todo o app — o
`state`/`nonce`/`code_verifier` viajam num cookie próprio, assinado, de 10
minutos, `SameSite=None; Secure; HttpOnly`, restrito a `Path=/entrar`.

**Vinculação de conta e o pre-hijack.** O ataque: alguém cadastra
`vitima@gmail.com` com senha e nunca confirma o e-mail; meses depois a vítima
clica em "Entrar com Google" e, se a vinculação for ingênua, cai dentro da
conta do atacante — que continua com a senha e passa a ler tudo.

A regra: o provedor afirmando `email_verified` **prova** posse do e-mail; uma
conta local não confirmada **não prova nada**. Quando a conta local existe e
não está confirmada, a identidade do provedor vence — o e-mail passa a
confirmado, a senha é invalidada e todas as sessões caem. Quando já estava
confirmada, os dois lados provaram o mesmo e-mail e a vinculação é direta.

Conta nova **não é criada na volta do provedor**: falta ano de nascimento e
aceite. Criar antes disso seria tratamento sem base legal, e conta de menor
criada sozinha é exatamente o que o art. 14 proíbe. A identidade verificada
fica na sessão até a pessoa completar `/criar-conta/social`.

Configuração — sem chave, o botão não aparece:

| Variável | O que é |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Credenciais OAuth do Google Cloud |
| `APPLE_CLIENT_ID` | O **Services ID**, não o bundle do app |
| `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` | A Apple não dá segredo: a gente assina um JWT ES256 com a chave `.p8`, válido por 1 hora e gerado a cada login |

O `redirect_uri` registrado no provedor precisa ser
`https://<domínio>/entrar/<google|apple>/retorno`.

## Entrar com biometria (passkeys)

A biometria **nunca** chega ao servidor. Ela destrava o aparelho; o aparelho
assina um desafio com uma chave privada que vive no Secure Enclave / TPM e não
é exportável. Guardamos só a chave pública, que sozinha não abre nada — um dump
da tabela `passkeys` não dá acesso a conta nenhuma. É o oposto de uma senha,
cujo hash guardado é sempre material de ataque offline.

Passkey resiste a phishing por construção: a assinatura é amarrada ao `rp_id` e
à origem, então um site clonado em `gr1fo.app` não produz assinatura que a gente
aceite — mesmo que a pessoa caia no golpe. Há teste com um autenticador de
software (chave EC de verdade, assinatura de verdade) afirmando exatamente isso.

Três verificações que nenhum refactor pode remover:

1. **Desafio de uso único**, guardado do lado do servidor. Sem isso, uma
   assinatura capturada uma vez valeria para sempre.
2. **`userVerification: required`** — garante que houve biometria ou PIN, e não
   só um aparelho destravado em cima da mesa.
3. **Contador do autenticador.** Se `sign_count` volta para trás, a credencial
   pode ter sido clonada: recusamos e registramos. Autenticadores com
   sincronização em nuvem (iCloud Keychain) mandam 0 sempre — aí o contador não
   diz nada, e a regra só vale quando ele existe.

As opções de login vão com a lista de credenciais **vazia** de propósito:
devolver as credenciais de um e-mail transformaria a tela de login num
verificador de quem tem conta aqui.

| Variável | O que faz |
|---|---|
| `WEBAUTHN_RP_ID` | Domínio registrável, sem esquema e sem porta. Vazio = deriva de `PUBLIC_URL` |
| `WEBAUTHN_ORIGIN` | Origens aceitas, separadas por vírgula. Vazio = `PUBLIC_URL` |
