from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.dependencies import get_current_user
from app.services import timeline_service

router = APIRouter(prefix="/api/timeline", tags=["Care Timeline"])


class TimelineCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    care_circle_id: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)


@router.get("")
def list_timeline(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    return timeline_service.list_timeline(current_user["id"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_timeline_event(
    data: TimelineCreate,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    created = timeline_service.create_timeline_event(
        current_user["id"], data.care_circle_id, data.description
    )
    if not created:
        raise HTTPException(
            status_code=404,
            detail=(
                "The care circle was not found or timeline updates "
                "are not authorized."
            )
        )
    return {"message": "Care activity recorded."}
