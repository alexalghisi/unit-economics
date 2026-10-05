from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ue_api.auth import CurrentPrincipal, Session
from ue_api.billing import service
from ue_api.billing.current_period import current_period_revenue
from ue_api.billing.meters import Meter, MeterDefinition
from ue_api.billing.schemas import (
    CurrentPeriod,
    CustomerIn,
    CustomerOut,
    ModelPriceIn,
    ModelPriceOut,
    ProductIn,
    ProductOut,
    SubscriptionIn,
    SubscriptionOut,
)
from ue_api.clock import utcnow
from ue_api.costs.service import BackdatedPriceError, add_price_version
from ue_api.deps import Today
from ue_api.models import ModelPrice

router = APIRouter(tags=["billing"])


@router.post("/model-prices", status_code=status.HTTP_201_CREATED)
async def create_model_price(
    body: ModelPriceIn, principal: CurrentPrincipal, session: Session
) -> ModelPriceOut:
    try:
        version = await add_price_version(session, principal.org_id, now=utcnow(), **body.model_dump())
    except BackdatedPriceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return ModelPriceOut(id=version.id, **body.model_dump())


@router.get("/model-prices")
async def list_model_prices(principal: CurrentPrincipal, session: Session) -> list[ModelPriceOut]:
    rows = await session.scalars(
        select(ModelPrice)
        .where(ModelPrice.org_id == principal.org_id)
        .order_by(ModelPrice.model, ModelPrice.effective_from)
    )
    return [
        ModelPriceOut(
            id=r.id,
            model=r.model,
            effective_from=r.effective_from,
            input_per_mtok=r.input_per_mtok,
            output_per_mtok=r.output_per_mtok,
            cached_per_mtok=r.cached_per_mtok,
        )
        for r in rows
    ]


@router.post("/meters", status_code=status.HTTP_201_CREATED)
async def create_meter(body: MeterDefinition, principal: CurrentPrincipal, session: Session) -> Meter:
    try:
        meter = await service.create_meter(session, principal.org_id, body)
    except service.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return meter


@router.get("/meters")
async def list_meters(principal: CurrentPrincipal, session: Session) -> list[Meter]:
    return await service.list_meters(session, principal.org_id)


@router.post("/products", status_code=status.HTTP_201_CREATED)
async def create_product(body: ProductIn, principal: CurrentPrincipal, session: Session) -> ProductOut:
    try:
        product = await service.create_product(session, principal.org_id, body)
    except service.NotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    return product


@router.get("/products")
async def list_products(principal: CurrentPrincipal, session: Session) -> list[ProductOut]:
    return await service.list_products(session, principal.org_id)


@router.put("/customers")
async def upsert_customer(body: CustomerIn, principal: CurrentPrincipal, session: Session) -> CustomerOut:
    customer = await service.upsert_customer(session, principal.org_id, body)
    await session.commit()
    return customer


@router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: SubscriptionIn, principal: CurrentPrincipal, session: Session
) -> SubscriptionOut:
    try:
        sub = await service.create_subscription(session, principal.org_id, body)
    except service.NotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    return sub


@router.get("/customers/{customer_id}/current-period")
async def current_period(
    customer_id: str, principal: CurrentPrincipal, session: Session, today: Today
) -> CurrentPeriod:
    result = await current_period_revenue(session, principal.org_id, customer_id, today)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer has no active subscription")
    return result
