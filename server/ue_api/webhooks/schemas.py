import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

EventType = Literal["customer.margin_negative", "customer.margin_recovered"]
EVENT_TYPES: tuple[EventType, ...] = ("customer.margin_negative", "customer.margin_recovered")


class EndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    event_types: Annotated[list[EventType], Field(min_length=1)] = Field(
        default_factory=lambda: list(EVENT_TYPES)
    )


class EndpointOut(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    disabled: bool


class EndpointCreated(EndpointOut):
    # Returned once, at creation, like an API key.
    secret: str


class DeliveryOut(BaseModel):
    id: str
    event_type: str
    status: str
    attempts: int
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
