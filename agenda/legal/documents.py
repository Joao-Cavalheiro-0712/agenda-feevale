"""Conteúdo dos Termos de Uso e da Política de Privacidade."""
from __future__ import annotations

from agenda import config

APP = config.APP_NAME
CONTATO = config.PRIVACY_EMAIL
ENCARREGADO = config.DPO_NAME


def plain_text(secoes: list[dict]) -> str:
    """Versão em texto puro — é dela que sai o hash guardado no consentimento."""
    partes: list[str] = []
    for secao in secoes:
        partes.append(secao["titulo"])
        for bloco in secao["blocos"]:
            if isinstance(bloco, str):
                partes.append(bloco)
            else:
                partes.extend(bloco.get("itens", []))
    return "\n".join(partes)


TERMS_SECTIONS: list[dict] = [
    {
        "titulo": "1. O que é o serviço",
        "blocos": [
            f"O {APP} é um organizador de agenda acadêmica. Você envia informações "
            "sobre seus estudos — por texto, voz, foto ou arquivo — e o serviço "
            "transforma isso em compromissos, prazos e lembretes.",
            "O serviço é uma ferramenta de organização pessoal. Ele não substitui "
            "os canais oficiais da sua instituição de ensino, nem garante que as "
            "informações repassadas por você estejam corretas ou atualizadas.",
        ],
    },
    {
        "titulo": "2. Quem pode usar",
        "blocos": [
            "Para criar uma conta por conta própria você precisa ter 16 anos ou mais.",
            "Quem tem menos de 16 anos só pode usar o serviço com o consentimento "
            "de um dos pais ou do responsável legal, dado no momento do cadastro. "
            "O responsável pode, a qualquer momento, revisar, corrigir ou pedir a "
            "exclusão dos dados do menor.",
            "Você é responsável pela veracidade das informações que fornece e por "
            "manter sua senha em segredo. Avise imediatamente se suspeitar que "
            "alguém acessou sua conta.",
        ],
    },
    {
        "titulo": "3. O conteúdo que você envia continua seu",
        "blocos": [
            "Cronogramas, arquivos, áudios e anotações que você envia continuam "
            "sendo seus. Você nos autoriza apenas a processá-los para prestar o "
            "serviço: extrair compromissos, montar sua agenda e enviar lembretes.",
            "Não vendemos seus dados, não os usamos para publicidade e não "
            "exibimos anúncios comportamentais — em hipótese alguma para menores.",
            "Você é responsável pelo que envia. Não envie material de terceiros "
            "sem autorização, nem conteúdo ilegal.",
        ],
    },
    {
        "titulo": "4. Sobre a interpretação automática",
        "blocos": [
            "O serviço usa interpretação automática para ler o que você envia. "
            "Ela erra às vezes: pode não entender uma data, trocar uma matéria ou "
            "deixar passar um item.",
            "Por isso o serviço mostra o que entendeu antes de salvar, indica o "
            "grau de confiança de cada item e permite desfazer qualquer ação "
            "automática. Confira sempre as datas que importam — prova, entrega, "
            "prazo de matrícula — na fonte oficial.",
            "Não nos responsabilizamos por prejuízo decorrente de informação "
            "incorreta que você tenha nos fornecido, ou de item que você deixou de "
            "conferir na revisão.",
        ],
    },
    {
        "titulo": "5. Planos e pagamento",
        "blocos": [
            "Há um plano gratuito com limites de uso e planos pagos com limites "
            "maiores, descritos na página de planos.",
            "Assinaturas são cobradas por período e podem ser canceladas a "
            "qualquer momento; o acesso continua até o fim do período já pago. "
            "Mudanças de preço são avisadas com no mínimo 30 dias de antecedência "
            "e valem para o período seguinte.",
            "Você pode pedir reembolso em até 7 dias da contratação, conforme o "
            "art. 49 do Código de Defesa do Consumidor.",
        ],
    },
    {
        "titulo": "6. Uso aceitável",
        "blocos": [
            "Não é permitido tentar acessar a conta ou os dados de outra pessoa, "
            "sobrecarregar a infraestrutura, contornar limites de uso, extrair "
            "dados em massa ou usar o serviço para fim ilícito.",
            "Contas que violarem estas regras podem ser suspensas. Quando "
            "possível, avisamos antes e damos chance de corrigir.",
        ],
    },
    {
        "titulo": "7. Disponibilidade",
        "blocos": [
            "Trabalhamos para manter o serviço no ar, mas ele é fornecido no "
            "estado em que está. Pode haver interrupção para manutenção, falha de "
            "terceiro ou indisponibilidade fora do nosso controle.",
            "Recomendamos manter uma cópia própria das informações críticas. Você "
            "pode exportar todos os seus dados a qualquer momento pelo perfil.",
        ],
    },
    {
        "titulo": "8. Encerramento",
        "blocos": [
            "Você pode excluir sua conta quando quiser, pelo próprio aplicativo. A "
            "exclusão remove seus dados pessoais conforme a Política de "
            "Privacidade.",
            "Podemos encerrar o serviço mediante aviso prévio de 30 dias, com "
            "tempo para você exportar seus dados.",
        ],
    },
    {
        "titulo": "9. Mudanças nestes termos",
        "blocos": [
            "Se mudarmos algo relevante, avisamos no aplicativo e pedimos um novo "
            "aceite. Guardamos o registro de qual versão você aceitou e quando.",
        ],
    },
    {
        "titulo": "10. Lei aplicável",
        "blocos": [
            "Estes termos seguem a lei brasileira. Fica eleito o foro do domicílio "
            "do consumidor para resolver qualquer questão, conforme o Código de "
            "Defesa do Consumidor.",
            f"Dúvidas: {CONTATO}.",
        ],
    },
]


