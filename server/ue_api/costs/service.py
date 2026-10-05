import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.costs.pricebook import PriceBook, PriceVersion
from ue_api.models import ModelPrice


class BackdatedPriceError(ValueError):
    pass


async def load_pricebook(session: AsyncSession, org_id: uuid.UUID) -> PriceBook:
    rows = (await session.scalars(select(ModelPrice).where(ModelPrice.org_id == org_id))).all()
    return PriceBook.of(_to_version(r) for r in rows)


async def add_price_version(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    model: str,
    effective_from: datetime,
    input_per_mtok: Decimal,
    output_per_mtok: Decimal,
    cached_per_mtok: Decimal,
    now: datetime,
) -> PriceVersion:
    """Append a price version.

    Events are priced when they are ingested, so a version that starts in the past would
    disagree with costs already recorded. Only the first version of a model may be
    backdated (there is nothing to disagree with yet, beyond events stored as unpriced).
    """
    has_versions = await session.scalar(
        select(ModelPrice.id).where(ModelPrice.org_id == org_id, ModelPrice.model == model).limit(1)
    )
    if has_versions is not None and effective_from < now:
        raise BackdatedPriceError(
            f"a new price for {model!r} must take effect in the future; "
            "costs already attributed to events are immutable"
        )
    row = ModelPrice(
        org_id=org_id,
        model=model,
        effective_from=effective_from,
        input_per_mtok=input_per_mtok,
        output_per_mtok=output_per_mtok,
        cached_per_mtok=cached_per_mtok,
    )
    session.add(row)
    await session.flush()
    return _to_version(row)


def _to_version(row: ModelPrice) -> PriceVersion:
    return PriceVersion(
        id=row.id,
        model=row.model,
        effective_from=row.effective_from,
        input_per_mtok=row.input_per_mtok,
        output_per_mtok=row.output_per_mtok,
        cached_per_mtok=row.cached_per_mtok,
    )
