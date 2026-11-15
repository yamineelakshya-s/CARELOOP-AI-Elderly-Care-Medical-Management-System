import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from app.config import settings
from app.database import connect
from app.services import mailer_service, notification_service

logger = logging.getLogger(__name__)

UPCOMING_WINDOW_SECONDS = 300
EMAIL_CLAIM_LEASE_SECONDS = 60


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_is_due(event_time: datetime, now: datetime) -> bool:
    return now - timedelta(seconds=UPCOMING_WINDOW_SECONDS) <= event_time <= now


def _candidate_doses() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT doses.id AS dose_id, doses.scheduled_at, doses.status,
                   medications.elder_user_id, users.email AS elder_email,
                   care_circles.id AS care_circle_id
            FROM medication_doses AS doses
            JOIN medications ON medications.id = doses.medication_id
            JOIN users ON users.id = medications.elder_user_id
            JOIN care_circles ON care_circles.owner_user_id = users.id
            WHERE doses.status = 'Pending'
            ORDER BY doses.scheduled_at, doses.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _candidate_appointments() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT appointments.id AS appointment_id, appointments.starts_at,
                   appointments.elder_user_id,
                   users.email AS elder_email, care_circles.id AS care_circle_id
            FROM appointments
            JOIN users ON users.id = appointments.elder_user_id
            JOIN care_circles ON care_circles.owner_user_id = users.id
            ORDER BY appointments.starts_at, appointments.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _dose_is_pending(dose_id: str) -> bool:
    with connect() as connection:
        row = connection.execute(
            "SELECT status FROM medication_doses WHERE id = ?",
            (dose_id,)
        ).fetchone()
    return row is not None and row["status"] == "Pending"


def _authorized_circle_members(
    care_circle_id: str,
    permissions: set[str]
) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT users.id, users.email, care_circle_members.permissions_json
            FROM care_circle_members
            JOIN users ON users.id = care_circle_members.user_id
            WHERE care_circle_members.care_circle_id = ?
            ORDER BY users.id
            """,
            (care_circle_id,)
        ).fetchall()
    authorized = []
    for row in rows:
        member_permissions = set(json.loads(row["permissions_json"]))
        if any(
            permission in member_permissions
            or f"{permission}_manage" in member_permissions
            for permission in permissions
        ):
            authorized.append({
                "id": row["id"],
                "email": row["email"]
            })
    return authorized


def _claim_email_event(
    event_key: str,
    recipient: dict,
    event_type: str,
    now: int
) -> bool:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            """
            SELECT status, updated_at, attempt_count
            FROM email_notification_log
            WHERE event_key = ? AND recipient_user_id = ?
            """,
            (event_key, recipient["id"])
        ).fetchone()
        if previous is not None:
            if previous["status"] == "sent":
                return False
            if (
                previous["status"] == "sending"
                and now - previous["updated_at"] < EMAIL_CLAIM_LEASE_SECONDS
            ):
                return False
            attempts = previous["attempt_count"] + 1
            connection.execute(
                """
                UPDATE email_notification_log
                SET recipient_email = ?, event_type = ?, status = 'sending',
                    attempt_count = ?, last_error = NULL, updated_at = ?,
                    sent_at = NULL
                WHERE event_key = ? AND recipient_user_id = ?
                """,
                (
                    recipient.get("email") or "",
                    event_type,
                    attempts,
                    now,
                    event_key,
                    recipient["id"]
                )
            )
        else:
            connection.execute(
                """
                INSERT INTO email_notification_log (
                    event_key, recipient_user_id, recipient_email, event_type,
                    status, attempt_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'sending', 1, ?, ?)
                """,
                (
                    event_key,
                    recipient["id"],
                    recipient.get("email") or "",
                    event_type,
                    now,
                    now
                )
            )
    return True


def _finish_email_event(
    event_key: str,
    recipient_user_id: str,
    status: str,
    now: int,
    error_code: str | None = None
) -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE email_notification_log
            SET status = ?, last_error = ?, updated_at = ?,
                sent_at = CASE WHEN ? = 'sent' THEN ? ELSE NULL END
            WHERE event_key = ? AND recipient_user_id = ?
            """,
            (
                status,
                error_code,
                now,
                status,
                now,
                event_key,
                recipient_user_id
            )
        )


