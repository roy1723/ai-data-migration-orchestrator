"""The only thing the agents know about a language model.

Every agent calls LLMClient.complete(system, prompt). Swapping Groq for any
other backend (OpenAI, Hugging Face Transformers, a local model) means writing
one small subclass.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    name = "base"
    model = ""

    @abstractmethod
    def complete(self, system: str, prompt: str, json_mode: bool = True) -> str:
        """Return the model's text reply. In json_mode the reply must be a JSON object."""

    def describe(self) -> str:
        return f"{self.name} ({self.model})"


def make_client(kind: str, **kwargs) -> LLMClient:
    """Factory used by main.py: 'fake' for the offline stub, 'groq' for the real model."""
    kind = kind.lower()
    if kind == "fake":
        from .fake_llm import FakeLLM
        return FakeLLM(**kwargs)
    if kind == "groq":
        from .groq_client import GroqClient
        return GroqClient(**kwargs)
    raise ValueError(f"Unknown LLM backend '{kind}'. Use 'fake' or 'groq'.")
