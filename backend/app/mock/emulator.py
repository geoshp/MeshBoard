"""Эмулятор companion-ноды MeshCore.

TCP-сервер, говорящий бинарным companion-протоколом (кадры '<' len16 payload / '>' len16 payload),
как TCP-companion прошивка. Backend подключается к нему обычным MeshCore.create_tcp(), поэтому
весь разбор идёт через настоящий парсер библиотеки meshcore.

Форматы кадров взяты из meshcore/reader.py (2.3.19). Эмулируется:
  * SELF_INFO, DEVICE_INFO, контакты, каналы, батарея, статистика (core/radio/packets);
  * входящие сообщения каналов и ЛС (очередь + MESSAGES_WAITING), LOG_DATA с зашифрованным
    GRP_TXT для сопоставления пути/RSSI;
  * отправка ЛС с ACK (иногда теряется) и автоответами;
  * логин в репитер, бинарные запросы STATUS / TELEMETRY / NEIGHBOURS;
  * trace (TRACE_DATA с SNR по хопам), path discovery, advert path;
  * периодические ADVERTISEMENT.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import random
import struct
import time
from collections import deque
from dataclasses import dataclass, field

from Crypto.Cipher import AES

log = logging.getLogger("meshboard.mock")

PUBLIC_CHANNEL_SECRET = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")

PHRASES = [
    "Всем привет! Слышно меня?", "Тест связи", "Доброе утро, эфир!", "Кто на связи в районе?",
    "Поставил новый репитер на крыше", "Сигнал отличный сегодня", "73!", "Принято, спасибо",
    "Батарея держит уже неделю", "Кто-нибудь видит репитер на севере?", "Проверка маршрута",
    "Погода хорошая, иду в поход с рацией", "Ответьте, кто слышит", "Пинг", "Ок, слышу хорошо",
]
REPLIES = ["Принял!", "Слышу хорошо", "Ок 👍", "Спасибо, понял", "На связи", "Да, всё работает"]


def _pad(s: str, n: int) -> bytes:
    b = s.encode("utf-8")[:n]
    return b + b"\0" * (n - len(b))


def frame(payload: bytes) -> bytes:
    return b">" + len(payload).to_bytes(2, "little") + payload


@dataclass
class MockContact:
    key: bytes
    name: str
    type: int  # 1 chat, 2 repeater, 3 room
    lat: float
    lon: float
    out_path: bytes = b""
    out_path_len: int = -1  # -1 = flood (путь неизвестен)
    last_advert: int = 0
    lastmod: int = 0
    # для репитеров
    boot_ts: int = 0
    counters: dict = field(default_factory=dict)
    neighbours: list = field(default_factory=list)  # [(MockContact, snr)]

    @property
    def hash1(self) -> bytes:
        return self.key[:1]


class MockNodeState:
    """Состояние эмулированной сети. Детерминировано при одинаковом seed."""

    def __init__(self, seed: int = 42, name: str = "MeshBoard Mock"):
        self.rnd = random.Random(seed)
        self.name = name
        self.boot_ts = int(time.time()) - 3 * 3600
        self.self_key = self._key(0x5E)
        self.lat, self.lon = 55.7520, 37.6175
        self.tx_air = 120
        self.rx_air = 900
        self.pkt = {"recv": 1500, "sent": 300, "flood_tx": 200, "direct_tx": 100,
                    "flood_rx": 1200, "direct_rx": 300}
        now = int(time.time())

        def c(first: int, name: str, t: int, dlat: float, dlon: float) -> MockContact:
            mc = MockContact(self._key(first), name, t, self.lat + dlat, self.lon + dlon)
            mc.last_advert = now - self.rnd.randint(60, 7200)
            mc.lastmod = mc.last_advert
            mc.boot_ts = now - self.rnd.randint(86400, 30 * 86400)
            return mc

        # репитеры
        r1 = c(0xA1, "RPT Центр", 2, 0.010, 0.020)
        r2 = c(0xB2, "RPT Север", 2, 0.120, 0.010)
        r3 = c(0xC3, "RPT Восток", 2, -0.020, 0.150)
        r4 = c(0xD4, "RPT Юг", 2, -0.140, 0.060)
        r5 = c(0xE5, "RPT Запад", 2, 0.030, -0.170)
        # чат-узлы
        u1 = c(0x11, "Алексей", 1, 0.140, 0.040)
        u2 = c(0x22, "Мария", 1, -0.030, 0.190)
        u3 = c(0x33, "Иван T-Deck", 1, -0.160, 0.080)
        u4 = c(0x44, "Ольга", 1, 0.020, -0.200)
        u5 = c(0x55, "Дмитрий", 1, 0.005, 0.008)
        u6 = c(0x66, "Сергей (без GPS)", 1, 0.0, 0.0)
        u6.lat = u6.lon = 0.0
        room = c(0x77, "Комната Клуб", 3, 0.015, 0.030)

        # маршруты (out_path: хэши хопов от нас к контакту)
        r1.out_path, r1.out_path_len = b"", 0
        r3.out_path, r3.out_path_len = b"", 0
        r5.out_path, r5.out_path_len = b"", 0
        r2.out_path, r2.out_path_len = r1.hash1, 1
        r4.out_path, r4.out_path_len = r3.hash1, 1
        u1.out_path, u1.out_path_len = r1.hash1 + r2.hash1, 2
        u2.out_path, u2.out_path_len = r3.hash1, 1
        u3.out_path, u3.out_path_len = r3.hash1 + r4.hash1, 2
        u4.out_path, u4.out_path_len = r5.hash1, 1
        u5.out_path, u5.out_path_len = b"", 0
        room.out_path, room.out_path_len = r1.hash1, 1
        # u6 — flood

        r1.neighbours = [(r2, 6.5), (r3, 2.25), (u5, 9.0), (room, 10.5)]
        r2.neighbours = [(r1, 5.75), (u1, 7.0)]
        r3.neighbours = [(r1, 3.0), (r4, -1.5), (u2, 8.25)]
        r4.neighbours = [(r3, -2.0), (u3, 4.5)]
        r5.neighbours = [(u4, 6.0)]
        for r in (r1, r2, r3, r4, r5):
            r.counters = {"recv": self.rnd.randint(5000, 20000), "sent": self.rnd.randint(1000, 5000),
                          "airtime": self.rnd.randint(1000, 5000), "rx_airtime": self.rnd.randint(9000, 30000)}

        self.repeaters = [r1, r2, r3, r4, r5]
        self.contacts: list[MockContact] = [r1, r2, r3, r4, r5, u1, u2, u3, u4, u5, u6, room]
        self.chat_contacts = [u1, u2, u3, u4, u5, u6]
        self.channels: dict[int, tuple[str, bytes]] = {
            0: ("Public", PUBLIC_CHANNEL_SECRET),
            1: ("#moscow", hashlib.sha256(b"#moscow").digest()[:16]),
            2: ("Тест", hashlib.sha256(b"meshboard-test").digest()[:16]),
        }
        self.max_channels = 8

    def _key(self, first: int) -> bytes:
        return bytes([first]) + bytes(self.rnd.getrandbits(8) for _ in range(31))

    def by_prefix(self, prefix: bytes) -> MockContact | None:
        for ct in self.contacts:
            if ct.key.startswith(prefix):
                return ct
        return None

    def battery_mv(self, phase: float = 0.0) -> int:
        t = time.time() / 3600.0
        return int(3950 + 180 * math.sin(t / 3 + phase))


class MockNode:
    """TCP-сервер эмулятора."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, speed: float = 1.0, seed: int = 42,
                 autonomous: bool = True, ack_loss: float = 0.1):
        self.host = host
        self.port = port
        self.speed = max(0.05, speed)
        self.state = MockNodeState(seed)
        self.autonomous = autonomous
        self.ack_loss = ack_loss
        self._server: asyncio.base_events.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._queue: deque[bytes] = deque()
        self._tasks: set[asyncio.Task] = set()
        self._gen_task: asyncio.Task | None = None
        self.received_commands: list[int] = []
        self.sent_channel_messages: list[tuple[int, str]] = []
        self.sent_direct_messages: list[tuple[str, str]] = []

    # --- жизненный цикл ---
    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("mock node listening on %s:%s", self.host, self.port)
        return self.port

    async def stop(self) -> None:
        if self._gen_task:
            self._gen_task.cancel()
        for t in list(self._tasks):
            t.cancel()
        if self._writer:
            self._writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def drop_client(self) -> None:
        """Обрыв соединения (для проверки переподключения)."""
        if self._writer:
            self._writer.close()
            self._writer = None

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _later(self, delay: float, fn, *args) -> None:
        await asyncio.sleep(delay / self.speed if delay > 0.2 else delay)
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("mock delayed action failed")

    def _send(self, payload: bytes) -> None:
        if self._writer and not self._writer.is_closing():
            self._writer.write(frame(payload))

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._writer:
            self._writer.close()
        self._writer = writer
        if self.autonomous and (self._gen_task is None or self._gen_task.done()):
            self._gen_task = asyncio.create_task(self._generator())
        try:
            while True:
                hdr = await reader.readexactly(3)
                if hdr[0:1] != b"<":
                    continue
                size = int.from_bytes(hdr[1:3], "little")
                data = await reader.readexactly(size)
                try:
                    self._handle_command(data)
                except Exception:  # noqa: BLE001
                    log.exception("mock command handling failed")
                    self._send(bytes([1, 6]))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if self._writer is writer:
                self._writer = None
            writer.close()

    # --- кодирование кадров ---
    def _contact_frame(self, ct: MockContact, code: int = 3) -> bytes:
        plen = 255 if ct.out_path_len < 0 else ct.out_path_len
        return (
            bytes([code]) + ct.key + bytes([ct.type, 0, plen])
            + ct.out_path.ljust(64, b"\0") + _pad(ct.name, 32)
            + struct.pack("<Iiii", ct.last_advert, int(ct.lat * 1e6), int(ct.lon * 1e6), ct.lastmod)
        )

    def _self_info(self) -> bytes:
        s = self.state
        return (
            bytes([5, 1, 22, 22]) + s.self_key + struct.pack("<ii", int(s.lat * 1e6), int(s.lon * 1e6))
            + bytes([0, 0, 0, 0]) + struct.pack("<II", 869525, 250000) + bytes([11, 5])
            + s.name.encode("utf-8")
        )

    def _device_info(self) -> bytes:
        return (
            bytes([13, 10, 100, self.state.max_channels]) + struct.pack("<I", 0)
            + _pad("11 Nov 2026", 12) + _pad("MeshBoard Emulator", 40) + _pad("v1.12.0-mock", 20)
            + bytes([0, 0])
        )

    def _channel_info(self, idx: int) -> bytes:
        name, secret = self.state.channels[idx]
        return bytes([18, idx]) + _pad(name, 32) + secret

    def _status_bytes(self, r: MockContact) -> bytes:
        now = int(time.time())
        c = r.counters
        c["recv"] += self.state.rnd.randint(5, 60)
        c["sent"] += self.state.rnd.randint(1, 20)
        c["airtime"] += self.state.rnd.randint(1, 8)
        c["rx_airtime"] += self.state.rnd.randint(5, 40)
        phase = r.key[1] / 40.0
        snr = self.state.rnd.uniform(-5, 11)
        return struct.pack(
            "<HHhhIIIIIIIIHhHHII",
            self.state.battery_mv(phase), self.state.rnd.randint(0, 3),
            -110 + self.state.rnd.randint(-6, 6), -90 + self.state.rnd.randint(-20, 20),
            c["recv"], c["sent"], c["airtime"], now - r.boot_ts,
            int(c["sent"] * 0.7), int(c["sent"] * 0.3), int(c["recv"] * 0.8), int(c["recv"] * 0.2),
            0, int(snr * 4), self.state.rnd.randint(0, 50), self.state.rnd.randint(50, 500),
            c["rx_airtime"], self.state.rnd.randint(0, 30),
        )

    def _telemetry_bytes(self, r: MockContact) -> bytes:
        volts = self.state.battery_mv(r.key[1] / 40.0) / 1000.0
        temp = 12.0 + 6 * math.sin(time.time() / 7200 + r.key[2])
        out = bytes([1, 116]) + int(round(volts * 100)).to_bytes(2, "big")
        out += bytes([2, 103]) + int(round(temp * 10)).to_bytes(2, "big", signed=True)
        return out

    def _neighbours_bytes(self, r: MockContact, req: bytes) -> bytes:
        count = req[1] if len(req) > 1 else 255
        offset = int.from_bytes(req[2:4], "little") if len(req) >= 4 else 0
        plen = req[5] if len(req) > 5 else 4
        items = r.neighbours[offset:offset + count]
        out = struct.pack("<hh", len(r.neighbours), len(items))
        for nb, snr in items:
            jitter = self.state.rnd.uniform(-1.0, 1.0)
            out += nb.key[:plen] + struct.pack("<i", self.state.rnd.randint(30, 3600))
            out += struct.pack("<b", int((snr + jitter) * 4))
        return out

    def _grp_txt_log(self, chan_idx: int, ts: int, text: str, hops: list[MockContact],
                     snr: float, rssi: int) -> bytes:
        """LOG_DATA с зашифрованным GRP_TXT, как его видит радиомодуль."""
        name, secret = self.state.channels[chan_idx]
        plain = struct.pack("<I", ts) + bytes([0]) + text.encode("utf-8")
        plain += b"\0" * (-len(plain) % 16)
        cipher = AES.new(secret, AES.MODE_ECB).encrypt(plain)
        mac = hmac.new(secret, cipher, hashlib.sha256).digest()[:2]
        chan_hash = hashlib.sha256(secret).digest()[:1]
        path = b"".join(h.hash1 for h in hops)
        pkt = bytes([(5 << 2) | 1, len(hops)]) + path + chan_hash + mac + cipher
        return bytes([0x88]) + struct.pack("<bb", int(snr * 4), rssi) + pkt

    # --- обработка команд ---
    def _handle_command(self, data: bytes) -> None:
        if not data:
            return
        cmd = data[0]
        self.received_commands.append(cmd)
        s = self.state
        now = int(time.time())

        if cmd == 1:  # APP_START
            self._send(self._self_info())
        elif cmd == 22:  # DEVICE_QUERY
            self._send(self._device_info())
        elif cmd == 4:  # GET_CONTACTS [since]
            since = int.from_bytes(data[1:5], "little") if len(data) >= 5 else 0
            items = [ct for ct in s.contacts if ct.lastmod > since]
            self._send(bytes([2]) + struct.pack("<I", len(items)))
            for ct in items:
                self._send(self._contact_frame(ct))
            lastmod = max([ct.lastmod for ct in s.contacts] + [since])
            self._send(bytes([4]) + struct.pack("<I", lastmod))
        elif cmd == 5:  # GET_DEVICE_TIME
            self._send(bytes([9]) + struct.pack("<I", now))
        elif cmd == 6:  # SET_DEVICE_TIME
            self._send(bytes([0]))
        elif cmd == 7:  # SEND_SELF_ADVERT
            self._send(bytes([0]))
        elif cmd == 10:  # SYNC_NEXT_MESSAGE
            self._send(self._queue.popleft() if self._queue else bytes([10]))
        elif cmd == 2:  # SEND_TXT_MSG
            attempt = data[2]
            prefix = data[7:13]
            text = data[13:].decode("utf-8", "ignore")
            ct = s.by_prefix(prefix)
            if ct is None:
                self._send(bytes([1, 2]))
                return
            ack = s.rnd.getrandbits(32).to_bytes(4, "little")
            flood = ct.out_path_len < 0
            s.tx_air += 1
            s.pkt["sent"] += 1
            self._send(bytes([6, 1 if flood else 0]) + ack + struct.pack("<I", 3000 + 1500 * max(0, ct.out_path_len)))
            self.sent_direct_messages.append((ct.key.hex(), text))
            if s.rnd.random() >= self.ack_loss or attempt > 0:
                trip = int(400 + 700 * max(1, ct.out_path_len) + s.rnd.randint(0, 400))
                self._spawn(self._later(min(trip / 1000, 2.5), self._send,
                                        bytes([0x82]) + ack + struct.pack("<I", trip)))
                if ct.type == 1 and s.rnd.random() < 0.5 and attempt == 0:
                    self._spawn(self._later(4.0, self._queue_dm, ct, s.rnd.choice(REPLIES)))
        elif cmd == 3:  # SEND_CHANNEL_TXT_MSG
            idx = data[2]
            if idx not in s.channels:
                self._send(bytes([1, 2]))
                return
            self.sent_channel_messages.append((idx, data[7:].decode("utf-8", "ignore")))
            s.tx_air += 1
            s.pkt["sent"] += 1
            s.pkt["flood_tx"] += 1
            self._send(bytes([0]))
        elif cmd == 20:  # GET_BATT_AND_STORAGE
            self._send(bytes([12]) + struct.pack("<HII", s.battery_mv(), 128, 1024))
        elif cmd == 31:  # GET_CHANNEL
            idx = data[1]
            if idx in s.channels:
                self._send(self._channel_info(idx))
            elif idx < s.max_channels:
                self._send(bytes([18, idx]) + b"\0" * 48)  # пустой слот
            else:
                self._send(bytes([1, 2]))
        elif cmd == 56:  # GET_STATS
            st = data[1] if len(data) > 1 else 0
            if st == 0:
                self._send(bytes([24, 0]) + struct.pack("<HIHB", s.battery_mv(), now - s.boot_ts, 0, 0))
            elif st == 1:
                s.rx_air += s.rnd.randint(1, 10)
                self._send(bytes([24, 1]) + struct.pack(
                    "<hbbII", -112 + s.rnd.randint(-4, 4), -80 + s.rnd.randint(-25, 15),
                    int(s.rnd.uniform(-4, 12) * 4), s.tx_air, s.rx_air))
            else:
                for k in s.pkt:
                    s.pkt[k] += s.rnd.randint(0, 15)
                p = s.pkt
                self._send(bytes([24, 2]) + struct.pack(
                    "<IIIIIII", p["recv"], p["sent"], p["flood_tx"], p["direct_tx"], p["flood_rx"],
                    p["direct_rx"], 3))
        elif cmd == 26:  # SEND_LOGIN
            dst = data[1:33]
            pwd = data[33:].decode("utf-8", "ignore")
            ct = s.by_prefix(dst)
            if ct is None:
                self._send(bytes([1, 2]))
                return
            self._send(bytes([6, 0]) + s.rnd.getrandbits(32).to_bytes(4, "little") + struct.pack("<I", 2400))
            if pwd == "wrong":
                self._spawn(self._later(0.6, self._send, bytes([0x86, 0]) + ct.key[:6]))
            else:
                self._spawn(self._later(0.6, self._send, bytes([0x85, 1]) + ct.key[:6]
                                        + struct.pack("<I", now) + bytes([3, 1])))
        elif cmd == 29:  # LOGOUT
            self._send(bytes([0]))
        elif cmd == 50:  # BINARY_REQ
            dst = data[1:33]
            rtype = data[33]
            body = data[34:]
            ct = s.by_prefix(dst)
            if ct is None:
                self._send(bytes([1, 2]))
                return
            tag = s.rnd.getrandbits(32).to_bytes(4, "little")
            self._send(bytes([6, 0]) + tag + struct.pack("<I", 2400))
            if rtype == 1:
                resp = self._status_bytes(ct)
            elif rtype == 3:
                resp = self._telemetry_bytes(ct)
            elif rtype == 6:
                resp = self._neighbours_bytes(ct, body)
            else:
                return
            self._spawn(self._later(0.5, self._send, bytes([0x8C, 0]) + tag + resp))
        elif cmd == 36:  # SEND_TRACE_PATH
            tag = data[1:5]
            auth = data[5:9]
            flags = data[9]
            hsize = 1 << (flags & 3)
            path = data[10:]
            hops = [path[i:i + hsize] for i in range(0, len(path) - hsize + 1, hsize)]
            self._send(bytes([6, 0]) + tag + struct.pack("<I", 2000 * max(1, len(hops))))
            nodes = [s.by_prefix(h) for h in hops]
            if hops and all(n is not None and n.type in (2, 3) for n in nodes):
                snrs = [int(s.rnd.uniform(-6, 12) * 4) for _ in hops]
                final = int(s.rnd.uniform(-3, 12) * 4)
                resp = (bytes([0x89, 0, len(path), flags]) + tag + auth + b"".join(hops)
                        + struct.pack(f"<{len(snrs)}b", *snrs) + struct.pack("<b", final))
                self._spawn(self._later(0.4 * len(hops), self._send, resp))
        elif cmd == 52:  # PATH_DISCOVERY
            dst = data[2:34]
            ct = s.by_prefix(dst)
            if ct is None:
                self._send(bytes([1, 2]))
                return
            self._send(bytes([6, 1]) + s.rnd.getrandbits(32).to_bytes(4, "little") + struct.pack("<I", 3000))
            n = max(0, ct.out_path_len)
            resp = (bytes([0x8D, 0]) + ct.key[:6] + bytes([n]) + ct.out_path + bytes([n])
                    + bytes(reversed(ct.out_path)))
            self._spawn(self._later(1.0, self._send, resp))
        elif cmd == 42:  # GET_ADVERT_PATH
            ct = s.by_prefix(data[2:34])
            if ct is None:
                self._send(bytes([1, 2]))
                return
            n = max(0, ct.out_path_len)
            self._send(bytes([22]) + struct.pack("<I", ct.last_advert) + bytes([n]) + bytes(reversed(ct.out_path)))
        elif cmd == 59:  # GET_AUTOADD_CONFIG
            self._send(bytes([25, 0, 0]))
        else:
            self._send(bytes([1, 1]))  # ERR_CODE_UNSUPPORTED_CMD

    # --- генерация входящего трафика ---
    def _queue_dm(self, ct: MockContact, text: str) -> None:
        s = self.state
        plen = 255 if ct.out_path_len < 0 else ct.out_path_len
        if plen == 255:
            plen = s.rnd.randint(1, 3)  # пришло флудом: число хопов известно
        snr = int(s.rnd.uniform(-6, 12) * 4)
        payload = (bytes([16]) + struct.pack("<b", snr) + b"\0\0" + ct.key[:6] + bytes([plen, 0])
                   + struct.pack("<I", int(time.time())) + text.encode("utf-8"))
        s.pkt["recv"] += 1
        self._queue.append(payload)
        self._send(bytes([0x83]))

    def inject_channel_message(self, chan_idx: int, sender: MockContact | None = None,
                               text: str | None = None) -> str:
        s = self.state
        sender = sender or s.rnd.choice(s.chat_contacts)
        body = text or s.rnd.choice(PHRASES)
        full = f"{sender.name}: {body}"
        ts = int(time.time())
        hops = self._route_hops(sender)
        snr = s.rnd.uniform(-7, 12)
        rssi = s.rnd.randint(-120, -60)
        self._send(self._grp_txt_log(chan_idx, ts, full, hops, snr, rssi))
        payload = (bytes([17]) + struct.pack("<b", int(snr * 4)) + b"\0\0" + bytes([chan_idx, len(hops), 0])
                   + struct.pack("<I", ts) + full.encode("utf-8"))
        s.pkt["recv"] += 1
        s.pkt["flood_rx"] += 1
        self._queue.append(payload)
        self._send(bytes([0x83]))
        return full

    def inject_dm(self, sender: MockContact | None = None, text: str | None = None) -> None:
        s = self.state
        self._queue_dm(sender or s.rnd.choice(s.chat_contacts), text or s.rnd.choice(PHRASES))

    def inject_advert(self, ct: MockContact | None = None) -> None:
        s = self.state
        ct = ct or s.rnd.choice(s.contacts)
        ct.last_advert = int(time.time())
        ct.lastmod = ct.last_advert
        self._send(bytes([0x80]) + ct.key)

    def _route_hops(self, sender: MockContact) -> list[MockContact]:
        """Путь флуд-пакета от отправителя к нам: обратный out_path (хопы, через которые он прошёл)."""
        hops = []
        for b in reversed(sender.out_path):
            ct = self.state.by_prefix(bytes([b]))
            if ct:
                hops.append(ct)
        return hops

    async def _generator(self) -> None:
        s = self.state
        await asyncio.sleep(3 / self.speed)
        tick = 0
        try:
            while True:
                await asyncio.sleep(5 / self.speed)
                tick += 1
                r = s.rnd.random()
                if r < 0.45:
                    self.inject_channel_message(s.rnd.choice([0, 0, 0, 1, 1, 2]))
                elif r < 0.55:
                    self.inject_dm()
                elif r < 0.80:
                    self.inject_advert()
        except asyncio.CancelledError:
            pass
