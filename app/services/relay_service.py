import time
import uuid

from app.database import connect
from app.repositories import sqlite_repository
from app.services import notification_service


class RelayConflictError(ValueError):
    pass


def _members_for_circle(connection, care_circle_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT users.id, users.name, users.role
        FROM users
        JOIN care_circles ON care_circles.owner_user_id = users.id
        WHERE care_circles.id = ?
        UNION
        SELECT users.id, users.name, users.role
        FROM care_circle_members
        JOIN users ON users.id = care_circle_members.user_id
        WHERE care_circle_members.care_circle_id = ?
        ORDER BY name
        """,
        (care_circle_id, care_circle_id)
    ).fetchall()
    return [
        dict(row)
        for row in rows
        if sqlite_repository.has_circle_permission(
            sqlite_repository.get_circle_scope(
                connection, row["id"], care_circle_id
            ),
            "relay"
        )
    ]


def list_relay_items(user_id: str) -> dict:
    scopes_by_id = {}
    for permission in ("relay", "medications"):
        for scope in sqlite_repository.get_user_circle_scopes(
            user_id, permission
        ):
            scopes_by_id[scope["care_circle_id"]] = scope
    scopes = list(scopes_by_id.values())
    items = []
    manageable_circles = []
    with connect() as connection:
        for scope in scopes:
            can_view_relay = sqlite_repository.has_circle_permission(
                scope, "relay"
            )
            can_view_medications = sqlite_repository.has_circle_permission(
                scope, "medications"
            )
            if not can_view_relay and not can_view_medications:
                continue
            can_manage = sqlite_repository.has_circle_permission(
                scope, "relay", manage=True
            )
            if can_manage:
                manageable_circles.append({
                    "id": scope["care_circle_id"],
                    "elder_user_id": scope["elder_user_id"],
                    "elder_name": scope["elder_name"],
                    "members": _members_for_circle(
                        connection, scope["care_circle_id"]
                    )
                })
            rows = connection.execute(
                """
                SELECT relay_items.id, relay_items.care_circle_id,
                       relay_items.created_by_user_id, creator.name AS creator_name,
                       relay_items.assigned_to_user_id, assignee.name AS assignee_name,
                       relay_items.title, relay_items.body, relay_items.status,
                       relay_items.medication_dose_id,
                       relay_items.claimed_by_user_id, claimant.name AS claimant_name,
                       relay_items.claimed_at, relay_items.contacted_at,
                       relay_items.resolved_at, relay_items.created_at,
                       relay_items.updated_at
                FROM relay_items
                JOIN users AS creator
                    ON creator.id = relay_items.created_by_user_id
                LEFT JOIN users AS assignee
                    ON assignee.id = relay_items.assigned_to_user_id
                LEFT JOIN users AS claimant
                    ON claimant.id = relay_items.claimed_by_user_id
                WHERE relay_items.care_circle_id = ?
                  AND (? = 1 OR relay_items.medication_dose_id IS NOT NULL)
                ORDER BY relay_items.created_at DESC, relay_items.id DESC
                """,
                (scope["care_circle_id"], int(can_view_relay))
            ).fetchall()
            items.extend({
                **dict(row),
                "elder_name": scope["elder_name"],
                "can_manage": can_manage,
                "can_claim": (
                    can_manage
                    and row["medication_dose_id"] is not None
                    and row["status"] == "open"
                )
            } for row in rows)
    return {"items": items, "manageable_circles": manageable_circles}


def _get_relay_item(connection, user_id: str, relay_id: str) -> dict | None:
    row = connection.execute(
        """
        SELECT relay_items.id, relay_items.care_circle_id,
               relay_items.created_by_user_id, creator.name AS creator_name,
               relay_items.assigned_to_user_id, assignee.name AS assignee_name,
               relay_items.title, relay_items.body, relay_items.status,
               relay_items.medication_dose_id,
               relay_items.claimed_by_user_id, claimant.name AS claimant_name,
               relay_items.claimed_at, relay_items.contacted_at,
               relay_items.resolved_at, relay_items.created_at,
               relay_items.updated_at,
               owners.name AS elder_name
        FROM relay_items
        JOIN care_circles ON care_circles.id = relay_items.care_circle_id
        JOIN users AS owners ON owners.id = care_circles.owner_user_id
        JOIN users AS creator ON creator.id = relay_items.created_by_user_id
        LEFT JOIN users AS assignee ON assignee.id = relay_items.assigned_to_user_id
        LEFT JOIN users AS claimant ON claimant.id = relay_items.claimed_by_user_id
        WHERE relay_items.id = ?
        """,
        (relay_id,)
    ).fetchone()
    if row is None:
        return None
    scope = sqlite_repository.get_circle_scope(
        connection, user_id, row["care_circle_id"]
    )
    can_view_relay = sqlite_repository.has_circle_permission(scope, "relay")
    can_view_medication_alert = (
        row["medication_dose_id"] is not None
        and sqlite_repository.has_circle_permission(scope, "medications")
    )
    if not can_view_relay and not can_view_medication_alert:
        return None
    can_manage = sqlite_repository.has_circle_permission(
        scope, "relay", manage=True
    )
    return {
        **dict(row),
        "can_manage": can_manage,
        "can_claim": (
            can_manage
            and row["medication_dose_id"] is not None
            and row["status"] == "open"
        )
    }


def get_relay_item(user_id: str, relay_id: str) -> dict | None:
    with connect() as connection:
        return _get_relay_item(connection, user_id, relay_id)


def _assignee_is_circle_member(
    connection,
    care_circle_id: str,
    assignee_user_id: str | None
) -> bool:
    if assignee_user_id is None:
        return True
    scope = sqlite_repository.get_circle_scope(
        connection, assignee_user_id, care_circle_id
    )
    return sqlite_repository.has_circle_permission(scope, "relay")


def create_relay_item(user_id: str, data: dict) -> dict | None:
    relay_id = str(uuid.uuid4())
    now = int(time.time())
    with connect() as connection:
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, data["care_circle_id"]
        )
        if not sqlite_repository.has_circle_permission(
            scope, "relay", manage=True
        ):
            return None
        if not _assignee_is_circle_member(
            connection,
            data["care_circle_id"],
            data["assigned_to_user_id"]
        ):
            return None
        connection.execute(
            """
            INSERT INTO relay_items (
                id, care_circle_id, created_by_user_id, assigned_to_user_id,
                title, body, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                relay_id, data["care_circle_id"], user_id,
                data["assigned_to_user_id"], data["title"], data["body"],
                now, now
            )
        )
        sqlite_repository.record_timeline_event(
            connection,
            scope["elder_user_id"],
            user_id,
            "A caregiver relay item was added.",
            "relay_created"
        )
        elder_user_id = scope["elder_user_id"]
        if elder_user_id != user_id:
            notification_service.insert_notification(
                connection,
                elder_user_id,
                "relay",
                "Caregiver relay updated",
                "A care-circle member added a relay item.",
                data["care_circle_id"]
            )
    return get_relay_item(user_id, relay_id)


