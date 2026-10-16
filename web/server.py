"""WARRANT demo server: FastAPI + Server-Sent Events.

Run:  python -m web.server   (then open http://127.0.0.1:8000)
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from demo.runner import run_lane, save_trace  # noqa: E402
from demo.scenarios import SCENARIOS  # noqa: E402
from warrant import config  # noqa: E402
from warrant.gate import verify_audit_log  # noqa: E402
from web.playground import router as playground_router  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="WARRANT demo")
app.include_router(playground_router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def no_stale_ui(request, call_next):
    # The UI has no build step; make browsers revalidate so a demo machine never runs a stale app.js.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
@app.get("/workspace")
def workspace_page():
    # The main UI: the WARRANT workspace (one assistant over mail, Slack, bank and drive, ordinary vs protected).
    return FileResponse(STATIC / "workspace" / "index.html")


@app.get("/classic")
def classic():
    # The original demo UI (live demo, playground, AgentDojo benchmark tab, how it works).
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config():
    return {
        "has_key": config.has_groq_key(), "planner_model": config.PLANNER_MODEL, "contract_model": config.CONTRACT_MODEL,
        "auditor_model": config.AUDITOR_MODEL if config.AUDITOR_USE_LLM else None,
    }


@app.get("/api/scenarios")
def scenarios():
    out = []
    for s in SCENARIOS.values():
        env = s.world()
        poisoned = None
        for e in env.inbox.received:
            if e.date >= "2026-10-13" and e.sender != "karan@acme.test":
                poisoned = {"kind": "email", "title": f"{e.sender} - {e.subject}", "text": e.body}
        for f in env.drive.files:
            if f.folder == "/bills":
                poisoned = {"kind": "file", "title": f"{f.folder}/{f.name}", "text": f.content}
        out.append({"id": s.id, "title": s.title, "tag": s.tag, "query": s.query, "attack": s.attack_summary, "benign": s.benign, "source": poisoned})
    return out


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


@app.get("/api/run")
def run(scenario: str, planner: str = "groq", mask: int = 1, lanes: str = "undefended,warrant"):
    if scenario not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    if planner == "groq" and not config.has_groq_key():
        planner = "scripted"
    q: queue.Queue = queue.Queue()
    lane_list = [x for x in lanes.split(",") if x in ("undefended", "warrant")]
    collected: list[dict] = []
    lock = threading.Lock()
    finished: list[str] = []

    def sink(ev: dict) -> None:
        with lock:
            collected.append(ev)
        q.put(ev)

    def worker(lane: str) -> None:
        # The trace is saved here, not in the stream, so a page reload or closed tab
        # mid-run does not lose a live run.
        try:
            run_lane(scenario, lane, planner, bool(mask), sink=sink)
        finally:
            with lock:
                collected.append({"lane": lane, "type": "lane_done"})
                finished.append(lane)
                name = save_trace(scenario, planner, bool(mask), list(collected)) if len(finished) == len(lane_list) else None
            q.put({"lane": lane, "type": "lane_done"})
            if name:
                q.put({"type": "saved", "trace": name})

    def stream():
        yield _sse({"type": "hello", "scenario": scenario, "planner": planner, "mask": bool(mask), "lanes": lane_list})
        threads = [threading.Thread(target=worker, args=(ln,), daemon=True) for ln in lane_list]
        for i, t in enumerate(threads):
            t.start()
            if i == 0 and len(threads) > 1:
                time.sleep(0.4)  # stagger the two lanes slightly to share rate limits
        while True:
            try:
                ev = q.get(timeout=20)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield _sse(ev)
            if ev.get("type") == "saved":
                break
        yield _sse({"type": "end"})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/traces")
def traces(scenario: str | None = None):
    items = []
    for p in sorted(config.TRACES_DIR.glob("*.json"), reverse=True):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if scenario and meta.get("scenario") != scenario:
            continue
        items.append({"name": p.name, "scenario": meta.get("scenario"), "planner": meta.get("planner"), "mask": meta.get("mask"), "saved": meta.get("saved")})
    return items[:50]


@app.get("/api/replay")
def replay(trace: str, speed: float = 1.0):
    path = config.TRACES_DIR / Path(trace).name
    if not path.exists():
        raise HTTPException(404, "trace not found")
    data = json.loads(path.read_text(encoding="utf-8"))

    def stream():
        yield _sse({"type": "hello", "scenario": data["scenario"], "planner": data["planner"], "mask": data["mask"], "replay": True, "lanes": ["undefended", "warrant"]})
        events = data["events"]
        # interleave by recorded time per lane
        events = sorted(events, key=lambda e: (e.get("t", 0)))
        last = 0.0
        for ev in events:
            dt = max(0.0, min(1.5, (ev.get("t", last) - last))) / max(speed, 0.1)
            if ev.get("type") not in ("stage",):
                time.sleep(max(dt, 0.12 / max(speed, 0.1)))
            last = ev.get("t", last)
            yield _sse(ev)
        yield _sse({"type": "end"})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/bench")
def bench():
    rows = []
    for p in sorted(config.RESULTS_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "defense" in d:
            d.pop("per_run", None)
            d.pop("benign_per_task", None)
            d.pop("benign_detail", None)
            rows.append(d)
    return rows


@app.get("/api/audit/verify")
def verify(run_id: str):
    path = config.AUDIT_DIR / f"{Path(run_id).name}.jsonl"
    if not path.exists():
        raise HTTPException(404, "audit log not found")
    return verify_audit_log(path)


@app.get("/api/audit/raw")
def audit_raw(run_id: str):
    path = config.AUDIT_DIR / f"{Path(run_id).name}.jsonl"
    if not path.exists():
        raise HTTPException(404, "audit log not found")
    return JSONResponse([json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()])


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    print(f"\n  WARRANT demo  ->  http://{a.host}:{a.port}\n  Groq key loaded: {config.has_groq_key()}  | planner: {config.PLANNER_MODEL}\n")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
