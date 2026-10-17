"""Live Gmail wiring in the workspace: only the protected lane may really send; the real contact is added;
fake emails cannot be planted while the agent reads the real inbox."""
import pytest
from fastapi import HTTPException

import web.playground as pg
from integrations import gmail


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(gmail, "available", lambda: True)
    yield
    gmail._live_ids.clear()


def test_only_the_protected_lane_is_live(live):
    s = pg.Session()
    assert gmail.is_live(s.lanes["warrant"].env.inbox)
    assert not gmail.is_live(s.lanes["undefended"].env.inbox)


def test_real_contact_added_when_live(live):
    emails = {c.email for c in pg.full_world().contacts}
    assert "sawantshubhan912@gmail.com" in emails


def test_no_real_contact_when_simulated(monkeypatch):
    monkeypatch.setattr(gmail, "available", lambda: False)
    assert "sawantshubhan912@gmail.com" not in {c.email for c in pg.full_world().contacts}


def test_planting_an_email_is_refused_when_live(live):
    s = pg.Session()
    pg.SESSIONS[s.id] = s
    with pytest.raises(HTTPException) as e:
        pg.add(pg.AddItem(sid=s.id, kind="email", sender="x@example.test", subject="hi", content="hello"), user=pg.store.demo_user())
    assert e.value.status_code == 400


def test_restored_session_keeps_undefended_lane_offline(live):
    s = pg.Session(pg.store.demo_user()["id"])
    pg.save_session(s)
    gmail._live_ids.clear()
    r = pg.load_session(s.id)
    assert gmail.is_live(r.lanes["warrant"].env.inbox)
    assert not gmail.is_live(r.lanes["undefended"].env.inbox)