def update_relay_item(
    user_id: str,
    relay_id: str,
    data: dict
) -> dict | None:
    now = int(time.time())
    with connect() as connection:
        row = connection.execute(
            """
            SELECT care_circle_id, created_by_user_id, medication_dose_id
            FROM relay_items WHERE id = ?
            """,
            (relay_id,)
        ).fetchone()
        if row is None or row["medication_dose_id"] is not None:
            return None
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, row["care_circle_id"]
        )
        if not sqlite_repository.has_circle_permission(
            scope, "relay", manage=True
        ):
            return None
        if not _assignee_is_circle_member(
            connection, row["care_circle_id"], data["assigned_to_user_id"]
        ):
            return None
        connection.execute(
            """
            UPDATE relay_items
            SET title = ?, body = ?, assigned_to_user_id = ?,
                status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                data["title"], data["body"], data["assigned_to_user_id"],
                data["status"], now, relay_id
            )
        )
        sqlite_repository.record_timeline_event(
            connection,
            scope["elder_user_id"],
            user_id,
            "A caregiver relay item was updated.",
            "relay_updated"
        )
        notify_user_id = row["created_by_user_id"]
        circle_id = row["care_circle_id"]
        if notify_user_id != user_id:
            notification_service.insert_notification(
                connection,
                notify_user_id,
                "relay",
                "Caregiver relay updated",
                "A care-circle member updated a relay item.",
                circle_id
            )
    return get_relay_item(user_id, relay_id)


def delete_relay_item(user_id: str, relay_id: str) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT relay_items.care_circle_id,
                   relay_items.created_by_user_id,
                   relay_items.medication_dose_id,
                   care_circles.owner_user_id AS elder_user_id
            FROM relay_items
            JOIN care_circles ON care_circles.id = relay_items.care_circle_id
            WHERE relay_items.id = ?
            """,
            (relay_id,)
        ).fetchone()
        if row is None or row["medication_dose_id"] is not None:
            return False
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, row["care_circle_id"]
        )
        if not sqlite_repository.has_circle_permission(
            scope, "relay", manage=True
        ):
            return False
        connection.execute("DELETE FROM relay_items WHERE id = ?", (relay_id,))
        sqlite_repository.record_timeline_event(
            connection,
            row["elder_user_id"],
            user_id,
            "A caregiver relay item was removed.",
            "relay_deleted"
        )
    return True


