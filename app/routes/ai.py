from fastapi import APIRouter, Depends, HTTPException, Response

from app.dependencies import get_current_user
from app.services.gemini_service import (
    GeminiUnavailableError,
    create_care_summary
)

router = APIRouter(prefix="/api/ai", tags=["AI Care Summary"])


@router.get("/summary")
def care_summary(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    try:
        summary = create_care_summary(current_user["id"])
    except GeminiUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if summary is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return summary