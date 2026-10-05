from datetime import date
from typing import Annotated

from fastapi import Depends

from ue_api.clock import utcnow


def get_today() -> date:
    return utcnow().date()


Today = Annotated[date, Depends(get_today)]
