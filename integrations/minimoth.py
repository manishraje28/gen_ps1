"""MiniMoth OTP (WhatsApp first, SMS fallback) for WARRANT approvals and phone verification.

API (https://minimoth.dev/docs): base https://api.minimoth.dev, header X-Api-Key.
  POST /v1/otp/send    {"phone": "+91..."}                 -> {"otp_id", "expires_at"}
  POST /v1/otp/verify  {"phone": "+91...", "code": "123456"} -> {"access_token", "refresh_token", "expires_at", ...}
Codes expire after 10 minutes; a live key allows 5 verify attempts per code and 3 sends per number per 10 minutes.

Set MINIMOTH_API_KEY in .env (mm_test_ keys send nothing and cost nothing; mm_live_ keys send real messages).
The code itself never reaches the agent or the audit log: it goes to the user's phone and back to this server.
"""
from __future__ import annotations

import os
import re

import httpx

BASE_URL = os.environ.get("MINIMOTH_BASE_URL", "https://api.minimoth.dev")
_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_CODE_RE = re.compile(r"^\d{6}$")


class MinimothError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.status = code, status


def api_key() -> str:
    return os.environ.get("MINIMOTH_API_KEY", "")


def available() -> bool:
    return bool(api_key())


def is_test_key() -> bool:
    return api_key().startswith("mm_test_")


def normalize_phone(phone: str) -> str:
    p = re.sub(r"[\s\-()]", "", phone or "")
    if re.fullmatch(r"\d{10}", p):  # bare Indian mobile number
        p = "+91" + p
    if not _PHONE_RE.match(p):
        raise MinimothError("INVALID_PHONE", "Enter the number with its country code, e.g. +919876543210", 422)
    return p


def mask(phone: str) -> str:
    return phone[:3] + "•" * max(len(phone) - 7, 0) + phone[-4:]


def _post(path: str, body: dict) -> dict:
    if not available():
        raise MinimothError("NOT_CONFIGURED", "MiniMoth is not configured (MINIMOTH_API_KEY)", 503)
    try:
        r = httpx.post(f"{BASE_URL}{path}", json=body, headers={"X-Api-Key": api_key()}, timeout=20)
    except httpx.HTTPError as exc:
        raise MinimothError("NETWORK", f"MiniMoth unreachable: {exc}", 502) from exc
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400:
        err = data.get("error", data) if isinstance(data, dict) else {}
        code = err.get("code") if isinstance(err, dict) else None
        msg = err.get("message") if isinstance(err, dict) else None
        raise MinimothError(str(code or f"HTTP_{r.status_code}"), str(msg or r.text[:200]), r.status_code)
    return data


def send(phone: str) -> dict:
    """Send a code to ``phone``. Returns {"otp_id", "expires_at"}."""
    return _post("/v1/otp/send", {"phone": normalize_phone(phone)})


def verify(phone: str, code: str) -> bool:
    """True if ``code`` is the current code for ``phone``. Wrong/expired codes raise MinimothError."""
    code = (code or "").strip()
    if not _CODE_RE.match(code):
        raise MinimothError("INVALID_OTP_CODE", "The code is 6 digits", 422)
    data = _post("/v1/otp/verify", {"phone": normalize_phone(phone), "code": code})
    return bool(data.get("access_token"))
