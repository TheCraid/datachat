"""A scripted stand-in for the LLM, so the agent and API run in tests with no network or API key."""

from __future__ import annotations

import json
from collections.abc import Callable


class ScriptedBackend:
    """Returns queued replies in order. A reply may be a dict, a string, or a function of the messages."""

    def __init__(self, replies: list | None = None, default: Callable | None = None):
        self.replies = list(replies or [])
        self.default = default
        self.calls: list[dict] = []

    def push(self, *replies) -> None:
        self.replies.extend(replies)

    def chat(self, model: str, messages: list[dict], json_mode: bool) -> tuple[str, int, int]:
        self.calls.append({"model": model, "messages": messages})
        if self.replies:
            reply = self.replies.pop(0)
        elif self.default is not None:
            reply = self.default
        else:
            raise AssertionError("ScriptedBackend ran out of replies")
        if callable(reply):
            reply = reply(messages)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return text, 100, 20
