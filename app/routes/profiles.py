from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user
from app.repositories import sqlite_repository

router = APIRouter(prefix="/api/profile", tags=["Profile"])


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)


@router.get("")
def get_profile(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    return {"profile": current_user}


@router.patch("")
def update_profile(
    data: ProfileUpdate,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    profile = sqlite_repository.update_user_name(
        current_user["id"], data.name
    )
    if profile is None:
        raise HTTPException(
            status_code=403,
            detail="Synthetic demo profiles cannot be changed."
        )
    return {"profile": profile}