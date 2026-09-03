"""Cobrança: interface do provedor, checkout e webhook.

Separado de `core/billing.py` de propósito. `billing` responde "o que este
plano permite" e nunca fala com a internet; `payments` fala com o gateway e
nunca decide permissão. Quando a chave do gateway chegar, nada em `billing`
muda — é o que torna trocar de provedor uma troca de arquivo.
"""
