import json
import time
import uuid
from pydantic import ValidationError

from app.database import connect
from app.repositories import sqlite_repository
from app.services import notification_service
from app.schemas import MedicationFields


class ApprovalConflictError(ValueError):
    pass


def _approval_response(row: dict) -> dict:
    record = dict(row)
    proposed_data = record.pop("proposed_data_json", None)
    record["proposed_changes"] = (
        json.loads(proposed_data) if proposed_data is not None else None
    )
    return record


def list_approval_requests(user_id: str) -> dict:
    scopes = sqlite_repository.get_user_circle_scopes(user_id, "approvals")
    requests = []
    requestable_circles = []
    with connect() as connection:
        for scope in scopes:
            if not scope["is_owner"] and "approvals" in scope["permissions"]:
                requestable_circles.append({
                    "id": scope["care_circle_id"],
                    "elder_user_id": scope["elder_user_id"],
                    "elder_name": scope["elder_name"]
                })
            if scope["is_owner"]:
                rows = connection.execute(
                    """
                    SELECT approval_requests.id, approval_requests.care_circle_id,
                           approval_requests.requested_by_user_id,
                           requester.name AS requester_name,
                           approval_requests.request_type, approval_requests.title,
                           approval_requests.details, approval_requests.status,
                           approval_requests.medication_id,
                           approval_requests.proposed_data_json,
                           approval_requests.decision_note,
                           approval_requests.created_at, approval_requests.decided_at
                    FROM approval_requests
                    JOIN users AS requester
                        ON requester.id = approval_requests.requested_by_user_id
                    WHERE approval_requests.care_circle_id = ?
                    ORDER BY approval_requests.created_at DESC
                    """,
                    (scope["care_circle_id"],)
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT approval_requests.id, approval_requests.care_circle_id,
                           approval_requests.requested_by_user_id,
                           requester.name AS requester_name,
                           approval_requests.request_type, approval_requests.title,
                           approval_requests.details, approval_requests.status,
                           approval_requests.medication_id,
                           approval_requests.proposed_data_json,
                           approval_requests.decision_note,
                           approval_requests.created_at, approval_requests.decided_at
                    FROM approval_requests
                    JOIN users AS requester
                        ON requester.id = approval_requests.requested_by_user_id
                    WHERE approval_requests.care_circle_id = ?
                      AND approval_requests.requested_by_user_id = ?
                    ORDER BY approval_requests.created_at DESC
                    """,
                    (scope["care_circle_id"], user_id)
                ).fetchall()
            requests.extend({
                **_approval_response(row),
                "elder_name": scope["elder_name"],
                "can_decide": scope["is_owner"]
            } for row in rows)
    return {
        "requests": requests,
        "requestable_circles": requestable_circles
    }


def create_approval_request(user_id: str, data: dict) -> dict | None:
    request_id = str(uuid.uuid4())
    now = int(time.time())
    with connect() as connection:
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, data["care_circle_id"]
        )
        if (
            scope is None
            or scope["is_owner"]
            or "approvals" not in scope["permissions"]
        ):
            return None
        medication_id = data.get("medication_id")
        proposed_changes = data.get("proposed_changes")
        proposed_json = None
        if data["request_type"] == "medication":
            if (
                medication_id is None
                or proposed_changes is None
                or not sqlite_repository.has_circle_permission(
                    scope, "medications"
                )
            ):
                return None
            medication = connection.execute(
                """
                SELECT elder_user_id FROM medications WHERE id = ?
                """,
                (medication_id,)
            ).fetchone()
            if (
                medication is None
                or medication["elder_user_id"] != scope["elder_user_id"]
            ):
                return None
            try:
                validated = MedicationFields.model_validate(proposed_changes)
            except ValidationError as error:
                raise ApprovalConflictError(
                    "The proposed medication changes are invalid."
                ) from error
            proposed_json = json.dumps(
                validated.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":")
            )
        elif medication_id is not None or proposed_changes is not None:
            return None
        connection.execute(
            """
            INSERT INTO approval_requests (
                id, care_circle_id, requested_by_user_id, medication_id,
                proposed_data_json, request_type, title, details, status,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                request_id,
                data["care_circle_id"],
                user_id,
                medication_id,
                proposed_json,
                data["request_type"],
                data["title"],
                data["details"],
                now
            )
        )
        sqlite_repository.record_timeline_event(
            connection,
            scope["elder_user_id"],
            user_id,
            "A care-circle approval was requested.",
            "approval_requested"
        )
        elder_user_id = scope["elder_user_id"]
        notification_service.insert_notification(
            connection,
            elder_user_id,
            "approval",
            "Approval requested",
            "A care-circle member submitted a request for your review.",
            data["care_circle_id"]
        )
    return get_approval_request(user_id, request_id)


