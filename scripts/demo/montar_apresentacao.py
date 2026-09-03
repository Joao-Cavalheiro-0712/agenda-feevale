"""Monta a apresentação comercial do Grifo em PDF.

Renderiza HTML no Chromium e imprime em PDF: o texto sai vetorial e os
screenshots entram na resolução nativa (1206x2622), o que dá ~4,6x de
sobreamostragem no tamanho impresso. Um PDF montado a partir de imagens de
página inteira ficaria pesado e borrado; este fica leve e nítido.
"""
import pathlib

from playwright.sync_api import sync_playwright

TELAS = "http://127.0.0.1:5177"          # serve as fontes da marca
DIR = pathlib.Path("/tmp/grifo-demo/telas")
SAIDA = pathlib.Path("/home/user/agenda-feevale/Grifo-apresentacao.pdf")


def tela(nome):
    return f"file://{DIR / (nome + '.png')}"


# --------------------------------------------------------------------------- #
# Roteiro da apresentação — ordem de venda, não ordem de menu
# --------------------------------------------------------------------------- #
def capa():
    return """
<section class="slide capa">
  <div class="capa-marca">GRIFO</div>
  <h1>A agenda que<br><em>se monta sozinha</em></h1>
  <p class="capa-sub">
    O estudante manda um áudio, uma foto do quadro ou o PDF do portal.
    O Grifo entende e devolve a semana organizada — com lembrete antes da hora.
  </p>
  <div class="capa-rodape">
    <span>Educação infantil ao doutorado</span>
    <span>Web e WhatsApp</span>
    <span>R$ 19,90 a R$ 39,90 / mês</span>
  </div>
</section>"""


def problema():
    return """
<section class="slide texto-cheio">
  <p class="eyebrow">O problema</p>
  <h2>A informação chega bagunçada.<br>Todo aluno resolve isso na memória.</h2>
  <div class="tres">
    <div><span class="num">1</span><h3>Chega de todo lado</h3>
      <p>Quadro na aula, áudio no grupo do WhatsApp, PDF no portal, recado da professora.</p></div>
    <div><span class="num">2</span><h3>Ninguém digita nada</h3>
      <p>Agenda que exige formulário é agenda abandonada na segunda semana.</p></div>
    <div><span class="num">3</span><h3>O prazo vence</h3>
      <p>Não é preguiça: é que organizar custa mais tempo do que a pessoa tem.</p></div>
  </div>
  <p class="remate">O Grifo tira o trabalho de organizar do aluno e deixa com o software.</p>
</section>"""


def como_funciona():
    return """
<section class="slide texto-cheio">
  <p class="eyebrow">Como funciona</p>
  <h2>Três passos, nenhum formulário</h2>
  <div class="tres passos">
    <div><span class="num">→</span><h3>Você manda</h3>
      <p>Texto torto, áudio no meio do corredor, foto do quadro, arquivo do portal.
         Vale pelo app ou pelo WhatsApp — é o mesmo cérebro atrás dos dois.</p></div>
    <div><span class="num">→</span><h3>Eu entendo</h3>
      <p>Base de conhecimento própria: abreviação, erro de português, gíria de sala.
         Só chama o modelo externo quando precisa — e isso segura o custo.</p></div>
    <div><span class="num">→</span><h3>Você confere</h3>
      <p>Nada é salvo sem aparecer como vai ficar. Quando tenho dúvida, pergunto
         em vez de chutar. E dá para desfazer com um toque.</p></div>
  </div>
  <p class="remate">A IA interpreta. O software decide. A pessoa confirma.</p>
</section>"""


def divisor(numero, titulo, linha):
    return f"""
<section class="slide divisor">
  <span class="divisor-num">{numero}</span>
  <h2>{titulo}</h2>
  <p>{linha}</p>
</section>"""


