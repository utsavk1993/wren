"""The model that decides what to say.

Behind an interface for two reasons. The conversation logic can be exercised
without a live model, which is what makes the call flow testable at all, and
the model itself can be changed without touching anything that governs the
call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

# Thinking is on by default on this model. For a phone call the wait before the
# first word matters more than the depth of the reasoning, so effort is held
# low rather than thinking being switched off: disabling it entirely can push
# the model into writing tool calls as visible text.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "low"
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
    ) -> Reply: ...


class ClaudeModel:
    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        import anthropic

        self.model = model or os.getenv("WREN_LLM_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.getenv("WREN_LLM_EFFORT", DEFAULT_EFFORT)
        self._client = anthropic.AsyncAnthropic()

    async def respond(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Reply:
        # The instructions and the tool list are identical on every turn of
        # every call, so they are marked for caching. What follows them changes
        # constantly and is left out of the cached prefix.
        system_blocks = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            tools=tools,
            messages=messages,
            output_config={"effort": self.effort},
        ) as stream:
            message = await stream.get_final_message()

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
    ) -> Reply:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self._replies:
            return Reply(text="(nothing further)")
        return self._replies.pop(0)


def get_model() -> LanguageModel:
    return ClaudeModel()