def get_approval_request(user_id: str, request_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT approval_requests.id, approval_requests.care_circle_id,
                   approval_requests.requested_by_user_id,
                   requester.name AS requester_name,
                   approval_requests.request_type, approval_requests.title,
                   approval_requests.details, approval_requests.status,
                   approval_requests.medication_id,
                   approval_requests.proposed_data_json,
                   approval_requests.decision_note,
                   approval_requests.created_at, approval_requests.decided_at,
                   care_circles.owner_user_id AS elder_user_id,
                   owners.name AS elder_name
            FROM approval_requests
            JOIN users AS requester
                ON requester.id = approval_requests.requested_by_user_id
            JOIN care_circles
                ON care_circles.id = approval_requests.care_circle_id
            JOIN users AS owners ON owners.id = care_circles.owner_user_id
            WHERE approval_requests.id = ?
            """,
            (request_id,)
        ).fetchone()
        if row is None:
            return None
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, row["care_circle_id"]
        )
        if scope is None or (
            not scope["is_owner"]
            and (
                "approvals" not in scope["permissions"]
                or row["requested_by_user_id"] != user_id
            )
        ):
            return None
        return {
            **_approval_response(row),
            "can_decide": scope["is_owner"]
        }


def decide_approval_request(
    user_id: str,
    request_id: str,
    status: str,
    decision_note: str
) -> dict | None:
    now = int(time.time())
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT care_circle_id, requested_by_user_id, status,
                   request_type, medication_id, proposed_data_json
            FROM approval_requests WHERE id = ?
            """,
            (request_id,)
        ).fetchone()
        if row is None or row["status"] != "pending":
            return None
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, row["care_circle_id"]
        )
        if scope is None or not scope["is_owner"]:
            return None
        medication_update = None
        if status == "approved" and row["request_type"] == "medication":
            if row["medication_id"] is None or row["proposed_data_json"] is None:
                raise ApprovalConflictError(
                    "The medication no longer exists or the proposal is incomplete."
                )
            medication = connection.execute(
                """
                SELECT elder_user_id FROM medications WHERE id = ?
                """,
                (row["medication_id"],)
            ).fetchone()
            if (
                medication is None
                or medication["elder_user_id"] != scope["elder_user_id"]
            ):
                raise ApprovalConflictError(
                    "The medication no longer belongs to this care circle."
                )
            try:
                validated = MedicationFields.model_validate(
                    json.loads(row["proposed_data_json"])
                )
            except (json.JSONDecodeError, ValidationError) as error:
                raise ApprovalConflictError(
                    "The proposed medication changes are invalid."
                ) from error
            medication_update = validated.model_dump(mode="json")

        cursor = connection.execute(
            """
            UPDATE approval_requests
            SET status = ?, decision_note = ?, decided_by_user_id = ?,
                decided_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, decision_note, user_id, now, request_id)
        )
        if cursor.rowcount != 1:
            return None
        if medication_update is not None:
            connection.execute(
                """
                UPDATE medications
                SET name = ?, dosage = ?, frequency = ?, start_date = ?,
                    end_date = ?, instructions = ?, updated_at = ?
                WHERE id = ? AND elder_user_id = ?
                """,
                (
                    medication_update["name"],
                    medication_update["dosage"],
                    medication_update["frequency"],
                    medication_update["start_date"],
                    medication_update["end_date"],
                    medication_update["instructions"],
                    now,
                    row["medication_id"],
                    scope["elder_user_id"]
                )
            )
            sqlite_repository.record_timeline_event(
                connection,
                scope["elder_user_id"],
                user_id,
                "A proposed medication change was applied after elder approval.",
                "medication_change_approved"
            )
        decision_description = (
            "A proposed medication change was approved."
            if status == "approved" and row["request_type"] == "medication"
            else "A proposed medication change was rejected."
            if status == "rejected" and row["request_type"] == "medication"
            else f"An approval request was {status}."
        )
        sqlite_repository.record_timeline_event(
            connection,
            scope["elder_user_id"],
            user_id,
            decision_description,
            "approval_decided"
        )
        requester_user_id = row["requested_by_user_id"]
        care_circle_id = row["care_circle_id"]
        if requester_user_id != user_id:
            notification_service.insert_notification(
                connection,
                requester_user_id,
                "approval",
                "Approval request updated",
                f"Your request was {status}.",
                care_circle_id
            )
    return get_approval_request(user_id, request_id)