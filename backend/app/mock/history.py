"""Синтетическая история для режима mock (только при пустой БД): метрики своей ноды и
репитеров, сообщения каналов и активность за последние часы — чтобы графики были не пустыми."""

from __future__ import annotations

import math
import random
import time

from ..db import Database
from .emulator import PHRASES, MockNodeState


def seed_history(db: Database, state: MockNodeState, hours: int = 48, step: int = 600) -> None:
    if db.metric_sources():
        return
    rnd = random.Random(7)
    now = int(time.time())
    start = now - hours * 3600
    counters = {"tx_air_secs": 50.0, "rx_air_secs": 400.0, "recv": 800.0, "sent": 150.0}
    rep_counters = {r.key.hex(): {"tx_air_secs": 1000.0, "rx_air_secs": 9000.0, "recv": 5000.0, "sent": 1200.0}
                    for r in state.repeaters[:3]}
    for ts in range(start, now, step):
        h = ts / 3600.0
        busy = 0.5 + 0.5 * math.sin((h % 24 - 8) / 24 * 2 * math.pi)
        for k, base in (("tx_air_secs", 3), ("rx_air_secs", 20), ("recv", 25), ("sent", 5)):
            counters[k] += base * busy * rnd.uniform(0.5, 1.5)
        db.add_metrics("self", {
            "battery_mv": 3950 + 180 * math.sin(h / 3) + rnd.uniform(-8, 8),
            "uptime": ts - (start - 3600),
            "noise_floor": -112 + rnd.uniform(-3, 3),
            "last_snr": rnd.uniform(-4, 11),
            "last_rssi": rnd.uniform(-115, -70),
            "rx_log_packets": int(30 * busy * rnd.uniform(0.6, 1.4)),
            **{k: round(v) for k, v in counters.items()},
        }, ts=ts)
        for i, r in enumerate(state.repeaters[:3]):
            if ts % 1800 >= step:
                continue
            rc = rep_counters[r.key.hex()]
            for k, base in (("tx_air_secs", 15), ("rx_air_secs", 90), ("recv", 120), ("sent", 30)):
                rc[k] += base * busy * rnd.uniform(0.5, 1.5)
            db.add_metrics(r.key.hex(), {
                "battery_mv": 3900 + 200 * math.sin(h / 4 + i) + rnd.uniform(-10, 10),
                "voltage": round((3900 + 200 * math.sin(h / 4 + i)) / 1000, 2),
                "uptime": ts - r.boot_ts,
                "noise_floor": -110 + rnd.uniform(-4, 4),
                "last_snr": rnd.uniform(-6, 10),
                "last_rssi": rnd.uniform(-118, -75),
                **{k: round(v) for k, v in rc.items()},
            }, ts=ts)
        # сообщения и активность
        if rnd.random() < 0.35 * busy + 0.05:
            sender = rnd.choice(state.chat_contacts)
            hops = [b for b in reversed(sender.out_path)]
            db.insert_message({
                "kind": "chan", "direction": "in", "channel_idx": rnd.choice([0, 0, 1, 2]),
                "sender_name": sender.name, "text": rnd.choice(PHRASES), "sender_ts": ts, "ts": ts,
                "snr": round(rnd.uniform(-7, 12), 2), "rssi": rnd.randint(-120, -65),
                "path_len": len(hops), "path": bytes(hops).hex() or None, "path_hash_size": 1,
                "txt_type": 0, "read": 1, "dedup": f"seed:{ts}",
            })
            db.add_activity(sender.key.hex(), "msg", ts)
        if rnd.random() < 0.4:
            db.add_activity(rnd.choice(state.contacts).key.hex(), "advert", ts)
