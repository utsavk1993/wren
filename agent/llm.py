"""The model that decides what to say.

Behind an interface for two reasons. The conversation logic can be exercised
without a live model, which is what makes the call flow testable at all, and
the model itself can be changed without touching anything that governs the
call.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

# Chosen by measurement rather than preference. Against the live API, with the
# full instructions and tool list cached, the time before the first token
# arrives was 2058 ms for Opus 5 at low effort, 1144 ms for Sonnet 5, and
# 589 ms for this model. A turn has roughly 1200 ms in total, of which speech
# recognition, turn detection and speech synthesis need most, so nothing larger
# fits. Raise it here for a text-only deployment where the wait does not matter.
DEFAULT_MODEL = "claude-haiku-4-5"

# Only the models that take an effort setting are given one; the smallest does
# not accept it.
DEFAULT_EFFORT = "low"
EFFORT_CAPABLE_PREFIXES = ("claude-opus-", "claude-sonnet-5", "claude-fable-")
MAX_TOKENS = 1024


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    raw_content: list[Any] = field(default_factory=list)

    @property
    def wants_a_tool(self) -> bool:
        return bool(self.tool_calls)


class LanguageModel(Protocol):
    async def respond(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timer: Any = None,
    ) -> Reply: ...


class ClaudeModel:
    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        import anthropic

        self.model = model or os.getenv("WREN_LLM_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.getenv("WREN_LLM_EFFORT", DEFAULT_EFFORT)
        self._client = anthropic.AsyncAnthropic()

    def _accepts_effort(self) -> bool:
        return self.model.startswith(EFFORT_CAPABLE_PREFIXES)

    async def respond(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timer: Any = None,
    ) -> Reply:
        # The instructions and the tool list are identical on every turn of
        # every call, so they are marked for caching. What follows them changes
        # constantly and is left out of the cached prefix.
        system_blocks = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system_blocks,
            "tools": tools,
            "messages": messages,
        }
        if self._accepts_effort():
            request["output_config"] = {"effort": self.effort}

        started = time.perf_counter()
        first_token_at: float | None = None
        async with self._client.messages.stream(**request) as stream:
            # Timed here rather than around the whole call: speech synthesis
            # starts on the first complete sentence, so this is the wait the
            # caller actually hears.
            async for _ in stream.text_stream:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
            message = await stream.get_final_message()

        if timer is not None:
            timer.count_llm_round()
            timer.record("first_token", ((first_token_at or time.perf_counter()) - started) * 1000)
            usage = getattr(message, "usage", None)
            if usage is not None:
                timer.cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

        text = "".join(b.text for b in message.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=b.input)
            for b in message.content
            if b.type == "tool_use"
        ]
        return Reply(
            text=text,
            tool_calls=calls,
            stop_reason=message.stop_reason or "end_turn",
            raw_content=list(message.content),
        )


class ScriptedModel:
    """A model that returns prepared replies, for exercising the call flow.

    The conversation logic is what needs testing: which tools are reached for,
    in what order, and which gates refuse. None of that involves the model
    thinking, and testing it against a live one would be slow, expensive and
    differently wrong on every run.
    """

    def __init__(self, replies: list[Reply]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def respond(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timer: Any = None,
    ) -> Reply:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self._replies:
            return Reply(text="(nothing further)")
        return self._replies.pop(0)


def get_model() -> LanguageModel:
    return ClaudeModel()