def slide(img, eyebrow, titulo, corpo, itens=(), destaque=""):
    lista = ""
    if itens:
        lista = "<ul>" + "".join(f"<li>{i}</li>" for i in itens) + "</ul>"
    marca = f'<p class="destaque">{destaque}</p>' if destaque else ""
    return f"""
<section class="slide um">
  <div class="fone"><img src="{tela(img)}" alt=""></div>
  <div class="fala">
    <p class="eyebrow">{eyebrow}</p>
    <h2>{titulo}</h2>
    <p class="corpo">{corpo}</p>
    {lista}{marca}
  </div>
</section>"""


def slide_duplo(imgs, eyebrow, titulo, corpo, itens=()):
    fones = "".join(f'<div class="fone"><img src="{tela(i)}" alt=""></div>' for i in imgs)
    lista = "<ul>" + "".join(f"<li>{i}</li>" for i in itens) + "</ul>" if itens else ""
    return f"""
<section class="slide dois">
  <div class="fala topo">
    <p class="eyebrow">{eyebrow}</p>
    <h2>{titulo}</h2>
    <p class="corpo">{corpo}</p>
    {lista}
  </div>
  <div class="fones">{fones}</div>
</section>"""


def galeria(imgs, eyebrow, titulo, legendas):
    fones = "".join(
        f'<figure><div class="fone"><img src="{tela(i)}" alt=""></div>'
        f'<figcaption>{l}</figcaption></figure>'
        for i, l in zip(imgs, legendas)
    )
    return f"""
<section class="slide galeria">
  <div class="galeria-cab">
    <p class="eyebrow">{eyebrow}</p>
    <h2>{titulo}</h2>
  </div>
  <div class="fileira">{fones}</div>
</section>"""


