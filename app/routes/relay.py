from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user
from app.services import relay_service

router = APIRouter(prefix="/api/relay", tags=["Caregiver Relay"])


def _mark_private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


class RelayCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    care_circle_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=2000)
    assigned_to_user_id: str | None = None


class RelayUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=2000)
    assigned_to_user_id: str | None = None
    status: Literal["open", "completed"]


@router.get("")
def list_relay_items(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    return relay_service.list_relay_items(current_user["id"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_relay_item(
    data: RelayCreate,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    item = relay_service.create_relay_item(
        current_user["id"], data.model_dump()
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The care circle was not found or relay management "
                "is not authorized."
            )
        )
    return item


@router.get("/{relay_id}")
def get_relay_item(
    relay_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    item = relay_service.get_relay_item(current_user["id"], relay_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Relay item not found.")
    return item


@router.put("/{relay_id}")
def update_relay_item(
    relay_id: str,
    data: RelayUpdate,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    item = relay_service.update_relay_item(
        current_user["id"], relay_id, data.model_dump()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Relay item not found.")
    return item


@router.delete("/{relay_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_relay_item(
    relay_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    if not relay_service.delete_relay_item(current_user["id"], relay_id):
        raise HTTPException(status_code=404, detail="Relay item not found.")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def _update_dose_alert(
    action,
    relay_id: str,
    response: Response,
    current_user: dict
):
    _mark_private(response)
    try:
        item = action(current_user["id"], relay_id)
    except relay_service.RelayConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Dose alert not found or relay management is not authorized."
        )
    return item


@router.post("/{relay_id}/claim")
def claim_dose_alert(
    relay_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    return _update_dose_alert(
        relay_service.claim_dose_alert, relay_id, response, current_user
    )


@router.post("/{relay_id}/contact")
def contact_dose_alert(
    relay_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    return _update_dose_alert(
        relay_service.contact_dose_alert, relay_id, response, current_user
    )


@router.post("/{relay_id}/resolve")
def resolve_dose_alert(
    relay_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    return _update_dose_alert(
        relay_service.resolve_dose_alert, relay_id, response, current_user
    )


@router.post("/{relay_id}/reopen")
def reopen_dose_alert(
    relay_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    return _update_dose_alert(
        relay_service.reopen_dose_alert, relay_id, response, current_user
    )