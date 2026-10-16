"""Persistent store for the WARRANT workspace (SQLite, stdlib only).

Tables
  users       identity (Google sub or the demo profile), name, email, picture, verified phone
  sessions    one workspace per row: the pickled worlds + agent memory of both lanes (server-written only)
  tasks       every task: prompt, status, timestamp and the full event stream (JSON)
  approvals   paused actions and the user's decision (fingerprint of the exact call)
  memory      values the user confirmed, with where they came from (provenance) and who confirmed them
  audit_log   append-only, hash-chained record of security decisions; UPDATE/DELETE are blocked by triggers

Security notes
  * Every query is parameterised (no SQL built from strings).
  * Rows are scoped by user_id; callers always pass the user they authenticated.
  * audit_log: each row stores sha256(prev_hash + canonical JSON of the row). verify_audit() recomputes the
    chain, so any edit made outside this module (e.g. with a SQLite browser after disabling triggers) shows up.
  * The agent has no tool that writes here. Only the server's approval path writes memory rows.
  * Session blobs are pickled by this server only and never accepted from clients.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from warrant import config

DB_PATH = config.ROOT / "data" / "warrant.db"
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_path: Path | None = None

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, google_sub TEXT UNIQUE, name TEXT NOT NULL, email TEXT NOT NULL,
  picture TEXT DEFAULT '', phone TEXT DEFAULT '', phone_verified INTEGER DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  sid TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), blob BLOB NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id, updated);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT NOT NULL, user_id TEXT NOT NULL, seq INTEGER NOT NULL,
  prompt TEXT NOT NULL, ts REAL NOT NULL, record TEXT NOT NULL, UNIQUE(sid, seq));
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT NOT NULL, user_id TEXT NOT NULL, call_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, decided REAL,
  method TEXT DEFAULT '', UNIQUE(sid, call_id));
CREATE TABLE IF NOT EXISTS memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, value_key TEXT NOT NULL, value TEXT NOT NULL,
  origin TEXT NOT NULL, confirmed_by TEXT NOT NULL, ts REAL NOT NULL, UNIQUE(user_id, value_key));
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, user_id TEXT NOT NULL, sid TEXT NOT NULL,
  kind TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""

DEMO_USER = "demo"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (and migrate) the store. Tests pass a temporary path."""
    global _conn, _path
    with _lock:
        path = Path(path or DB_PATH)
        if _conn is not None and _path == path:
            return _conn
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        for col in ("otp_id TEXT DEFAULT ''", "otp_sends INTEGER DEFAULT 0", "otp_last REAL DEFAULT 0"):
            try:
                _conn.execute(f"ALTER TABLE approvals ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # already migrated
        _path = path
        return _conn


def db() -> sqlite3.Connection:
    return _conn or connect()


# ---------------------------------------------------------------- users
def upsert_user(user_id: str, name: str, email: str, google_sub: str | None = None, picture: str = "") -> dict:
    with _lock:
        db().execute(
            "INSERT INTO users(id, google_sub, name, email, picture, created) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, email=excluded.email, picture=excluded.picture, "
            "google_sub=COALESCE(excluded.google_sub, users.google_sub)",
            (user_id, google_sub, name, email, picture, time.time()))
    return get_user(user_id)


def get_user(user_id: str) -> dict | None:
    row = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def demo_user() -> dict:
    name = os.environ.get("WARRANT_DEMO_NAME", "Demo user")
    return get_user(DEMO_USER) or upsert_user(DEMO_USER, name, "demo@warrant.local")


def set_phone(user_id: str, phone: str, verified: bool) -> None:
    with _lock:
        db().execute("UPDATE users SET phone=?, phone_verified=? WHERE id=?", (phone, int(verified), user_id))


# ---------------------------------------------------------------- sessions
def save_session(sid: str, user_id: str, blob: dict) -> None:
    if user_id == DEMO_USER:
        demo_user()  # make sure the demo profile row exists (foreign key)
    data = pickle.dumps(blob)
    with _lock:
        db().execute("INSERT INTO sessions(sid, user_id, blob, updated) VALUES (?,?,?,?) "
                     "ON CONFLICT(sid) DO UPDATE SET blob=excluded.blob, updated=excluded.updated "
                     "WHERE sessions.user_id=excluded.user_id", (sid, user_id, data, time.time()))


def load_session(sid: str, user_id: str | None = None) -> tuple[str, dict] | None:
    q, args = ("SELECT user_id, blob FROM sessions WHERE sid=?", (sid,)) if user_id is None else \
              ("SELECT user_id, blob FROM sessions WHERE sid=? AND user_id=?", (sid, user_id))
    row = db().execute(q, args).fetchone()
    if not row:
        return None
    try:
        return row["user_id"], pickle.loads(row["blob"])
    except Exception:
        return None


def latest_session(user_id: str) -> str | None:
    row = db().execute("SELECT sid FROM sessions WHERE user_id=? ORDER BY updated DESC LIMIT 1", (user_id,)).fetchone()
    return row["sid"] if row else None


def delete_tasks(sid: str, user_id: str) -> None:
    with _lock:
        db().execute("DELETE FROM tasks WHERE sid=? AND user_id=?", (sid, user_id))
        db().execute("DELETE FROM approvals WHERE sid=? AND user_id=?", (sid, user_id))


# ---------------------------------------------------------------- tasks
def save_task(sid: str, user_id: str, seq: int, record: dict) -> None:
    with _lock:
        db().execute("INSERT INTO tasks(sid, user_id, seq, prompt, ts, record) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(sid, seq) DO UPDATE SET record=excluded.record",
                     (sid, user_id, seq, record.get("prompt", ""), record.get("ts", time.time()), json.dumps(record, default=str)))


def tasks(sid: str, user_id: str) -> list[dict]:
    rows = db().execute("SELECT record FROM tasks WHERE sid=? AND user_id=? ORDER BY seq", (sid, user_id)).fetchall()
    return [json.loads(r["record"]) for r in rows]


# ---------------------------------------------------------------- approvals
def fingerprint(call: dict) -> str:
    """Hash of the exact action (function + arguments). A later approval may only release this exact call."""
    body = json.dumps({"function": call.get("function"), "args": call.get("args")}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def add_approval(sid: str, user_id: str, call_id: str, call: dict, summary: str) -> str:
    fp = fingerprint(call)
    with _lock:
        db().execute("INSERT OR IGNORE INTO approvals(sid, user_id, call_id, fingerprint, summary, status, created) "
                     "VALUES (?,?,?,?,?, 'pending', ?)", (sid, user_id, call_id, fp, summary, time.time()))
    return fp


def decide_approval(sid: str, user_id: str, call_id: str, status: str, method: str) -> None:
    with _lock:
        db().execute("UPDATE approvals SET status=?, decided=?, method=? WHERE sid=? AND user_id=? AND call_id=? AND status='pending'",
                     (status, time.time(), method, sid, user_id, call_id))


def approval(sid: str, user_id: str, call_id: str) -> dict | None:
    row = db().execute("SELECT * FROM approvals WHERE sid=? AND user_id=? AND call_id=?", (sid, user_id, call_id)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- memory
def remember(user_id: str, value_key: str, value: str, origin: str, confirmed_by: str) -> None:
    with _lock:
        db().execute("INSERT INTO memory(user_id, value_key, value, origin, confirmed_by, ts) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(user_id, value_key) DO UPDATE SET origin=excluded.origin, confirmed_by=excluded.confirmed_by, ts=excluded.ts",
                     (user_id, value_key, value, origin, confirmed_by, time.time()))


def memories(user_id: str) -> list[dict]:
    return [dict(r) for r in db().execute("SELECT value_key, value, origin, confirmed_by, ts FROM memory WHERE user_id=? ORDER BY ts", (user_id,))]


# ---------------------------------------------------------------- audit log (append-only, hash-chained)
def _row_hash(prev: str, ts: float, user_id: str, sid: str, kind: str, payload: str) -> str:
    body = json.dumps([prev, round(ts, 6), user_id, sid, kind, payload], separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def audit(user_id: str, sid: str, kind: str, payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, default=str)
    with _lock:
        last = db().execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else "0" * 64
        ts = round(time.time(), 6)
        h = _row_hash(prev, ts, user_id, sid, kind, text)
        db().execute("INSERT INTO audit_log(ts, user_id, sid, kind, payload, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                     (ts, user_id, sid, kind, text, prev, h))
    return h


def verify_audit() -> dict:
    """Recompute the hash chain. Returns {ok, rows, broken_at}."""
    prev, n = "0" * 64, 0
    for r in db().execute("SELECT * FROM audit_log ORDER BY id"):
        n += 1
        if r["prev_hash"] != prev or _row_hash(prev, r["ts"], r["user_id"], r["sid"], r["kind"], r["payload"]) != r["hash"]:
            return {"ok": False, "rows": n, "broken_at": r["id"]}
        prev = r["hash"]
    return {"ok": True, "rows": n, "broken_at": None}


def audit_rows(user_id: str, sid: str | None = None, limit: int = 200) -> list[dict]:
    q, args = ("SELECT id, ts, kind, payload, hash FROM audit_log WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)) if sid is None else \
              ("SELECT id, ts, kind, payload, hash FROM audit_log WHERE user_id=? AND sid=? ORDER BY id DESC LIMIT ?", (user_id, sid, limit))
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in db().execute(q, args)]


def approval_otp_sent(sid: str, user_id: str, call_id: str, otp_id: str) -> None:
    with _lock:
        db().execute("UPDATE approvals SET otp_id=?, otp_sends=otp_sends+1, otp_last=? WHERE sid=? AND user_id=? AND call_id=?",
                     (otp_id, time.time(), sid, user_id, call_id))
