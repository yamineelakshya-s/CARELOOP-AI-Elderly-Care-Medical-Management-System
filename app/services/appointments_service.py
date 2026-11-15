import uuid

from app.database import connect
from app.repositories import sqlite_repository
from app.time_utils import normalize_utc


def list_appointments(user_id: str) -> dict:
    scopes = sqlite_repository.get_user_circle_scopes(user_id, "appointments")
    appointments = []
    manageable_circles = []
    with connect() as connection:
        for scope in scopes:
            can_manage = sqlite_repository.has_circle_permission(
                scope, "appointments", manage=True
            )
            if can_manage:
                manageable_circles.append({
                    "id": scope["care_circle_id"],
                    "elder_user_id": scope["elder_user_id"],
                    "elder_name": scope["elder_name"]
                })
            rows = connection.execute(
                """
                SELECT id, elder_user_id, title, provider, starts_at, location
                FROM appointments
                WHERE elder_user_id = ?
                ORDER BY starts_at ASC, id
                """,
                (scope["elder_user_id"],)
            ).fetchall()
            appointments.extend({
                **dict(row),
                "care_circle_id": scope["care_circle_id"],
                "elder_name": scope["elder_name"],
                "can_manage": can_manage
            } for row in rows)
    return {
        "appointments": appointments,
        "manageable_circles": manageable_circles
    }


def get_appointment(user_id: str, appointment_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT a.id, a.elder_user_id, a.title, a.provider,
                   a.starts_at, a.location, c.id AS care_circle_id,
                   owners.name AS elder_name
            FROM appointments AS a
            JOIN care_circles AS c ON c.owner_user_id = a.elder_user_id
            JOIN users AS owners ON owners.id = c.owner_user_id
            WHERE a.id = ?
            """,
            (appointment_id,)
        ).fetchone()
        if row is None:
            return None
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, row["care_circle_id"]
        )
        if not sqlite_repository.has_circle_permission(
            scope, "appointments"
        ):
            return None
        can_manage = sqlite_repository.has_circle_permission(
            scope, "appointments", manage=True
        )
        return {**dict(row), "can_manage": can_manage}


def create_appointment(user_id: str, data: dict) -> dict | None:
    appointment_id = str(uuid.uuid4())
    with connect() as connection:
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, data["care_circle_id"]
        )
        if not sqlite_repository.has_circle_permission(
            scope, "appointments", manage=True
        ):
            return None
        connection.execute(
            """
            INSERT INTO appointments (
                id, elder_user_id, title, provider, starts_at, location
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                scope["elder_user_id"],
                data["title"],
                data["provider"],
                normalize_utc(data["starts_at"]).isoformat(),
                data["location"]
            )
        )
        sqlite_repository.record_timeline_event(
            connection,
            scope["elder_user_id"],
            user_id,
            "An appointment was added.",
            "appointment_created"
        )
    return get_appointment(user_id, appointment_id)


def update_appointment(
    user_id: str,
    appointment_id: str,
    data: dict
) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT a.elder_user_id, c.id AS care_circle_id
            FROM appointments AS a
            JOIN care_circles AS c ON c.owner_user_id = a.elder_user_id
            WHERE a.id = ?
            """,
            (appointment_id,)
        ).fetchone()
        if row is None:
            return None
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, row["care_circle_id"]
        )
        if not sqlite_repository.has_circle_permission(
            scope, "appointments", manage=True
        ):
            return None
        connection.execute(
            """
            UPDATE appointments
            SET title = ?, provider = ?, starts_at = ?, location = ?
            WHERE id = ?
            """,
            (
                data["title"],
                data["provider"],
                normalize_utc(data["starts_at"]).isoformat(),
                data["location"],
                appointment_id
            )
        )
        sqlite_repository.record_timeline_event(
            connection,
            row["elder_user_id"],
            user_id,
            "An appointment was updated.",
            "appointment_updated"
        )
    return get_appointment(user_id, appointment_id)


def delete_appointment(user_id: str, appointment_id: str) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT a.elder_user_id, c.id AS care_circle_id
            FROM appointments AS a
            JOIN care_circles AS c ON c.owner_user_id = a.elder_user_id
            WHERE a.id = ?
            """,
            (appointment_id,)
        ).fetchone()
        if row is None:
            return False
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, row["care_circle_id"]
        )
        if not sqlite_repository.has_circle_permission(
            scope, "appointments", manage=True
        ):
            return False
        connection.execute(
            "DELETE FROM appointments WHERE id = ?",
            (appointment_id,)
        )
        sqlite_repository.record_timeline_event(
            connection,
            row["elder_user_id"],
            user_id,
            "An appointment was removed.",
            "appointment_deleted"
        )
    return True
