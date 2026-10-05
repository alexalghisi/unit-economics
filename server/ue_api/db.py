import asyncio
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ue_api.settings import get_settings

# asyncpg connections belong to the event loop that opened them. The API runs on one loop,
# but Dramatiq actors run on the worker's own loop and tests on pytest's, so engines are
# cached per loop instead of being module globals.
_engines: dict[int, tuple[asyncio.AbstractEventLoop, AsyncEngine]] = {}


def get_engine() -> AsyncEngine:
    loop = asyncio.get_running_loop()
    cached = _engines.get(id(loop))
    if cached is not None and cached[0] is loop:
        return cached[1]
    engine = create_async_engine(get_settings().database_url, pool_size=10, max_overflow=10)
    _engines[id(loop)] = (loop, engine)
    return engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session


async def dispose_engines() -> None:
    loop = asyncio.get_running_loop()
    entry = _engines.pop(id(loop), None)
    if entry is not None:
        await entry[1].dispose()
