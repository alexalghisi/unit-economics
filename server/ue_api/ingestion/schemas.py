from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

LLM_USAGE = "llm_usage"

Key = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
Label = Annotated[str, StringConstraints(max_length=100, strip_whitespace=True)]
EventName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{0,99}$")]
TokenCount = Annotated[int, Field(ge=0, le=50_000_000)]
MetadataValue = str | int | float | bool | None
Metadata = Annotated[dict[str, MetadataValue], Field(max_length=50)]


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: Key
    customer_id: Key
    timestamp: AwareDatetime
    metadata: Metadata = Field(default_factory=dict)


class LlmUsageEvent(_EventBase):
    """One LLM request. `input_tokens` excludes `cached_tokens`; each is billed at its own rate."""

    kind: Literal["llm_usage"]
    feature: Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)]
    model: Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)]
    input_tokens: TokenCount
    output_tokens: TokenCount
    cached_tokens: TokenCount = 0


class MeterEvent(_EventBase):
    """Anything else worth metering: documents processed, seats, tool calls."""

    kind: Literal["event"]
    name: EventName
    feature: Label = ""


IngestEvent = Annotated[LlmUsageEvent | MeterEvent, Field(discriminator="kind")]
event_adapter: TypeAdapter[LlmUsageEvent | MeterEvent] = TypeAdapter(IngestEvent)


class IngestRequest(BaseModel):
    # Items stay untyped here so that one bad event is reported, not the whole batch rejected.
    events: list[dict[str, object]] = Field(min_length=1)


class ItemError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ItemResult(BaseModel):
    index: int
    status: Literal["accepted", "duplicate", "invalid"]
    idempotency_key: str | None = None
    errors: list[ItemError] | None = None
    warnings: list[str] | None = None


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int
    invalid: int
    results: list[ItemResult]


def is_future(ts: datetime, now: datetime, skew_seconds: int) -> bool:
    return (ts - now).total_seconds() > skew_seconds
