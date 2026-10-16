"""MiniMoth OTP approvals with a fake MiniMoth (no real messages, no credits)."""
import pytest
from fastapi import HTTPException

import web.playground as pg
from integrations import minimoth
from web import store


class FakeMiniMoth:
    def __init__(self):
        self.sent, self.code = [], "123456"

    def __call__(self, path, body):
        if path == "/v1/otp/send":
            self.sent.append(body["phone"])
            return {"otp_id": f"otp-{len(self.sent)}", "expires_at": "soon"}
        if path == "/v1/otp/verify":
            if body["code"] != self.code:
                raise minimoth.MinimothError("INVALID_OTP", "Incorrect code", 400)
            return {"access_token": "a", "refresh_token": "r", "expires_at": "x"}
        raise AssertionError(path)


@pytest.fixture
def mm(monkeypatch):
    fake = FakeMiniMoth()
    monkeypatch.setenv("MINIMOTH_API_KEY", "mm_test_fake")
    monkeypatch.setattr(minimoth, "_post", fake)
    monkeypatch.setattr(pg, "OTP_MIN_GAP", 0.0)
    pg.PHONE_PENDING.clear()
    return fake


def _paused(user, amount=2360):
    s = pg.Session(user["id"])
    pg.SESSIONS[s.id] = s
    call = {"function": "send_money", "args": {"recipient_iban": "IN58BRNT0000664120", "amount": amount, "subject": "x"}}
    store.add_approval(s.id, user["id"], "c1", call, "pay")
    s.pending["c1"] = {**call, "values": ["IN58BRNT0000664120"], "code": "NEW_PAYEE"}
    return s


def _verified_user(mm):
    user = store.demo_user()
    pg.phone_send(pg.PhoneSend(phone="9876543210"), user=user)
    pg.phone_verify(pg.PhoneVerify(code="123456"), user=user)
    return store.get_user(user["id"])


def test_phone_normalisation_and_mask():
    assert minimoth.normalize_phone("98765 43210") == "+919876543210"
    assert minimoth.mask("+919876543210") == "+91••••••3210"
    with pytest.raises(minimoth.MinimothError):
        minimoth.normalize_phone("12345")


def test_phone_verification_flow(mm):
    user = _verified_user(mm)
    assert user["phone"] == "+919876543210" and user["phone_verified"] == 1
    assert pg.otp_required(user)


def test_wrong_phone_code_does_not_verify(mm):
    user = store.demo_user()
    pg.phone_send(pg.PhoneSend(phone="+919876543210"), user=user)
    with pytest.raises(HTTPException):
        pg.phone_verify(pg.PhoneVerify(code="000000"), user=user)
    assert not store.get_user(user["id"])["phone_verified"]


def test_button_approval_is_refused_once_otp_is_on(mm):
    user = _verified_user(mm)
    s = _paused(user)
    with pytest.raises(HTTPException) as e:
        pg.approve(pg.Approval(sid=s.id, id="c1", approve=True), user=user)
    assert e.value.status_code == 403
    assert "c1" in s.pending  # still waiting


def test_otp_approval_releases_exactly_that_action(mm):
    user = _verified_user(mm)
    s = _paused(user)
    r = pg.approve_otp(pg.ApprovalOtp(sid=s.id, id="c1"), user=user)
    assert r["sent"] and r["to"].endswith("3210")
    with pytest.raises(HTTPException):
        pg.approve(pg.Approval(sid=s.id, id="c1", approve=True, code="999999"), user=user)
    ok = pg.approve(pg.Approval(sid=s.id, id="c1", approve=True, code="123456"), user=user)
    assert ok["approved"] and ok["ok"]
    assert store.approval(s.id, user["id"], "c1")["method"] == "otp"
    kinds = [r["kind"] for r in store.audit_rows(user["id"], s.id)]
    assert "otp_sent" in kinds and "otp_failed" in kinds and "otp_verified" in kinds
    assert not any("123456" in str(r["payload"]) for r in store.audit_rows(user["id"]))  # codes never logged


def test_otp_sends_are_capped_per_action(mm):
    user = _verified_user(mm)
    s = _paused(user)
    for _ in range(pg.OTP_MAX_SENDS):
        pg.approve_otp(pg.ApprovalOtp(sid=s.id, id="c1"), user=user)
    with pytest.raises(HTTPException) as e:
        pg.approve_otp(pg.ApprovalOtp(sid=s.id, id="c1"), user=user)
    assert e.value.status_code == 429


def test_reject_needs_no_code(mm):
    user = _verified_user(mm)
    s = _paused(user)
    assert pg.approve(pg.Approval(sid=s.id, id="c1", approve=False), user=user)["approved"] is False
