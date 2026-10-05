from fastapi import APIRouter, HTTPException, status

from ue_api.auth import CurrentPrincipal, Session
from ue_api.billing.service import NotFoundError
from ue_api.deps import Today
from ue_api.economics.service import window_for
from ue_api.simulator.schemas import SimulationRequest, SimulationResult
from ue_api.simulator.service import simulate

router = APIRouter(tags=["simulator"])


@router.post("/simulations")
async def run_simulation(
    body: SimulationRequest, principal: CurrentPrincipal, session: Session, today: Today
) -> SimulationResult:
    """Replay the last `days` of usage for a product's customers under candidate prices.
    Nothing is persisted."""
    try:
        return await simulate(
            session, principal.org_id, body.product_id, list(body.prices), window_for(body.days, today)
        )
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
