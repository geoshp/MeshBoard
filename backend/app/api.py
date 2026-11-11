"""REST API и WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .auth import COOKIE_NAME

log = logging.getLogger("meshboard.api")

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api")


# --- зависимости ---

def services(request: Request):
    return request.app.state.svc


def require_auth(request: Request) -> None:
    svc = request.app.state.svc
    if not svc.auth.verify(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="unauthorized")


def same_origin(origin: str | None, host: str | None) -> bool:
    """Запросы, меняющие состояние, принимаем только со своего origin (защита от CSRF)."""
    if not origin:
        return True  # не браузер / тот же origin без заголовка
    try:
        return urlparse(origin).netloc == (host or "")
    except ValueError:
        return False


def client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# --- публичные ---

class LoginBody(BaseModel):
    password: str = Field(max_length=256)


@public.get("/health")
def health(request: Request) -> dict:
    svc = request.app.state.svc
    return {"ok": True, "node": svc.node.status["state"]}


@public.get("/session")
def session(request: Request) -> dict:
    svc = request.app.state.svc
    return {"authenticated": svc.auth.verify(request.cookies.get(COOKIE_NAME))}


@public.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    svc = request.app.state.svc
    cid = client_id(request)
    if svc.auth.rate_limited(cid):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    if not svc.auth.check_password(body.password):
        svc.auth.register_failure(cid)
        await asyncio.sleep(0.5)
        log.warning("failed login attempt from %s", cid)
        raise HTTPException(status_code=401, detail="bad_password")
    svc.auth.reset_failures(cid)
    response.set_cookie(
        COOKIE_NAME, svc.auth.issue(), max_age=svc.auth.session_seconds, httponly=True,
        samesite="strict", secure=svc.settings.cookie_secure, path="/",
    )
    return {"ok": True}


@public.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# --- защищённые ---

@router.get("/status", dependencies=[Depends(require_auth)])
def status(svc=Depends(services)) -> dict:
    return svc.node.status


@router.get("/config", dependencies=[Depends(require_auth)])
def config(svc=Depends(services)) -> dict:
    s = svc.settings
    return {
        "tile_url": s.tile_url,
        "tile_attribution": s.tile_attribution,
        "mock": s.mock,
        "self_poll_sec": s.self_poll_sec,
        "retention_messages_days": s.retention_messages_days,
        "retention_metrics_days": s.retention_metrics_days,
    }


@router.get("/node/metrics", dependencies=[Depends(require_auth)])
def node_metrics(svc=Depends(services)) -> dict:
    return svc.db.latest_metrics("self")


@router.post("/node/reconnect", dependencies=[Depends(require_auth)])
def reconnect(svc=Depends(services)) -> dict:
    svc.node.force_reconnect("переподключение по запросу")
    return {"ok": True}


# --- контакты, каналы, ЛС ---

class SendBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


def _clean_text(text: str, limit: int) -> str:
    text = text.strip()
    if not text:
        raise body_error("text_empty")
    if len(text.encode("utf-8")) > limit:
        raise body_error("text_too_long")
    return text


def _own_set(svc) -> set[str]:
    return set(svc.db.get_setting("own_repeaters", []) or [])


def _contact_out(c: dict, own: set[str]) -> dict:
    c = dict(c)
    c["own"] = c["public_key"] in own
    return c


@router.get("/contacts", dependencies=[Depends(require_auth)])
def contacts(svc=Depends(services)) -> list[dict]:
    own = _own_set(svc)
    return [_contact_out(c, own) for c in svc.db.list_contacts()]


@router.get("/contacts/{key}", dependencies=[Depends(require_auth)])
def contact(key: str, svc=Depends(services)) -> dict:
    c = svc.db.get_contact(key.lower())
    if not c:
        raise body_error("contact_not_found", 404)
    return _contact_out(c, _own_set(svc))


@router.get("/unread", dependencies=[Depends(require_auth)])
def unread(svc=Depends(services)) -> dict:
    return svc.db.unread_summary()


@router.get("/channels", dependencies=[Depends(require_auth)])
def channels(svc=Depends(services)) -> dict:
    return {"channels": svc.db.list_channels(), "max_text": svc.node.max_channel_text()}


@router.get("/channels/{idx}/messages", dependencies=[Depends(require_auth)])
def channel_messages(idx: int, before: int | None = None, limit: int = 50, svc=Depends(services)) -> list[dict]:
    return svc.db.list_channel_messages(idx, before, max(1, min(limit, 200)))


@router.post("/channels/{idx}/messages", dependencies=[Depends(require_auth)])
async def channel_send(idx: int, body: SendBody, svc=Depends(services)) -> dict:
    if svc.db.get_channel(idx) is None:
        raise body_error("channel_not_found", 404)
    text = _clean_text(body.text, svc.node.max_channel_text())
    if not svc.node.connected:
        raise node_unavailable()
    return await svc.node.send_channel(idx, text)


@router.post("/channels/{idx}/read", dependencies=[Depends(require_auth)])
def channel_read(idx: int, svc=Depends(services)) -> dict:
    n = svc.db.mark_read("chan", idx)
    if n:
        svc.hub.publish("read", {"kind": "chan", "key": idx})
    return {"ok": True, "marked": n}


@router.get("/dm", dependencies=[Depends(require_auth)])
def conversations(svc=Depends(services)) -> dict:
    return {"conversations": svc.db.list_conversations(), "max_text": svc.node.MAX_TEXT_BYTES}


@router.get("/dm/{key}/messages", dependencies=[Depends(require_auth)])
def dm_messages(key: str, before: int | None = None, limit: int = 50, svc=Depends(services)) -> list[dict]:
    return svc.db.list_dm_messages(key.lower(), before, max(1, min(limit, 200)))


@router.post("/dm/{key}/messages", dependencies=[Depends(require_auth)])
async def dm_send(key: str, body: SendBody, svc=Depends(services)) -> dict:
    c = svc.db.get_contact(key.lower())
    if not c:
        raise body_error("contact_not_found", 404)
    text = _clean_text(body.text, svc.node.MAX_TEXT_BYTES)
    if not svc.node.connected:
        raise node_unavailable()
    return await svc.node.send_dm(c["public_key"], text)


@router.post("/dm/{key}/read", dependencies=[Depends(require_auth)])
def dm_read(key: str, svc=Depends(services)) -> dict:
    n = svc.db.mark_read("dm", key.lower())
    if n:
        svc.hub.publish("read", {"kind": "dm", "key": key.lower()})
    return {"ok": True, "marked": n}


# --- репитеры ---

PERIODS = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


class OwnBody(BaseModel):
    own: bool


class SettingsBody(BaseModel):
    repeater_poll_min: int = Field(ge=1, le=1440)


@router.get("/repeaters", dependencies=[Depends(require_auth)])
def repeaters(svc=Depends(services)) -> dict:
    rp = svc.repeaters
    own = set(rp.own())
    items = [rp.describe(c, own) for c in svc.db.list_contacts() if c["type"] in (2, 3)]
    items.sort(key=lambda r: (not r["own"], -(r["last_heard"] or 0)))
    return {"repeaters": items, "interval_min": rp.interval_min}


@router.put("/repeaters/{key}/own", dependencies=[Depends(require_auth)])
def repeater_own(key: str, body: OwnBody, svc=Depends(services)) -> dict:
    c = svc.db.get_contact(key.lower())
    if not c or c["type"] not in (2, 3):
        raise body_error("contact_not_found", 404)
    svc.repeaters.set_own(c["public_key"], body.own)
    svc.hub.publish("repeater", {"public_key": c["public_key"]})
    return {"ok": True}


@router.post("/repeaters/{key}/poll", dependencies=[Depends(require_auth)])
def repeater_poll(key: str, svc=Depends(services)) -> dict:
    c = svc.db.get_contact(key.lower())
    if not c or c["type"] not in (2, 3):
        raise body_error("contact_not_found", 404)
    if not svc.node.connected:
        raise node_unavailable()
    svc.repeaters.request_poll(c["public_key"])
    return {"ok": True}


@router.get("/repeaters/{key}/history", dependencies=[Depends(require_auth)])
def repeater_history(key: str, svc=Depends(services)) -> list[dict]:
    return svc.db.repeater_poll_history(key.lower(), 20)


@router.put("/settings", dependencies=[Depends(require_auth)])
def put_settings(body: SettingsBody, svc=Depends(services)) -> dict:
    svc.repeaters.set_interval(body.repeater_poll_min)
    svc.hub.publish("repeater", {})
    return {"ok": True, "repeater_poll_min": svc.repeaters.interval_min}


# --- графики ---

def _range(period: str) -> tuple[int, int, int]:
    import time as _t
    span = PERIODS.get(period, 86400)
    until = int(_t.time())
    bucket = max(60, span // 180)
    return until - span, until, bucket


COUNTERS = {"tx_air_secs", "rx_air_secs", "recv", "sent", "flood_tx", "direct_tx", "flood_rx",
            "direct_rx", "recv_errors", "direct_dups", "flood_dups"}


@router.get("/charts/metrics", dependencies=[Depends(require_auth)])
def chart_metrics(source: str = "self", names: str = "", period: str = "24h", svc=Depends(services)) -> dict:
    since, until, bucket = _range(period)
    wanted = [n for n in names.split(",") if n][:20]
    gauges = [n for n in wanted if n not in COUNTERS]
    counters = [n for n in wanted if n in COUNTERS]
    series = svc.db.query_metrics(source.lower(), gauges, since, until, bucket) if gauges else {}
    if counters:
        # счётчики накопительные: берём максимум в корзине и разность соседних корзин
        raw = svc.db.query_metrics_max(source.lower(), counters, since - bucket, until, bucket)
        for name, pts in raw.items():
            out = []
            for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
                d = v1 - v0
                out.append([t1, round(d, 3) if d >= 0 else None])
            series[name] = out
    return {"since": since, "until": until, "bucket": bucket, "series": series}


@router.get("/charts/sources", dependencies=[Depends(require_auth)])
def chart_sources(svc=Depends(services)) -> list[dict]:
    have = set(svc.db.metric_sources())
    out = [{"key": "self", "name": None}]
    for c in svc.db.list_contacts():
        if c["public_key"] in have:
            out.append({"key": c["public_key"], "name": c["name"]})
    return out


@router.get("/charts/messages", dependencies=[Depends(require_auth)])
def chart_messages(period: str = "24h", svc=Depends(services)) -> dict:
    since, until, _ = _range(period)
    bucket = 3600 if PERIODS.get(period, 86400) >= 86400 else 600
    return {"bucket": bucket, "rows": svc.db.messages_per_bucket(since, until, bucket)}


@router.get("/charts/signal", dependencies=[Depends(require_auth)])
def chart_signal(period: str = "24h", svc=Depends(services)) -> dict:
    since, until, bucket = _range(period)
    return {"bucket": bucket, "rows": svc.db.signal_series(since, until, bucket)}


@router.get("/charts/activity", dependencies=[Depends(require_auth)])
def chart_activity(period: str = "24h", svc=Depends(services)) -> dict:
    since, until, _ = _range(period)
    bucket = 3600 if PERIODS.get(period, 86400) >= 86400 else 600
    return {
        "bucket": bucket,
        "series": svc.db.activity_per_bucket(since, until, bucket),
        "top": svc.db.contact_activity(since, until, 15),
    }


# --- карта и трассировки ---

def _trace_out(tr: dict, contacts: list[dict]) -> dict:
    from . import parse
    tr = dict(tr)
    hops = tr.get("hops") or []
    tr["resolved"] = parse.resolve_hops([h.get("hash") or "" for h in hops if h.get("hash")], contacts)
    sent = (tr.get("path_sent") or "").split(",") if tr.get("path_sent") else []
    tr["path_resolved"] = parse.resolve_hops(sent, contacts)
    return tr


@router.post("/contacts/{key}/trace", dependencies=[Depends(require_auth)])
async def trace(key: str, svc=Depends(services)) -> dict:
    c = svc.db.get_contact(key.lower())
    if not c:
        raise body_error("contact_not_found", 404)
    if not svc.node.connected:
        raise node_unavailable()
    tr = await svc.node.start_trace(c)
    return _trace_out(tr, svc.db.list_contacts())


@router.get("/traces", dependencies=[Depends(require_auth)])
def traces(period: str = "7d", svc=Depends(services)) -> list[dict]:
    since, _, _ = _range(period)
    contacts = svc.db.list_contacts()
    return [_trace_out(t, contacts) for t in svc.db.list_traces(since, 50)]


@router.get("/traces/{trace_id}", dependencies=[Depends(require_auth)])
def trace_get(trace_id: int, svc=Depends(services)) -> dict:
    tr = svc.db.get_trace(trace_id)
    if not tr:
        raise body_error("not_found", 404)
    return _trace_out(tr, svc.db.list_contacts())


@router.get("/map", dependencies=[Depends(require_auth)])
def map_data(period: str = "24h", svc=Depends(services)) -> dict:
    from . import parse
    since, _, _ = _range(period)
    contacts = svc.db.list_contacts()
    own = _own_set(svc)
    by_key = {c["public_key"]: c for c in contacts}
    si = svc.node.status.get("self_info") or {}
    self_node = {
        "public_key": si.get("public_key"), "name": si.get("name"),
        "lat": si.get("adv_lat") or None, "lon": si.get("adv_lon") or None,
    }

    def keys(resolved: list[dict]) -> list[str | None]:
        return [r["candidates"][0]["public_key"] if len(r["candidates"]) == 1 else None for r in resolved]

    # маршруты к контактам (out_path): мы -> хопы -> контакт
    routes = []
    for c in contacts:
        if c["out_path_len"] > 0:
            hops = parse.split_hops(c["out_path"], c["out_path_hash_mode"] + 1)
            routes.append({"public_key": c["public_key"], "hops": keys(parse.resolve_hops(hops, contacts))})

    # пути принятых сообщений: отправитель -> хопы -> мы
    agg: dict[tuple, dict] = {}
    for m in svc.db.recent_message_paths(since):
        sender = m["contact_key"]
        if not sender and m["sender_name"]:
            sc = svc.db.find_contact_by_name(m["sender_name"])
            sender = sc["public_key"] if sc else None
        hops = parse.split_hops(m["path"], m["path_hash_size"])
        k = (sender, m["path"])
        a = agg.setdefault(k, {"sender": sender, "sender_name": m["sender_name"],
                               "hops": keys(parse.resolve_hops(hops, contacts)), "raw": hops,
                               "count": 0, "last_ts": 0})
        a["count"] += 1
        a["last_ts"] = max(a["last_ts"], m["ts"])

    # соседи своих репитеров
    links = []
    for key in own:
        nb = svc.db.last_neighbours(key)
        for n in (nb or {}).get("neighbours") or []:
            cands = [c for c in contacts if c["public_key"].startswith((n.get("pubkey") or "~").lower())]
            if len(cands) == 1:
                links.append({"from": key, "to": cands[0]["public_key"], "snr": n.get("snr")})

    trs = [_trace_out(t, contacts) for t in svc.db.list_traces(since, 30) if t["status"] == "ok"]
    return {
        "self": self_node,
        "contacts": [_contact_out(c, own) for c in contacts],
        "routes": routes,
        "message_paths": sorted(agg.values(), key=lambda a: -a["count"])[:200],
        "neighbours": links,
        "traces": [{"id": t["id"], "ts": t["ts"], "target_key": t["target_key"],
                    "hops": keys(t["resolved"]), "snr": [h.get("snr") for h in t["hops"] or []]}
                   for t in trs],
        "unknown_keys": [k for k in by_key if by_key[k]["lat"] is None],
    }


# --- WebSocket ---

async def websocket_endpoint(ws: WebSocket) -> None:
    svc = ws.app.state.svc
    if not svc.auth.verify(ws.cookies.get(COOKIE_NAME)) or not same_origin(
        ws.headers.get("origin"), ws.headers.get("host")
    ):
        await ws.close(code=4401)
        return
    await ws.accept()
    q = svc.hub.subscribe()

    async def sender() -> None:
        await ws.send_text(json.dumps({"type": "status", "data": svc.node.status}, ensure_ascii=False))
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25)
            except asyncio.TimeoutError:
                msg = '{"type":"ping"}'
            await ws.send_text(msg)
            if msg == '{"type": "overflow"}':
                return

    async def receiver() -> None:
        while True:
            await ws.receive_text()  # клиентские ping игнорируем; исключение = отключение

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        svc.hub.unsubscribe(q)
        try:
            await ws.close()
        except RuntimeError:
            pass


def body_error(detail: str, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def node_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="node_not_connected")


Json = dict[str, Any]
