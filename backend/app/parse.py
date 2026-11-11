"""Чистые функции разбора payload событий библиотеки meshcore в записи хранилища.

Без ввода-вывода — тестируются отдельно (tests/test_parse.py).
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

# типы узлов (meshcore.packets.AdvType)
NODE_TYPES = {0: "none", 1: "chat", 2: "repeater", 3: "room", 4: "sensor"}
TYPE_REPEATER = 2
TYPE_ROOM = 3

_SENDER_RE = re.compile(r"^([^:\n]{1,32}): (.*)$", re.S)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def contact_from_lib(c: dict) -> dict:
    """Контакт из payload CONTACTS / NEXT_CONTACT / NEW_CONTACT -> строка таблицы contacts."""
    lat = c.get("adv_lat") or 0.0
    lon = c.get("adv_lon") or 0.0
    has_gps = not (abs(lat) < 1e-6 and abs(lon) < 1e-6)
    return {
        "public_key": c["public_key"].lower(),
        "name": (c.get("adv_name") or "").strip(),
        "type": int(c.get("type", 0)),
        "flags": int(c.get("flags", 0)),
        "out_path_len": int(c.get("out_path_len", -1)),
        "out_path_hash_mode": max(0, int(c.get("out_path_hash_mode", 0))),
        "out_path": c.get("out_path") or "",
        "lat": lat if has_gps else None,
        "lon": lon if has_gps else None,
        "last_advert": int(c.get("last_advert", 0)),
        "lastmod": int(c.get("lastmod", 0)),
    }


def split_hops(path_hex: str | None, hash_size: int | None) -> list[str]:
    """'a1b2c3' + размер 1 -> ['a1','b2','c3']."""
    if not path_hex:
        return []
    size = max(1, int(hash_size or 1)) * 2
    return [path_hex[i:i + size] for i in range(0, len(path_hex) - size + 1, size)]


def split_sender(text: str) -> tuple[str | None, str]:
    """Сообщения каналов по соглашению прошивки имеют вид 'Имя: текст'."""
    m = _SENDER_RE.match(text or "")
    if m:
        return m.group(1).strip(), m.group(2)
    return None, text or ""


def _path_info(payload: dict) -> tuple[int | None, int | None]:
    """(path_len в хопах или -1 для прямого маршрута, размер хэша хопа в байтах)."""
    plen = payload.get("path_len")
    mode = payload.get("path_hash_mode")
    if plen is None:
        return None, None
    if plen == 255 or mode == -1:
        return -1, None
    return int(plen), int(mode or 0) + 1


def channel_message_row(payload: dict, ts: int | None = None) -> dict:
    """payload CHANNEL_MSG_RECV -> строка messages."""
    text = payload.get("text", "")
    sender, body = split_sender(text)
    path_len, hsize = _path_info(payload)
    sender_ts = payload.get("sender_timestamp")
    idx = payload.get("channel_idx")
    return {
        "kind": "chan",
        "direction": "in",
        "channel_idx": idx,
        "sender_name": sender,
        "text": body,
        "sender_ts": sender_ts,
        "ts": ts or int(time.time()),
        "snr": payload.get("SNR"),
        "rssi": payload.get("RSSI"),
        "path_len": path_len,
        "path": payload.get("path") or None,
        "path_hash_size": hsize,
        "txt_type": payload.get("txt_type"),
        "dedup": f"c:{idx}:{sender_ts}:{_hash(text)}",
    }


def dm_message_row(payload: dict, contact_key: str | None, ts: int | None = None) -> dict:
    """payload CONTACT_MSG_RECV -> строка messages."""
    text = payload.get("text", "")
    prefix = (payload.get("pubkey_prefix") or "").lower()
    path_len, hsize = _path_info(payload)
    sender_ts = payload.get("sender_timestamp")
    return {
        "kind": "dm",
        "direction": "in",
        "contact_key": contact_key,
        "pubkey_prefix": prefix,
        "text": text,
        "sender_ts": sender_ts,
        "ts": ts or int(time.time()),
        "snr": payload.get("SNR"),
        "rssi": None,  # библиотека не отдаёт RSSI для ЛС
        "path_len": path_len,
        "path": None,
        "path_hash_size": hsize,
        "txt_type": payload.get("txt_type"),
        "dedup": f"d:{prefix}:{sender_ts}:{_hash(text)}",
    }


def outgoing_row(kind: str, text: str, *, channel_idx: int | None = None,
                 contact_key: str | None = None, sender_name: str | None = None,
                 ts: int | None = None) -> dict:
    t = ts or int(time.time())
    return {
        "kind": kind,
        "direction": "out",
        "channel_idx": channel_idx,
        "contact_key": contact_key,
        "pubkey_prefix": contact_key[:12] if contact_key else None,
        "sender_name": sender_name,
        "text": text,
        "sender_ts": t,
        "ts": t,
        "status": "sending",
        "read": 1,
    }


# --- метрики ---

SELF_BATTERY_KEYS = {"level": "battery_mv", "used_kb": "storage_used_kb", "total_kb": "storage_total_kb"}


def battery_metrics(p: dict) -> dict:
    """BATTERY: level — напряжение в мВ (так шлёт прошивка, несмотря на имя поля)."""
    return {SELF_BATTERY_KEYS[k]: v for k, v in p.items() if k in SELF_BATTERY_KEYS}


def stats_core_metrics(p: dict) -> dict:
    return {
        "battery_mv": p.get("battery_mv"),
        "uptime": p.get("uptime_secs"),
        "errors": p.get("errors"),
        "queue_len": p.get("queue_len"),
    }


def stats_radio_metrics(p: dict) -> dict:
    return {
        "noise_floor": p.get("noise_floor"),
        "last_rssi": p.get("last_rssi"),
        "last_snr": p.get("last_snr"),
        "tx_air_secs": p.get("tx_air_secs"),
        "rx_air_secs": p.get("rx_air_secs"),
    }


def stats_packets_metrics(p: dict) -> dict:
    return {
        "recv": p.get("recv"),
        "sent": p.get("sent"),
        "flood_tx": p.get("flood_tx"),
        "direct_tx": p.get("direct_tx"),
        "flood_rx": p.get("flood_rx"),
        "direct_rx": p.get("direct_rx"),
        "recv_errors": p.get("recv_errors"),
    }


def repeater_status_metrics(s: dict) -> dict:
    """Результат req_status_sync (parse_status) -> метрики репитера.

    airtime / rx_airtime в прошивке — секунды; bat — мВ.
    """
    return {
        "battery_mv": s.get("bat"),
        "uptime": s.get("uptime"),
        "tx_air_secs": s.get("airtime"),
        "rx_air_secs": s.get("rx_airtime"),
        "noise_floor": s.get("noise_floor"),
        "last_rssi": s.get("last_rssi"),
        "last_snr": s.get("last_snr"),
        "recv": s.get("nb_recv"),
        "sent": s.get("nb_sent"),
        "flood_tx": s.get("sent_flood"),
        "direct_tx": s.get("sent_direct"),
        "flood_rx": s.get("recv_flood"),
        "direct_rx": s.get("recv_direct"),
        "tx_queue_len": s.get("tx_queue_len"),
        "direct_dups": s.get("direct_dups"),
        "flood_dups": s.get("flood_dups"),
        "recv_errors": s.get("recv_errors"),
    }


def telemetry_metrics(lpp: list[dict] | None) -> dict:
    """LPP-список [{channel, type, value}] -> плоские метрики 'tm_<type>_<ch>'.

    Числовые значения сохраняются, составные (gps, accelerometer) — пропускаются, кроме gps.
    """
    out: dict[str, float] = {}
    for item in lpp or []:
        t = str(item.get("type", "")).replace(" ", "_")
        ch = item.get("channel", 0)
        v = item.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[f"tm_{t}_{ch}"] = float(v)
            if t == "voltage" and ch == 1:
                out["voltage"] = float(v)
    return out


def telemetry_gps(lpp: list[dict] | None) -> tuple[float, float] | None:
    for item in lpp or []:
        if item.get("type") == "gps" and isinstance(item.get("value"), dict):
            v = item["value"]
            lat, lon = v.get("latitude"), v.get("longitude")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and (lat or lon):
                return float(lat), float(lon)
    return None


# --- трассировки ---

def trace_hops(payload: dict) -> list[dict]:
    """TRACE_DATA -> [{hash, snr}], последний элемент — наш узел (без hash)."""
    return [{"hash": n.get("hash"), "snr": n.get("snr")} for n in payload.get("path") or []]


def build_trace_path(contact: dict) -> tuple[str, int]:
    """Маршрут trace «туда и обратно»: хопы out_path + хэш цели + хопы в обратном порядке.

    Возвращает (строка 'aa,bb,cc' для commands.send_trace, размер хэша в байтах).
    Размер хэша берётся из out_path_hash_mode контакта; допустимы 1, 2, 4 байта.
    """
    mode = max(0, int(contact.get("out_path_hash_mode") or 0))
    size = {0: 1, 1: 2, 2: 4}.get(mode, 1)
    hops: list[str] = []
    if int(contact.get("out_path_len", -1)) > 0:
        hops = split_hops(contact.get("out_path"), mode + 1)
        if mode + 1 != size:  # не поддерживаемый send_trace размер — путь не используем
            hops = []
    target = contact["public_key"][: size * 2].lower()
    full = hops + [target] + list(reversed(hops))
    return ",".join(full), size


def resolve_hops(hops: list[str], contacts: list[dict]) -> list[dict]:
    """Сопоставляет хэши хопов с контактами (только репитеры/комнаты) по префиксу ключа."""
    out = []
    for h in hops:
        if not h:
            out.append({"hash": h, "candidates": []})
            continue
        cands = [
            c for c in contacts
            if c["public_key"].startswith(h.lower()) and c.get("type") in (TYPE_REPEATER, TYPE_ROOM)
        ] or [c for c in contacts if c["public_key"].startswith(h.lower())]
        out.append({
            "hash": h,
            "candidates": [
                {"public_key": c["public_key"], "name": c["name"], "lat": c.get("lat"),
                 "lon": c.get("lon")}
                for c in cands
            ],
        })
    return out


def jsonable(obj: Any) -> Any:
    """bytes -> hex для отправки в JSON."""
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj
