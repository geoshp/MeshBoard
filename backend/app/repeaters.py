"""Опрос своих репитеров по расписанию: логин, статус, телеметрия, соседи. Только чтение.

Используемые API meshcore: commands.send_login (+ событие LOGIN_SUCCESS/LOGIN_FAILED),
commands.send_binary_req(..., BinaryReqType.STATUS / TELEMETRY) + STATUS_RESPONSE /
TELEMETRY_RESPONSE по tag, commands.req_neighbours_async + NEIGHBOURS_RESPONSE по tag.
Это те же шаги, что внутри req_status_sync / req_telemetry_sync / fetch_all_neighbours,
но блокировка команд держится только на время отправки, а ответ ждём вне её.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from meshcore import EventType
from meshcore.packets import BinaryReqType

from . import parse
from .config import Settings
from .db import Database
from .hub import Hub
from .node import EventWaiter, NodeService

log = logging.getLogger("meshboard.repeaters")

REPEATER_TYPES = (parse.TYPE_REPEATER, parse.TYPE_ROOM)


class RepeaterPoller:
    def __init__(self, settings: Settings, db: Database, hub: Hub, node: NodeService):
        self.s = settings
        self.db = db
        self.hub = hub
        self.node = node
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._manual: asyncio.Queue[str] = asyncio.Queue()
        self.polling: str | None = None
        node.on_connected.append(self._on_connected)

    # --- настройки ---
    @property
    def interval_min(self) -> int:
        return int(self.db.get_setting("repeater_poll_min", self.s.repeater_poll_min))

    def set_interval(self, minutes: int) -> None:
        self.db.set_setting("repeater_poll_min", max(1, min(int(minutes), 24 * 60)))
        self._wake.set()

    def own(self) -> list[str]:
        return list(self.db.get_setting("own_repeaters", []) or [])

    def set_own(self, key: str, own: bool) -> None:
        cur = set(self.own())
        if own:
            cur.add(key)
        else:
            cur.discard(key)
        self.db.set_setting("own_repeaters", sorted(cur))
        self._wake.set()

    async def _on_connected(self) -> None:
        """При первом подключении инициализируем список своих репитеров из окружения."""
        if self.db.get_setting("own_repeaters") is not None:
            self._wake.set()
            return
        keys: set[str] = set()
        for prefix in self.s.own_repeaters:
            for c in self.db.find_contacts_by_prefix(prefix):
                if c["type"] in REPEATER_TYPES:
                    keys.add(c["public_key"])
        if not keys and self.s.mock:
            reps = [c for c in self.db.list_contacts() if c["type"] == parse.TYPE_REPEATER]
            keys = {c["public_key"] for c in sorted(reps, key=lambda c: c["name"])[:3]}
        self.db.set_setting("own_repeaters", sorted(keys))
        self._wake.set()

    def password_configured(self, c: dict) -> bool:
        pwd = self._password(c)
        return pwd is not None

    def _password(self, c: dict) -> str | None:
        pwd = self.s.password_for_repeater(c["public_key"], c["name"])
        if pwd is None and self.s.mock:
            return ""
        return pwd

    def next_poll(self, key: str) -> int | None:
        last = self.db.last_repeater_poll(key)
        if last is None:
            return int(time.time())
        return last["ts"] + self.interval_min * 60

    # --- жизненный цикл ---
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="repeater-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def request_poll(self, key: str) -> None:
        self._manual.put_nowait(key)
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                self._wake.clear()
                if self.node.connected:
                    while not self._manual.empty():
                        await self.poll(self._manual.get_nowait())
                    now = int(time.time())
                    for key in self.own():
                        if not self.node.connected:
                            break
                        nxt = self.next_poll(key)
                        if nxt is not None and nxt <= now:
                            await self.poll(key)
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("repeater poll loop error")
                await asyncio.sleep(10)

    # --- опрос ---
    async def poll(self, key: str) -> dict | None:
        c = self.db.get_contact(key)
        if c is None or not self.node.connected:
            return None
        self.polling = key
        self.hub.publish("repeater", {"public_key": key, "polling": True})
        login_ok: bool | None = None
        status = telemetry = neighbours = None
        errors: list[str] = []
        try:
            pwd = self._password(c)
            if pwd is not None:
                login_ok = await self._login(key, pwd)
                if not login_ok:
                    errors.append("login")
            status = await self._binary(key, BinaryReqType.STATUS, EventType.STATUS_RESPONSE)
            if status is None:
                errors.append("status")
            lpp = await self._binary(key, BinaryReqType.TELEMETRY, EventType.TELEMETRY_RESPONSE)
            telemetry = lpp.get("lpp") if lpp else None
            if telemetry is None:
                errors.append("telemetry")
            neighbours = await self._neighbours(key)
            if neighbours is None:
                errors.append("neighbours")
        except Exception as e:  # noqa: BLE001
            log.warning("repeater poll failed: %s", type(e).__name__)
            errors.append(type(e).__name__)
        finally:
            self.polling = None

        if status:
            status = {k: v for k, v in status.items() if k != "tag"}
            self.db.add_metrics(key, parse.repeater_status_metrics(status))
        if telemetry:
            self.db.add_metrics(key, parse.telemetry_metrics(telemetry))
        if status or telemetry or neighbours:
            self.db.touch_contact(key)
        poll = self.db.add_repeater_poll(key, login_ok, status, telemetry, neighbours,
                                         ",".join(errors) or None)
        self.hub.publish("repeater", {"public_key": key, "polling": False, "poll_id": poll["id"]})
        return poll

    async def _login(self, key: str, password: str) -> bool:
        mc = self.node.require()
        prefix = key[:12]
        ok_w = EventWaiter(mc, EventType.LOGIN_SUCCESS)
        fail_w = EventWaiter(mc, EventType.LOGIN_FAILED)
        try:
            ev = await self.node.cmd(mc.commands.send_login, key, password)
            if ev.type == EventType.ERROR:
                return False
            timeout = max(5.0, ev.payload.get("suggested_timeout", 4000) / 800)
            match = lambda e: (e.payload or {}).get("pubkey_prefix", prefix) == prefix  # noqa: E731
            ok_t = asyncio.create_task(ok_w.wait(match, timeout))
            fail_t = asyncio.create_task(fail_w.wait(match, timeout))
            done, pending = await asyncio.wait({ok_t, fail_t}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            if fail_t in done and fail_t.result() is not None:
                return False
            return ok_t in done and ok_t.result() is not None
        finally:
            ok_w.close()
            fail_w.close()

    async def _binary(self, key: str, req: BinaryReqType, resp: EventType) -> dict | None:
        mc = self.node.require()
        waiter = EventWaiter(mc, resp)
        try:
            ev = await self.node.cmd(mc.commands.send_binary_req, key, req)
            if ev.type == EventType.ERROR:
                return None
            tag = ev.payload["expected_ack"].hex()
            timeout = max(8.0, ev.payload.get("suggested_timeout", 4000) / 800)
            got = await waiter.wait(lambda e: e.attributes.get("tag") == tag, timeout)
            return dict(got.payload) if got else None
        finally:
            waiter.close()

    async def _neighbours(self, key: str) -> list[dict] | None:
        """Все соседи с постраничной догрузкой (как fetch_all_neighbours)."""
        mc = self.node.require()
        result: list[dict] = []
        total = None
        for _ in range(10):
            waiter = EventWaiter(mc, EventType.NEIGHBOURS_RESPONSE)
            try:
                ev = await self.node.cmd(mc.commands.req_neighbours_async, key, count=255,
                                         offset=len(result), pubkey_prefix_length=4)
                if ev is None or ev.type == EventType.ERROR:
                    return result if result else None
                tag = ev.payload["expected_ack"].hex()
                timeout = max(10.0, ev.payload.get("suggested_timeout", 4000) / 800)
                got = await waiter.wait(lambda e: e.attributes.get("tag") == tag, timeout)
            finally:
                waiter.close()
            if got is None:
                return result if result else None
            total = got.payload.get("neighbours_count", 0)
            page = got.payload.get("neighbours") or []
            result.extend(page)
            if not page or len(result) >= total:
                break
        return result

    # --- представление для API ---
    def describe(self, c: dict, own: set[str]) -> dict[str, Any]:
        key = c["public_key"]
        last = self.db.last_repeater_poll(key)
        ok = self.db.last_repeater_poll(key, successful=True)
        nb = self.db.last_neighbours(key)
        neighbours = []
        if nb and nb.get("neighbours"):
            for n in nb["neighbours"]:
                cands = self.db.find_contacts_by_prefix(n.get("pubkey", ""))
                m = cands[0] if len(cands) == 1 else None
                neighbours.append({
                    "prefix": n.get("pubkey"), "snr": n.get("snr"), "secs_ago": n.get("secs_ago"),
                    "public_key": m["public_key"] if m else None, "name": m["name"] if m else None,
                })
        return {
            **c,
            "own": key in own,
            "password_configured": self.password_configured(c),
            "polling": self.polling == key,
            "last_poll": last,
            "last_ok": ok,
            "neighbours": neighbours,
            "neighbours_ts": nb["ts"] if nb else None,
            "next_poll": self.next_poll(key) if key in own else None,
        }
