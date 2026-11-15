from fastapi import APIRouter, Depends, HTTPException, Response

from app.dependencies import get_current_user
from app.services import notification_service

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("")
def list_notifications(
    response: Response,
    unread_only: bool = False,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    return {
        "notifications": notification_service.list_notifications(
            current_user["id"], unread_only
        )
    }


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    notification = notification_service.mark_notification_read(
        current_user["id"], notification_id
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return notification