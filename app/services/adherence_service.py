from datetime import date, datetime, time, timedelta, timezone

from app.database import connect
from app.repositories import sqlite_repository


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def weekly_adherence(user_id: str, selected_date: date | None = None) -> dict:
    selected_date = selected_date or datetime.now(timezone.utc).date()
    week_start = selected_date - timedelta(days=selected_date.weekday())
    start = datetime.combine(week_start, time.min, timezone.utc)
    end = start + timedelta(days=7)
    scopes = sqlite_repository.get_user_circle_scopes(user_id, "medications")
    circles = []

    with connect() as connection:
        for scope in scopes:
            rows = connection.execute(
                """
                SELECT doses.scheduled_at, doses.status
                FROM medication_doses AS doses
                JOIN medications ON medications.id = doses.medication_id
                WHERE medications.elder_user_id = ?
                """,
                (scope["elder_user_id"],)
            ).fetchall()
            counts = {"taken": 0, "missed": 0, "pending": 0}
            for row in rows:
                scheduled_at = _as_utc(row["scheduled_at"])
                if not start <= scheduled_at < end:
                    continue
                counts[row["status"].lower()] += 1
            scheduled = sum(counts.values())
            circles.append({
                "care_circle_id": scope["care_circle_id"],
                "elder_name": scope["elder_name"],
                "taken": counts["taken"],
                "missed": counts["missed"],
                "pending": counts["pending"],
                "scheduled": scheduled,
                "adherence_percent": (
                    round(counts["taken"] / scheduled * 100, 1)
                    if scheduled else None
                )
            })

    totals = {
        key: sum(circle[key] for circle in circles)
        for key in ("taken", "missed", "pending", "scheduled")
    }
    return {
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "taken": totals["taken"],
        "missed": totals["missed"],
        "pending": totals["pending"],
        "scheduled": totals["scheduled"],
        "adherence_percent": (
            round(totals["taken"] / totals["scheduled"] * 100, 1)
            if totals["scheduled"] else None
        ),
        "care_circles": circles
    }
