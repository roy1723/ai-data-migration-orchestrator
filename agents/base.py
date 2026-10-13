"""Shared plumbing for the agents.

An agent is a small class with one job. It reads fields from the shared state,
may ask the LLM one or more questions through ask(), and returns a partial state
update that always includes one message for the audit trail.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.trace import make_message
from llm.base import LLMClient

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class AgentError(RuntimeError):
    """Raised when the LLM keeps returning something the agent cannot use."""


def load_prompt(step: str, prompts_dir: Path = PROMPTS_DIR) -> tuple[str, str]:
    """Read prompts/<step>.md and split it into (system, user) sections."""
    text = (prompts_dir / f"{step}.md").read_text(encoding="utf-8")
    m = re.match(r"\s*## SYSTEM\s*(.*?)\s*## USER\s*(.*)\Z", text, re.S)
    if not m:
        raise ValueError(f"Prompt {step}.md must contain '## SYSTEM' and '## USER' sections")
    return m.group(1).strip(), m.group(2).strip()


def extract_json(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating code fences and stray prose."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


class BaseAgent:
    name = "base"

    def __init__(self, llm: LLMClient, prompts_dir: Path = PROMPTS_DIR):
        self.llm = llm
        self.prompts_dir = Path(prompts_dir)

    def ask(self, step: str, variables: dict | None = None, retries: int = 1) -> dict:
        """Render prompts/<step>.md, call the LLM, and return the parsed JSON reply.

        If the reply is not valid JSON, the agent asks again once with a reminder
        before giving up. Variables are JSON-encoded into the {{placeholders}}.
        """
        system, user = load_prompt(step, self.prompts_dir)
        for key, value in (variables or {}).items():
            rendered = value if isinstance(value, str) and key == "fewshot" else json.dumps(value, ensure_ascii=False, indent=1)
            user = user.replace("{{" + key + "}}", rendered)
        last_error: Exception | None = None
        prompt = user
        for _ in range(retries + 1):
            reply = self.llm.complete(system, prompt, json_mode=True)
            try:
                return extract_json(reply)
            except json.JSONDecodeError as e:
                last_error = e
                prompt = user + "\n\nYour previous reply was not valid JSON. Reply with one JSON object only."
        raise AgentError(f"{self.name}: no valid JSON from the model for step '{step}': {last_error}")

    def message(self, to: str, kind: str, payload: dict, attempt: int = 0) -> dict:
        return make_message(self.name, to, kind, payload, attempt)

    def run(self, state: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError
