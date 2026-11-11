"""Настоящий парсер библиотеки meshcore против эмулятора ноды (разбор событий)."""

import asyncio
import random

import pytest
from meshcore import EventType, MeshCore

from app import parse
from app.mock.emulator import MockNode


@pytest.fixture
async def mc_node():
    node = MockNode(speed=20, autonomous=False, ack_loss=0.0)
    port = await node.start()
    mc = await MeshCore.create_tcp("127.0.0.1", port, default_timeout=5)
    assert mc is not None
    yield mc, node
    await mc.disconnect()
    await node.stop()


async def test_self_info_contacts_channels(mc_node):
    mc, node = mc_node
    assert mc.self_info["name"] == "MeshBoard Mock"
    ev = await mc.commands.get_contacts()
    assert ev.type == EventType.CONTACTS and len(ev.payload) == len(node.state.contacts)
    rows = [parse.contact_from_lib(c) for c in ev.payload.values()]
    assert sum(1 for r in rows if r["lat"] is None) == 1  # контакт без GPS
    ch = await mc.commands.get_channel(0)
    assert ch.payload["channel_name"] == "Public"


async def test_channel_message_gets_path_and_rssi(mc_node):
    mc, node = mc_node
    await mc.commands.get_channel(0)
    mc.set_decrypt_channel_logs(True)
    got = asyncio.get_running_loop().create_future()
    mc.subscribe(EventType.CHANNEL_MSG_RECV, lambda e: got.done() or got.set_result(e.payload))
    sender = node.state.chat_contacts[0]  # путь через 2 репитера
    node.inject_channel_message(0, sender, "проверка")
    await asyncio.sleep(0.1)
    await mc.commands.get_msg()
    p = await asyncio.wait_for(got, 3)
    row = parse.channel_message_row(p)
    assert row["sender_name"] == sender.name and row["text"] == "проверка"
    assert row["path"] == bytes(reversed(sender.out_path)).hex()
    assert row["rssi"] is not None and row["snr"] is not None


async def test_dm_and_ack(mc_node):
    mc, node = mc_node
    await mc.commands.get_contacts()
    target = mc.get_contact_by_name("Мария")
    ev = await mc.commands.send_msg(target, "hi")
    assert ev.type == EventType.MSG_SENT
    code = ev.payload["expected_ack"].hex()
    ack = await mc.wait_for_event(EventType.ACK, {"code": code}, timeout=5)
    assert ack is not None and ack.payload["trip_time"] > 0


async def test_repeater_requests(mc_node):
    mc, node = mc_node
    await mc.commands.get_contacts()
    rpt = mc.get_contact_by_name("RPT Восток")
    assert await mc.commands.send_login_sync(rpt, "pw") is not None
    st = await mc.commands.req_status_sync(rpt)
    assert st and st["bat"] > 3000 and st["uptime"] > 0
    m = parse.repeater_status_metrics(st)
    assert m["battery_mv"] == st["bat"]
    tm = await mc.commands.req_telemetry_sync(rpt)
    assert parse.telemetry_metrics(tm)["voltage"] > 3
    nb = await mc.commands.fetch_all_neighbours(rpt)
    assert nb["neighbours_count"] == len(nb["neighbours"]) == 3


async def test_trace(mc_node):
    mc, node = mc_node
    await mc.commands.get_contacts()
    rpt = parse.contact_from_lib(mc.get_contact_by_name("RPT Юг"))
    path, _ = parse.build_trace_path(rpt)
    tag = random.randint(1, 2**31)
    ev = await mc.commands.send_trace(path=path, tag=tag)
    assert ev.type == EventType.MSG_SENT
    tr = await mc.wait_for_event(EventType.TRACE_DATA, {"tag": tag}, timeout=5)
    hops = parse.trace_hops(tr.payload)
    assert [h["hash"] for h in hops] == path.split(",") + [None]


async def test_self_stats(mc_node):
    mc, _ = mc_node
    assert (await mc.commands.get_bat()).payload["level"] > 3000
    assert "uptime_secs" in (await mc.commands.get_stats_core()).payload
    assert "noise_floor" in (await mc.commands.get_stats_radio()).payload
    assert "recv" in (await mc.commands.get_stats_packets()).payload