SLIDES = [
    capa(),
    problema(),
    como_funciona(),

    # ------------------------------------------------------------------ #
    divisor("01", "Primeiro contato",
            "Da página de entrada até a conta criada, sem fricção e dentro da LGPD."),
    slide("01-landing", "Página de entrada", "A promessa em uma frase",
          "Quem chega entende em cinco segundos o que o produto faz: você manda, "
          "ele organiza. Sem jargão de software, sem tour de recursos.",
          ["Mobile-first de verdade — desenhado para 360–430px",
           "Um só produto, da educação infantil ao doutorado"]),
    slide("02-landing-abaixo", "Página de entrada", "A prova antes do preço",
          "Logo abaixo, o que o app faz com o que a pessoa manda — e só depois "
          "o convite para criar conta.",
          ["Prova antes de pedir cadastro", "Preço visível, sem “fale com vendas”"]),
    slide("03-criar-conta", "Cadastro", "Curto, e já em conformidade",
          "Cinco campos. O ano de nascimento não é curiosidade: menor de 18 não "
          "cria conta sozinho — nem para aceitar contrato, nem para consentir "
          "com o tratamento dos próprios dados.",
          ["Aceite registrado com versão, data, IP embaralhado e hash do texto",
           "É a prova que a LGPD art. 8º §1º exige em juízo"],
          "Consentimento de IA separado do aceite dos termos — desligável a qualquer momento."),
    slide("04-entrar", "Login", "Três formas de entrar",
          "Senha, chave de acesso (Face ID ou digital) e login social. O botão "
          "de cada um só aparece quando ele está realmente configurado.",
          ["Face ID e digital: a biometria nunca sai do aparelho",
           "Google e Apple com PKCE, state, nonce e assinatura verificada"]),
    slide("05-recuperar", "Recuperação", "Uma resposta só, exista ou não a conta",
          "A tela responde exatamente a mesma coisa para um e-mail cadastrado e "
          "para um inexistente. Qualquer diferença aqui transformaria a "
          "recuperação num verificador de quem é cliente.",
          ["Link de uso único, válido por 30 minutos",
           "Trocar a senha derruba todas as sessões abertas"]),

    # ------------------------------------------------------------------ #
    divisor("02", "O treinamento",
            "Uma apresentação animada no primeiro acesso — pulável, e revista quando quiser."),
    galeria(["06-tour-1", "07-tour-2", "08-tour-3"],
            "Tour guiado", "Mostra a transformação, não a interface",
            ["A bagunça virando agenda", "O botão do meio", "A confirmação antes de salvar"]),
    galeria(["09-tour-4", "10-tour-6", "14-captura"],
            "Tour guiado", "E termina pedindo a primeira captura",
            ["O resultado na semana", "A primeira ação", "A caixa que aceita tudo"]),
    slide("06-tour-1", "Tour guiado", "O roteiro fala a língua de cada aluno",
          "O mesmo tour muda de vocabulário conforme o nível de ensino. Quem "
          "está no 2º ano não ouve falar de “artigo para submissão”; quem faz "
          "doutorado não vê “levar cartolina”.",
          ["Pular tem o mesmo peso visual do avançar",
           "Sob “menos animação” do sistema, tudo aparece parado",
           "Dá para rever pelas configurações, quando quiser"]),

    # ------------------------------------------------------------------ #
    divisor("03", "O dia a dia",
            "A tela que o aluno abre todo dia, e o caminho de tudo que entra nela."),
    slide("11-hoje", "Tela inicial", "O dia, na ordem que importa",
          "Aula, prova e entrega de hoje no topo — com sala, horário e matéria. "
          "Nada de calendário vazio pedindo para ser preenchido.",
          ["Saudação pelo horário e pelo período letivo certo",
           "Cada linha leva ao detalhe com um toque"]),
    slide("12-hoje-entregas", "Tela inicial", "E o que vem chegando",
          "Logo abaixo, as entregas próximas com a distância em dias — “amanhã”, "
          "“em 3 dias”, “em 1 semana”. É como a pessoa pensa, não como o banco guarda."),
    slide("13-hoje-resumo", "Tela inicial", "O resumo que evita o susto",
          "9 aulas, 4 entregas, 2 provas. O dia mais cheio da semana. E, no fim, "
          "as três coisas que resolvem o dia se só der para fazer três.",
          ["“Seu dia mais cheio é quinta” — antes de a quinta chegar",
           "Contagem regressiva do período letivo"]),
    slide("14-captura", "Captura", "Um botão, todos os formatos",
          "O “+” abre a mesma caixa para texto, áudio, foto e arquivo. Escrever "
          "torto funciona: “prova de Civil sexta sobre contratos” já basta.",
          ["Áudio direto, sem sair da tela",
           "Foto do quadro, print do portal, PDF, planilha"],
          "É o coração do produto: zero fricção entre lembrar e registrar."),
    slide("15-assistente", "Assistente", "Conversa, não formulário",
          "A pessoa pergunta “o que eu tenho essa semana?” e recebe a resposta em "
          "português. As mensagens do WhatsApp e do app entram na mesma conversa.",
          ["O mesmo núcleo atende web e WhatsApp",
           "Histórico preservado entre os canais"]),
    slide("16-semana", "Semana", "A semana inteira de relance",
          "Sete dias com o peso de cada um. Serve para responder “quando eu estudo?” "
          "sem abrir planilha.",
          ["Filtro por aulas, avaliações e entregas", "Navegação semana a semana"]),
    slide("17-mes", "Mês", "O mês, para enxergar o acúmulo",
          "Onde as provas se amontoam e onde há espaço. É a visão que o aluno usa "
          "para negociar prazo antes de virar problema.",
          ["Densidade visível: onde as provas se amontoam", "Serve para negociar prazo antes de virar problema"]),
    slide("18-agenda", "Agenda", "Tudo, em ordem, com filtro",
          "A lista completa do período — por matéria, por tipo, por status.",
          ["Filtros por matéria, tipo e status", "Mesma lista que alimenta o calendário exportado"]),
    slide("19-entregas", "Entregas", "Só o que tem prazo",
          "Trabalho, artigo, petição, fichamento. Com peso e nota máxima quando "
          "a pessoa informou, para saber o que vale mais.",
          ["Peso e nota máxima quando informados", "Ordenado por urgência real, não por data de criação"]),
    slide("20-linha-do-tempo", "Linha do tempo", "O semestre inteiro numa tela",
          "Passado e futuro do período, para planejar de verdade — e para "
          "arquivar sem apagar nada.",
          ["Períodos anteriores arquivados, não apagados", "Responde “quando foi a prova do semestre passado?”"]),
    slide("21-materias", "Matérias", "Cada disciplina com o que é dela",
          "Professor, sala, cor e horário. A cor não é enfeite: é o que faz a "
          "agenda ser lida de relance.",
          ["Professor, sala e cor por disciplina", "A cor é o que permite ler a agenda de relance"]),
    slide("22-plano-estudo", "Plano de estudo", "Blocos antes da prova",
          "O app quebra a prova em blocos de estudo e distribui nos dias que "
          "sobram — respeitando as aulas que já estão lá.",
          ["“100 minutos estudados” aparece na prova de hoje",
           "Não é cobrança: é crédito pelo que já foi feito"]),
    slide("23-documentos", "Documentos", "O PDF do portal vira agenda",
          "Plano de ensino, cronograma, edital. O app lê, extrai as datas e "
          "mostra o que entendeu antes de salvar.",
          ["PDF, Word, planilha e print do portal", "Sempre revisável antes de entrar na agenda"]),
    slide("24-notificacoes", "Lembretes", "Chegam antes, não no dia",
          "Sete dias e um dia antes, por padrão — no app, no push e no WhatsApp. "
          "Lembrete no dia da entrega não é lembrete, é aviso de fracasso.",
          ["No app, no push e no WhatsApp", "Sete dias e um dia antes, por padrão"]),
    slide("25-buscar", "Busca", "Acha pelo jeito que a pessoa fala",
          "Escreve “prova” e vem tudo. Escreve errado e vem também: a busca "
          "entende fonética do português.",
          ["Entende fonética do português: “cauculo” acha “Cálculo”", "Aprende o vocabulário de cada aluno com o uso"]),
    slide("26-periodos", "Períodos", "Semestre, bimestre, trimestre, módulo",
          "Sete formatos de período letivo, porque escola, faculdade e curso "
          "técnico não usam o mesmo calendário.",
          ["Sete formatos de período letivo", "O app aceita o calendário da instituição, não o contrário"]),
    slide("27-conectar", "WhatsApp", "O canal onde o aluno já vive",
          "Conecta o número e manda o áudio direto de lá, no meio da aula. "
          "Aparece no app na hora.",
          ["Mesmo núcleo, mesmas regras, mesma segurança",
           "Sem app novo para o aluno instalar"]),

    # ------------------------------------------------------------------ #
    divisor("04", "A família",
            "O responsável acompanha sem invadir. A criança usa o app dela."),
    slide_duplo(["28-familia-mae", "29-hoje-crianca"],
                "Contas ligadas", "Duas contas, dois aplicativos, um vínculo",
                "A mãe cria a conta do filho autenticada — que é a melhor prova "
                "possível para o art. 14 da LGPD. Ela acompanha pelo vínculo, "
                "com as permissões do vínculo; não entra “dentro” da conta dele.",
                ["A criança tem login próprio, no celular dela",
                 "O responsável vê o que o vínculo permite — e o filho sabe disso",
                 "Menor começa sem automação silenciosa, por padrão"]),
    slide("30-materias-crianca", "Educação infantil e fundamental",
          "Mesmo produto, outra língua",
          "“Tema de casa” em vez de “trabalho”. “Levar a cartolina” como tipo de "
          "compromisso de verdade. Um núcleo só, catorze níveis de ensino — "
          "incluindo EJA, porque adulto no fundamental é comum no Brasil.",
          ["A automação segue a pessoa, não a série",
           "Um adulto no EJA é um adulto: não perde recurso"]),

    # ------------------------------------------------------------------ #
    divisor("05", "O negócio",
            "Escada de planos por uso, ciclo anual e crescimento sem tráfego pago."),
    slide("31-planos", "Planos", "Todo plano pago tem o produto inteiro",
          "O que muda é quanto de uso cabe. É o formato certo para um produto "
          "de IA, porque o custo escala com o uso e não com o recurso: quem "
          "manda 40 áudios por dia custa mais que quem manda dois.",
          ["Estudante R$ 19,90 · Pro R$ 29,90 · Família R$ 39,90",
           "Anual com 20% de desconto",
           "Margem verificada por teste — nenhum plano nasce no prejuízo"]),
    slide("32-planos-lista", "Planos", "Limites que a pessoa entende",
          "Mensagens, minutos de áudio e documentos por mês — em números que o "
          "aluno consegue conferir, não em créditos inventados.",
          ["Aviso quando o uso se aproxima do limite", "Upgrade sem perder nada do que já está lá"]),
    slide("33-planos-anual", "Cobrança", "Cartão, Pix, Apple Pay e Google Pay",
          "As carteiras andam junto do cartão, sem digitar número. O Pix é "
          "pagamento avulso — compra um período e avisa três dias antes de acabar, "
          "porque fingir que Pix renova sozinho seria mentir para o cliente.",
          ["O valor cobrado sai sempre do nosso catálogo, nunca do navegador",
           "Webhook com assinatura, janela de replay e idempotência"]),
    slide("34-indicar", "Indicação", "Crescer sem gastar em tráfego",
          "Quem indica ganha mês grátis; quem entra pelo código ganha desconto na "
          "primeira cobrança. Programa de um lado só depende de altruísmo — com "
          "desconto para o convidado, o link vira presente.",
          ["Recompensa só nasce depois do pagamento e da janela de reembolso",
           "Três indicações pagantes = R$ 6,63 de custo por assinante"],
          "Nenhum tráfego pago no Brasil bate esse custo de aquisição."),

    # ------------------------------------------------------------------ #
    divisor("06", "Confiança",
            "Segurança e privacidade não são tela de configuração: são o produto."),
    slide("36-seguranca", "Segurança da conta", "A pessoa vê quem está conectado",
          "Todos os dispositivos ativos, com data e último acesso — e o botão de "
          "desconectar do lado.",
          ["Sessão guardada no banco, nunca só no cookie: dá para revogar",
           "Trocar a senha derruba os outros aparelhos"]),
    slide("37-passkey", "Entrar sem senha", "Face ID, digital, Windows Hello",
          "A biometria nunca chega ao servidor: ela destrava o aparelho, e o "
          "aparelho assina uma prova. Guardamos só a chave pública, que sozinha "
          "não abre nada.",
          ["Resiste a phishing por construção: a assinatura é presa ao domínio",
           "Um vazamento da tabela não dá acesso a conta nenhuma"]),
    slide("38-privacidade", "Central de privacidade", "Ligar e desligar a IA, em um toque",
          "A interpretação automática é consentimento separado e revogável. "
          "Desligada, o app continua funcionando no modo manual — sem punição.",
          ["Registro de tratamento visível ao titular (art. 37)",
           "Subprocessadores listados, com transferência internacional declarada"]),
    slide("39-privacidade-dir", "Seus direitos", "Levar embora ou apagar, sem pedir",
          "Exportação completa em JSON legível e exclusão da conta pelo próprio "
          "app. Portabilidade de verdade, não formato proprietário.",
          ["O arquivo não leva hash de senha, token nem dado de terceiro",
           "Histórico de consentimentos com versão e data"]),
    slide("40-termos", "Documentos", "Termos e política escritos para ler",
          "Dez seções de termos e onze de política, em português claro — com "
          "versão e hash guardados junto de cada aceite.",
          ["Versão e hash guardados junto de cada aceite", "Encarregado de dados e canal do titular declarados"]),

    # ------------------------------------------------------------------ #
    """
<section class="slide texto-cheio fecho">
  <p class="eyebrow">Onde está hoje</p>
  <h2>Pronto para receber as chaves e vender</h2>
  <div class="numeros">
    <div><span class="n">568</span><span class="r">testes automatizados</span></div>
    <div><span class="n">14</span><span class="r">níveis de ensino</span></div>
    <div><span class="n">38</span><span class="r">tabelas em produção</span></div>
    <div><span class="n">20/20</span><span class="r">vulnerabilidades cobertas</span></div>
  </div>
  <div class="fecho-grade">
    <div><h3>No ar</h3><p>Publicado, com banco gerenciado, volume persistente,
      backup diário com retenção em escada e restauração testada.</p></div>
    <div><h3>Falta só a chave</h3><p>IA, e-mail, pagamento e login social falham
      fechados até a credencial entrar — nada finge que funciona.</p></div>
    <div><h3>Segurança auditada</h3><p>A lista das vinte vulnerabilidades mais
      comuns de SaaS virou suíte de teste, e roda a cada mudança.</p></div>
  </div>
  <p class="remate">Grifo — a agenda que se monta sozinha.</p>
</section>""",
]


