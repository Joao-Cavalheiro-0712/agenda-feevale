"""Envio de e-mail — provedor plugável, falha honesta.

Igual ao resto do produto: interface abstrata, provedor por variável de
ambiente, e sem chave o sistema **diz que não enviou** em vez de fingir. Um
e-mail de recuperação que "foi enviado" e nunca chega é pior que um erro
visível, porque a pessoa fica esperando.

Dois caminhos para o mundo real: `RESEND_API_KEY` sozinha (HTTPS, erro
legível) ou os cinco campos de SMTP (serve qualquer provedor). Em
desenvolvimento o provedor nulo imprime o link no console, e dá para testar o
fluxo inteiro sem configurar nada.
"""
from __future__ import annotations

import dataclasses
import smtplib
import ssl
from email.message import EmailMessage

from agenda import config


@dataclasses.dataclass(frozen=True)
class Envio:
    ok: bool
    detalhe: str = ""


class EmailProvider:
    name = "base"

    @property
    def configured(self) -> bool:
        raise NotImplementedError

    def send(self, *, to: str, subject: str, text: str, html: str = "") -> Envio:
        raise NotImplementedError


class NullEmailProvider(EmailProvider):
    """Sem provedor: registra no log e avisa que não enviou.

    O corpo vai para o console em desenvolvimento porque é a única forma de
    testar recuperação de senha sem SMTP. Em produção só o assunto e o
    destinatário mascarado são registrados — o corpo carrega o token.
    """

    name = "none"

    @property
    def configured(self) -> bool:
        return False

    def send(self, *, to: str, subject: str, text: str, html: str = "") -> Envio:
        if config.IS_PRODUCTION:
            print(f"[email] NÃO ENVIADO (sem provedor) para {_mascarar(to)}: {subject}")
        else:
            print(f"\n[email:dev] para {to}\n[email:dev] {subject}\n{text}\n")
        return Envio(ok=False, detalhe="provedor de e-mail não configurado")


class ResendProvider(EmailProvider):
    """Resend pela API HTTPS — uma chave, e erro que explica o que houve.

    Por que preferir isto ao SMTP do próprio Resend, que também funciona:

    * **O erro é legível.** A API responde "domínio não verificado" ou "from
      inválido"; o SMTP responde um 550 genérico. Quando a recuperação de senha
      parar de funcionar às duas da manhã, a diferença entre os dois é a
      diferença entre consertar em um minuto e passar a noite adivinhando.
    * **Uma porta só.** HTTPS sai de qualquer lugar; 587 e 465 são bloqueados
      ou estrangulados por vários provedores de hospedagem.
    * **Devolve o id da mensagem**, que é o que se leva ao suporte quando o
      cliente jura que não recebeu.
    """

    name = "resend"
    URL = "https://api.resend.com/emails"

    @property
    def configured(self) -> bool:
        return bool(config.RESEND_API_KEY and config.EMAIL_FROM)

    def send(self, *, to: str, subject: str, text: str, html: str = "") -> Envio:
        if not self.configured:
            return Envio(ok=False, detalhe="Resend incompleto")

        import requests

        corpo = {"from": config.EMAIL_FROM, "to": [to], "subject": subject, "text": text}
        if html:
            corpo["html"] = html

        try:
            resposta = requests.post(
                self.URL,
                json=corpo,
                headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
                timeout=15,
            )
        except Exception as erro:  # noqa: BLE001 - rede é falível
            print(f"[email] Resend inacessível para {_mascarar(to)}: {erro}")
            return Envio(ok=False, detalhe=str(erro)[:200])

        if resposta.status_code >= 300:
            # A mensagem do provedor entra no log inteira: é ela que diz o que
            # consertar. A chave nunca aparece — ela vai só no cabeçalho.
            motivo = _motivo_do_resend(resposta)
            print(f"[email] Resend recusou para {_mascarar(to)}: {motivo}")
            return Envio(ok=False, detalhe=motivo[:200])

        return Envio(ok=True, detalhe=str((resposta.json() or {}).get("id", "")))


def _motivo_do_resend(resposta) -> str:
    try:
        dados = resposta.json() or {}
    except ValueError:
        return f"HTTP {resposta.status_code}"
    return str(dados.get("message") or dados.get("name") or f"HTTP {resposta.status_code}")


class SmtpEmailProvider(EmailProvider):
    """SMTP comum — serve Gmail, Resend, Postmark, SES e qualquer outro."""

    name = "smtp"

    @property
    def configured(self) -> bool:
        return bool(config.SMTP_HOST and config.EMAIL_FROM)

    def send(self, *, to: str, subject: str, text: str, html: str = "") -> Envio:
        if not self.configured:
            return Envio(ok=False, detalhe="SMTP incompleto")

        mensagem = EmailMessage()
        mensagem["Subject"] = subject
        mensagem["From"] = config.EMAIL_FROM
        mensagem["To"] = to
        mensagem.set_content(text)
        if html:
            mensagem.add_alternative(html, subtype="html")

        try:
            contexto = ssl.create_default_context()
            if config.SMTP_PORT == 465:
                with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                                      context=contexto, timeout=20) as servidor:
                    _autenticar(servidor)
                    servidor.send_message(mensagem)
            else:
                with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as servidor:
                    servidor.starttls(context=contexto)
                    _autenticar(servidor)
                    servidor.send_message(mensagem)
        except Exception as erro:  # noqa: BLE001 - rede e SMTP falham de muitas formas
            print(f"[email] falha ao enviar para {_mascarar(to)}: {erro}")
            return Envio(ok=False, detalhe=str(erro)[:200])
        return Envio(ok=True)


def _autenticar(servidor: smtplib.SMTP) -> None:
    if config.SMTP_USER:
        servidor.login(config.SMTP_USER, config.SMTP_PASSWORD)


def _mascarar(endereco: str) -> str:
    """Nunca registramos o e-mail inteiro no log."""
    nome, _, dominio = (endereco or "").partition("@")
    if not dominio:
        return "***"
    return f"{nome[:2]}***@{dominio}"


_provedor: EmailProvider | None = None


def provider() -> EmailProvider:
    """Resend se houver chave, SMTP se houver host, e o nulo honesto se não.

    A ordem importa: quem configurou os dois quis o Resend — ninguém coloca uma
    chave de API para continuar usando SMTP.
    """
    global _provedor
    if _provedor is None:
        if config.RESEND_API_KEY:
            _provedor = ResendProvider()
        elif config.SMTP_HOST:
            _provedor = SmtpEmailProvider()
        else:
            _provedor = NullEmailProvider()
    return _provedor


def reset_provider() -> None:
    global _provedor
    _provedor = None


def enabled() -> bool:
    return provider().configured


def send(*, to: str, subject: str, text: str, html: str = "") -> Envio:
    return provider().send(to=to, subject=subject, text=text, html=html)
