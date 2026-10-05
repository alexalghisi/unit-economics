from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts for one request. `input_tokens` excludes `cached_tokens`."""

    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def openai_usage(response: Any) -> Usage:
    """Read usage from an OpenAI-compatible chat completion (object or dict).

    Those APIs report cached tokens as a subset of `prompt_tokens`; they are split out here
    because the price table bills them at their own rate.
    """
    usage = _get(response, "usage")
    if usage is None:
        raise ValueError("response has no usage block")
    prompt = int(_get(usage, "prompt_tokens") or 0)
    completion = int(_get(usage, "completion_tokens") or 0)
    cached = int(_get(_get(usage, "prompt_tokens_details"), "cached_tokens") or 0)
    return Usage(input_tokens=prompt - cached, output_tokens=completion, cached_tokens=cached)


@dataclass(slots=True)
class LlmCall:
    """Handed out by `UnitEconomics.llm_call()`; call `record()` once the response is in."""

    customer_id: str
    feature: str
    model: str
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)
    usage: Usage | None = None
    started: float = field(default_factory=time.perf_counter)

    def record(
        self, usage: Usage | None = None, *, response: Any = None, **metadata: str | int | float | bool | None
    ) -> None:
        if usage is None:
            if response is None:
                raise ValueError("pass a Usage or an OpenAI-compatible response")
            usage = openai_usage(response)
        self.usage = usage
        self.metadata.update(metadata)
