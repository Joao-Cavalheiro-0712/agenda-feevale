"""Captura a jornada do Grifo em formato de celular real.

Toda imagem sai 402x874 @3x (1206x2622). É o que a pessoa vê no aparelho dela,
e é o que cabe num slide sem virar uma tira ilegível. Telas longas ganham uma
segunda captura, rolada — a parte de baixo vira o próprio slide.
"""
import pathlib

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5177"
SAIDA = pathlib.Path("/tmp/grifo-demo/telas")
SAIDA.mkdir(parents=True, exist_ok=True)
CHROME = "/opt/pw-browsers/chromium"

ANA = ("ana@exemplo.com", "demonstracao123")
MAE = ("regina@exemplo.com", "demonstracao123")
TEO = ("teo@exemplo.com", "demonstracao123")

# (arquivo, url, quem, ação-antes, rolagem em px)
ROTEIRO = [
    ("01-landing",         "/",                     None, None, 0),
    ("02-landing-abaixo",  "/",                     None, None, 820),
    ("03-criar-conta",     "/criar-conta",          None, None, 0),
    ("04-entrar",          "/entrar",               None, None, 0),
    ("05-recuperar",       "/recuperar",            None, None, 0),

    ("06-tour-1",          "/apresentacao",         ANA,  None, 0),
    ("07-tour-2",          "/apresentacao",         ANA,  1,    0),
    ("08-tour-3",          "/apresentacao",         ANA,  2,    0),
    ("09-tour-4",          "/apresentacao",         ANA,  3,    0),
    ("10-tour-6",          "/apresentacao",         ANA,  5,    0),

    ("11-hoje",            "/hoje",                 ANA,  None, 0),
    ("12-hoje-entregas",   "/hoje",                 ANA,  None, 760),
    ("13-hoje-resumo",     "/hoje",                 ANA,  None, 1560),
    ("14-captura",         "/hoje",                 ANA,  "captura", 0),
    ("15-assistente",      "/assistente",           ANA,  None, 0),
    ("16-semana",          "/semana",               ANA,  None, 0),
    ("17-mes",             "/mes",                  ANA,  None, 0),
    ("18-agenda",          "/agenda",               ANA,  None, 0),
    ("19-entregas",        "/entregas",             ANA,  None, 0),
    ("20-linha-do-tempo",  "/linha-do-tempo",       ANA,  None, 0),
    ("21-materias",        "/materias",             ANA,  None, 0),
    ("22-plano-estudo",    "/plano-de-estudo",      ANA,  None, 0),
    ("23-documentos",      "/documentos",           ANA,  None, 0),
    ("24-notificacoes",    "/notificacoes",         ANA,  None, 0),
    ("25-buscar",          "/buscar?q=prova",       ANA,  None, 0),
    ("26-periodos",        "/periodos",             ANA,  None, 0),
    ("27-conectar",        "/conectar",             ANA,  None, 0),

    ("28-familia-mae",     "/familia",              MAE,  None, 0),
    ("29-hoje-crianca",    "/hoje",                 TEO,  None, 0),
    ("30-materias-crianca","/materias",             TEO,  None, 0),

    ("31-planos",          "/planos",               ANA,  None, 0),
    ("32-planos-lista",    "/planos",               ANA,  None, 700),
    ("33-planos-anual",    "/planos?ciclo=ANNUAL",  ANA,  None, 640),
    ("34-indicar",         "/convidar",             ANA,  None, 0),
    ("35-perfil",          "/perfil",               ANA,  None, 0),
    ("36-seguranca",       "/conta/seguranca",      ANA,  None, 0),
    ("37-passkey",         "/conta/seguranca",      ANA,  None, 620),
    ("38-privacidade",     "/conta/privacidade",    ANA,  None, 0),
    ("39-privacidade-dir", "/conta/privacidade",    ANA,  None, 560),
    ("40-termos",          "/termos",               ANA,  None, 0),
]


def entrar(page, credencial):
    email, senha = credencial
    page.goto(f"{BASE}/entrar", wait_until="networkidle")
    page.fill('input[name=email]', email)
    page.fill('input[name=password]', senha)
    page.click('button[type=submit]')
    page.wait_for_load_state("networkidle")


def main():
    erros = []
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(executable_path=CHROME)
        opcoes = dict(viewport={"width": 402, "height": 874}, device_scale_factor=3,
                      locale="pt-BR", timezone_id="America/Sao_Paulo",
                      color_scheme="light", reduced_motion="reduce")

        sessoes = {}
        for cred in (ANA, MAE, TEO):
            pg = navegador.new_context(**opcoes).new_page()
            entrar(pg, cred)
            sessoes[cred] = pg
        anonimo = navegador.new_context(**opcoes).new_page()

        for nome, url, quem, antes, rolagem in ROTEIRO:
            page = sessoes[quem] if quem else anonimo
            try:
                page.goto(f"{BASE}{url}", wait_until="networkidle")
                page.wait_for_timeout(650)

                if isinstance(antes, int):
                    for _ in range(antes):
                        page.click("#tour-avancar")
                        page.wait_for_timeout(480)
                    page.wait_for_timeout(650)
                elif antes == "captura":
                    page.click('.fab, [data-open-sheet], [aria-label*="apturar"]')
                    page.wait_for_timeout(750)

                if rolagem:
                    page.evaluate(f"window.scrollTo(0, {rolagem})")
                    page.wait_for_timeout(500)

                destino = SAIDA / f"{nome}.png"
                page.screenshot(path=str(destino))
                print(f"  ok  {nome:22s} {destino.stat().st_size//1024:4d} KB")
            except Exception as erro:
                erros.append((nome, str(erro)[:110]))
                print(f"  FALHOU {nome}: {str(erro)[:110]}")
        navegador.close()

    print(f"\n{len(list(SAIDA.glob('*.png')))} telas")
    if erros:
        print("erros:", erros)


main()