def _send_email(
    event_key: str,
    recipient: dict,
    event_type: str,
    subject: str,
    text_content: str,
    now: int
) -> str:
    if not _claim_email_event(event_key, recipient, event_type, now):
        return "skipped"

    if (
        not recipient.get("email")
        or settings.brevo_api_key is None
        or not settings.sender_email
    ):
        _finish_email_event(
            event_key,
            recipient["id"],
            "skipped",
            now,
            "missing_recipient_or_brevo_configuration"
        )
        logger.warning(
            "Email event skipped because recipient or Brevo configuration is missing."
        )
        return "skipped"

    try:
        mailer_service.send_transactional_email(
            recipient["email"],
            subject,
            text_content
        )
    except HTTPError:
        _finish_email_event(
            event_key, recipient["id"], "failed", now, "brevo_http_error"
        )
        logger.warning("Brevo email attempt failed with an HTTP error.")
        return "failed"
    except (URLError, TimeoutError, OSError):
        _finish_email_event(
            event_key, recipient["id"], "failed", now, "network_error"
        )
        logger.warning("Brevo email attempt failed because of a network error.")
        return "failed"
    except (RuntimeError, ValueError):
        _finish_email_event(
            event_key, recipient["id"], "failed", now, "mailer_configuration_error"
        )
        logger.warning("Brevo email attempt failed because configuration is invalid.")
        return "failed"

    _finish_email_event(event_key, recipient["id"], "sent", now)
    return "sent"


