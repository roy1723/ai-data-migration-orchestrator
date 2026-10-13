"""Groq backend using the OpenAI-compatible chat completions API.

Set GROQ_API_KEY. The model name comes from GROQ_MODEL and defaults to a
current Llama model on Groq's free tier. If a call fails with a transient
error the client retries with exponential backoff.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from .base import LLMClient

log = logging.getLogger("llm.groq")

DEFAULT_MODEL = "openai/gpt-oss-20b"
API_URL = "https://api.groq.com/openai/v1/chat/completions"
_RETRY_SECONDS_RE = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)


class RateLimitError(RuntimeError):
    """A 429 from Groq, with the server's own suggested wait time (seconds) if it gave one."""

    def __init__(self, message: str, retry_after: float | None):
        super().__init__(message)
        self.retry_after = retry_after


class GroqClient(LLMClient):
    name = "groq"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 temperature: float = 0.0, max_retries: int = 3, sleep=time.sleep,
                 post=None):
        self.model = model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
        self.temperature = temperature
        self.max_retries = max_retries
        self._sleep = sleep
        self._post = post or self._http_post
        self._key = api_key or os.environ.get("GROQ_API_KEY")
        if post is None and not self._key:
            raise RuntimeError(
                "No Groq API key found. Set GROQ_API_KEY, or run with --llm fake for the offline stub."
            )

    def _http_post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                # Groq's Cloudflare WAF rejects urllib's default UA (looks like a bot/scraper).
                "User-Agent": "multi-agent-sql-tool/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            log.error("Groq HTTP %s: %s", e.code, body)
            if e.code == 429:
                m = _RETRY_SECONDS_RE.search(body)
                retry_after = float(m.group(1)) if m else None
                raise RateLimitError(f"HTTP 429: {body}", retry_after) from None
            raise RuntimeError(f"HTTP {e.code}: {body}") from None

    def complete(self, system: str, prompt: str, json_mode: bool = True) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            # gpt-oss models spend tokens on hidden reasoning before the answer; without
            # a budget and a low effort level they can exhaust max tokens mid-JSON (a real
            # failure seen in testing: "max completion tokens reached before generating a
            # valid document") and burn through the free-tier TPM budget much faster.
            "max_completion_tokens": 4096,
        }
        if "gpt-oss" in self.model:
            payload["reasoning_effort"] = "low"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            log.info("Groq request model=%s attempt=%d/%d", self.model, attempt + 1, self.max_retries)
            try:
                data = self._post(payload)
                if "error" in data:
                    raise RuntimeError(data["error"])
                text = data["choices"][0]["message"]["content"]
                if text:
                    log.info("Groq response attempt=%d: %d chars", attempt + 1, len(text))
                    return text
                last_error = RuntimeError("Empty response from Groq")
            except RateLimitError as e:
                last_error = e
                log.warning("Groq attempt %d/%d rate-limited: %s", attempt + 1, self.max_retries, e)
                if attempt < self.max_retries - 1:
                    wait = e.retry_after if e.retry_after is not None else 2 ** attempt
                    log.info("Waiting %.1fs before retrying (server-suggested)", wait + 0.5)
                    self._sleep(wait + 0.5)  # small buffer past the server's own estimate
                continue
            except (urllib.error.URLError, RuntimeError, KeyError, IndexError) as e:
                last_error = e
                log.warning("Groq attempt %d/%d failed: %s", attempt + 1, self.max_retries, e)
            if attempt < self.max_retries - 1 and not isinstance(last_error, RateLimitError):
                self._sleep(2 ** attempt)
        raise RuntimeError(f"Groq call failed after {self.max_retries} attempts: {last_error}")
