"""Textos legais versionados.

Ficam em código, e não num CMS, por um motivo: o hash do texto é registrado
junto do consentimento. Se alguém perguntar "o que exatamente eu aceitei em
março?", a resposta é verificável — o texto está no histórico do repositório e
o hash bate com o registro.

AVISO INTERNO: estas são minutas escritas para descrever com precisão o que o
sistema faz. Elas não substituem revisão por advogado, obrigatória antes do
lançamento comercial — especialmente pelo tratamento de dados de menores.
"""
from agenda.legal.documents import PRIVACY_SECTIONS, TERMS_SECTIONS, plain_text

__all__ = ["PRIVACY_SECTIONS", "TERMS_SECTIONS", "plain_text"]
