from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ue_api.auth import CurrentPrincipal, Session
from ue_api.deps import Today
from ue_api.economics import service
from ue_api.economics.engine import Window
from ue_api.economics.schemas import CustomerDetail, CustomerList, DayPoint, FeatureMargin, Summary

router = APIRouter(prefix="/economics", tags=["unit economics"])


def get_window(today: Today, days: Annotated[int, Query(ge=1, le=90)] = 30) -> Window:
    return service.window_for(days, today)


WindowDep = Annotated[Window, Depends(get_window)]


@router.get("/summary")
async def summary(principal: CurrentPrincipal, session: Session, window: WindowDep) -> Summary:
    return await service.summary(session, principal.org_id, window)


@router.get("/customers")
async def customers(
    principal: CurrentPrincipal,
    session: Session,
    window: WindowDep,
    sort: service.CustomerSort = "margin",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerList:
    return await service.customers(session, principal.org_id, window, sort=sort, limit=limit, offset=offset)


@router.get("/customers/unprofitable")
async def unprofitable(
    principal: CurrentPrincipal,
    session: Session,
    window: WindowDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 10,
) -> CustomerList:
    return await service.customers(session, principal.org_id, window, limit=limit, only_unprofitable=True)


@router.get("/customers/{customer_id}")
async def customer(
    customer_id: str, principal: CurrentPrincipal, session: Session, window: WindowDep
) -> CustomerDetail:
    detail = await service.customer_detail(session, principal.org_id, customer_id, window)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no usage or subscription for this customer")
    return detail


@router.get("/features")
async def features(principal: CurrentPrincipal, session: Session, window: WindowDep) -> list[FeatureMargin]:
    return await service.features(session, principal.org_id, window)


@router.get("/timeseries")
async def timeseries(principal: CurrentPrincipal, session: Session, window: WindowDep) -> list[DayPoint]:
    return await service.timeseries(session, principal.org_id, window)
