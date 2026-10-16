"""Sign in with Google: signed session cookie, allow-list, state check, and the demo fallback."""
import pytest
from fastapi.testclient import TestClient

from web import auth, store
from web.server import app


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("WARRANT_SECRET", "test-secret")


def test_cookie_round_trip_and_tamper(secret):
    tok = auth.sign("g-123")
    assert auth.unsign(tok) == "g-123"
    body, mac = tok.rsplit(".", 1)
    assert auth.unsign(body + "." + ("0" * len(mac))) is None
    forged = auth.sign("g-123").replace(auth.sign("g-123").split(".")[0], "eyJ1IjogImctYWRtaW4iLCAiZXhwIjogOTk5OTk5OTk5OX0")
    assert auth.unsign(forged) is None


def test_expired_cookie_is_rejected(secret):
    assert auth.unsign(auth.sign("g-1", max_age=-10)) is None


def test_allow_list(monkeypatch):
    monkeypatch.setenv("WARRANT_ALLOWED_EMAILS", "a@x.com,@team.in")
    assert auth.allowed("a@x.com") and auth.allowed("b@team.in")
    assert not auth.allowed("c@evil.test")
    monkeypatch.delenv("WARRANT_ALLOWED_EMAILS")
    assert auth.allowed("anyone@example.com")


def test_signed_in_user_is_attached_and_demo_otherwise(secret):
    c = TestClient(app)
    assert c.get("/api/play/me").json()["demo"] is True
    store.upsert_user("g-42", "Shubhan Sawant", "s@example.com", google_sub="42")
    c.cookies.set(auth.COOKIE, auth.sign("g-42"))
    me = c.get("/api/play/me").json()
    assert me["demo"] is False and me["user"]["name"] == "Shubhan Sawant"


def test_login_needs_configuration(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    r = TestClient(app).get("/auth/google/login", follow_redirects=False)
    assert r.status_code == 503


def test_login_redirects_with_state(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    r = TestClient(app).get("/auth/google/login", follow_redirects=False)
    assert r.status_code == 302 and "accounts.google.com" in r.headers["location"] and "state=" in r.headers["location"]
    assert auth.STATE_COOKIE in r.headers.get("set-cookie", "")


def test_callback_rejects_mismatched_state(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    c = TestClient(app)
    c.cookies.set(auth.STATE_COOKIE, "expected")
    r = c.get("/auth/google/callback?code=abc&state=attacker", follow_redirects=False)
    assert r.status_code == 400


def test_other_users_session_is_hidden(secret):
    c = TestClient(app)
    sid = c.post("/api/play/session").json()["sid"]          # demo user's session
    store.upsert_user("g-7", "Eve", "eve@example.com", google_sub="7")
    c.cookies.set(auth.COOKIE, auth.sign("g-7"))
    assert c.get(f"/api/play/world?sid={sid}").status_code == 404
