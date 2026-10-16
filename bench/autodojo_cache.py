"""Replays a cached AutoDojo injection set on stock agentdojo 0.1.35 (no fork needed).

AUTODOJO_CACHE points at one `injections.json` (AutoDojo's `variants/<suite>/<model>/<defence>/injections.json`)
or at a folder holding `<suite>.json` files. Variant AUTODOJO_VARIANT (default 0) of each injection vector is
used, falling back to the original text. Registered as attack `autodojo` only if the fork did not register it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from agentdojo.attacks.attack_registry import ATTACKS, register_attack
from agentdojo.attacks.base_attacks import BaseAttack


def _cache_file(suite: str) -> Path:
    p = Path(os.environ.get("AUTODOJO_CACHE", ""))
    if p.is_dir():
        p = p / f"{suite}.json"
    if not p.is_file():
        raise FileNotFoundError(f"AUTODOJO_CACHE: no injection cache for suite {suite} at {p}")
    return p


class AutoDojoCacheAttack(BaseAttack):
    name = "autodojo"

    def __init__(self, task_suite, target_pipeline) -> None:
        super().__init__(task_suite, target_pipeline)
        self.cache = json.loads(_cache_file(task_suite.name).read_text(encoding="utf-8"))["injection_tasks"]
        self.variant = int(os.environ.get("AUTODOJO_VARIANT", "0"))

    def attack(self, user_task, injection_task) -> dict[str, str]:
        vectors = self.cache.get(injection_task.ID, {})
        out = {}
        for inj in self.get_injection_candidates(user_task):
            entry = vectors.get(inj) or {}
            variants = entry.get("variants") or []
            out[inj] = variants[self.variant] if self.variant < len(variants) else entry.get("original", injection_task.GOAL)
        # Stock agentdojo pastes injections into a YAML template unescaped; quotes / newlines in AutoDojo text
        # then break loading. Escape (as inside a double-quoted YAML string) only the vectors that need it.
        for inj in list(out):
            try:
                self.task_suite.load_and_inject_default_environment({**{k: "x" for k in out}, inj: out[inj]})
            except Exception:
                out[inj] = json.dumps(out[inj], ensure_ascii=False)[1:-1]
        return out


if "autodojo" not in ATTACKS:
    register_attack(AutoDojoCacheAttack)
