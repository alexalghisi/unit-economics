import uuid

from fastapi import APIRouter, status

from ue_api.auth import CurrentPrincipal, Session
from ue_api.webhooks import service
from ue_api.webhooks.schemas import DeliveryOut, EndpointCreated, EndpointIn, EndpointOut

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/endpoints", status_code=status.HTTP_201_CREATED)
async def create_endpoint(body: EndpointIn, principal: CurrentPrincipal, session: Session) -> EndpointCreated:
    created = await service.create_endpoint(session, principal.org_id, body)
    await session.commit()
    return created


@router.get("/endpoints")
async def list_endpoints(principal: CurrentPrincipal, session: Session) -> list[EndpointOut]:
    return await service.list_endpoints(session, principal.org_id)


@router.get("/endpoints/{endpoint_id}/deliveries")
async def list_deliveries(
    endpoint_id: uuid.UUID, principal: CurrentPrincipal, session: Session
) -> list[DeliveryOut]:
    return await service.list_deliveries(session, principal.org_id, endpoint_id)