CSS = """
@font-face { font-family: "Fraunces"; src: url("URLBASE/static/fonts/fraunces-latin.woff2") format("woff2");
             font-weight: 100 900; font-display: block; }
@font-face { font-family: "Space Grotesk"; src: url("URLBASE/static/fonts/space-grotesk-latin.woff2") format("woff2");
             font-weight: 300 700; font-display: block; }

@page { size: 338mm 190mm; margin: 0; }

:root {
  --paper: #faf6ef; --paper-2: #f3ede2; --card: #fffdf9;
  --ink: #16130e; --ink-2: #4b463d; --ink-3: #7d766a;
  --rule: #e2d9c9; --rule-strong: #cbbfa9;
  --grifo: #d8f24b; --grifo-deep: #c2dd2c;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body { font-family: "Space Grotesk", sans-serif; color: var(--ink);
       background: var(--paper); -webkit-print-color-adjust: exact;
       print-color-adjust: exact; }

.slide {
  width: 338mm; height: 190mm; padding: 16mm 18mm;
  background: var(--paper); position: relative; overflow: hidden;
  page-break-after: always; break-after: page;
  display: flex; flex-direction: column;
}
.slide:last-child { page-break-after: auto; }

.eyebrow { font-size: 3.1mm; font-weight: 700; letter-spacing: .22em;
           text-transform: uppercase; color: var(--ink-3); margin-bottom: 3mm; }

h1 { font-family: "Fraunces", serif; font-weight: 600; letter-spacing: -.02em; }
h2 { font-family: "Fraunces", serif; font-weight: 600; letter-spacing: -.015em;
     font-size: 11mm; line-height: 1.08; text-wrap: balance; }
h3 { font-size: 4.4mm; font-weight: 700; margin-bottom: 1.8mm; }

/* --- Capa ------------------------------------------------------------- */
.capa { justify-content: center; background: var(--ink); color: var(--paper); }
.capa-marca { font-family: "Fraunces", serif; font-size: 5mm; font-weight: 700;
              letter-spacing: .42em; color: var(--grifo); margin-bottom: 12mm; }
.capa h1 { font-size: 25mm; line-height: 1.12; max-width: 260mm; color: var(--paper); }
.capa h1 br { line-height: 0; }
.capa h1 em { font-style: normal; background: var(--grifo); color: var(--ink);
              padding: 0 2mm; }
.capa-sub { font-size: 5.4mm; line-height: 1.55; color: #cfc7b8;
            max-width: 165mm; margin-top: 9mm; }
.capa-rodape { position: absolute; left: 18mm; right: 18mm; bottom: 14mm;
               display: flex; gap: 10mm; font-size: 3.4mm; letter-spacing: .12em;
               text-transform: uppercase; color: #8d8578;
               border-top: 1px solid #3a352c; padding-top: 5mm; }

/* --- Divisores --------------------------------------------------------- */
.divisor { justify-content: center; background: var(--paper-2); }
.divisor-num { font-family: "Fraunces", serif; font-size: 8mm; font-weight: 700;
               color: var(--grifo-deep); letter-spacing: .1em; margin-bottom: 5mm; }
.divisor h2 { font-size: 22mm; }
.divisor p { font-size: 5.6mm; color: var(--ink-2); margin-top: 6mm; max-width: 190mm; }

/* --- Slide com um celular ---------------------------------------------- */
.um { flex-direction: row; align-items: center; gap: 20mm; }
.um .fone { flex: 0 0 auto; }
.um .fala { flex: 1 1 auto; max-width: 150mm; }

.fone img { height: 152mm; width: auto; display: block;
            border-radius: 6mm; border: 1px solid var(--rule);
            box-shadow: 0 3mm 14mm rgba(22,19,14,.10); }

.corpo { font-size: 4.9mm; line-height: 1.55; color: var(--ink-2); margin-top: 5mm; }
.fala ul { margin-top: 6mm; list-style: none; display: flex;
           flex-direction: column; gap: 3mm; }
.fala li { font-size: 4.2mm; line-height: 1.45; color: var(--ink-2);
           padding-left: 7mm; position: relative; }
.fala li::before { content: ""; position: absolute; left: 0; top: 1.9mm;
                   width: 4mm; height: 1.6mm; background: var(--grifo);
                   border-radius: 1mm; }
.destaque { margin-top: 7mm; font-size: 4.3mm; font-weight: 600; line-height: 1.45;
            padding: 4mm 5mm; background: var(--card);
            border-left: 1.2mm solid var(--grifo-deep); border-radius: 0 2mm 2mm 0; }

/* --- Slide com dois celulares ------------------------------------------ */
.dois { gap: 8mm; }
.dois .fala.topo { max-width: 215mm; }
.dois .fones { display: flex; gap: 14mm; align-items: flex-start;
               justify-content: center; margin-top: 4mm; }
.dois .fone img { height: 92mm; }

/* --- Galeria de três ---------------------------------------------------- */
.galeria-cab { margin-bottom: 6mm; }
.galeria .fileira { display: flex; gap: 16mm; justify-content: center;
                    align-items: flex-start; flex: 1; }
.galeria figure { display: flex; flex-direction: column; align-items: center; gap: 4mm; }
.galeria .fone img { height: 124mm; }
.galeria figcaption { font-size: 3.7mm; color: var(--ink-3); text-align: center;
                      letter-spacing: .04em; }

/* --- Slides de texto ---------------------------------------------------- */
.texto-cheio { justify-content: center; }
.texto-cheio h2 { font-size: 15mm; max-width: 230mm; }
.tres { display: flex; gap: 14mm; margin-top: 14mm; }
.tres > div { flex: 1; }
.tres .num { font-family: "Fraunces", serif; font-size: 9mm; font-weight: 700;
             color: var(--grifo-deep); display: block; margin-bottom: 3mm; }
.tres p { font-size: 4.3mm; line-height: 1.5; color: var(--ink-2); }
.passos .num { font-size: 11mm; }
.remate { margin-top: 14mm; font-size: 5.4mm; font-weight: 600;
          padding-top: 6mm; border-top: 2px solid var(--ink); }

/* --- Fecho -------------------------------------------------------------- */
.numeros { display: flex; gap: 6mm; margin: 11mm 0 10mm; }
.numeros > div { flex: 1; background: var(--card); border: 1px solid var(--rule);
                 border-radius: 3mm; padding: 6mm; }
.numeros .n { font-family: "Fraunces", serif; font-size: 13mm; font-weight: 600;
              display: block; line-height: 1; }
.numeros .r { font-size: 3.3mm; letter-spacing: .1em; text-transform: uppercase;
              color: var(--ink-3); margin-top: 2.5mm; display: block; }
.fecho-grade { display: flex; gap: 12mm; }
.fecho-grade > div { flex: 1; }
.fecho-grade p { font-size: 4.1mm; line-height: 1.5; color: var(--ink-2); }
.fecho .remate { margin-top: 11mm; }
"""


def main():
    html = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Grifo — apresentação</title>
<style>{CSS.replace('URLBASE', TELAS)}</style></head><body>
{''.join(SLIDES)}
</body></html>"""

    origem = pathlib.Path("/tmp/grifo-demo/deck/deck.html")
    origem.write_text(html, encoding="utf-8")

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        page = navegador.new_page()
        page.goto(f"file://{origem}", wait_until="networkidle")
        page.wait_for_timeout(2500)          # fontes e imagens
        page.pdf(path=str(SAIDA), width="338mm", height="190mm",
                 print_background=True, prefer_css_page_size=True, scale=1)
        navegador.close()

    tamanho = SAIDA.stat().st_size / 1_048_576
    print(f"PDF: {SAIDA}  ({tamanho:.1f} MB, {len(SLIDES)} slides)")


main()
