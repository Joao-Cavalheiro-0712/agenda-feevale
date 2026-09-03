"""Atualização em tempo real por SSE (SPEC §141).

Quando o usuário manda algo pelo WhatsApp com o app aberto, o evento aparece
sem refresh. Implementado com Server-Sent Events: uma conexão só, por usuário,
que a própria aplicação alimenta — sem servidor extra.

O barramento é em memória (suficiente para um processo). Com vários workers,
troque `_BUS` por Redis pub/sub mantendo esta mesma interface.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import defaultdict

_LOCK = threading.Lock()
_BUS: dict[str, list[queue.Queue]] = defaultdict(list)
_MAX_LISTENERS_PER_USER = 4


def subscribe(user_id: str) -> queue.Queue:
    canal: queue.Queue = queue.Queue(maxsize=32)
    with _LOCK:
        ouvintes = _BUS[user_id]
        # Evita que uma aba órfã segure recursos para sempre.
        while len(ouvintes) >= _MAX_LISTENERS_PER_USER:
            ouvintes.pop(0)
        ouvintes.append(canal)
    return canal


def unsubscribe(user_id: str, canal: queue.Queue) -> None:
    with _LOCK:
        if canal in _BUS.get(user_id, []):
            _BUS[user_id].remove(canal)
        if not _BUS.get(user_id):
            _BUS.pop(user_id, None)


def publish(user_id: str, event: str, data: dict | None = None) -> int:
    """Publica para as abas abertas daquele usuário — e só daquele usuário."""
    payload = {"event": event, "data": data or {}, "ts": time.time()}
    entregues = 0
    with _LOCK:
        ouvintes = list(_BUS.get(user_id, []))
    for canal in ouvintes:
        try:
            canal.put_nowait(payload)
            entregues += 1
        except queue.Full:
            pass
    return entregues


# Tempo máximo de uma conexão SSE. O cliente reconecta sozinho; sem isso, uma
# aba esquecida segura uma thread do servidor para sempre.
MAX_STREAM_SECONDS = 600


def stream(user_id: str):
    """Gerador de SSE. Envia heartbeat para o proxy não derrubar a conexão."""
    canal = subscribe(user_id)
    inicio = time.time()
    try:
        yield "retry: 5000\n\n"
        ultimo_ping = time.time()
        while time.time() - inicio < MAX_STREAM_SECONDS:
            try:
                mensagem = canal.get(timeout=15)
                yield f"event: {mensagem['event']}\ndata: {json.dumps(mensagem['data'])}\n\n"
            except queue.Empty:
                if time.time() - ultimo_ping > 14:
                    ultimo_ping = time.time()
                    yield ": ping\n\n"
        yield "event: reconnect\ndata: {}\n\n"
    finally:
        unsubscribe(user_id, canal)
