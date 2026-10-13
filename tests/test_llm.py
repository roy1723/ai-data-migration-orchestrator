"""Gemini client behaviour with a mocked SDK (no network, no key)."""
import pytest

from agents.base import AgentError, BaseAgent, extract_json
from llm.base import LLMClient, make_client
from llm.gemini_client import GeminiClient


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


def test_gemini_client_uses_injected_sdk_client_and_retries():
    class Resp:
        text = '{"ok": true}'

    class Models:
        def __init__(self):
            self.calls = 0

        def generate_content(self, **kw):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("503 unavailable")
            return Resp()

    class Fake:
        models = Models()

    client = GeminiClient(model="test-model", client=Fake(), sleep=lambda s: None)
    assert client.complete("sys", "hello") == '{"ok": true}'
    assert Fake.models.calls == 2


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiClient()
