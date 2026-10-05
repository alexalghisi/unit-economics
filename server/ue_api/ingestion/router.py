from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ue_api.auth import CurrentPrincipal, Session
from ue_api.clock import utcnow
from ue_api.ingestion.schemas import IngestRequest, IngestResponse
from ue_api.ingestion.service import IngestLimits, RollupScheduler, ingest
from ue_api.settings import get_settings

router = APIRouter(tags=["ingestion"])


def get_rollup_scheduler() -> RollupScheduler:
    # Imported lazily: declaring the actors binds them to the configured broker.
    from ue_api.workers import rollup_scheduler

    return rollup_scheduler()


Scheduler = Annotated[RollupScheduler, Depends(get_rollup_scheduler)]


@router.post("/events/ingest", response_model_exclude_none=True)
async def ingest_events(
    body: IngestRequest, principal: CurrentPrincipal, session: Session, scheduler: Scheduler
) -> IngestResponse:
    """Ingest a batch of events. Each item is validated on its own: the response has one
    result per item, in order, and a bad item never rejects the rest of the batch."""
    settings = get_settings()
    if len(body.events) > settings.max_batch_size:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"at most {settings.max_batch_size} events per request"
        )
    limits = IngestLimits(
        max_age=timedelta(days=settings.max_event_age_days),
        max_future_skew=timedelta(seconds=settings.max_event_future_skew_seconds),
    )
    return await ingest(
        session, principal.org_id, body.events, now=utcnow(), limits=limits, scheduler=scheduler
    )
