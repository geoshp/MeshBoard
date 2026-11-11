"""SQLite-хранилище (WAL). Одно соединение, защищённое блокировкой; запросы короткие.

REST-обработчики FastAPI объявлены синхронными и выполняются в пуле потоков, поэтому
соединение открыто с check_same_thread=False и все обращения идут под self._lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS contacts (
  public_key TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  type INTEGER NOT NULL DEFAULT 0,
  flags INTEGER NOT NULL DEFAULT 0,
  out_path_len INTEGER NOT NULL DEFAULT -1,
  out_path_hash_mode INTEGER NOT NULL DEFAULT 0,
  out_path TEXT NOT NULL DEFAULT '',
  lat REAL,
  lon REAL,
  last_advert INTEGER NOT NULL DEFAULT 0,
  lastmod INTEGER NOT NULL DEFAULT 0,
  first_seen INTEGER NOT NULL,
  last_heard INTEGER NOT NULL DEFAULT 0,
  on_device INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS channels (
  idx INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  channel_hash TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('chan', 'dm')),
  direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
  channel_idx INTEGER,
  contact_key TEXT,
  pubkey_prefix TEXT,
  sender_name TEXT,
  text TEXT NOT NULL,
  sender_ts INTEGER,
  ts INTEGER NOT NULL,
  snr REAL,
  rssi REAL,
  path_len INTEGER,
  path TEXT,
  path_hash_size INTEGER,
  txt_type INTEGER,
  status TEXT,
  expected_ack TEXT,
  trip_ms INTEGER,
  attempts INTEGER,
  read INTEGER NOT NULL DEFAULT 0,
  dedup TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_msg_chan ON messages (kind, channel_idx, ts);
CREATE INDEX IF NOT EXISTS idx_msg_contact ON messages (kind, contact_key, ts);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages (ts);

CREATE TABLE IF NOT EXISTS metrics (
  ts INTEGER NOT NULL,
  source TEXT NOT NULL,
  name TEXT NOT NULL,
  value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics ON metrics (source, name, ts);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics (ts);

CREATE TABLE IF NOT EXISTS activity (
  ts INTEGER NOT NULL,
  contact_key TEXT NOT NULL,
  kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity ON activity (ts);

CREATE TABLE IF NOT EXISTS repeater_polls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  contact_key TEXT NOT NULL,
  login_ok INTEGER,
  status_json TEXT,
  telemetry_json TEXT,
  neighbours_json TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_rpolls ON repeater_polls (contact_key, ts);

CREATE TABLE IF NOT EXISTS traces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  target_key TEXT,
  tag INTEGER,
  path_sent TEXT,
  status TEXT NOT NULL,
  hops_json TEXT,
  source TEXT NOT NULL DEFAULT 'trace'
);
CREATE INDEX IF NOT EXISTS idx_traces ON traces (ts);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now() -> int:
    return int(time.time())


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            if path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # --- низкоуровневые помощники ---
    @staticmethod
    def _p(params: Any) -> Any:
        return params if isinstance(params, dict) else tuple(params)

    def _all(self, sql: str, params: Iterable[Any] | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, self._p(params)).fetchall()]

    def _one(self, sql: str, params: Iterable[Any] | dict = ()) -> dict | None:
        with self._lock:
            r = self.conn.execute(sql, self._p(params)).fetchone()
            return dict(r) if r else None

    def _exec(self, sql: str, params: Iterable[Any] | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, self._p(params))

    # --- настройки ---
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # --- контакты ---
    def upsert_contact(self, c: dict) -> None:
        ts = now()
        self._exec(
            """
            INSERT INTO contacts (public_key, name, type, flags, out_path_len, out_path_hash_mode,
                                  out_path, lat, lon, last_advert, lastmod, first_seen, last_heard,
                                  on_device)
            VALUES (:public_key, :name, :type, :flags, :out_path_len, :out_path_hash_mode,
                    :out_path, :lat, :lon, :last_advert, :lastmod, :ts, :last_advert, 1)
            ON CONFLICT(public_key) DO UPDATE SET
              name = excluded.name, type = excluded.type, flags = excluded.flags,
              out_path_len = excluded.out_path_len, out_path_hash_mode = excluded.out_path_hash_mode,
              out_path = excluded.out_path, lat = excluded.lat, lon = excluded.lon,
              last_advert = excluded.last_advert, lastmod = excluded.lastmod,
              last_heard = MAX(contacts.last_heard, excluded.last_advert), on_device = 1
            """,
            {**c, "ts": ts},
        )

    def mark_contacts_absent(self, present_keys: set[str]) -> None:
        with self._lock:
            rows = self.conn.execute("SELECT public_key FROM contacts WHERE on_device = 1").fetchall()
            for r in rows:
                if r["public_key"] not in present_keys:
                    self.conn.execute(
                        "UPDATE contacts SET on_device = 0 WHERE public_key = ?", (r["public_key"],)
                    )

    def set_on_device(self, public_key: str, on_device: bool) -> None:
        self._exec("UPDATE contacts SET on_device = ? WHERE public_key = ?",
                   (int(on_device), public_key))

    def touch_contact(self, public_key: str, ts: int | None = None) -> None:
        self._exec(
            "UPDATE contacts SET last_heard = MAX(last_heard, ?) WHERE public_key = ?",
            (ts or now(), public_key),
        )

    def list_contacts(self) -> list[dict]:
        return self._all("SELECT * FROM contacts ORDER BY last_heard DESC, name")

    def get_contact(self, public_key: str) -> dict | None:
        return self._one("SELECT * FROM contacts WHERE public_key = ?", (public_key,))

    def find_contact_by_prefix(self, prefix: str) -> dict | None:
        rows = self.find_contacts_by_prefix(prefix)
        return rows[0] if len(rows) == 1 else None

    def find_contacts_by_prefix(self, prefix: str) -> list[dict]:
        prefix = (prefix or "").lower()
        if not prefix:
            return []
        return self._all(
            "SELECT * FROM contacts WHERE public_key LIKE ? ORDER BY last_heard DESC",
            (prefix + "%",),
        )

    def find_contact_by_name(self, name: str) -> dict | None:
        return self._one(
            "SELECT * FROM contacts WHERE lower(name) = lower(?) ORDER BY last_heard DESC LIMIT 1",
            (name,),
        )

    # --- каналы ---
    def upsert_channel(self, idx: int, name: str, channel_hash: str | None) -> None:
        self._exec(
            "INSERT INTO channels (idx, name, channel_hash, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(idx) DO UPDATE SET name = excluded.name, "
            "channel_hash = excluded.channel_hash, updated_at = excluded.updated_at",
            (idx, name, channel_hash, now()),
        )

    def delete_channels_except(self, keep: set[int]) -> None:
        with self._lock:
            for r in self.conn.execute("SELECT idx FROM channels").fetchall():
                if r["idx"] not in keep:
                    self.conn.execute("DELETE FROM channels WHERE idx = ?", (r["idx"],))

    def list_channels(self) -> list[dict]:
        return self._all(
            """
            SELECT c.idx, c.name, c.channel_hash,
              (SELECT COUNT(*) FROM messages m WHERE m.kind = 'chan' AND m.channel_idx = c.idx
                 AND m.direction = 'in' AND m.read = 0) AS unread,
              (SELECT MAX(ts) FROM messages m WHERE m.kind = 'chan' AND m.channel_idx = c.idx)
                 AS last_ts
            FROM channels c ORDER BY c.idx
            """
        )

    def get_channel(self, idx: int) -> dict | None:
        return self._one("SELECT * FROM channels WHERE idx = ?", (idx,))

    # --- сообщения ---
    _MSG_COLS = (
        "kind", "direction", "channel_idx", "contact_key", "pubkey_prefix", "sender_name", "text",
        "sender_ts", "ts", "snr", "rssi", "path_len", "path", "path_hash_size", "txt_type",
        "status", "expected_ack", "trip_ms", "attempts", "read", "dedup",
    )

    def insert_message(self, msg: dict) -> dict | None:
        """Вставляет сообщение; при дубликате (dedup) возвращает None."""
        row = {k: msg.get(k) for k in self._MSG_COLS}
        if row["ts"] is None:
            row["ts"] = now()
        if row["read"] is None:
            row["read"] = 1 if row["direction"] == "out" else 0
        cols = ", ".join(self._MSG_COLS)
        ph = ", ".join(f":{k}" for k in self._MSG_COLS)
        with self._lock:
            cur = self.conn.execute(f"INSERT OR IGNORE INTO messages ({cols}) VALUES ({ph})", row)
            if cur.rowcount == 0:
                return None
            return self.get_message(cur.lastrowid)

    def get_message(self, msg_id: int) -> dict | None:
        return self._one("SELECT * FROM messages WHERE id = ?", (msg_id,))

    def update_message(self, msg_id: int, **fields: Any) -> dict | None:
        if not fields:
            return self.get_message(msg_id)
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE messages SET {sets} WHERE id = ?", (*fields.values(), msg_id))
        return self.get_message(msg_id)

    def list_channel_messages(self, idx: int, before_id: int | None, limit: int) -> list[dict]:
        sql = "SELECT * FROM messages WHERE kind = 'chan' AND channel_idx = ?"
        params: list[Any] = [idx]
        if before_id:
            sql += " AND id < ?"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return list(reversed(self._all(sql, params)))

    def list_dm_messages(self, contact_key: str, before_id: int | None, limit: int) -> list[dict]:
        sql = "SELECT * FROM messages WHERE kind = 'dm' AND contact_key = ?"
        params: list[Any] = [contact_key]
        if before_id:
            sql += " AND id < ?"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return list(reversed(self._all(sql, params)))

    def list_conversations(self) -> list[dict]:
        """Диалоги ЛС: контакт, последнее сообщение, непрочитанные."""
        return self._all(
            """
            SELECT m.contact_key,
                   MAX(m.ts) AS last_ts,
                   SUM(CASE WHEN m.direction = 'in' AND m.read = 0 THEN 1 ELSE 0 END) AS unread,
                   (SELECT text FROM messages x WHERE x.kind = 'dm' AND x.contact_key = m.contact_key
                      ORDER BY x.id DESC LIMIT 1) AS last_text
            FROM messages m
            WHERE m.kind = 'dm' AND m.contact_key IS NOT NULL
            GROUP BY m.contact_key
            ORDER BY last_ts DESC
            """
        )

    def mark_read(self, kind: str, key: int | str) -> int:
        if kind == "chan":
            cur = self._exec(
                "UPDATE messages SET read = 1 WHERE kind = 'chan' AND channel_idx = ? AND read = 0",
                (key,),
            )
        else:
            cur = self._exec(
                "UPDATE messages SET read = 1 WHERE kind = 'dm' AND contact_key = ? AND read = 0",
                (key,),
            )
        return cur.rowcount

    def unread_summary(self) -> dict:
        rows = self._all(
            "SELECT kind, channel_idx, contact_key, COUNT(*) AS n FROM messages "
            "WHERE direction = 'in' AND read = 0 GROUP BY kind, channel_idx, contact_key"
        )
        chan = {str(r["channel_idx"]): r["n"] for r in rows if r["kind"] == "chan"}
        dm = {r["contact_key"]: r["n"] for r in rows if r["kind"] == "dm" and r["contact_key"]}
        return {"channels": chan, "dms": dm, "total_channels": sum(chan.values()),
                "total_dms": sum(dm.values())}

    def recent_message_paths(self, since: int, limit: int = 500) -> list[dict]:
        return self._all(
            "SELECT id, ts, kind, channel_idx, contact_key, sender_name, path, path_hash_size, "
            "path_len, snr, rssi FROM messages WHERE direction = 'in' AND ts >= ? "
            "AND path IS NOT NULL AND path != '' ORDER BY ts DESC LIMIT ?",
            (since, limit),
        )

    def resolve_dm_prefix(self, prefix: str, full_key: str) -> None:
        self._exec(
            "UPDATE messages SET contact_key = ? WHERE kind = 'dm' AND contact_key IS NULL "
            "AND pubkey_prefix = ?",
            (full_key, prefix),
        )

    # --- метрики ---
    def add_metrics(self, source: str, values: dict[str, float | int | None], ts: int | None = None) -> None:
        ts = ts or now()
        rows = [
            (ts, source, k, float(v))
            for k, v in values.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if rows:
            with self._lock:
                self.conn.executemany(
                    "INSERT INTO metrics (ts, source, name, value) VALUES (?, ?, ?, ?)", rows
                )

    def query_metrics(
        self, source: str, names: list[str], since: int, until: int, bucket: int
    ) -> dict[str, list[list[float]]]:
        bucket = max(1, int(bucket))
        out: dict[str, list[list[float]]] = {}
        for name in names:
            rows = self._all(
                "SELECT (ts / ?) * ? AS t, AVG(value) AS v FROM metrics "
                "WHERE source = ? AND name = ? AND ts BETWEEN ? AND ? GROUP BY t ORDER BY t",
                (bucket, bucket, source, name, since, until),
            )
            out[name] = [[r["t"], round(r["v"], 3)] for r in rows]
        return out

    def latest_metrics(self, source: str) -> dict:
        rows = self._all(
            "SELECT m.name, m.value, m.ts FROM metrics m JOIN (SELECT name, MAX(ts) AS ts FROM metrics "
            "WHERE source = ? GROUP BY name) x ON x.name = m.name AND x.ts = m.ts WHERE m.source = ?",
            (source, source),
        )
        return {"values": {r["name"]: r["value"] for r in rows},
                "ts": max((r["ts"] for r in rows), default=None)}

    def metric_sources(self) -> list[str]:
        return [r["source"] for r in self._all("SELECT DISTINCT source FROM metrics")]

    def messages_per_bucket(self, since: int, until: int, bucket: int) -> list[dict]:
        return self._all(
            "SELECT (ts / ?) * ? AS t, kind, direction, COUNT(*) AS n FROM messages "
            "WHERE ts BETWEEN ? AND ? GROUP BY t, kind, direction ORDER BY t",
            (bucket, bucket, since, until),
        )

    def signal_series(self, since: int, until: int, bucket: int) -> list[dict]:
        """SNR/RSSI принятых сообщений, усреднённые по корзинам."""
        return self._all(
            "SELECT (ts / ?) * ? AS t, AVG(snr) AS snr, AVG(rssi) AS rssi, COUNT(*) AS n "
            "FROM messages WHERE direction = 'in' AND ts BETWEEN ? AND ? "
            "AND (snr IS NOT NULL OR rssi IS NOT NULL) GROUP BY t ORDER BY t",
            (bucket, bucket, since, until),
        )

    # --- активность ---
    def add_activity(self, contact_key: str, kind: str, ts: int | None = None) -> None:
        self._exec(
            "INSERT INTO activity (ts, contact_key, kind) VALUES (?, ?, ?)",
            (ts or now(), contact_key, kind),
        )

    def contact_activity(self, since: int, until: int, limit: int = 20) -> list[dict]:
        return self._all(
            """
            SELECT a.contact_key, COALESCE(c.name, substr(a.contact_key, 1, 12)) AS name,
                   SUM(CASE WHEN a.kind = 'advert' THEN 1 ELSE 0 END) AS adverts,
                   SUM(CASE WHEN a.kind = 'msg' THEN 1 ELSE 0 END) AS messages,
                   COUNT(*) AS total, MAX(a.ts) AS last_ts
            FROM activity a LEFT JOIN contacts c ON c.public_key = a.contact_key
            WHERE a.ts BETWEEN ? AND ?
            GROUP BY a.contact_key ORDER BY total DESC LIMIT ?
            """,
            (since, until, limit),
        )

    def activity_per_bucket(self, since: int, until: int, bucket: int) -> list[dict]:
        return self._all(
            "SELECT (ts / ?) * ? AS t, COUNT(DISTINCT contact_key) AS contacts, COUNT(*) AS events "
            "FROM activity WHERE ts BETWEEN ? AND ? GROUP BY t ORDER BY t",
            (bucket, bucket, since, until),
        )

    # --- репитеры ---
    def add_repeater_poll(self, contact_key: str, login_ok: bool | None, status: Any,
                          telemetry: Any, neighbours: Any, error: str | None) -> dict:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO repeater_polls (ts, contact_key, login_ok, status_json, telemetry_json, "
                "neighbours_json, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    now(), contact_key, None if login_ok is None else int(login_ok),
                    json.dumps(status) if status is not None else None,
                    json.dumps(telemetry) if telemetry is not None else None,
                    json.dumps(neighbours) if neighbours is not None else None,
                    error,
                ),
            )
            return self._decode_poll(dict(self.conn.execute(
                "SELECT * FROM repeater_polls WHERE id = ?", (cur.lastrowid,)).fetchone()))

    @staticmethod
    def _decode_poll(row: dict | None) -> dict | None:
        if row is None:
            return None
        for k in ("status_json", "telemetry_json", "neighbours_json"):
            v = row.pop(k)
            row[k.removesuffix("_json")] = json.loads(v) if v else None
        return row

    def last_repeater_poll(self, contact_key: str, successful: bool = False) -> dict | None:
        sql = "SELECT * FROM repeater_polls WHERE contact_key = ?"
        if successful:
            sql += " AND status_json IS NOT NULL"
        sql += " ORDER BY id DESC LIMIT 1"
        return self._decode_poll(self._one(sql, (contact_key,)))

    def last_neighbours(self, contact_key: str) -> dict | None:
        return self._decode_poll(self._one(
            "SELECT * FROM repeater_polls WHERE contact_key = ? AND neighbours_json IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (contact_key,)))

    def repeater_poll_history(self, contact_key: str, limit: int = 20) -> list[dict]:
        rows = self._all(
            "SELECT * FROM repeater_polls WHERE contact_key = ? ORDER BY id DESC LIMIT ?",
            (contact_key, limit),
        )
        return [self._decode_poll(r) for r in rows]

    # --- трассировки ---
    def add_trace(self, target_key: str | None, tag: int | None, path_sent: str | None,
                  status: str, hops: Any = None, source: str = "trace") -> dict:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO traces (ts, target_key, tag, path_sent, status, hops_json, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now(), target_key, tag, path_sent, status,
                 json.dumps(hops) if hops is not None else None, source),
            )
            return self.get_trace(cur.lastrowid)

    def update_trace(self, trace_id: int, status: str, hops: Any = None) -> dict | None:
        self._exec(
            "UPDATE traces SET status = ?, hops_json = ? WHERE id = ?",
            (status, json.dumps(hops) if hops is not None else None, trace_id),
        )
        return self.get_trace(trace_id)

    def get_trace(self, trace_id: int) -> dict | None:
        row = self._one("SELECT * FROM traces WHERE id = ?", (trace_id,))
        if row:
            v = row.pop("hops_json")
            row["hops"] = json.loads(v) if v else None
        return row

    def list_traces(self, since: int, limit: int = 100) -> list[dict]:
        rows = self._all(
            "SELECT * FROM traces WHERE ts >= ? ORDER BY id DESC LIMIT ?", (since, limit)
        )
        for row in rows:
            v = row.pop("hops_json")
            row["hops"] = json.loads(v) if v else None
        return rows

    # --- ретеншн ---
    def cleanup(self, messages_days: int, metrics_days: int) -> dict[str, int]:
        t = now()
        m_cut = t - messages_days * 86400
        x_cut = t - metrics_days * 86400
        res = {}
        with self._lock:
            res["messages"] = self.conn.execute("DELETE FROM messages WHERE ts < ?", (m_cut,)).rowcount
            res["traces"] = self.conn.execute("DELETE FROM traces WHERE ts < ?", (m_cut,)).rowcount
            res["metrics"] = self.conn.execute("DELETE FROM metrics WHERE ts < ?", (x_cut,)).rowcount
            res["activity"] = self.conn.execute("DELETE FROM activity WHERE ts < ?", (x_cut,)).rowcount
            res["repeater_polls"] = self.conn.execute(
                "DELETE FROM repeater_polls WHERE ts < ?", (x_cut,)).rowcount
        return res
