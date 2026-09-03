"""Envio de e-mail — provedor plugável, falha honesta.

Igual ao resto do produto: interface abstrata, provedor por variável de
ambiente, e sem chave o sistema **diz que não enviou** em vez de fingir. Um
e-mail de recuperação que "foi enviado" e nunca chega é pior que um erro
visível, porque a pessoa fica esperando.

Em desenvolvimento o provedor nulo imprime o link no console: dá para testar o
fluxo inteiro sem SMTP nenhum.
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


class SmtpEmailProvider(EmailProvider):
    """SMTP comum — serve Gmail, Resend, Postmark, SES e qualquer outro."""

    name = "smtp"

    @property
    def configured(self) -> bool:
        return bool(config.SMTP_HOST and config.SMTP_FROM)

    def send(self, *, to: str, subject: str, text: str, html: str = "") -> Envio:
        if not self.configured:
            return Envio(ok=False, detalhe="SMTP incompleto")

        mensagem = EmailMessage()
        mensagem["Subject"] = subject
        mensagem["From"] = config.SMTP_FROM
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
    global _provedor
    if _provedor is None:
        _provedor = SmtpEmailProvider() if config.SMTP_HOST else NullEmailProvider()
    return _provedor


def reset_provider() -> None:
    global _provedor
    _provedor = None


def enabled() -> bool:
    return provider().configured


def send(*, to: str, subject: str, text: str, html: str = "") -> Envio:
    return provider().send(to=to, subject=subject, text=text, html=html)
