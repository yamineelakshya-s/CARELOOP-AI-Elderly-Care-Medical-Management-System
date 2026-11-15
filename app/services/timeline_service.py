from datetime import datetime, timezone
import uuid

from app.database import connect
from app.repositories import sqlite_repository


def list_timeline(user_id: str) -> dict:
    scopes = sqlite_repository.get_user_circle_scopes(user_id, "activities")
    events = []
    manageable_circles = []
    with connect() as connection:
        for scope in scopes:
            if sqlite_repository.has_circle_permission(
                scope, "activities", manage=True
            ):
                manageable_circles.append({
                    "id": scope["care_circle_id"],
                    "elder_user_id": scope["elder_user_id"],
                    "elder_name": scope["elder_name"]
                })
            rows = connection.execute(
                """
                SELECT care_activities.id, care_activities.description,
                       care_activities.occurred_at, care_activities.event_type,
                       care_activities.actor_user_id, actor.name AS actor_name
                FROM care_activities
                LEFT JOIN users AS actor
                    ON actor.id = care_activities.actor_user_id
                WHERE care_activities.elder_user_id = ?
                ORDER BY care_activities.occurred_at DESC, care_activities.id DESC
                LIMIT 100
                """,
                (scope["elder_user_id"],)
            ).fetchall()
            events.extend({
                **dict(row),
                "care_circle_id": scope["care_circle_id"],
                "elder_name": scope["elder_name"]
            } for row in rows)
    events.sort(key=lambda event: (event["occurred_at"], event["id"]), reverse=True)
    return {
        "events": events[:100],
        "manageable_circles": manageable_circles
    }


def create_timeline_event(user_id: str, care_circle_id: str, description: str):
    with connect() as connection:
        scope = sqlite_repository.get_circle_scope(
            connection, user_id, care_circle_id
        )
        if not sqlite_repository.has_circle_permission(
            scope, "activities", manage=True
        ):
            return None
        connection.execute(
            """
            INSERT INTO care_activities (
                id, elder_user_id, description, occurred_at,
                actor_user_id, event_type
            ) VALUES (?, ?, ?, ?, ?, 'care_note')
            """,
            (
                str(uuid.uuid4()),
                scope["elder_user_id"],
                description,
                datetime.now(timezone.utc).isoformat(),
                user_id
            )
        )
    return True
