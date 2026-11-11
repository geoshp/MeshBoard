"""Хранилище SQLite."""

import time

from app import parse
from app.db import Database


def db(tmp_path) -> Database:
    return Database(str(tmp_path / "t.db"))


def test_wal_enabled(tmp_path):
    d = db(tmp_path)
    assert d.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def contact(key="aa" * 32, name="Alice", type_=1):
    return parse.contact_from_lib({"public_key": key, "type": type_, "adv_name": name, "out_path_len": 1,
                                   "out_path_hash_mode": 0, "out_path": "c3", "last_advert": 100,
                                   "adv_lat": 55.1, "adv_lon": 37.2, "lastmod": 100})


def test_contacts_upsert_prefix_absent(tmp_path):
    d = db(tmp_path)
    d.upsert_contact(contact())
    d.upsert_contact(contact("bb" * 32, "Bob"))
    d.upsert_contact(contact(name="Alice2"))
    assert len(d.list_contacts()) == 2
    assert d.get_contact("aa" * 32)["name"] == "Alice2"
    assert d.find_contact_by_prefix("aaaa")["name"] == "Alice2"
    assert d.find_contact_by_name("bob")["public_key"] == "bb" * 32
    d.mark_contacts_absent({"aa" * 32})
    assert d.get_contact("bb" * 32)["on_device"] == 0


def test_message_dedup_unread_read(tmp_path):
    d = db(tmp_path)
    row = parse.channel_message_row({"channel_idx": 0, "sender_timestamp": 1, "text": "A: hi",
                                     "path_len": 0, "path_hash_mode": 0})
    assert d.insert_message(row) is not None
    assert d.insert_message(dict(row)) is None  # повтор
    d.upsert_channel(0, "Public", "11")
    assert d.list_channels()[0]["unread"] == 1
    assert d.unread_summary()["total_channels"] == 1
    assert d.mark_read("chan", 0) == 1
    assert d.unread_summary()["total_channels"] == 0


def test_outgoing_status_and_conversations(tmp_path):
    d = db(tmp_path)
    key = "aa" * 32
    m = d.insert_message(parse.outgoing_row("dm", "hello", contact_key=key))
    assert m["status"] == "sending" and m["read"] == 1
    m = d.update_message(m["id"], status="delivered", trip_ms=900)
    assert m["status"] == "delivered" and m["trip_ms"] == 900
    d.insert_message(parse.dm_message_row({"pubkey_prefix": key[:12], "sender_timestamp": 3,
                                           "text": "re", "path_len": 1, "path_hash_mode": 0}, key))
    conv = d.list_conversations()
    assert conv[0]["contact_key"] == key and conv[0]["unread"] == 1 and conv[0]["last_text"] == "re"


def test_resolve_dm_prefix(tmp_path):
    d = db(tmp_path)
    key = "cc" * 32
    d.insert_message(parse.dm_message_row({"pubkey_prefix": key[:12], "sender_timestamp": 1, "text": "x",
                                           "path_len": 0, "path_hash_mode": 0}, None))
    d.resolve_dm_prefix(key[:12], key)
    assert d.list_dm_messages(key, None, 10)[0]["text"] == "x"


def test_metrics_buckets_and_max(tmp_path):
    d = db(tmp_path)
    t0 = 1_000_000
    for i in range(6):
        d.add_metrics("self", {"battery_mv": 4000 + i, "recv": 10 * i, "skip": None, "flag": True}, ts=t0 + i * 60)
    s = d.query_metrics("self", ["battery_mv"], t0, t0 + 600, 120)
    assert len(s["battery_mv"]) == 3
    mx = d.query_metrics_max("self", ["recv"], t0, t0 + 600, 120)
    assert [v for _, v in mx["recv"]] == [10, 30, 50]
    assert set(d.latest_metrics("self")["values"]) == {"battery_mv", "recv"}


def test_retention(tmp_path):
    d = db(tmp_path)
    old = int(time.time()) - 100 * 86400
    row = parse.channel_message_row({"channel_idx": 0, "sender_timestamp": 1, "text": "old"}, ts=old)
    d.insert_message(row)
    d.insert_message(parse.channel_message_row({"channel_idx": 0, "sender_timestamp": 2, "text": "new"}))
    d.add_metrics("self", {"x": 1}, ts=old)
    d.add_metrics("self", {"x": 2})
    res = d.cleanup(messages_days=90, metrics_days=30)
    assert res["messages"] == 1 and res["metrics"] == 1
    assert [m["text"] for m in d.list_channel_messages(0, None, 10)] == ["new"]


def test_settings_and_polls_traces(tmp_path):
    d = db(tmp_path)
    assert d.get_setting("missing", 5) == 5
    d.set_setting("own_repeaters", ["a"])
    assert d.get_setting("own_repeaters") == ["a"]
    p = d.add_repeater_poll("k", True, {"bat": 1}, [{"type": "voltage"}], [{"pubkey": "aa"}], None)
    assert p["status"] == {"bat": 1} and d.last_neighbours("k")["neighbours"] == [{"pubkey": "aa"}]
    tr = d.add_trace("k", 5, "aa,bb", "pending")
    tr = d.update_trace(tr["id"], "ok", [{"hash": "aa", "snr": 1}])
    assert tr["hops"][0]["snr"] == 1 and d.list_traces(0)[0]["status"] == "ok"
