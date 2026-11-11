"""NodeService: подключение к companion-ноде через библиотеку meshcore.

* Подключение serial/tcp (mock = tcp к встроенному эмулятору).
* Собственный супервизор: при обрыве — полный пересоздание MeshCore и ресинхронизация,
  бесконечные попытки с экспоненциальной паузой (1 → 60 с).
* Все команды к ноде сериализуются одной блокировкой: библиотека сопоставляет ответы по типу
  события (OK/ERROR/MSG_SENT), и параллельные команды могли бы получить чужой ответ.
  Ожидание удалённых ответов (ACK, STATUS_RESPONSE, TRACE_DATA...) идёт вне блокировки.
* Выборка входящих сообщений — get_msg() под той же блокировкой по MESSAGES_WAITING
  (вместо start_auto_message_fetching, который вызывает get_msg без синхронизации).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from meshcore import EventType, MeshCore

from . import parse
from .config import Settings
from .db import Database
from .hub import Hub

log = logging.getLogger("meshboard.node")


class EventWaiter:
    """Подписка ДО отправки команды; ждёт событие, удовлетворяющее предикату."""

    def __init__(self, mc: MeshCore, event_type: EventType):
        self._events: list[Any] = []
        self._cond = asyncio.Event()
        self._sub = mc.subscribe(event_type, self._on)

    def _on(self, event) -> None:
        self._events.append(event)
        self._cond.set()

    async def wait(self, predicate: Callable[[Any], bool], timeout: float):
        deadline = time.monotonic() + timeout
        try:
            while True:
                for ev in self._events:
                    if predicate(ev):
                        return ev
                self._cond.clear()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(self._cond.wait(), remaining)
                except asyncio.TimeoutError:
                    return None
        finally:
            self._sub.unsubscribe()

    def close(self) -> None:
        self._sub.unsubscribe()


class NodeNotConnected(RuntimeError):
    pass


class NodeService:
    def __init__(self, settings: Settings, db: Database, hub: Hub):
        self.s = settings
        self.db = db
        self.hub = hub
        self.mc: MeshCore | None = None
        self.lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._lost = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._supervisor: asyncio.Task | None = None
        self._session_tasks: list[asyncio.Task] = []
        self._fetch_task: asyncio.Task | None = None
        self._contacts_refresh: asyncio.Task | None = None
        self._lastmod = 0
        self._stats_supported = True
        self._poll_failures = 0
        self._rx_log_count = 0
        self._recent_acks: dict[str, dict] = {}
        self.mock_node = None  # MockNode при MESHCORE_MOCK=1
        self.on_connected: list[Callable[[], Awaitable[None]]] = []

        self.status: dict[str, Any] = {
            "state": "starting",
            "mode": "mock" if settings.mock else settings.connection,
            "target": self._target_desc(),
            "since": int(time.time()),
            "attempt": 0,
            "retry_in": None,
            "last_error": None,
            "self_info": None,
            "device_info": None,
        }

    # --- статус ---
    def _target_desc(self) -> str:
        if self.s.mock:
            return "эмулятор"
        if self.s.connection == "serial":
            return f"{self.s.serial_port} @ {self.s.serial_baudrate}"
        return f"{self.s.tcp_host}:{self.s.tcp_port}"

    def _set_status(self, **kw: Any) -> None:
        if "state" in kw and kw["state"] != self.status["state"]:
            kw.setdefault("since", int(time.time()))
        self.status.update(kw)
        self.hub.publish("status", self.status)

    @property
    def connected(self) -> bool:
        return self.mc is not None and self.mc.is_connected and self.status["state"] == "connected"

    def require(self) -> MeshCore:
        if not self.connected or self.mc is None:
            raise NodeNotConnected()
        return self.mc

    @property
    def self_key(self) -> str | None:
        si = self.status.get("self_info") or {}
        return si.get("public_key")

    # --- жизненный цикл ---
    def spawn(self, coro, name: str | None = None) -> asyncio.Task:
        t = asyncio.create_task(coro, name=name)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        t.add_done_callback(self._log_task_error)
        return t

    @staticmethod
    def _log_task_error(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is not None:
            log.error("task %s failed: %r", t.get_name(), t.exception())

    async def start(self) -> None:
        if self.s.mock:
            from .mock.emulator import MockNode

            self.mock_node = MockNode(speed=self.s.mock_speed)
            await self.mock_node.start()
        self._supervisor = asyncio.create_task(self._supervise(), name="node-supervisor")

    async def stop(self) -> None:
        self._stop.set()
        self._lost.set()
        if self._supervisor:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._teardown()
        for t in list(self._tasks):
            t.cancel()
        if self.mock_node:
            await self.mock_node.stop()

    async def _create(self) -> MeshCore | None:
        if self.s.mock:
            assert self.mock_node is not None
            return await MeshCore.create_tcp("127.0.0.1", self.mock_node.port, default_timeout=10)
        if self.s.connection == "serial":
            return await MeshCore.create_serial(
                self.s.serial_port, self.s.serial_baudrate, default_timeout=10
            )
        return await MeshCore.create_tcp(self.s.tcp_host, self.s.tcp_port, default_timeout=10)

    async def _supervise(self) -> None:
        delay = 1.0
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            self._set_status(state="connecting", attempt=attempt, retry_in=None)
            mc = None
            try:
                mc = await asyncio.wait_for(self._create(), timeout=45)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("connection failed: %s", type(e).__name__)
                self._set_status(last_error=f"{type(e).__name__}: {e}"[:200])
            if mc is not None:
                self.mc = mc
                self._lost.clear()
                mc.subscribe(EventType.DISCONNECTED, self._on_disconnected)
                try:
                    await self._on_session_start()
                except Exception as e:  # noqa: BLE001
                    log.exception("initial sync failed")
                    self._set_status(last_error=f"sync: {type(e).__name__}"[:200])
                    self._lost.set()
                if not self._lost.is_set():
                    attempt = 0
                    delay = 1.0
                    self._set_status(state="connected", attempt=0, last_error=None)
                    for cb in self.on_connected:
                        self.spawn(cb(), name="on-connected")
                    await self._lost.wait()
                await self._teardown()
                if self._stop.is_set():
                    break
            elif self.status.get("last_error") is None:
                self._set_status(last_error="нет ответа от ноды")
            self._set_status(state="disconnected", retry_in=int(delay))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 60.0)

    def _on_disconnected(self, event) -> None:
        reason = (event.payload or {}).get("reason", "unknown")
        if reason != "manual_disconnect":
            log.warning("node disconnected: %s", reason)
            self.status["last_error"] = f"соединение потеряно: {reason}"
        self._lost.set()

    def force_reconnect(self, reason: str) -> None:
        log.warning("forcing reconnect: %s", reason)
        self.status["last_error"] = reason
        self._lost.set()

    async def _teardown(self) -> None:
        for t in self._session_tasks:
            t.cancel()
        self._session_tasks.clear()
        for t in (self._fetch_task, self._contacts_refresh):
            if t and not t.done():
                t.cancel()
        mc, self.mc = self.mc, None
        if mc is not None:
            try:
                await asyncio.wait_for(mc.disconnect(), timeout=10)
            except Exception:  # noqa: BLE001
                pass

    # --- синхронизация сессии ---
    async def _on_session_start(self) -> None:
        mc = self.mc
        assert mc is not None
        self._lastmod = 0
        self._stats_supported = True
        self._poll_failures = 0
        si = dict(mc.self_info or {})
        di_ev = await self.cmd(mc.commands.send_device_query)
        di = di_ev.payload if di_ev.type != EventType.ERROR else {}
        self.status["self_info"] = {
            k: si.get(k) for k in ("name", "public_key", "adv_type", "tx_power", "max_tx_power",
                                   "adv_lat", "adv_lon", "radio_freq", "radio_bw", "radio_sf",
                                   "radio_cr")
        }
        self.status["device_info"] = {
            "fw_ver": di.get("fw ver"), "fw_build": di.get("fw_build"), "model": di.get("model"),
            "ver": di.get("ver"), "max_contacts": di.get("max_contacts"),
            "max_channels": di.get("max_channels"),
        }
        # путь/RSSI сообщений каналов: библиотека сопоставляет RX_LOG_DATA с сообщением
        mc.set_decrypt_channel_logs(True)

        mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_dm)
        mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_chan)
        mc.subscribe(EventType.MESSAGES_WAITING, lambda e: self.schedule_fetch())
        mc.subscribe(EventType.ADVERTISEMENT, self._on_advert)
        mc.subscribe(EventType.PATH_UPDATE, self._on_path_update)
        mc.subscribe(EventType.NEW_CONTACT, self._on_new_contact)
        mc.subscribe(EventType.ACK, self._on_ack)
        mc.subscribe(EventType.RX_LOG_DATA, self._on_rx_log)

        await self.sync_contacts(full=True)
        await self.sync_channels(di.get("max_channels") or 8)
        await self.poll_self()
        self.schedule_fetch()
        self._session_tasks.append(self.spawn(self._self_poll_loop(), name="self-poll"))
        self._session_tasks.append(self.spawn(self._fetch_safety_loop(), name="fetch-safety"))

    async def cmd(self, fn: Callable[..., Awaitable[Any]], *args: Any, timeout: float = 20, **kw: Any):
        """Выполняет команду библиотеки под общей блокировкой."""
        async with self.lock:
            return await asyncio.wait_for(fn(*args, **kw), timeout=timeout)

    async def sync_contacts(self, full: bool = False) -> int:
        mc = self.mc
        if mc is None:
            return 0
        since = 0 if full else self._lastmod
        ev = await self.cmd(mc.commands.get_contacts, lastmod=since, timeout=60)
        if ev.type == EventType.ERROR:
            log.warning("get_contacts failed: %s", ev.payload)
            return 0
        self._lastmod = ev.attributes.get("lastmod", self._lastmod)
        contacts = ev.payload or {}
        for c in contacts.values():
            row = parse.contact_from_lib(c)
            self.db.upsert_contact(row)
            self.db.resolve_dm_prefix(row["public_key"][:12], row["public_key"])
        if full:
            self.db.mark_contacts_absent({k.lower() for k in contacts})
        self.hub.publish("contacts", {"changed": len(contacts)})
        return len(contacts)

    async def sync_channels(self, max_channels: int) -> None:
        mc = self.mc
        if mc is None:
            return
        present: set[int] = set()
        for idx in range(int(max_channels)):
            ev = await self.cmd(mc.commands.get_channel, idx)
            if ev.type == EventType.ERROR:
                break
            p = ev.payload
            name = (p.get("channel_name") or "").strip()
            secret = p.get("channel_secret") or b""
            if not name or not any(secret):
                continue  # пустой слот
            # секрет канала не сохраняем и не отдаём наружу
            self.db.upsert_channel(idx, name, p.get("channel_hash"))
            present.add(idx)
        self.db.delete_channels_except(present)
        self.hub.publish("channels", {"count": len(present)})

    def schedule_contacts_refresh(self, delay: float = 2.0) -> None:
        if self._contacts_refresh and not self._contacts_refresh.done():
            return

        async def run():
            await asyncio.sleep(delay)
            try:
                await self.sync_contacts()
            except Exception as e:  # noqa: BLE001
                log.warning("contacts refresh failed: %r", e)

        self._contacts_refresh = self.spawn(run(), name="contacts-refresh")

    # --- выборка сообщений ---
    def schedule_fetch(self) -> None:
        if self._fetch_task and not self._fetch_task.done():
            self._fetch_again = True
            return
        self._fetch_again = False
        self._fetch_task = self.spawn(self._fetch_messages(), name="fetch-messages")

    async def _fetch_messages(self) -> None:
        mc = self.mc
        while mc is not None and mc.is_connected:
            ev = await self.cmd(mc.commands.get_msg)
            if ev.type in (EventType.NO_MORE_MSGS, EventType.ERROR):
                if getattr(self, "_fetch_again", False):
                    self._fetch_again = False
                    continue
                break
            await asyncio.sleep(0.05)

    async def _fetch_safety_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            self.schedule_fetch()

    # --- обработчики событий ---
    def _on_dm(self, event) -> None:
        p = event.payload
        prefix = (p.get("pubkey_prefix") or "").lower()
        contact = self.db.find_contact_by_prefix(prefix)
        key = contact["public_key"] if contact else None
        row = parse.dm_message_row(p, key)
        msg = self.db.insert_message(row)
        if msg is None:
            return  # повтор
        if key:
            self.db.touch_contact(key)
            self.db.add_activity(key, "msg")
        else:
            self.schedule_contacts_refresh(0.5)
        self.hub.publish("message", msg)

    def _on_chan(self, event) -> None:
        row = parse.channel_message_row(event.payload)
        msg = self.db.insert_message(row)
        if msg is None:
            return
        if row["sender_name"]:
            c = self.db.find_contact_by_name(row["sender_name"])
            if c:
                self.db.touch_contact(c["public_key"])
                self.db.add_activity(c["public_key"], "msg")
        self.hub.publish("message", msg)

    def _on_advert(self, event) -> None:
        key = (event.payload or {}).get("public_key", "").lower()
        if key:
            self.db.add_activity(key, "advert")
            self.db.touch_contact(key)
            self.hub.publish("advert", {"public_key": key, "ts": int(time.time())})
        self.schedule_contacts_refresh()

    def _on_path_update(self, event) -> None:
        self.schedule_contacts_refresh()

    def _on_new_contact(self, event) -> None:
        # узел, ещё не добавленный в контакты ноды (режим ручного добавления)
        row = parse.contact_from_lib(event.payload)
        self.db.upsert_contact(row)
        self.db.set_on_device(row["public_key"], False)
        self.schedule_contacts_refresh()

    def _on_ack(self, event) -> None:
        code = (event.payload or {}).get("code")
        if code:
            self._recent_acks[code] = {"ts": time.time(), "trip": event.payload.get("trip_time")}
            if len(self._recent_acks) > 200:
                for k in sorted(self._recent_acks, key=lambda k: self._recent_acks[k]["ts"])[:100]:
                    self._recent_acks.pop(k, None)

    def _on_rx_log(self, event) -> None:
        self._rx_log_count += 1

    # --- метрики своей ноды ---
    async def poll_self(self) -> dict:
        mc = self.mc
        if mc is None:
            return {}
        values: dict[str, Any] = {}
        ev = await self.cmd(mc.commands.get_bat)
        if ev.type == EventType.ERROR:
            self._poll_failures += 1
            if self._poll_failures >= 3:
                self.force_reconnect("нода не отвечает на запросы")
            return {}
        self._poll_failures = 0
        values.update(parse.battery_metrics(ev.payload))
        if self._stats_supported:
            for fn, conv in (
                (mc.commands.get_stats_core, parse.stats_core_metrics),
                (mc.commands.get_stats_radio, parse.stats_radio_metrics),
                (mc.commands.get_stats_packets, parse.stats_packets_metrics),
            ):
                ev = await self.cmd(fn)
                if ev.type == EventType.ERROR:
                    if (ev.payload or {}).get("error_code") == 1:  # не поддерживается прошивкой
                        self._stats_supported = False
                    break
                values.update(conv(ev.payload))
        values["rx_log_packets"] = self._rx_log_count
        self._rx_log_count = 0
        values = {k: v for k, v in values.items() if v is not None}
        self.db.add_metrics("self", values)
        self.hub.publish("metrics", {"source": "self", "values": values, "ts": int(time.time())})
        return values

    async def _self_poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.self_poll_sec)
            try:
                await self.poll_self()
            except asyncio.TimeoutError:
                self._poll_failures += 1
                if self._poll_failures >= 3:
                    self.force_reconnect("нода не отвечает на запросы")
            except Exception as e:  # noqa: BLE001
                log.warning("self poll failed: %r", e)

    # --- отправка сообщений ---
    MAX_TEXT_BYTES = 150  # MAX_TEXT_LEN прошивки 160 байт с запасом

    def max_channel_text(self) -> int:
        name = ((self.status.get("self_info") or {}).get("name") or "")
        # прошивка сама добавляет "Имя: " к сообщению канала
        return max(20, self.MAX_TEXT_BYTES - len(name.encode("utf-8")) - 2)

    async def send_channel(self, idx: int, text: str) -> dict:
        mc = self.require()
        name = (self.status.get("self_info") or {}).get("name")
        row = parse.outgoing_row("chan", text, channel_idx=idx, sender_name=name)
        msg = self.db.insert_message(row)
        assert msg is not None
        self.hub.publish("message", msg)
        try:
            ev = await self.cmd(mc.commands.send_chan_msg, idx, text, msg["sender_ts"])
            ok = ev.type != EventType.ERROR
        except Exception as e:  # noqa: BLE001
            log.warning("send_chan_msg failed: %r", e)
            ok = False
        msg = self.db.update_message(msg["id"], status="sent" if ok else "failed", attempts=1)
        self.hub.publish("message_update", msg)
        return msg

    async def send_dm(self, contact_key: str, text: str, max_attempts: int = 3) -> dict:
        self.require()
        row = parse.outgoing_row("dm", text, contact_key=contact_key)
        msg = self.db.insert_message(row)
        assert msg is not None
        self.hub.publish("message", msg)
        self.spawn(self._deliver_dm(msg["id"], contact_key, text, msg["sender_ts"], max_attempts),
                   name="deliver-dm")
        return msg

    async def _deliver_dm(self, msg_id: int, key: str, text: str, ts: int, max_attempts: int) -> None:
        """Отправка ЛС с ожиданием ACK и повторами (attempt=0..n, тот же timestamp).

        Аналог commands.send_msg_with_retry, но блокировка держится только на время send_msg,
        а ACK ждём вне её. ACK на любую из попыток означает доставку.
        """
        codes: set[str] = set()
        for attempt in range(max_attempts):
            mc = self.mc
            if mc is None or not mc.is_connected:
                break
            waiter = EventWaiter(mc, EventType.ACK)
            try:
                try:
                    ev = await self.cmd(mc.commands.send_msg, key, text, ts, attempt=attempt)
                except Exception as e:  # noqa: BLE001
                    log.warning("send_msg failed: %r", e)
                    continue
                if ev.type == EventType.ERROR:
                    log.warning("send_msg error: %s", ev.payload)
                    continue
                code = ev.payload["expected_ack"].hex()
                codes.add(code)
                self.hub.publish("message_update", self.db.update_message(
                    msg_id, status="sent", expected_ack=code, attempts=attempt + 1))
                timeout = max(5.0, ev.payload.get("suggested_timeout", 5000) / 1000 * 1.2)
                # ACK мог прийти раньше подписки этой попытки — проверяем кэш
                hit = next((c for c in codes if c in self._recent_acks), None)
                ack = None
                if hit is None:
                    ack = await waiter.wait(lambda e: e.payload.get("code") in codes, timeout)
                if hit or ack:
                    c = hit or ack.payload["code"]
                    trip = (self._recent_acks.get(c) or {}).get("trip")
                    if ack is not None:
                        trip = ack.payload.get("trip_time", trip)
                    self.hub.publish("message_update", self.db.update_message(
                        msg_id, status="delivered", trip_ms=trip))
                    self.db.touch_contact(key)
                    return
            finally:
                waiter.close()
        self.hub.publish("message_update", self.db.update_message(msg_id, status="failed"))
