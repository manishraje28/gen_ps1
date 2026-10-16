"""Workspace store: append-only hash-chained audit log, per-user isolation, exact-action approvals, memory provenance."""
import pytest
from fastapi import HTTPException

import web.playground as pg
from web import store


def test_audit_chain_verifies_and_is_append_only():
    for i in range(4):
        store.audit("demo", "s1", "gate", {"i": i})
    assert store.verify_audit() == {"ok": True, "rows": 4, "broken_at": None}
    with pytest.raises(Exception):
        store.db().execute("UPDATE audit_log SET kind='x' WHERE id=2")
    with pytest.raises(Exception):
        store.db().execute("DELETE FROM audit_log WHERE id=2")


def test_audit_tampering_outside_the_app_is_detected():
    for i in range(3):
        store.audit("demo", "s1", "gate", {"i": i})
    db = store.db()
    db.execute("DROP TRIGGER audit_no_update")          # an attacker with file access
    db.execute("UPDATE audit_log SET payload='{\"i\": 99}' WHERE id=2")
    assert store.verify_audit()["broken_at"] == 2


def test_sessions_are_isolated_per_user():
    alice = store.upsert_user("u-alice", "Alice", "alice@example.com", google_sub="g1")
    bob = store.upsert_user("u-bob", "Bob", "bob@example.com", google_sub="g2")
    s = pg.Session(alice["id"])
    pg.save_session(s)
    pg.SESSIONS.clear()
    assert pg.load_session(s.id, bob["id"]) is None
    with pytest.raises(HTTPException):
        pg.get_session(s.id, bob)
    assert pg.get_session(s.id, alice).user_id == alice["id"]


def test_tasks_persist_and_reload():
    s = pg.Session()
    s.history.append({"prompt": "hello", "events": [], "ts": 1.0})
    pg.save_task(s, 0)
    pg.save_session(s)
    pg.SESSIONS.clear()
    r = pg.load_session(s.id)
    assert [t["prompt"] for t in r.history] == ["hello"]


def test_approval_only_releases_the_exact_paused_action():
    user = store.demo_user()
    s = pg.Session(user["id"])
    pg.SESSIONS[s.id] = s
    call = {"function": "send_money", "args": {"recipient_iban": "IN58BRNT0000664120", "amount": 2360, "subject": "x"}}
    store.add_approval(s.id, user["id"], "c1", call, "pay")
    # the pending call is swapped for a different amount before approval
    s.pending["c1"] = {**call, "args": {**call["args"], "amount": 99999}, "values": [], "code": "NEW_PAYEE"}
    with pytest.raises(HTTPException) as e:
        pg.approve(pg.Approval(sid=s.id, id="c1", approve=True), user=user)
    assert e.value.status_code == 409


def test_approved_value_is_remembered_with_provenance():
    user = store.demo_user()
    s = pg.Session(user["id"])
    pg.SESSIONS[s.id] = s
    call = {"function": "send_money", "args": {"recipient_iban": "IN58BRNT0000664120", "amount": 10, "subject": "x"}}
    store.add_approval(s.id, user["id"], "c2", call, "pay")
    s.pending["c2"] = {**call, "values": ["IN58BRNT0000664120"], "code": "NEW_PAYEE"}
    r = pg.approve(pg.Approval(sid=s.id, id="c2", approve=True), user=user)
    assert r["approved"] and r["ok"]
    mem = store.memories(user["id"])
    assert mem and mem[0]["value"] == "IN58BRNT0000664120" and mem[0]["origin"] == "NEW_PAYEE" and mem[0]["confirmed_by"] == "user"
    assert store.approval(s.id, user["id"], "c2")["status"] == "approved"
    assert any(row["kind"] == "approval" for row in store.audit_rows(user["id"], s.id))
