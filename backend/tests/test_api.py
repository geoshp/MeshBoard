"""API целиком: приложение + NodeService + эмулятор."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings, wait_until


@pytest.fixture
def client(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        svc = app.state.svc
        wait_until(lambda: svc.node.connected, 15)
        c.svc = svc
        yield c


def login(c):
    r = c.post("/api/login", json={"password": "test-password-123"})
    assert r.status_code == 200


def test_auth_required(client):
    assert client.get("/api/health").json()["ok"] is True
    for url in ("/api/status", "/api/contacts", "/api/channels", "/api/map", "/api/repeaters"):
        assert client.get(url).status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/api/session").json() == {"authenticated": False}
    login(client)
    assert client.get("/api/session").json() == {"authenticated": True}
    assert client.get("/api/status").json()["state"] == "connected"
    client.post("/api/logout")
    client.cookies.clear()
    assert client.get("/api/status").status_code == 401


def test_csrf_origin_and_headers(client):
    login(client)
    r = client.post("/api/channels/0/read", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.get("/api/status")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert "httponly" in client.post("/api/login", json={"password": "test-password-123"}).headers["set-cookie"].lower()


def test_status_does_not_leak_secrets(client):
    login(client)
    text = client.get("/api/status").text + client.get("/api/config").text + client.get("/api/channels").text
    assert "test-password-123" not in text and "test-secret" not in text
    assert "channel_secret" not in text and "8b3387e9" not in text


def test_contacts_channels(client):
    login(client)
    contacts = client.get("/api/contacts").json()
    assert len(contacts) == 12 and any(c["type"] == 2 for c in contacts)
    chans = client.get("/api/channels").json()["channels"]
    assert [c["name"] for c in chans] == ["Public", "#moscow", "Тест"]


def test_incoming_channel_message_and_unread(client):
    login(client)
    node = client.svc.node.mock_node
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["type"] == "status"
        client.portal.call(node.inject_channel_message, 0, node.state.chat_contacts[1], "тест ленты")
        msgs = wait_until(lambda: client.get("/api/channels/0/messages").json())
        m = msgs[-1]
        assert m["sender_name"] == node.state.chat_contacts[1].name and m["text"] == "тест ленты"
        assert m["path"] and m["rssi"] is not None and m["snr"] is not None
        types = set()
        for _ in range(10):
            ev = ws.receive_json()
            types.add(ev["type"])
            if ev["type"] == "message":
                break
        assert "message" in types
    assert client.get("/api/unread").json()["channels"]["0"] == 1
    client.post("/api/channels/0/read")
    assert client.get("/api/unread").json()["total_channels"] == 0


def test_send_channel_message(client):
    login(client)
    r = client.post("/api/channels/1/messages", json={"text": "привет канал"})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert (1, "привет канал") in client.svc.node.mock_node.sent_channel_messages
    assert client.post("/api/channels/1/messages", json={"text": "   "}).status_code == 400
    assert client.post("/api/channels/1/messages", json={"text": "я" * 200}).status_code == 400
    assert client.post("/api/channels/9/messages", json={"text": "x"}).status_code == 404


def test_send_dm_delivered(client):
    login(client)
    key = next(c["public_key"] for c in client.get("/api/contacts").json() if c["name"] == "Мария")
    r = client.post(f"/api/dm/{key}/messages", json={"text": "ау"})
    assert r.status_code == 200
    mid = r.json()["id"]
    m = wait_until(lambda: next((x for x in client.get(f"/api/dm/{key}/messages").json()
                                 if x["id"] == mid and x["status"] == "delivered"), None), 15)
    assert m["trip_ms"] and m["attempts"] >= 1
    assert client.get("/api/dm").json()["conversations"][0]["contact_key"] == key


def test_incoming_dm_resolved_to_contact(client):
    login(client)
    node = client.svc.node.mock_node
    sender = node.state.chat_contacts[2]
    client.portal.call(node.inject_dm, sender, "личное")
    key = sender.key.hex()
    m = wait_until(lambda: client.get(f"/api/dm/{key}/messages").json())
    assert m[-1]["text"] == "личное" and m[-1]["path_len"] is not None
    assert client.get("/api/unread").json()["dms"][key] == 1


def test_repeater_poll_and_settings(client):
    login(client)
    reps = client.get("/api/repeaters").json()["repeaters"]
    key = next(r["public_key"] for r in reps if r["name"] == "RPT Юг")
    assert client.put(f"/api/repeaters/{key}/own", json={"own": True}).status_code == 200
    assert client.post(f"/api/repeaters/{key}/poll").status_code == 200

    def polled():
        r = next(x for x in client.get("/api/repeaters").json()["repeaters"] if x["public_key"] == key)
        return r if r["last_ok"] else None

    r = wait_until(polled, 30)
    assert r["own"] and r["last_poll"]["login_ok"] == 1
    assert r["last_ok"]["status"]["bat"] > 3000 and r["last_ok"]["telemetry"]
    assert {n["name"] for n in r["neighbours"]} >= {"RPT Восток"}
    assert client.put("/api/settings", json={"repeater_poll_min": 5}).json()["repeater_poll_min"] == 5
    assert client.put("/api/settings", json={"repeater_poll_min": 0}).status_code == 422
    series = client.get(f"/api/charts/metrics?source={key}&names=battery_mv,uptime&period=1h").json()["series"]
    assert series["battery_mv"]


def test_trace_and_map(client):
    login(client)
    key = next(c["public_key"] for c in client.get("/api/contacts").json() if c["name"] == "RPT Юг")
    tr = client.post(f"/api/contacts/{key}/trace").json()
    assert tr["status"] == "pending" and tr["path_sent"].count(",") == 2
    done = wait_until(lambda: (lambda t: t if t["status"] != "pending" else None)(
        client.get(f"/api/traces/{tr['id']}").json()), 20)
    assert done["status"] == "ok"
    assert [r["candidates"][0]["name"] for r in done["resolved"]] == ["RPT Восток", "RPT Юг", "RPT Восток"]
    mp = client.get("/api/map").json()
    assert mp["self"]["lat"] and mp["routes"] and mp["traces"][0]["id"] == tr["id"]


def test_charts_endpoints(client):
    login(client)
    client.svc.db.add_metrics("self", {"recv": 10})
    for p in ("1h", "24h", "7d"):
        assert "series" in client.get(f"/api/charts/metrics?names=battery_mv,recv&period={p}").json()
        assert "rows" in client.get(f"/api/charts/messages?period={p}").json()
        assert "rows" in client.get(f"/api/charts/signal?period={p}").json()
        assert "top" in client.get(f"/api/charts/activity?period={p}").json()
    assert client.get("/api/charts/sources").json()[0]["key"] == "self"
    assert "battery_mv" in client.get("/api/node/metrics").json()["values"]


def test_reconnect_after_drop(client):
    node = client.svc.node
    client.portal.call(node.force_reconnect, "test")
    wait_until(lambda: node.status["state"] != "connected", 5)
    wait_until(lambda: node.connected, 20)
    login(client)
    assert client.get("/api/status").json()["state"] == "connected"
