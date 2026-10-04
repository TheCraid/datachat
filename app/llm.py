"""LLM access: one small interface over any OpenAI-compatible chat API (Groq by default).

Every call goes through `LLMClient.json()`, which returns parsed JSON and records tokens and cost.
That single entry point lets the whole agent run in tests against a scripted fake model.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import Settings

log = logging.getLogger("datachat.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    models: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, model: str, tin: int, tout: int, cost: float) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += tin
            self.output_tokens += tout
            self.cost_usd += cost
            if model not in self.models:
                self.models.append(model)

    def summary(self) -> dict:
        return {"llm_calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_usd, 6), "models": list(self.models)}


class ChatBackend(Protocol):
    def chat(self, model: str, messages: list[dict], json_mode: bool) -> tuple[str, int, int]:
        """Return (text, input_tokens, output_tokens)."""


class OpenAICompatibleBackend:
    """Calls /chat/completions on Groq or any OpenAI-compatible endpoint."""

    def __init__(self, settings: Settings):
        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set. Add it to your .env file or the host's environment.")
        self.client = httpx.Client(
            base_url=settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            timeout=settings.llm_timeout_s,
        )
        self.retries = settings.rate_limit_retries
        self.max_wait = settings.rate_limit_max_wait_s

    def _post(self, body: dict) -> httpx.Response:
        """POST, waiting and retrying when the provider reports a rate limit (HTTP 429)."""
        for attempt in range(self.retries + 1):
            resp = self.client.post("/chat/completions", json=body)
            if resp.status_code != 429 or attempt == self.retries:
                return resp
            wait = retry_after_seconds(resp)
            if wait > self.max_wait:
                return resp
            log.info("Rate limited by %s; waiting %.1f s", body["model"], wait)
            time.sleep(wait)
        return resp

    def chat(self, model: str, messages: list[dict], json_mode: bool) -> tuple[str, int, int]:
        body: dict = {"model": model, "messages": messages, "temperature": 0}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = self._post(body)
        if resp.status_code == 400 and json_mode and "response_format" in resp.text:
            body.pop("response_format")  # model without JSON mode: rely on the prompt instead
            resp = self._post(body)
        if resp.status_code >= 400:
            raise LLMError(f"{model} returned HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}
        return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def retry_after_seconds(resp: httpx.Response) -> float:
    """Seconds to wait, from the Retry-After header or a message like 'try again in 1m2.5s'."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return float(header) + 0.5
        except ValueError:
            pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(m?s)", resp.text)
    if m:
        secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
        return int(m.group(1) or 0) * 60 + secs + 0.5
    return 5.0


def parse_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences or text around it."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        val = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("The model did not return JSON.") from None
        try:
            val = json.loads(t[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("The model returned malformed JSON.") from exc
    if not isinstance(val, dict):
        raise LLMError("The model returned JSON that is not an object.")
    return val


class LLMClient:
    """Role-based access: 'sql' uses the large model (with fallback), 'small' the cheaper one."""

    def __init__(self, backend: ChatBackend, settings: Settings):
        self.backend = backend
        self.s = settings

    def _price(self, model: str) -> tuple[float, float]:
        if model == self.s.sql_model:
            return self.s.price_in_per_m, self.s.price_out_per_m
        return self.s.small_price_in_per_m, self.s.small_price_out_per_m

    def json(self, role: str, messages: list[dict], usage: Usage) -> dict:
        primary = self.s.sql_model if role == "sql" else self.s.insight_model
        chain = [primary, self.s.fallback_model] + ([self.s.sql_model] if role != "sql" else [])
        models = [m for i, m in enumerate(chain) if m and m not in chain[:i]]
        last_error: Exception | None = None
        for model in models:
            for attempt in range(2):  # one retry on malformed JSON
                try:
                    text, tin, tout = self.backend.chat(model, messages, json_mode=True)
                except (LLMError, httpx.HTTPError) as exc:
                    last_error = exc
                    log.warning("LLM call to %s failed: %s", model, exc)
                    time.sleep(0.5)
                    break  # go to the fallback model
                pin, pout = self._price(model)
                usage.add(model, tin, tout, (tin * pin + tout * pout) / 1_000_000)
                try:
                    return parse_json(text)
                except LLMError as exc:
                    last_error = exc
                    if attempt == 0:
                        messages = messages + [{"role": "assistant", "content": text[:2000]},
                                               {"role": "user", "content": "Reply with only the JSON object."}]
        raise LLMError(f"The language model is unavailable right now ({last_error}).")
