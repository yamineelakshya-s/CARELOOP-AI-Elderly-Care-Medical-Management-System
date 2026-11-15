import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from app.config import settings
from app.services import tick_service

router = APIRouter(tags=["Scheduled tasks"])


@router.post("/api/tick")
def run_tick(request: Request, response: Response):
    supplied_secret = request.headers.get("X-Tick-Secret")
    expected_secret = (
        settings.tick_secret.get_secret_value()
        if settings.tick_secret is not None
        else None
    )
    if (
        supplied_secret is None
        or expected_secret is None
        or not hmac.compare_digest(
            supplied_secret.encode("utf-8"),
            expected_secret.encode("utf-8")
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid tick secret is required."
        )

    response.headers["Cache-Control"] = "no-store"
    return tick_service.process_tick()
