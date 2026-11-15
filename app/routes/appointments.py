from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user
from app.services import appointments_service

router = APIRouter(prefix="/api/appointments", tags=["Appointments"])


def _mark_private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


class AppointmentFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    provider: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=300)


class AppointmentCreate(AppointmentFields):
    care_circle_id: str = Field(min_length=1, max_length=100)


@router.get("")
def list_appointments(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    return appointments_service.list_appointments(current_user["id"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_appointment(
    data: AppointmentCreate,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    record = appointments_service.create_appointment(
        current_user["id"], data.model_dump()
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The care circle was not found or appointment management "
                "is not authorized."
            )
        )
    return record


@router.get("/{appointment_id}")
def get_appointment(
    appointment_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    record = appointments_service.get_appointment(
        current_user["id"], appointment_id
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return record


@router.put("/{appointment_id}")
def update_appointment(
    appointment_id: str,
    data: AppointmentFields,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    record = appointments_service.update_appointment(
        current_user["id"], appointment_id, data.model_dump()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return record


@router.delete(
    "/{appointment_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete_appointment(
    appointment_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    if not appointments_service.delete_appointment(
        current_user["id"], appointment_id
    ):
        raise HTTPException(status_code=404, detail="Appointment not found.")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
