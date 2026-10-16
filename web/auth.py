"""Sign in with Google for the WARRANT workspace.

Flow (OAuth 2.0 authorization code + OpenID Connect):
  GET /auth/google/login     -> redirect to Google with a random `state` (also stored in a short-lived cookie)
  GET /auth/google/callback  -> check `state`, exchange the code, verify the ID token with google-auth
                                (signature, audience = our client id, issuer, expiry, email_verified), then
                                create/update the user and set a signed session cookie
  GET /auth/logout           -> clear the cookie (back to the demo profile)
  GET /auth/status           -> whether sign-in is configured and who is signed in

The session cookie holds only the user id and an expiry, signed with HMAC-SHA256 using WARRANT_SECRET (or a
random key generated once into data/secret.key). It is HttpOnly and SameSite=Lax; set WARRANT_COOKIE_SECURE=1
behind HTTPS. Optional WARRANT_ALLOWED_EMAILS (comma-separated emails or @domains) restricts who may sign in.

Configure a *Web application* OAuth client in Google Cloud (not the Gmail desktop client) with the redirect URI
http://localhost:8000/auth/google/callback and put in .env:
  GOOGLE_CLIENT_ID=...   GOOGLE_CLIENT_SECRET=...   (optional) GOOGLE_REDIRECT_URI=...
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from warrant import config
from web import store

router = APIRouter(prefix="/auth")
COOKIE = "warrant_session"
STATE_COOKIE = "warrant_oauth_state"
MAX_AGE = 14 * 24 * 3600
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


# ---------------------------------------------------------------- config
def client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "")


def client_secret() -> str:
    return os.environ.get("GOOGLE_CLIENT_SECRET", "")


def configured() -> bool:
    return bool(client_id() and client_secret())


def redirect_uri(request: Request) -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI") or str(request.url_for("google_callback"))


def _secret() -> bytes:
    env = os.environ.get("WARRANT_SECRET", "")
    if env:
        return env.encode()
    path = config.ROOT / "data" / "secret.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip().encode()


def allowed(email: str) -> bool:
    rules = [r.strip().lower() for r in os.environ.get("WARRANT_ALLOWED_EMAILS", "").split(",") if r.strip()]
    if not rules:
        return True
    e = email.lower()
    return any(e == r or (r.startswith("@") and e.endswith(r)) for r in rules)


# ---------------------------------------------------------------- signed cookie
def sign(user_id: str, max_age: int = MAX_AGE) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": user_id, "exp": int(time.time()) + max_age}).encode()).decode().rstrip("=")
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def unsign(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, mac = token.rsplit(".", 1)
    if not hmac.compare_digest(mac, hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return str(data.get("u") or "") or None


def _cookie_args() -> dict:
    return {"httponly": True, "samesite": "lax", "secure": os.environ.get("WARRANT_COOKIE_SECURE") == "1", "path": "/"}


async def attach_user(request: Request, call_next):
    """Middleware: put the signed-in user (if any) on request.state.user."""
    uid = unsign(request.cookies.get(COOKIE))
    request.state.user = store.get_user(uid) if uid else None
    return await call_next(request)


# ---------------------------------------------------------------- routes
@router.get("/status")
def status(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    return {"configured": configured(), "signed_in": bool(user),
            "user": {k: user.get(k) for k in ("name", "email", "picture")} if user else None}


@router.get("/google/login")
def google_login(request: Request):
    if not configured():
        raise HTTPException(503, "Google sign-in is not configured: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env")
    state = secrets.token_urlsafe(24)
    params = {"client_id": client_id(), "redirect_uri": redirect_uri(request), "response_type": "code",
              "scope": "openid email profile", "state": state, "prompt": "select_account", "access_type": "online"}
    resp = RedirectResponse(f"{AUTH_URL}?{urllib.parse.urlencode(params)}", status_code=302)
    resp.set_cookie(STATE_COOKIE, state, max_age=600, **_cookie_args())
    return resp


def verify_id_token(token: str) -> dict:
    """Verify signature, audience, issuer and expiry with Google's public keys."""
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token

    info = id_token.verify_oauth2_token(token, grequests.Request(), client_id())
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("wrong issuer")
    if not info.get("email_verified"):
        raise ValueError("email not verified")
    return info


@router.get("/google/callback", name="google_callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        raise HTTPException(400, f"Google sign-in was cancelled: {error}")
    expected = request.cookies.get(STATE_COOKIE)
    if not code or not state or not expected or not hmac.compare_digest(state, expected):
        raise HTTPException(400, "sign-in state did not match; please try again")
    r = httpx.post(TOKEN_URL, data={"code": code, "client_id": client_id(), "client_secret": client_secret(),
                                    "redirect_uri": redirect_uri(request), "grant_type": "authorization_code"}, timeout=20)
    if r.status_code != 200:
        raise HTTPException(400, "could not complete Google sign-in")
    try:
        info = verify_id_token(r.json().get("id_token", ""))
    except Exception as exc:
        raise HTTPException(400, f"invalid Google identity: {exc}") from exc
    email = info["email"]
    if not allowed(email):
        raise HTTPException(403, "this Google account is not allowed to use this workspace")
    user = store.upsert_user(f"g-{info['sub']}", info.get("name") or email.split("@")[0], email,
                             google_sub=info["sub"], picture=info.get("picture", ""))
    store.audit(user["id"], "-", "sign_in", {"email": email})
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(COOKIE, sign(user["id"]), max_age=MAX_AGE, **_cookie_args())
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(COOKIE, path="/")
    return resp
