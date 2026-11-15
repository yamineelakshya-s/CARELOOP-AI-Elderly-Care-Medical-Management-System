import time
import uuid

from app.database import connect


def insert_notification(
    connection,
    user_id: str,
    category: str,
    title: str,
    message: str,
    care_circle_id: str | None = None
) -> str:
    notification_id = str(uuid.uuid4())
    now = int(time.time())
    connection.execute(
        """
        INSERT INTO notifications (
            id, user_id, care_circle_id, category, title, message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            notification_id,
            user_id,
            care_circle_id,
            category,
            title,
            message,
            now
        )
    )
    return notification_id


def list_notifications(user_id: str, unread_only: bool = False) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, care_circle_id, category, title, message, created_at, read_at
            FROM notifications
            WHERE user_id = ? AND (? = 0 OR read_at IS NULL)
            ORDER BY created_at DESC, id DESC
            LIMIT 100
            """,
            (user_id, int(unread_only))
        ).fetchall()
    return [dict(row) for row in rows]


def mark_notification_read(user_id: str, notification_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id FROM notifications
            WHERE id = ? AND user_id = ?
            """,
            (notification_id, user_id)
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """
            UPDATE notifications SET read_at = COALESCE(read_at, ?)
            WHERE id = ? AND user_id = ?
            """,
            (int(time.time()), notification_id, user_id)
        )
        updated = connection.execute(
            """
            SELECT id, care_circle_id, category, title, message, created_at, read_at
            FROM notifications WHERE id = ? AND user_id = ?
            """,
            (notification_id, user_id)
        ).fetchone()
    return dict(updated)