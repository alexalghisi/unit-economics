import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.billing.meters import Meter, MeterDefinition
from ue_api.billing.pricing import AnyPrice, meter_of, price_adapter
from ue_api.billing.schemas import (
    CustomerIn,
    CustomerOut,
    ProductIn,
    ProductOut,
    SubscriptionIn,
    SubscriptionOut,
)
from ue_api.clock import utcnow
from ue_api.models import Customer, Price, Product, Subscription
from ue_api.models import Meter as MeterRow


class ConflictError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class CustomerPlan:
    customer_external_id: str
    customer_name: str
    product_id: uuid.UUID
    started_on: date
    prices: tuple[AnyPrice, ...]


def _meter_out(row: MeterRow) -> Meter:
    return Meter.model_validate(
        {
            "id": row.id,
            "slug": row.slug,
            "name": row.name,
            "event_name": row.event_name,
            "aggregation": row.aggregation,
            "property": row.property,
            "filters": row.filters,
        }
    )


async def create_meter(session: AsyncSession, org_id: uuid.UUID, definition: MeterDefinition) -> Meter:
    row = MeterRow(
        org_id=org_id,
        slug=definition.slug,
        name=definition.name,
        event_name=definition.event_name,
        aggregation=definition.aggregation,
        property=definition.property,
        filters=[f.model_dump() for f in definition.filters],
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"meter {definition.slug!r} already exists") from exc
    return _meter_out(row)


async def list_meters(session: AsyncSession, org_id: uuid.UUID) -> list[Meter]:
    rows = await session.scalars(select(MeterRow).where(MeterRow.org_id == org_id).order_by(MeterRow.slug))
    return [_meter_out(r) for r in rows]


async def meters_by_id(session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, Meter]:
    return {m.id: m for m in await list_meters(session, org_id)}


async def validate_meter_refs(session: AsyncSession, org_id: uuid.UUID, prices: list[AnyPrice]) -> None:
    known = await meters_by_id(session, org_id)
    for i, price in enumerate(prices):
        meter_id = meter_of(price)
        if meter_id is not None and meter_id not in known:
            raise NotFoundError(f"prices[{i}]: unknown meter {meter_id}")


async def create_product(session: AsyncSession, org_id: uuid.UUID, product: ProductIn) -> ProductOut:
    await validate_meter_refs(session, org_id, list(product.prices))
    row = Product(org_id=org_id, name=product.name)
    session.add(row)
    await session.flush()
    session.add_all(
        Price(product_id=row.id, position=i, spec=p.model_dump(mode="json"))
        for i, p in enumerate(product.prices)
    )
    await session.flush()
    return ProductOut(id=row.id, name=row.name, prices=product.prices)


async def list_products(session: AsyncSession, org_id: uuid.UUID) -> list[ProductOut]:
    products = (
        await session.scalars(select(Product).where(Product.org_id == org_id).order_by(Product.name))
    ).all()
    prices = await _prices_for(session, [p.id for p in products])
    return [ProductOut(id=p.id, name=p.name, prices=list(prices[p.id])) for p in products]


async def _prices_for(session: AsyncSession, product_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[AnyPrice]]:
    out: dict[uuid.UUID, list[AnyPrice]] = defaultdict(list)
    if not product_ids:
        return out
    rows = await session.scalars(
        select(Price).where(Price.product_id.in_(product_ids)).order_by(Price.product_id, Price.position)
    )
    for row in rows:
        out[row.product_id].append(price_adapter.validate_python(row.spec))
    return out


async def upsert_customer(session: AsyncSession, org_id: uuid.UUID, customer: CustomerIn) -> CustomerOut:
    stmt = (
        insert(Customer)
        .values(org_id=org_id, external_id=customer.external_id, name=customer.name, email=customer.email)
        .on_conflict_do_update(
            index_elements=[Customer.org_id, Customer.external_id],
            set_={"name": customer.name, "email": customer.email},
        )
        .returning(Customer.id)
    )
    customer_id = (await session.execute(stmt)).scalar_one()
    return CustomerOut(id=customer_id, **customer.model_dump())


async def list_customers(session: AsyncSession, org_id: uuid.UUID) -> list[CustomerOut]:
    rows = await session.scalars(
        select(Customer).where(Customer.org_id == org_id).order_by(Customer.external_id)
    )
    return [CustomerOut(id=r.id, external_id=r.external_id, name=r.name, email=r.email) for r in rows]


async def create_subscription(
    session: AsyncSession, org_id: uuid.UUID, sub: SubscriptionIn
) -> SubscriptionOut:
    customer_id = await session.scalar(
        select(Customer.id).where(Customer.org_id == org_id, Customer.external_id == sub.customer_external_id)
    )
    if customer_id is None:
        raise NotFoundError(f"unknown customer {sub.customer_external_id!r}")
    product = await session.scalar(
        select(Product.id).where(Product.org_id == org_id, Product.id == sub.product_id)
    )
    if product is None:
        raise NotFoundError(f"unknown product {sub.product_id}")
    # One active subscription per customer: switching plans cancels the previous one.
    await session.execute(
        update(Subscription)
        .where(Subscription.customer_id == customer_id, Subscription.canceled_at.is_(None))
        .values(canceled_at=utcnow())
    )
    row = Subscription(
        org_id=org_id, customer_id=customer_id, product_id=sub.product_id, started_on=sub.started_on
    )
    session.add(row)
    await session.flush()
    return SubscriptionOut(id=row.id, **sub.model_dump())


async def load_plans(
    session: AsyncSession, org_id: uuid.UUID, customer: str | None = None
) -> list[CustomerPlan]:
    query = (
        select(Customer.external_id, Customer.name, Subscription.product_id, Subscription.started_on)
        .join(Subscription, Subscription.customer_id == Customer.id)
        .where(Subscription.org_id == org_id, Subscription.canceled_at.is_(None))
    )
    if customer is not None:
        query = query.where(Customer.external_id == customer)
    rows = (await session.execute(query)).all()
    prices = await _prices_for(session, list({r.product_id for r in rows}))
    return [
        CustomerPlan(
            customer_external_id=r.external_id,
            customer_name=r.name,
            product_id=r.product_id,
            started_on=r.started_on,
            prices=tuple(prices[r.product_id]),
        )
        for r in rows
    ]


async def customer_names(session: AsyncSession, org_id: uuid.UUID) -> dict[str, str]:
    rows = await session.execute(select(Customer.external_id, Customer.name).where(Customer.org_id == org_id))
    return {r.external_id: r.name for r in rows}
