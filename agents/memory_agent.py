"""Memory Agent (bonus).

Remembers which SQL defects were hit on earlier runs and how they were fixed, then
hands those lessons to the SQL Generator as hints on the next run. The store is a
plain JSON file (data/memory.json). A hint is only recalled when it still applies
to the current schemas and mapping, so stale memory cannot cause harm.

Hint structure: {target, code, advice, action, value, times_seen, last_seen}
  action = skip_column  -> do not write this column (the target field does not exist)
  action = use_default  -> set this required column to `value`
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseAgent

ACTIONS = {"unknown_column": "skip_column", "required_missing": "use_default"}
ADVICE = {
    "skip_column": "The mapping sheet points at a target field that does not exist. Leave it out of the INSERT and report it.",
    "use_default": "A required target field has no mapping. Set it to the documented default.",
}


class MemoryAgent(BaseAgent):
    name = "memory_agent"

    def __init__(self, llm, path: str | Path, prompts_dir=None, enabled: bool = True):
        super().__init__(llm, *([prompts_dir] if prompts_dir else []))
        self.path = Path(path)
        self.enabled = enabled

    # ------------------------------------------------------------- storage
    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("hints", [])
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, hints: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"hints": hints}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # --------------------------------------------------------------- recall
    def recall(self, state: dict) -> dict:
        hints = self._applicable(self._load(), state) if self.enabled else []
        msg = self.message("schema_analyst", "memory_recall", {
            "enabled": self.enabled, "hints": [{"target": h["target"], "action": h["action"]} for h in hints]})
        return {"memory_hints": hints, "messages": [msg]}

    @staticmethod
    def _applicable(hints: list[dict], state: dict) -> list[dict]:
        target_fields = {f"{o}.{f}": m for o, d in state["target_schema"]["objects"].items() for f, m in d["fields"].items()}
        mapped = {r["target_object"] + "." + r["target_field"] for r in state["mapping"]}
        keep = []
        for h in hints:
            if h["action"] == "skip_column" and h["target"] in mapped and h["target"] not in target_fields:
                keep.append(h)
            elif h["action"] == "use_default" and target_fields.get(h["target"], {}).get("required") and h["target"] not in mapped:
                keep.append(h)
        return keep

    # --------------------------------------------------------------- update
    def update(self, state: dict) -> dict:
        if not self.enabled:
            msg = self.message("end", "memory_update", {"enabled": False, "stored": 0})
            return {"messages": [msg]}
        defaults = {a["target"]: a.get("default_value") for a in state.get("brief", {}).get("assumptions", [])}
        stored = {(h["target"], h["code"]): h for h in self._load()}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        learned, done = 0, set()
        # Only issues that were seen and then resolved teach the generator something.
        history = state.get("history", [])
        final_open = {(f["code"], f["target"]) for h in history[-1:] for f in h["fixable"]}
        for h in history:
            for f in h["fixable"]:
                action = ACTIONS.get(f["code"])
                if not action or (f["code"], f["target"]) in final_open:
                    continue
                key = (f["target"], f["code"])
                entry = stored.get(key, {"target": f["target"], "code": f["code"], "advice": ADVICE[action],
                                         "action": action, "value": defaults.get(f["target"]) if action == "use_default" else None,
                                         "times_seen": 0})
                if key not in stored and key not in done:
                    learned += 1
                if key in done:
                    continue
                done.add(key)
                entry["times_seen"] = entry.get("times_seen", 0) + 1
                entry["last_seen"] = now
                stored[key] = entry
        self._save(sorted(stored.values(), key=lambda e: (e["target"], e["code"])))
        msg = self.message("end", "memory_update", {"enabled": True, "new_hints": learned, "total_hints": len(stored)})
        return {"messages": [msg]}

    def run(self, state: dict) -> dict:  # pragma: no cover - the graph calls recall()/update()
        return self.recall(state)
