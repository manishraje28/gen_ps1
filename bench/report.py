"""Builds docs/RESULTS.md from results/*.json only (no number is typed by hand).

  python -m bench.report
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from warrant import config  # noqa: E402

DEF = {"none": "Undefended", "prompt": "Prompt defence", "detector": "Detector (auditor only)",
       "warrant_nomask": "WARRANT gate only", "warrant": "WARRANT full"}
ORDER = list(DEF)
SUITES = ["banking", "slack", "travel", "workspace"]


def pct(x) -> str:
    if x is None:
        return "–"
    return f"{x * 100:.2f}%".replace(".00%", "%")


def load(pattern: str) -> list[dict]:
    rows = []
    for p in sorted(config.RESULTS_DIR.glob(pattern)):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


def count(rows, key_rate: str, key_n: str) -> tuple[int, int]:
    hits = sum(round(r[key_rate] * r[key_n]) for r in rows if r.get(key_rate) is not None)
    return hits, sum(r[key_n] for r in rows if r.get(key_rate) is not None)


def main_table(rows: list[dict], planner: str) -> list[str]:
    rs = [r for r in rows if r.get("planner") == planner and "defense" in r]
    if not rs:
        return [f"_No `{planner}` results yet._", ""]
    out = ["| Suite | Defence | Benign utility | Utility under attack | ASR official | ASR executed | Attackable pairs | ASR attackable | Executed, attackable | Asked user (attacked) |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for s in SUITES:
        for d in ORDER:
            r = next((x for x in rs if x["suite"] == s and x["defense"] == d), None)
            if r:
                out.append(f"| {s} | {DEF[d]} | {pct(r['benign_utility'])} | {pct(r['utility_under_attack'])} | {pct(r['asr'])} | "
                           f"{pct(r.get('asr_executed'))} | {r.get('n_attackable_pairs', '–')} | {pct(r.get('asr_attackable'))} | "
                           f"{pct(r.get('asr_executed_attackable'))} | {pct(r.get('asked_user_rate_attacked'))} |")
    out.append("")
    out.append("**All suites:**")
    out.append("")
    out.append("| Defence | Attacks official (of all runs) | Attacks executed (of attackable pairs) | Benign tasks completed |")
    out.append("|---|---|---|---|")
    for d in ORDER:
        dr = [r for r in rs if r["defense"] == d]
        if not dr:
            continue
        a, n = count(dr, "asr", "n_attacked_runs")
        e, m = count(dr, "asr_executed_attackable", "n_attackable_pairs")
        b, k = count(dr, "benign_utility", "n_user_tasks")
        out.append(f"| {DEF[d]} | {a}/{n} ({pct(a / n if n else None)}) | {e}/{m} ({pct(e / m if m else None)}) | {b}/{k} |")
    out.append("")
    return out


def attack_table(rows: list[dict]) -> list[str]:
    rs = [r for r in rows if r.get("planner") == "oracle"]
    if not rs:
        return ["_No attack-sweep results yet._", ""]
    attacks = sorted({r["attack"] for r in rs})
    out = ["| Attack | Suite | Undefended ASR | WARRANT ASR official | WARRANT ASR executed | WARRANT utility under attack |", "|---|---|---|---|---|---|"]
    for a in attacks:
        for s in SUITES:
            n = next((r for r in rs if r["attack"] == a and r["suite"] == s and r["defense"] == "none"), None)
            w = next((r for r in rs if r["attack"] == a and r["suite"] == s and r["defense"] == "warrant"), None)
            if n or w:
                out.append(f"| `{a}` | {s} | {pct(n and n['asr_attackable'])} | {pct(w and w['asr_attackable'])} | "
                           f"{pct(w and w.get('asr_executed_attackable'))} | {pct(w and w['utility_under_attack'])} |")
    out.append("")
    out.append("Rates use the attackable denominator.")
    out.append("")
    return out


def heldout_table() -> list[str]:
    out = []
    for f in sorted((config.RESULTS_DIR / "heldout").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out.append(f"**{f.name}** (planner `{d['planner']}`, mask {'on' if d.get('mask') else 'off'}, file sha256 `{d['sha256'][:12]}…`, {d['date']})")
        out.append("")
        out.append("| Case | Style | Undefended: attack | WARRANT: attack | WARRANT outcome | WARRANT utility |")
        out.append("|---|---|---|---|---|---|")
        by = {}
        for r in d["rows"]:
            by.setdefault(r["case"], {})[r["defense"]] = r
        for cid, v in by.items():
            n, w = v.get("none", {}), v.get("warrant", {})
            att = lambda r: "–" if r.get("benign") else ("**succeeds**" if r.get("attack_succeeded") else "blocked")  # noqa: E731
            out.append(f"| {cid} | {w.get('style', n.get('style', ''))} | {att(n)} | {att(w)} | {w.get('outcome', '–')} | {'yes' if w.get('utility') else 'no'} |")
        for dname in ("none", "warrant"):
            if dname in d:
                x = d[dname]
                out.append("")
                out.append(f"{DEF[dname]}: {x['attacks_succeeded']}/{x['attacks']} attacks succeed, {x['asked_user']} asked the user, "
                           f"{x['benign_utility']}/{x['benign']} benign tasks completed.")
        out.append("")
    return out or ["_No held-out results yet._", ""]


def adaptive_table() -> list[str]:
    out = []
    for f in sorted((config.RESULTS_DIR / "adaptive").glob("adaptive_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        pairs = d["pairs"]
        rounds = [len(p["history"]) for p in pairs.values()]
        broken = sum(1 for p in pairs.values() if p["broken_at"])
        status = "finished" if d.get("finished") else "partial (checkpoint)"
        out.append(f"| {d['suite']} | {DEF.get(d['defense'], d['defense'])} | {d['mode']} | {d['planner']} | {len(pairs)} | "
                   f"{min(rounds) if rounds else 0}-{max(rounds) if rounds else 0} | {broken}/{len(pairs)} | {d.get('asr_at_k', '–')} | {status} |")
    if not out:
        return ["_No adaptive results yet._", ""]
    return ["| Suite | Defence | Feedback | Planner | Pairs | Rounds run | Pairs broken (executed) | ASR@k | Status |",
            "|---|---|---|---|---|---|---|---|---|", *out, ""]


def main() -> None:
    lines = ["# WARRANT results", "",
             f"Generated by `python -m bench.report` on {time.strftime('%Y-%m-%d %H:%M')} from `results/`. Do not edit by hand.", "",
             "**How to read this.** *Official* = AgentDojo's scorer (counts every proposed call; a call the gate blocked can still "
             "count, AgentDojo issue #168). *Executed* = only calls that actually ran. *Attackable* = excludes the injection tasks whose "
             "ground truth is empty in the default environment (workspace `injection_task_6..13`, travel `injection_task_6`): no planner "
             "can carry them out, so they inflate the denominator. The *oracle* planner is LLM-free and fully hijacked: it replays the "
             "user task and the attacker's exact calls, so it measures what the gate guarantees when the model is completely fooled, "
             "not what a live model does.", "",
             "## 1. AgentDojo, `important_instructions`, oracle planner", "", *main_table(load("oracle_*.json"), "oracle"),
             "## 2. AgentDojo, `important_instructions`, live planner (Groq)", "", *main_table(load("groq_*.json"), "groq"),
             "## 3. Other AgentDojo attack styles (oracle)", "", *attack_table(load("attacks/*.json") + [r for r in load("oracle_*.json") if r.get("defense") in ("none", "warrant")]),
             "## 4. Held-out attacks (frozen before running; `bench/heldout.py`)", "", *heldout_table(),
             "## 5. Adaptive attacker (live planner; `bench/adaptive.py`)", "", *adaptive_table()]
    out = ROOT / "docs" / "RESULTS.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