PRIVACY_SECTIONS: list[dict] = [
    {
        "titulo": "Resumo em uma tela",
        "blocos": [
            "Coletamos o mínimo necessário para montar sua agenda e te lembrar na "
            "hora certa. Não vendemos seus dados nem fazemos publicidade com eles.",
            {
                "tipo": "lista",
                "itens": [
                    "Você pode exportar tudo em um arquivo, a qualquer momento.",
                    "Você pode apagar sua conta e seus dados, a qualquer momento.",
                    "Áudios são transcritos e depois descartados no prazo definido.",
                    "Menores de 16 anos só usam o serviço com consentimento do responsável.",
                    "Guardamos o IP apenas como código embaralhado, nunca em claro.",
                ],
            },
        ],
    },
    {
        "titulo": "Quem é o controlador",
        "blocos": [
            f"O {APP} é o controlador dos dados tratados no serviço.",
            f"Encarregado pelo tratamento de dados (DPO): {ENCARREGADO}. "
            f"Contato para qualquer assunto de privacidade: {CONTATO}.",
        ],
    },
    {
        "titulo": "Que dados tratamos e por quê",
        "blocos": [
            "Cada finalidade tem sua base legal. Não usamos um dado coletado para "
            "uma finalidade em outra sem te avisar.",
            {"tipo": "tabela_tratamento"},
        ],
    },
    {
        "titulo": "O conteúdo que você envia",
        "blocos": [
            "Arquivos, fotos, áudios e mensagens são processados para extrair "
            "compromissos. Para isso, o conteúdo pode ser enviado a um provedor de "
            "interpretação automática — só o conteúdo necessário, sem os "
            "identificadores da sua conta.",
            "O áudio é transcrito e a gravação é descartada conforme o prazo de "
            "retenção. A transcrição fica junto do compromisso criado, para você "
            "saber de onde veio cada item.",
            "Se você não quiser esse processamento, pode revogar o consentimento "
            "de interpretação automática no perfil. O serviço continua "
            "funcionando com cadastro manual.",
        ],
    },
    {
        "titulo": "Com quem compartilhamos",
        "blocos": [
            "Só com quem é necessário para o serviço funcionar. Nenhum deles pode "
            "usar seus dados para finalidade própria.",
            {"tipo": "tabela_subprocessadores"},
            "Esses fornecedores processam dados fora do Brasil. A transferência "
            "internacional acontece com base em cláusulas contratuais e nas "
            "garantias exigidas pelos arts. 33 e 34 da LGPD.",
        ],
    },
    {
        "titulo": "Crianças e adolescentes",
        "blocos": [
            "O serviço atende estudantes de todas as idades, inclusive de escola. "
            "Tratamos dados de menores de 16 anos apenas com consentimento "
            "específico e destacado de um dos pais ou do responsável legal, dado no "
            "cadastro e registrado com data e hora.",
            "O melhor interesse da criança orienta as decisões do produto: nada de "
            "publicidade, nada de perfilamento comportamental, automação "
            "desligada por padrão nesses perfis e coleta reduzida ao mínimo.",
            "O responsável pode, a qualquer momento, acessar, corrigir ou pedir a "
            "exclusão dos dados do menor pelo contato de privacidade.",
        ],
    },
    {
        "titulo": "Por quanto tempo guardamos",
        "blocos": [
            "Enquanto sua conta existir, para os dados da agenda. Depois do pedido "
            "de exclusão, removemos ou anonimizamos em até 30 dias.",
            "Alguns registros são mantidos por prazo próprio: tentativas de login "
            "por 30 dias (segurança) e registros de auditoria por 12 meses. Dados "
            "necessários para cumprir obrigação legal são mantidos pelo prazo "
            "exigido em lei.",
        ],
    },
    {
        "titulo": "Seus direitos",
        "blocos": [
            "Você pode, a qualquer momento e sem custo:",
            {
                "tipo": "lista",
                "itens": [
                    "confirmar se tratamos seus dados e acessá-los;",
                    "corrigir dados incompletos ou desatualizados;",
                    "pedir a portabilidade em formato aberto (o botão de exportar);",
                    "pedir a eliminação dos dados tratados com base em consentimento;",
                    "saber com quem compartilhamos seus dados;",
                    "revogar consentimentos, sabendo o que isso desliga;",
                    "se opor a um tratamento feito com base em legítimo interesse.",
                ],
            },
            f"Exportar e excluir estão no seu perfil. Para os demais pedidos, "
            f"escreva para {CONTATO} — respondemos em até 15 dias.",
            "Você também pode reclamar à Autoridade Nacional de Proteção de Dados "
            "(ANPD).",
        ],
    },
    {
        "titulo": "Segurança",
        "blocos": [
            "Tráfego criptografado, senhas guardadas apenas como hash, sessões "
            "revogáveis por dispositivo, isolamento entre contas verificado por "
            "testes automatizados e registro de auditoria das ações relevantes.",
            "Nenhum sistema é imune. Se acontecer um incidente que possa te causar "
            "risco relevante, comunicamos você e a ANPD, como manda o art. 48.",
        ],
    },
    {
        "titulo": "Cookies",
        "blocos": [
            "Usamos um único cookie, estritamente necessário para manter você "
            "conectado. Ele não serve para publicidade nem para rastrear você em "
            "outros sites. Por ser essencial ao funcionamento, não depende de "
            "consentimento — mas você pode sair da conta a qualquer momento.",
        ],
    },
    {
        "titulo": "Mudanças nesta política",
        "blocos": [
            "Se mudarmos algo relevante, avisamos no aplicativo e pedimos um novo "
            "aceite. O histórico de versões e a data do seu aceite ficam "
            "registrados na sua conta.",
        ],
    },
]
