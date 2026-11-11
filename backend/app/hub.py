"""Рассылка live-событий подключённым WebSocket-клиентам."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .parse import jsonable

log = logging.getLogger("meshboard.hub")


class Hub:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._clients.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def publish(self, event_type: str, data: Any) -> None:
        msg = json.dumps({"type": event_type, "data": jsonable(data)}, ensure_ascii=False)
        for q in list(self._clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # медленный клиент: отключаем, фронтенд переподключится и перечитает состояние
                log.warning("websocket client queue overflow, dropping client")
                self._clients.discard(q)
                try:
                    q.put_nowait(json.dumps({"type": "overflow"}))
                except asyncio.QueueFull:
                    pass
