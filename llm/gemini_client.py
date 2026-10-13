"""Gemini backend using the google-genai SDK.

Set GEMINI_API_KEY (or GOOGLE_API_KEY). The model name comes from GEMINI_MODEL
and defaults to the model used in Google's current quickstart. If a call fails
with a transient error the client retries with exponential backoff.
"""
from __future__ import annotations

import os
import time

from .base import LLMClient

DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiClient(LLMClient):
    name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 temperature: float = 0.0, max_retries: int = 3, client=None, sleep=time.sleep):
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self.temperature = temperature
        self.max_retries = max_retries
        self._sleep = sleep
        if client is not None:  # tests inject a fake SDK client here
            self._client = client
            return
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "No Gemini API key found. Set GEMINI_API_KEY, or run with --llm fake for the offline stub."
            )
        from google import genai  # imported lazily so the fake backend needs no SDK
        self._client = genai.Client(api_key=key)

    def complete(self, system: str, prompt: str, json_mode: bool = True) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
                text = response.text
                if text:
                    return text
                last_error = RuntimeError("Empty response from Gemini")
            except Exception as e:  # network, quota or server errors
                last_error = e
            if attempt < self.max_retries - 1:
                self._sleep(2 ** attempt)
        raise RuntimeError(f"Gemini call failed after {self.max_retries} attempts: {last_error}")
