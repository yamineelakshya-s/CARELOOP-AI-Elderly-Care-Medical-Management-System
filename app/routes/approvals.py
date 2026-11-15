from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dependencies import get_current_user
from app.schemas import MedicationFields
from app.services import approval_service

router = APIRouter(prefix="/api/approvals", tags=["Elder Approvals"])


def _mark_private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    care_circle_id: str = Field(min_length=1, max_length=100)
    request_type: Literal["medication", "appointment", "other"]
    title: str = Field(min_length=1, max_length=160)
    details: str = Field(default="", max_length=2000)
    medication_id: str | None = Field(default=None, max_length=100)
    proposed_changes: MedicationFields | None = None

    @model_validator(mode="after")
    def validate_medication_proposal(self):
        if self.request_type == "medication":
            if self.medication_id is None or self.proposed_changes is None:
                raise ValueError(
                    "Select a medication and provide all proposed changes."
                )
        elif self.medication_id is not None or self.proposed_changes is not None:
            raise ValueError(
                "Medication changes are only allowed for medication requests."
            )
        return self


class ApprovalDecision(BaseModel):
    status: Literal["approved", "rejected"]
    decision_note: str = Field(default="", max_length=1000)


@router.get("")
def list_approval_requests(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    return approval_service.list_approval_requests(current_user["id"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_approval_request(
    data: ApprovalCreate,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    try:
        request = approval_service.create_approval_request(
            current_user["id"], data.model_dump(mode="json")
        )
    except approval_service.ApprovalConflictError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if request is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The care circle was not found or approval requests "
                "are not authorized."
            )
        )
    return request


@router.get("/{request_id}")
def get_approval_request(
    request_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    request = approval_service.get_approval_request(
        current_user["id"], request_id
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    return request


@router.post("/{request_id}/decision")
def decide_approval_request(
    request_id: str,
    data: ApprovalDecision,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    _mark_private(response)
    try:
        request = approval_service.decide_approval_request(
            current_user["id"],
            request_id,
            data.status,
            data.decision_note.strip()
        )
    except approval_service.ApprovalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if request is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Approval request not found, already decided, or only the "
                "elder may decide it."
            )
        )
    return request