"""Разбор payload событий meshcore в записи хранилища."""

from app import parse


def test_contact_from_lib_gps_and_flood():
    c = parse.contact_from_lib({
        "public_key": "AABB" + "00" * 30, "type": 2, "flags": 0, "out_path_len": -1,
        "out_path_hash_mode": -1, "out_path": "", "adv_name": "RPT ", "last_advert": 100,
        "adv_lat": 0.0, "adv_lon": 0.0, "lastmod": 5,
    })
    assert c["public_key"].startswith("aabb")
    assert c["name"] == "RPT"
    assert c["lat"] is None and c["lon"] is None
    assert c["out_path_len"] == -1 and c["out_path_hash_mode"] == 0


def test_split_sender():
    assert parse.split_sender("Алиса: привет: как дела") == ("Алиса", "привет: как дела")
    assert parse.split_sender("без отправителя") == (None, "без отправителя")
    assert parse.split_sender("x" * 40 + ": текст")[0] is None


def test_channel_row_with_path_rssi():
    row = parse.channel_message_row({
        "type": "CHAN", "SNR": 6.5, "channel_idx": 1, "path_hash_mode": 0, "path_len": 2,
        "txt_type": 0, "sender_timestamp": 1000, "text": "Bob: hi", "path": "b2a1", "RSSI": -101,
    }, ts=2000)
    assert row["sender_name"] == "Bob" and row["text"] == "hi"
    assert row["snr"] == 6.5 and row["rssi"] == -101
    assert row["path"] == "b2a1" and row["path_len"] == 2 and row["path_hash_size"] == 1
    assert row["dedup"].startswith("c:1:1000:")


def test_dm_row_direct_route():
    row = parse.dm_message_row({
        "type": "PRIV", "SNR": 2.0, "pubkey_prefix": "A1B2C3D4E5F6", "path_hash_mode": -1,
        "path_len": 255, "txt_type": 0, "sender_timestamp": 5, "text": "yo",
    }, "a1b2c3d4e5f6" + "00" * 26)
    assert row["path_len"] == -1 and row["path_hash_size"] is None
    assert row["pubkey_prefix"] == "a1b2c3d4e5f6"
    assert row["rssi"] is None


def test_dedup_same_retry():
    p = {"pubkey_prefix": "aa" * 6, "sender_timestamp": 7, "text": "x", "path_len": 1, "path_hash_mode": 0}
    assert parse.dm_message_row(p, None)["dedup"] == parse.dm_message_row(dict(p, SNR=3), None)["dedup"]


def test_split_hops():
    assert parse.split_hops("a1b2c3", 1) == ["a1", "b2", "c3"]
    assert parse.split_hops("a1b2c3d4", 2) == ["a1b2", "c3d4"]
    assert parse.split_hops("", 1) == []


def test_build_trace_path_round_trip():
    c = {"public_key": "d4" + "11" * 31, "out_path_len": 2, "out_path_hash_mode": 0, "out_path": "c3e5"}
    path, size = parse.build_trace_path(c)
    assert path == "c3,e5,d4,e5,c3" and size == 1
    flood = {"public_key": "d4" + "11" * 31, "out_path_len": -1, "out_path_hash_mode": 0, "out_path": ""}
    assert parse.build_trace_path(flood)[0] == "d4"


def test_resolve_hops_prefers_repeaters_and_ambiguity():
    contacts = [
        {"public_key": "aa01", "name": "R1", "type": 2},
        {"public_key": "aa02", "name": "U", "type": 1},
        {"public_key": "bb01", "name": "R2", "type": 2},
        {"public_key": "bb02", "name": "R3", "type": 2},
    ]
    r = parse.resolve_hops(["aa", "bb", "cc"], contacts)
    assert [c["name"] for c in r[0]["candidates"]] == ["R1"]
    assert len(r[1]["candidates"]) == 2
    assert r[2]["candidates"] == []


def test_metrics_conversions():
    m = parse.repeater_status_metrics({"bat": 3900, "uptime": 10, "airtime": 5, "rx_airtime": 7,
                                       "nb_recv": 1, "nb_sent": 2, "last_snr": 1.5})
    assert m["battery_mv"] == 3900 and m["tx_air_secs"] == 5 and m["rx_air_secs"] == 7
    t = parse.telemetry_metrics([
        {"channel": 1, "type": "voltage", "value": 3.81},
        {"channel": 2, "type": "temperature", "value": 15.2},
        {"channel": 3, "type": "gps", "value": {"latitude": 1, "longitude": 2, "altitude": 3}},
    ])
    assert t == {"tm_voltage_1": 3.81, "voltage": 3.81, "tm_temperature_2": 15.2}
    assert parse.battery_metrics({"level": 4012, "used_kb": 1, "total_kb": 2})["battery_mv"] == 4012


def test_trace_hops():
    hops = parse.trace_hops({"path": [{"hash": "c3", "snr": 1.0}, {"snr": 9.5}]})
    assert hops == [{"hash": "c3", "snr": 1.0}, {"hash": None, "snr": 9.5}]


def test_jsonable_bytes():
    assert parse.jsonable({"a": b"\x01\x02", "b": [b"\xff"]}) == {"a": "0102", "b": ["ff"]}
