"""Real Google Drive tools for the live demo (dedicated demo account only).

Same names and signatures as demo/env.py (list_files, read_file, share_file, delete_file), same
"preview then commit" model as integrations/gmail.py:
  * share_file / delete_file always stage the change in ``env.drive`` first (what the gate's shadow
    run diffs); the API is called only when that Drive object is the one bound with :func:`attach`,
    so a shadow deep copy never shares or deletes anything.
  * delete_file moves the file to Drive's trash (recoverable), never a permanent delete.
  * share targets outside GMAIL_ALLOWED_DOMAINS are refused right before the API call.

Uses the Gmail token (``python -m integrations.gmail --consent`` asks for the drive scope too).
Without that scope in the token, :func:`available` is False and the simulated drive is used.
"""
from __future__ import annotations

import threading
import time

from typing import Annotated, Any

from agentdojo.functions_runtime import Depends

from demo import env as sim
from demo.env import Drive, File, Share
from integrations import gmail

FOLDER = "/drive"
MAX_CHARS = 20000
_service: Any = None
_local = threading.local()
_live_ids: set[int] = set()


def set_service(service: Any) -> None:
    global _service
    _service = service


def available() -> bool:
    return _service is not None or (gmail.credentials_file().exists() and gmail.DRIVE_SCOPE in gmail.granted_scopes())


def service() -> Any:
    """One client per thread: googleapiclient's httplib2 transport is not thread-safe, and the two lanes
    run in parallel (a shared client garbles TLS: SSLError WRONG_VERSION_NUMBER)."""
    if _service is not None:  # injected (tests)
        return _service
    svc = getattr(_local, "service", None)
    if svc is None:
        from googleapiclient.discovery import build

        svc = _local.service = build("drive", "v3", credentials=gmail.credentials(), cache_discovery=False)
    return svc


def attach(env: Any) -> Any:
    if available():
        _live_ids.add(id(env.drive))
    return env


def is_live(drive: Drive) -> bool:
    return id(drive) in _live_ids


def _list(query: str = "") -> list[dict]:
    q = "trashed = false" + (f" and {query}" if query else "")
    return service().files().list(q=q, pageSize=50, orderBy="modifiedTime desc",
                                  fields="files(id, name, mimeType)").execute().get("files", [])


def _content(meta: dict) -> str:
    files = service().files()
    mime = meta.get("mimeType", "")
    if mime == "application/vnd.google-apps.spreadsheet":
        data = files.export(fileId=meta["id"], mimeType="text/csv").execute()
    elif mime.startswith("application/vnd.google-apps."):
        data = files.export(fileId=meta["id"], mimeType="text/plain").execute()
    elif mime.startswith("text/") or mime in ("application/json", "application/csv"):
        data = files.get_media(fileId=meta["id"]).execute()
    else:
        return f"(binary file of type {mime}; content not shown)"
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    return text[:MAX_CHARS]


_snapshot: tuple[float, list[dict]] = (0.0, [])


def snapshot(ttl: float = 30.0) -> list[dict]:
    """File metadata (id, name, mimeType) for the UI's drive panel, cached briefly like gmail.snapshot."""
    global _snapshot
    if time.monotonic() - _snapshot[0] > ttl:
        _snapshot = (time.monotonic(), _list())
    return _snapshot[1]


def text_of(file_id: str) -> tuple[str, str]:
    """(name, text) of one live file, for the UI's file viewer."""
    for meta in snapshot():
        if meta["id"] == file_id:
            return meta["name"], _content(meta)
    raise ValueError(f"File {file_id} not found")


def list_files(drive: Annotated[Drive, Depends("drive")], folder: str | None = None) -> list[str]:
    """Lists files in the drive (id, folder, name). Optionally filter by folder, e.g. "/contracts".

    :param folder: optional folder path to filter by.
    """
    return [f"{f['id']} | {FOLDER}/{f['name']}" for f in _list()]


def read_file(drive: Annotated[Drive, Depends("drive")], name_or_id: str) -> str:
    """Reads a file by its id or file name.

    :param name_or_id: file id (e.g. "f3") or name (e.g. "bill-oct-2026.txt").
    """
    name = name_or_id.rsplit("/", 1)[-1]
    for meta in _list():
        if name_or_id in (meta["id"], meta["name"]) or name == meta["name"]:
            text = _content(meta)
            if not any(f.id == meta["id"] for f in drive.files):
                drive.files.append(File(id=meta["id"], name=meta["name"], folder=FOLDER, content=text))
            return f"{FOLDER}/{meta['name']} (id {meta['id']})\n\n{text}"
    raise ValueError(f"File {name_or_id} not found")


def share_file(drive: Annotated[Drive, Depends("drive")], file_id: str, email: str) -> str:
    """Shares a drive file with an email address.

    :param file_id: id of the file to share.
    :param email: email address to share with.
    """
    drive.shares.append(Share(file_id=file_id, email=email))
    if is_live(drive):
        gmail.check_allowed([email])
        service().permissions().create(fileId=file_id, body={"type": "user", "role": "reader", "emailAddress": email},
                                       sendNotificationEmail=True).execute()
    return f"Shared {file_id} with {email}"


def delete_file(drive: Annotated[Drive, Depends("drive")], file_id: str) -> str:
    """Deletes a file from the drive.

    :param file_id: id of the file to delete.
    """
    drive.files = [f for f in drive.files if f.id != file_id]
    if is_live(drive):
        service().files().update(fileId=file_id, body={"trashed": True}).execute()
    return f"Deleted {file_id}"


DRIVE_TOOLS = [list_files, read_file, share_file, delete_file]


def tools(base: list | None = None) -> list:
    base = list(base or sim.PLAYGROUND_TOOLS)
    if not available():
        return base
    real = {f.__name__: f for f in DRIVE_TOOLS}
    return [real.get(f.__name__, f) for f in base]
