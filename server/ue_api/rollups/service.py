import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.rollups.sql import ROLLUP_CHUNK


@dataclass(frozen=True, slots=True)
class ChunkResult:
    events: int
    daily_rows: int
    oldest_day: date | None
    max_lag: timedelta | None


async def rollup_chunk(session: AsyncSession, org_id: uuid.UUID, limit: int) -> ChunkResult:
    row = (await session.execute(ROLLUP_CHUNK, {"org_id": org_id, "limit": limit})).one()
    await session.commit()
    return ChunkResult(
        events=row.events, daily_rows=row.daily_rows, oldest_day=row.oldest_day, max_lag=row.max_lag
    )