def _create_overdue_alert(dose: dict, now: int) -> tuple[bool, list[dict]]:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current_dose = connection.execute(
            """
            SELECT doses.status, medications.elder_user_id
            FROM medication_doses AS doses
            JOIN medications ON medications.id = doses.medication_id
            WHERE doses.id = ? AND medications.elder_user_id = ?
            """,
            (dose["dose_id"], dose["elder_user_id"])
        ).fetchone()
        if current_dose is None or current_dose["status"] != "Pending":
            return False, []
        existing = connection.execute(
            "SELECT id FROM relay_items WHERE medication_dose_id = ?",
            (dose["dose_id"],)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO relay_items (
                    id, care_circle_id, medication_dose_id, created_by_user_id,
                    title, body, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    dose["care_circle_id"],
                    dose["dose_id"],
                    dose["elder_user_id"],
                    "Unconfirmed medication dose",
                    (
                        "A scheduled dose remains unconfirmed and may not "
                        "have been taken. Please check in with the elder."
                    ),
                    now,
                    now
                )
            )
            connection.execute(
                """
                INSERT INTO care_activities (
                    id, elder_user_id, description, occurred_at,
                    actor_user_id, event_type
                ) VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (
                    str(uuid.uuid4()),
                    dose["elder_user_id"],
                    (
                        "A scheduled medication dose remains unconfirmed "
                        "and may not have been taken."
                    ),
                    datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    "medication_dose_unconfirmed_alert"
                )
            )
            members = connection.execute(
                """
                SELECT users.id, care_circle_members.permissions_json
                FROM care_circle_members
                JOIN users ON users.id = care_circle_members.user_id
                WHERE care_circle_members.care_circle_id = ?
                """,
                (dose["care_circle_id"],)
            ).fetchall()
            for member in members:
                permissions = set(json.loads(member["permissions_json"]))
                if permissions.intersection({
                    "medications", "medications_manage", "relay", "relay_manage"
                }):
                    notification_service.insert_notification(
                        connection,
                        member["id"],
                        "medication",
                        "Unconfirmed dose",
                        (
                            "A scheduled dose is unconfirmed and may not "
                            "have been taken. Please check the care relay."
                        ),
                        dose["care_circle_id"]
                    )
            created = True
        else:
            created = False

        members = connection.execute(
            """
            SELECT users.id, users.email, care_circle_members.permissions_json
            FROM care_circle_members
            JOIN users ON users.id = care_circle_members.user_id
            WHERE care_circle_members.care_circle_id = ?
            ORDER BY users.id
            """,
            (dose["care_circle_id"],)
        ).fetchall()
        recipients = []
        for member in members:
            permissions = set(json.loads(member["permissions_json"]))
            if permissions.intersection({
                "medications", "medications_manage", "relay", "relay_manage"
            }):
                recipients.append({
                    "id": member["id"],
                    "email": member["email"]
                })
    return created, recipients


def _appointment_recipients(appointment: dict) -> list[dict]:
    recipients = [{
        "id": appointment["elder_user_id"],
        "email": appointment["elder_email"]
    }]
    recipients.extend(_authorized_circle_members(
        appointment["care_circle_id"],
        {"appointments"}
    ))
    unique = {}
    for recipient in recipients:
        unique[recipient["id"]] = recipient
    return list(unique.values())


def process_tick(now: int | None = None) -> dict:
    now_epoch = int(time.time()) if now is None else int(now)
    now_datetime = datetime.fromtimestamp(now_epoch, timezone.utc)
    result = {
        "processed_events": 0,
        "emails_sent": 0,
        "failed_attempts": 0,
        "skipped_events": 0,
        "alerts_created": 0
    }

    for dose in _candidate_doses():
        scheduled_at = _utc_datetime(dose["scheduled_at"])
        reminder_at = scheduled_at - timedelta(minutes=10)
        overdue_at = scheduled_at + timedelta(minutes=15)
        if (
            _event_is_due(reminder_at, now_datetime)
            and _dose_is_pending(dose["dose_id"])
        ):
            result["processed_events"] += 1
            event_key = f"medication-dose-upcoming:{dose['dose_id']}"
            status = _send_email(
                event_key,
                {"id": dose["elder_user_id"], "email": dose["elder_email"]},
                "medication_dose_upcoming",
                "Upcoming medication dose",
                (
                    "A medication dose is scheduled in about 10 minutes. "
                    f"Review your schedule at {settings.app_base_url}/."
                ),
                now_epoch
            )
            result["emails_sent"] += status == "sent"
            result["failed_attempts"] += status == "failed"
            result["skipped_events"] += status == "skipped"
        if scheduled_at <= now_datetime - timedelta(minutes=15):
            result["processed_events"] += 1
            created, recipients = _create_overdue_alert(dose, now_epoch)
            result["alerts_created"] += created
            for recipient in recipients:
                status = _send_email(
                    f"medication-dose-unconfirmed:{dose['dose_id']}",
                    recipient,
                    "medication_dose_unconfirmed",
                    "Unconfirmed medication dose",
                    (
                        "A scheduled medication dose is unconfirmed and may "
                        "not have been taken. Please check in with the elder "
                        "and review the care relay."
                    ),
                    now_epoch
                )
                result["emails_sent"] += status == "sent"
                result["failed_attempts"] += status == "failed"
                result["skipped_events"] += status == "skipped"

    for appointment in _candidate_appointments():
        starts_at = _utc_datetime(appointment["starts_at"])
        reminder_at = starts_at - timedelta(hours=24)
        if not _event_is_due(reminder_at, now_datetime):
            continue
        result["processed_events"] += 1
        for recipient in _appointment_recipients(appointment):
            status = _send_email(
                (
                    f"appointment-upcoming:{appointment['appointment_id']}:"
                    f"{appointment['starts_at']}"
                ),
                recipient,
                "appointment_upcoming",
                "Upcoming care appointment",
                (
                    "A care appointment is scheduled in about 24 hours. "
                    f"Review your schedule at {settings.app_base_url}/."
                ),
                now_epoch
            )
            result["emails_sent"] += status == "sent"
            result["failed_attempts"] += status == "failed"
            result["skipped_events"] += status == "skipped"

    return result
