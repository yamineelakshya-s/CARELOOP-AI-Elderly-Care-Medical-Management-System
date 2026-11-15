from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.dependencies import get_current_user
from app.schemas import MedicationFields
from app.services import adherence_service
from app.services import medication_service

router = APIRouter(prefix="/api/medications", tags=["Medications"])


def _mark_private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


class MedicationCreate(MedicationFields):
    care_circle_id: str = Field(min_length=1, max_length=100)


class DoseCreate(BaseModel):
    scheduled_at: datetime
    status: Literal["Taken", "Missed", "Pending"] = "Pending"


class DoseStatusUpdate(BaseModel):
    status: Literal["Taken", "Missed", "Pending"]


@router.get("")
def list_medications(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    return medication_service.list_medications(current_user["id"])


@router.get("/adherence")
def get_weekly_adherence(
    response: Response,
    week_start: date | None = None,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    return adherence_service.weekly_adherence(
        current_user["id"], week_start
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_medication(
    response: Response,
    data: MedicationCreate,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    record = medication_service.create_medication(
        current_user["id"], data.model_dump()
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The care circle was not found or medication management "
                "is not authorized."
            )
        )
    return record


@router.get("/{medication_id}")
def get_medication(
    response: Response,
    medication_id: str,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    record = medication_service.get_medication(
        current_user["id"], medication_id
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Medication not found.")
    return record


@router.put("/{medication_id}")
def update_medication(
    response: Response,
    medication_id: str,
    data: MedicationFields,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    record = medication_service.update_medication(
        current_user["id"], medication_id, data.model_dump()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Medication not found.")
    return record


@router.delete("/{medication_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_medication(
    response: Response,
    medication_id: str,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    if not medication_service.delete_medication(
        current_user["id"], medication_id
    ):
        raise HTTPException(status_code=404, detail="Medication not found.")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/{medication_id}/doses",
    status_code=status.HTTP_201_CREATED
)
def record_dose(
    response: Response,
    medication_id: str,
    data: DoseCreate,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    try:
        record = medication_service.record_dose(
            current_user["id"],
            medication_id,
            data.scheduled_at,
            data.status
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Medication not found or dose management is not authorized."
            )
        )
    return record


@router.patch("/{medication_id}/doses/{dose_id}")
def update_dose(
    response: Response,
    medication_id: str,
    dose_id: str,
    data: DoseStatusUpdate,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    record = medication_service.update_dose(
        current_user["id"], medication_id, dose_id, data.status
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Medication dose not found or dose management "
                "is not authorized."
            )
        )
    return record