"""Records live demo runs for offline Replay (Lane F).

For each demo scenario, runs both lanes live (undefended, then WARRANT) and keeps the trace only if it is a
clean take: for attack scenarios the undefended lane was hijacked AND WARRANT blocked the attack while
completing the task; for the benign scenario WARRANT completed it. Other takes go to traces/rejected/ with
the reason, so nothing is hidden. Mask off is recorded first, then mask on, so Replay picks mask on.

  python -m bench.record_traces --scenarios exfil,iban,benign,delegation --tries 2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo.runner import run_lane, save_trace  # noqa: E402
from demo.scenarios import SCENARIOS  # noqa: E402
from warrant import config  # noqa: E402


def take(scenario_id: str, mask: bool) -> tuple[bool, str, list[dict]]:
    events: list[dict] = []
    sink = events.append
    und = run_lane(scenario_id, "undefended", "groq", mask, sink=sink)["verdict"]
    events.append({"lane": "undefended", "type": "lane_done"})
    war = run_lane(scenario_id, "warrant", "groq", mask, sink=sink)["verdict"]
    events.append({"lane": "warrant", "type": "lane_done"})
    benign = SCENARIOS[scenario_id].benign
    if war.get("error") or und.get("error"):
        return False, f"error: {war.get('error') or und.get('error')}", events
    if benign:
        ok = war["utility"]
        why = "benign task completed" if ok else f"WARRANT did not complete the benign task ({war['utility_detail']})"
    else:
        ok = und["attack_succeeded"] and not war["attack_succeeded"] and war["utility"]
        why = (f"undefended hijacked={und['attack_succeeded']}, WARRANT attack={war['attack_succeeded']}, "
               f"WARRANT utility={war['utility']} ({war['utility_detail']})")
    return ok, why, events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", default="exfil,iban,benign,delegation")
    ap.add_argument("--tries", type=int, default=2)
    args = ap.parse_args()
    if not config.has_groq_key():
        sys.exit("Set GROQ_API_KEY in .env first.")
    rejected = config.TRACES_DIR / "rejected"
    rejected.mkdir(parents=True, exist_ok=True)
    for sid in args.scenarios.split(","):
        for mask in (False, True):
            for attempt in range(1, args.tries + 1):
                ok, why, events = take(sid, mask)
                if ok:
                    name = save_trace(sid, "groq", mask, events)
                    print(f"  KEPT     {sid:10} mask={mask!s:5} try {attempt}: {why} -> traces/{name}", flush=True)
                    break
                name = f"{time.strftime('%Y%m%d-%H%M%S')}_{sid}_groq{'' if mask else '_nomask'}.json"
                (rejected / name).write_text(json.dumps({"scenario": sid, "mask": mask, "why": why, "events": events}, default=str), encoding="utf-8")
                print(f"  REJECTED {sid:10} mask={mask!s:5} try {attempt}: {why}", flush=True)


if __name__ == "__main__":
    main()
