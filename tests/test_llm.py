"""LLM client plumbing, and the Groq client's behaviour with an injected transport (no network, no key)."""
import pytest

from agents.base import AgentError, BaseAgent, extract_json
from llm.base import LLMClient, make_client
from llm.groq_client import GroqClient


class Scripted(LLMClient):
    name = "scripted"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, system, prompt, json_mode=True):
        self.calls += 1
        return self.replies.pop(0)


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 2} hope it helps') == {"a": 2}


def test_agent_reasks_once_on_bad_json():
    agent = BaseAgent(Scripted(["not json", '{"overview": "ok"}']))
    assert agent.ask("explainer_overview", {"load_order": [], "attempts": 1, "passed": True,
                                            "row_counts": {}, "open_findings": []}) == {"overview": "ok"}


def test_agent_gives_up_after_retry():
    agent = BaseAgent(Scripted(["nope", "still nope"]))
    with pytest.raises(AgentError):
        agent.ask("explainer_overview", {"load_order": [], "attempts": 1, "passed": True, "row_counts": {}, "open_findings": []})


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        make_client("gpt-none")


def test_groq_client_retries_on_transient_error():
    calls = {"n": 0}

    def post(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("503 unavailable")
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    client = GroqClient(model="test-model", post=post, sleep=lambda s: None)
    assert client.complete("sys", "hello") == '{"ok": true}'
    assert calls["n"] == 2


def test_groq_requires_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        GroqClient()
