# LGPD — o que está implementado, o que é decisão de negócio

> **Aviso interno, para ficar registrado:** os textos de `agenda/legal/documents.py`
> são minutas. Foram escritas para descrever com precisão o que o sistema faz —
> que é a parte que ninguém além de quem escreveu o código consegue fazer — mas
> **precisam de revisão por advogado antes do lançamento comercial**, com
> atenção especial ao tratamento de dados de menores e às cláusulas de limitação
> de responsabilidade. Um documento não blinda ninguém: ele reduz risco quando
> descreve a realidade. Se o produto mudar, o texto muda junto e a versão sobe.

## 1. Idade e capacidade civil

A regra do produto é mais restritiva que a LGPD, de propósito:

| Situação | O que acontece |
|---|---|
| 18 anos ou mais | Cria a própria conta, aceita os documentos, consente por si |
| Menor de 18 | **Não cria conta sozinho.** A conta é criada pelo responsável, autenticado |

Por que 18 e não 16: a LGPD trata de consentimento a partir dos 16 (art. 14),
mas criar conta também é **aceitar um contrato**, e aí vale o Código Civil.
Adotar o critério mais alto elimina a zona cinzenta do adolescente de 16–17 anos
aceitando termos de um serviço pago.

O caminho do menor:

1. O responsável cria a conta dele (adulto, aceite normal).
2. Vai em **Família → Criar conta de estudante** (`/familia/novo-estudante`).
3. Preenche nome, ano de nascimento, e-mail e senha do filho, declara o vínculo
   e autoriza — consentimento **específico e em destaque**, como pede o art. 14 §1º.
4. O estudante recebe o login e usa o app **no celular dele**, com a experiência
   do nível dele. O responsável acompanha do celular dele, pelo vínculo, com as
   permissões do vínculo. Ninguém entra "dentro" da conta do outro.

Padrões mais protetivos para menores, ligados automaticamente:

* automação silenciosa desligada (`auto_create_enabled = False`) — o critério é
  **a pessoa, não a série**: adulto no EJA ou no fundamental mantém tudo;
* interpretação automática **desligada por padrão** — o responsável precisa marcar;
* sem publicidade e sem perfilamento comportamental (não existem no produto);
* o responsável pode revisar, corrigir e pedir exclusão a qualquer momento.

### E se a criança mentir a idade?

Nenhum serviço na internet resolve isso sozinho — verificar idade de verdade
exigiria documento ou cartão de crédito, o que a lei **não** exige e criaria um
problema de privacidade maior do que o que resolve. O que a lei cobra é esforço
razoável, um caminho claro para o responsável e ação quando o fato chega ao seu
conhecimento. São três camadas:

1. **Pergunta direta no cadastro.** Idade declarada abaixo de 18 → conta não é
   criada, e a tela explica o caminho do responsável.
2. **Conferência cruzada no onboarding — uma pergunta, não um bloqueio.** Uma
   conta que se declarou adulta e escolhe educação infantil ou fundamental
   responde *"quem vai usar essa agenda?"*, com três saídas de mesmo peso:
   **EJA/supletivo** (adulto que voltou a estudar — ganha o perfil adulto),
   **sou eu mesmo neste nível** (segue como escolheu) e **é de uma criança**
   (leva à conta criada pelo responsável). Nenhuma delas tira recurso de
   ninguém. Ensino médio e técnico não disparam a pergunta: aos 18 é comum e
   legítimo estar neles.

   No Brasil, adulto no ensino básico não é exceção — é uma modalidade inteira,
   com gente que trabalha o dia todo e estuda à noite. Um bloqueio aqui erraria
   justamente com quem voltou a estudar, que é a última pessoa que este produto
   pode empurrar para fora. Por isso `EJA` é um nível de primeira classe, com
   vocabulário e tom de adulto, e não um caso especial do perfil infantil.
3. **Remediação quando alguém avisa.** O canal de privacidade recebe o aviso e a
   operação marca a conta no painel (`/admin`): ela é pausada na hora pela trava
   de consentimento, as sessões são encerradas, automação e IA são desligadas, e
   o acesso só volta com a autorização de um responsável. Nada é apagado — o
   titular e o responsável continuam podendo exportar e pedir exclusão.

Uma criança determinada a mentir passa pela camada 2 clicando "sou eu que
estudo". O que fica registrado é que perguntamos duas vezes, bloqueamos quando
soubemos e temos um caminho de correção — que é exatamente o que se cobra do
controlador. E, em qualquer cenário, o produto não faz publicidade, não faz
perfilamento e não vende dado — as três coisas que transformam um cadastro de
menor em processo.

## 2. Prova do consentimento (art. 8º §1º)

O ônus de provar que o titular consentiu é do controlador. A prova é a tabela
`consent_records`, uma linha por evento, nunca sobrescrita:

| Campo | Para que serve na prova |
|---|---|
| `kind` | qual consentimento (termos, política, responsável, IA, marketing) |
| `version` | qual versão do documento |
| `document_hash` | SHA-256 do texto exato aceito — verificável contra o repositório |
| `granted` | aceite ou revogação (a revogação é uma linha nova, não um `UPDATE`) |
| `ip_hash` | de onde veio, sem guardar IP em claro |
| `user_agent`, `origin` | por qual dispositivo e por qual caminho (`web`, `guardian`) |
| `guardian_*` | quem autorizou, quando é consentimento de responsável |
| `created_at` | quando |

O texto legal vive em código (`agenda/legal/documents.py`), não num CMS,
justamente para que o hash seja auditável: "o que exatamente eu aceitei em
março?" se responde com o histórico do git.

## 3. Trava de consentimento (fail-closed)

`agenda/web/deps.py:_consent_gate` roda no `before_request` de **toda**
requisição. Se houver pendência, nada do produto responde:

* menor sem consentimento do responsável → 403 com a tela explicando o caminho;
* documentos desatualizados → redireciona para `/aceite`;
* nas rotas `/api/` e `/webhooks/` → 403 JSON, sem vazar dados da agenda.

Continuam abertas só as rotas que o titular precisa mesmo estando travado: ler
os documentos, resolver a pendência, ver a central de privacidade, **exportar os
dados** e sair da conta. O canal do WhatsApp faz a mesma checagem em
`channels/whatsapp.py:process`, porque o webhook não passa pelo `before_request`.

A ordem importa: a falta de responsável é verificada **antes** da pendência de
documentos — não faz sentido mandar um menor para uma tela de aceite que ele não
tem capacidade de dar.

## 4. Registro das operações (art. 37)

`privacy.TREATMENT_RECORD` — seis operações, cada uma com finalidade, dados,
base legal e prazo de retenção. É a mesma fonte que alimenta a tabela da
política de privacidade e a central de privacidade do usuário: se mudar o
código, muda o documento. Não há duas versões da verdade.

`privacy.SUBPROCESSORS` — Railway (hospedagem), Google/Gemini (interpretação),
Meta/WhatsApp (mensagens). Todos nos Estados Unidos, o que caracteriza
**transferência internacional** e por isso está declarado na política (arts. 33 e 34).

## 5. Revogação e o que ela desliga (art. 8º §5º)

O único consentimento tecnicamente revogável mantendo a conta é o de
**interpretação automática**. Ao revogar (`/conta/privacidade`):

* `user.ai_processing_enabled = False`;
* `privacy.ai_allowed()` passa a barrar os quatro pontos onde conteúdo sairia
  daqui: interpretação de texto, transcrição de áudio (web e WhatsApp), leitura
  de imagem e extração de documento;
* o app **continua funcionando** com a heurística local, que roda inteiramente
  no nosso servidor.

Termos e política não são revogáveis com a conta aberta — sem eles não há
contrato. O caminho é a exclusão da conta, que está no mesmo lugar.

## 6. Direitos do titular (art. 18)

| Direito | Onde |
|---|---|
| Confirmação e acesso | `/conta/privacidade` e o app inteiro |
| Portabilidade | `/api/export` — JSON completo, funciona até com a conta travada |
| Eliminação | `/conta/seguranca` → excluir conta |
| Informação sobre compartilhamento | tabela de subprocessadores, nas duas telas |
| Revogação | `/conta/privacidade` |
| Correção e oposição | canal de privacidade (`PRIVACY_EMAIL`), resposta em 15 dias |

## 7. O que ainda depende de decisão de negócio

Estes itens **não são código** — nenhum deles pode ser resolvido aqui dentro:

1. **Encarregado (DPO), art. 41.** `DPO_NAME` está como "a definir antes do
   lançamento comercial". Precisa de nome e canal público reais.
2. **CNPJ e razão social do controlador.** `COMPANY_NAME` / `COMPANY_DOC`.
3. **E-mail de privacidade que alguém realmente leia.** `PRIVACY_EMAIL` hoje
   aponta para um endereço que precisa existir e ter dono.
4. **Contrato com os subprocessadores**, com as cláusulas de transferência
   internacional que a política afirma existirem.
5. **Revisão jurídica das minutas**, com atenção a menores e a limitação de
   responsabilidade.
6. **Procedimento de resposta a incidente** (art. 48): quem decide, em quanto
   tempo, quem comunica a ANPD.

Enquanto (1)–(3) não estiverem preenchidos por variável de ambiente, a política
publicada diz literalmente "a definir" — o que é honesto, mas não é lançável.

## 8. Onde está cada coisa

| Arquivo | Papel |
|---|---|
| `agenda/core/privacy.py` | consentimento, idade, registro de tratamento, trava |
| `agenda/legal/documents.py` | texto dos termos e da política, versionado |
| `agenda/models.py` | `ConsentRecord`, `ConsentKind`, campos de idade no `User` |
| `agenda/web/deps.py` | `_consent_gate` — a trava fail-closed |
| `agenda/web/pages.py` | `/termos`, `/privacidade`, `/aceite`, `/conta/privacidade`, `/familia/novo-estudante` |
| `agenda/core/family.py` | `create_student_account` — conta do filho pelo responsável |
| `tests/test_privacidade.py` | 20 testes: prova, menores, revogação, re-aceite |