def _transition_dose_alert(
    user_id: str,
    relay_id: str,
    action: str
) -> dict | None:
    now = int(time.time())
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT relay_items.care_circle_id, relay_items.medication_dose_id,
                   relay_items.status, relay_items.claimed_by_user_id,
                   care_circles.owner_user_id AS elder_user_id
            FROM relay_items
            JOIN care_circles ON care_circles.id = relay_items.care_circle_id
            WHERE relay_items.id = ?
            """,
            (relay_id,)
        ).fetchone()
        if row is None or row["medication_dose_id"] is None:
            return None

        scope = sqlite_repository.get_circle_scope(
            connection, user_id, row["care_circle_id"]
        )
        if not sqlite_repository.has_circle_permission(
            scope, "relay", manage=True
        ):
            return None

        allowed_statuses = {
            "claim": {"open"},
            "contact": {"claimed"},
            "resolve": {"claimed", "contacted"},
            "reopen": {"resolved"}
        }
        if row["status"] not in allowed_statuses[action]:
            raise RelayConflictError(
                "This dose alert has already changed state."
            )
        if action in {"contact", "resolve"} and (
            row["claimed_by_user_id"] != user_id
            and not scope["is_owner"]
        ):
            raise RelayConflictError(
                "Only the assigned caregiver or elder can update this alert."
            )

        updates = {
            "claim": (
                "status = 'claimed', claimed_by_user_id = ?, claimed_at = ?, "
                "contacted_at = NULL, resolved_at = NULL",
                (user_id, now)
            ),
            "contact": (
                "status = 'contacted', contacted_at = ?",
                (now,)
            ),
            "resolve": (
                "status = 'resolved', resolved_at = ?",
                (now,)
            ),
            "reopen": (
                "status = 'open', claimed_by_user_id = NULL, claimed_at = NULL, "
                "contacted_at = NULL, resolved_at = NULL",
                ()
            )
        }
        update_sql, update_values = updates[action]
        cursor = connection.execute(
            f"""
            UPDATE relay_items
            SET {update_sql}, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (*update_values, now, relay_id, row["status"])
        )
        if cursor.rowcount != 1:
            raise RelayConflictError(
                "This dose alert has already changed state."
            )

        event_details = {
            "claim": (
                "A caregiver claimed an unconfirmed dose alert.",
                "relay_alert_claimed"
            ),
            "contact": (
                "A care-circle member contacted the elder about an unconfirmed dose.",
                "relay_alert_contacted"
            ),
            "resolve": (
                "An unconfirmed dose follow-up was resolved.",
                "relay_alert_resolved"
            ),
            "reopen": (
                "An unconfirmed dose follow-up was reopened.",
                "relay_alert_reopened"
            )
        }
        description, event_type = event_details[action]
        sqlite_repository.record_timeline_event(
            connection,
            row["elder_user_id"],
            user_id,
            description,
            event_type
        )
        if user_id != row["elder_user_id"]:
            notification_service.insert_notification(
                connection,
                row["elder_user_id"],
                "relay",
                "Medication follow-up updated",
                "A care-circle member updated an unconfirmed dose follow-up.",
                row["care_circle_id"]
            )

        return _get_relay_item(connection, user_id, relay_id)


def claim_dose_alert(user_id: str, relay_id: str) -> dict | None:
    return _transition_dose_alert(user_id, relay_id, "claim")


def contact_dose_alert(user_id: str, relay_id: str) -> dict | None:
    return _transition_dose_alert(user_id, relay_id, "contact")


def resolve_dose_alert(user_id: str, relay_id: str) -> dict | None:
    return _transition_dose_alert(user_id, relay_id, "resolve")


def reopen_dose_alert(user_id: str, relay_id: str) -> dict | None:
    return _transition_dose_alert(user_id, relay_id, "reopen")