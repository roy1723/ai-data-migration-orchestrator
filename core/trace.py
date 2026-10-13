"""Append-only trace of every message exchanged between agents.

The brief says agents interact "via messages or files". Here each agent returns
a message dict and the orchestrator appends it to outputs/agent_trace.jsonl, so
a reviewer can replay the conversation step by step.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def make_message(sender: str, receiver: str, kind: str, payload: dict, attempt: int = 0) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from": sender,
        "to": receiver,
        "kind": kind,
        "attempt": attempt,
        "payload": payload,
    }


class TraceLog:
    def __init__(self, path: str | Path, reset: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if reset:
            self.path.write_text("", encoding="utf-8")

    def write(self, messages: list[dict]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
