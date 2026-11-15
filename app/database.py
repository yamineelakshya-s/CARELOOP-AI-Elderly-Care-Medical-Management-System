import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
    role TEXT NOT NULL CHECK (role IN ('elder', 'caregiver', 'family')),
    is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
    email TEXT,
    password_hash TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS care_circles (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS care_circle_members (
    care_circle_id TEXT NOT NULL REFERENCES care_circles(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permissions_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (care_circle_id, user_id)
);

CREATE TABLE IF NOT EXISTS invitations (
    code_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('elder', 'caregiver', 'family')),
    care_circle_id TEXT REFERENCES care_circles(id) ON DELETE CASCADE,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_codes (
    code_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS medications (
    id TEXT PRIMARY KEY,
    elder_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    dosage TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT,
    instructions TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS medication_doses (
    id TEXT PRIMARY KEY,
    medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Taken', 'Missed', 'Pending')),
    updated_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS medication_dose_history (
    id TEXT PRIMARY KEY,
    dose_id TEXT NOT NULL REFERENCES medication_doses(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('Taken', 'Missed', 'Pending')),
    changed_by_user_id TEXT NOT NULL REFERENCES users(id),
    changed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    elder_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    starts_at TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS care_activities (
    id TEXT PRIMARY KEY,
    elder_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    actor_user_id TEXT REFERENCES users(id),
    event_type TEXT NOT NULL DEFAULT 'care_note'
);

CREATE TABLE IF NOT EXISTS relay_items (
    id TEXT PRIMARY KEY,
    care_circle_id TEXT NOT NULL REFERENCES care_circles(id) ON DELETE CASCADE,
    medication_dose_id TEXT REFERENCES medication_doses(id) ON DELETE SET NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    assigned_to_user_id TEXT REFERENCES users(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (
        status IN ('open', 'claimed', 'contacted', 'resolved', 'completed')
    ),
    claimed_by_user_id TEXT REFERENCES users(id),
    claimed_at INTEGER,
    contacted_at INTEGER,
    resolved_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    care_circle_id TEXT NOT NULL REFERENCES care_circles(id) ON DELETE CASCADE,
    requested_by_user_id TEXT NOT NULL REFERENCES users(id),
    medication_id TEXT REFERENCES medications(id) ON DELETE SET NULL,
    proposed_data_json TEXT,
    request_type TEXT NOT NULL CHECK (
        request_type IN ('medication', 'appointment', 'other')
    ),
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'approved', 'rejected')
    ),
    decision_note TEXT NOT NULL DEFAULT '',
    decided_by_user_id TEXT REFERENCES users(id),
    created_at INTEGER NOT NULL,
    decided_at INTEGER
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    care_circle_id TEXT REFERENCES care_circles(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    read_at INTEGER
);

CREATE TABLE IF NOT EXISTS email_notification_log (
    event_key TEXT NOT NULL,
    recipient_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_email TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('sending', 'sent', 'failed', 'skipped')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    sent_at INTEGER,
    PRIMARY KEY (event_key, recipient_user_id)
);

CREATE INDEX IF NOT EXISTS idx_circle_members_user
    ON care_circle_members(user_id);
CREATE INDEX IF NOT EXISTS idx_medications_elder
    ON medications(elder_user_id);
CREATE INDEX IF NOT EXISTS idx_medication_doses_medication
    ON medication_doses(medication_id, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_medication_dose_history_dose
    ON medication_dose_history(dose_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_appointments_elder
    ON appointments(elder_user_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_activities_elder
    ON care_activities(elder_user_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_relay_items_circle
    ON relay_items(care_circle_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_approval_requests_circle
    ON approval_requests(care_circle_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications(user_id, read_at, created_at);
CREATE INDEX IF NOT EXISTS idx_email_notification_status
    ON email_notification_log(status, updated_at);
"""

DEMO_ACCOUNTS = (
    ("demo-lakshmi", "Lakshmi", "elder", "LAKSHMI1"),
    ("demo-meena", "Meena", "caregiver", "MEENA001"),
    ("demo-arjun", "Arjun", "family", "FAMILY01"),
)
DEMO_MEMBER_PERMISSIONS = {
    "demo-meena": ("activities_manage", "appointments", "medications"),
    "demo-arjun": ("activities", "appointments", "medications")
}


@contextmanager
def connect(database_path: str | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(database_path or settings.database_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(database_path: str | None = None) -> None:
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        _migrate_auth_credentials(connection)
        _migrate_medications(connection)
        _migrate_care_activities(connection)
        _migrate_tick_and_relay(connection)
        _migrate_relay_workflow(connection)
        _migrate_approval_proposals(connection)
        if settings.enable_demo_accounts:
            _seed_demo_accounts(connection)


def _migrate_auth_credentials(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(users)")
    }
    if "email" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "password_hash" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
        ON users(email COLLATE NOCASE)
        WHERE email IS NOT NULL AND email <> ''
        """
    )


def _migrate_medications(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(medications)")
    }
    additions = {
        "frequency": "TEXT NOT NULL DEFAULT ''",
        "start_date": "TEXT NOT NULL DEFAULT ''",
        "end_date": "TEXT",
        "updated_at": "INTEGER NOT NULL DEFAULT 0"
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(
                f"ALTER TABLE medications ADD COLUMN {column} {definition}"
            )
    if "schedule" in columns:
        connection.execute(
            """
            UPDATE medications
            SET frequency = schedule
            WHERE frequency = '' AND schedule <> ''
            """
        )


def _migrate_care_activities(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(care_activities)")
    }
    if "actor_user_id" not in columns:
        connection.execute(
            "ALTER TABLE care_activities ADD COLUMN actor_user_id TEXT"
        )
    if "event_type" not in columns:
        connection.execute(
            """
            ALTER TABLE care_activities
            ADD COLUMN event_type TEXT NOT NULL DEFAULT 'care_note'
            """
        )


def _migrate_tick_and_relay(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(relay_items)")
    }
    if "medication_dose_id" not in columns:
        connection.execute(
            """
            ALTER TABLE relay_items
            ADD COLUMN medication_dose_id TEXT
            REFERENCES medication_doses(id) ON DELETE SET NULL
            """
        )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_items_medication_dose
        ON relay_items(medication_dose_id)
        WHERE medication_dose_id IS NOT NULL
        """
    )


def _migrate_relay_workflow(connection: sqlite3.Connection) -> None:
    definition = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'relay_items'
        """
    ).fetchone()["sql"]
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(relay_items)")
    }
    if (
        "claimed" not in definition
        or not {
            "claimed_by_user_id",
            "claimed_at",
            "contacted_at",
            "resolved_at"
        }.issubset(columns)
    ):
        connection.execute(
            """
            CREATE TABLE relay_items_new (
                id TEXT PRIMARY KEY,
                care_circle_id TEXT NOT NULL
                    REFERENCES care_circles(id) ON DELETE CASCADE,
                medication_dose_id TEXT
                    REFERENCES medication_doses(id) ON DELETE SET NULL,
                created_by_user_id TEXT NOT NULL REFERENCES users(id),
                assigned_to_user_id TEXT REFERENCES users(id),
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (
                    status IN (
                        'open', 'claimed', 'contacted', 'resolved', 'completed'
                    )
                ),
                claimed_by_user_id TEXT REFERENCES users(id),
                claimed_at INTEGER,
                contacted_at INTEGER,
                resolved_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        select = {
            "medication_dose_id": (
                "medication_dose_id"
                if "medication_dose_id" in columns else "NULL"
            ),
            "claimed_by_user_id": (
                "claimed_by_user_id"
                if "claimed_by_user_id" in columns else "NULL"
            ),
            "claimed_at": "claimed_at" if "claimed_at" in columns else "NULL",
            "contacted_at": (
                "contacted_at" if "contacted_at" in columns else "NULL"
            ),
            "resolved_at": (
                "resolved_at" if "resolved_at" in columns else "NULL"
            )
        }
        connection.execute(
            f"""
            INSERT INTO relay_items_new (
                id, care_circle_id, medication_dose_id, created_by_user_id,
                assigned_to_user_id, title, body, status, claimed_by_user_id,
                claimed_at, contacted_at, resolved_at, created_at, updated_at
            )
            SELECT id, care_circle_id, {select["medication_dose_id"]},
                   created_by_user_id, assigned_to_user_id, title, body, status,
                   {select["claimed_by_user_id"]}, {select["claimed_at"]},
                   {select["contacted_at"]}, {select["resolved_at"]},
                   created_at, updated_at
            FROM relay_items
            """
        )
        connection.execute("DROP TABLE relay_items")
        connection.execute("ALTER TABLE relay_items_new RENAME TO relay_items")

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_relay_items_circle
        ON relay_items(care_circle_id, status, created_at)
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_items_medication_dose
        ON relay_items(medication_dose_id)
        WHERE medication_dose_id IS NOT NULL
        """
    )


def _migrate_approval_proposals(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(approval_requests)")
    }
    if "medication_id" not in columns:
        connection.execute(
            """
            ALTER TABLE approval_requests
            ADD COLUMN medication_id TEXT REFERENCES medications(id) ON DELETE SET NULL
            """
        )
    if "proposed_data_json" not in columns:
        connection.execute(
            "ALTER TABLE approval_requests ADD COLUMN proposed_data_json TEXT"
        )


def _seed_demo_accounts(connection: sqlite3.Connection) -> None:
    import hashlib
    import hmac
    import time
    import uuid

    for user_id, name, role, code in DEMO_ACCOUNTS:
        connection.execute(
            """
            INSERT OR IGNORE INTO users (id, name, role, is_demo, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (user_id, name, role, int(time.time()))
        )
        user = connection.execute(
            "SELECT is_demo, role FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if (
            user is None
            or not user["is_demo"]
            or user["role"] != role
        ):
            continue
        if role == "elder":
            connection.execute(
                """
                INSERT OR IGNORE INTO care_circles (id, owner_user_id, name)
                VALUES (?, ?, ?)
                """,
                (str(uuid.uuid4()), user_id, "Care circle")
            )
        code_hash = hmac.new(
            settings.session_secret.encode("utf-8"),
            code.encode("ascii"),
            hashlib.sha256
        ).hexdigest()
        connection.execute(
            "INSERT OR IGNORE INTO demo_codes (code_hash, user_id) VALUES (?, ?)",
            (code_hash, user_id)
        )

    elder_id = "demo-lakshmi"
    elder = connection.execute(
        "SELECT is_demo FROM users WHERE id = ? AND role = 'elder'",
        (elder_id,)
    ).fetchone()
    circle = connection.execute(
        "SELECT id FROM care_circles WHERE owner_user_id = ?",
        (elder_id,)
    ).fetchone()
    if elder is None or not elder["is_demo"] or circle is None:
        return

    now = datetime.now(timezone.utc)
    now_timestamp = int(now.timestamp())
    circle_id = circle["id"]
    for member_id, expected_role in (
        ("demo-meena", "caregiver"),
        ("demo-arjun", "family")
    ):
        member = connection.execute(
            "SELECT is_demo, role FROM users WHERE id = ?",
            (member_id,)
        ).fetchone()
        if (
            member is None
            or not member["is_demo"]
            or member["role"] != expected_role
        ):
            continue
        permissions_json = json.dumps(
            DEMO_MEMBER_PERMISSIONS[member_id]
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO care_circle_members (
                care_circle_id, user_id, permissions_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (circle_id, member_id, permissions_json, now_timestamp)
        )

    medication_id = "demo-lakshmi-sample-medication"
    connection.execute(
        """
        INSERT OR IGNORE INTO medications (
            id, elder_user_id, name, dosage, frequency, start_date,
            instructions, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            medication_id,
            elder_id,
            "DEMO: Sample medication",
            "Synthetic sample only",
            "Synthetic demo schedule",
            now.date().isoformat(),
            "Synthetic demo data; not medical advice.",
            now_timestamp,
            now_timestamp
        )
    )

    dose_id = "demo-lakshmi-sample-dose"
    dose_at = (now - timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0
    )
    existing_dose = connection.execute(
        "SELECT id FROM medication_doses WHERE id = ?",
        (dose_id,)
    ).fetchone()
    if existing_dose is None:
        connection.execute(
            """
            INSERT INTO medication_doses (
                id, medication_id, scheduled_at, status,
                updated_by_user_id, created_at, updated_at
            ) VALUES (?, ?, ?, 'Taken', ?, ?, ?)
            """,
            (
                dose_id,
                medication_id,
                dose_at.isoformat(),
                elder_id,
                now_timestamp,
                now_timestamp
            )
        )
        connection.execute(
            """
            INSERT INTO medication_dose_history (
                id, dose_id, status, changed_by_user_id, changed_at
            ) VALUES (?, ?, 'Taken', ?, ?)
            """,
            (
                "demo-lakshmi-sample-dose-history",
                dose_id,
                elder_id,
                now_timestamp
            )
        )

    appointment_id = "demo-lakshmi-sample-appointment"
    upcoming_at = (now + timedelta(days=7)).replace(
        minute=0, second=0, microsecond=0
    )
    existing_appointment = connection.execute(
        "SELECT starts_at FROM appointments WHERE id = ?",
        (appointment_id,)
    ).fetchone()
    if existing_appointment is None:
        connection.execute(
            """
            INSERT INTO appointments (
                id, elder_user_id, title, provider, starts_at, location
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                elder_id,
                "DEMO: Sample appointment",
                "Demo provider",
                upcoming_at.isoformat(),
                "Demo location"
            )
        )
    else:
        try:
            existing_at = datetime.fromisoformat(existing_appointment["starts_at"])
            if existing_at.tzinfo is None:
                existing_at = existing_at.replace(tzinfo=timezone.utc)
            else:
                existing_at = existing_at.astimezone(timezone.utc)
        except ValueError:
            existing_at = datetime.min.replace(tzinfo=timezone.utc)
        if existing_at <= now:
            connection.execute(
                "UPDATE appointments SET starts_at = ? WHERE id = ?",
                (upcoming_at.isoformat(), appointment_id)
            )

    caregiver = connection.execute(
        """
        SELECT id FROM users
        WHERE id = 'demo-meena' AND role = 'caregiver' AND is_demo = 1
        """
    ).fetchone()
    activity_actor_id = caregiver["id"] if caregiver else elder_id
    connection.execute(
        """
        INSERT OR IGNORE INTO care_activities (
            id, elder_user_id, description, occurred_at, actor_user_id, event_type
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "demo-lakshmi-sample-activity",
            elder_id,
            "DEMO: Meena shared a sample care-circle update (synthetic only).",
            now.isoformat(),
            activity_actor_id,
            "demo_seed"
        )
    )
