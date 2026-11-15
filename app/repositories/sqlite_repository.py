import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from app.database import connect

ALL_PERMISSIONS = ("medications", "appointments", "activities")


class EmailAlreadyRegisteredError(ValueError):
    pass


def _user_record(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "is_demo": bool(row["is_demo"])
    }


def get_user(user_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, name, role, is_demo FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
    return _user_record(row)


def get_authentication_record(email: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, name, role, is_demo, password_hash
            FROM users
            WHERE email = ? COLLATE NOCASE AND is_demo = 0
            """,
            (email,)
        ).fetchone()
    if row is None:
        return None
    return {
        "user": _user_record(row),
        "password_hash": row["password_hash"]
    }


def has_user_credentials(user_id: str) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM users
            WHERE id = ? AND email IS NOT NULL AND password_hash IS NOT NULL
            """,
            (user_id,)
        ).fetchone()
    return row is not None


def set_user_credentials(user_id: str, email: str, password_hash: str) -> bool:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            "SELECT is_demo, email, password_hash FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if (
            user is None
            or user["is_demo"]
            or user["email"] is not None
            or user["password_hash"] is not None
        ):
            return False
        if connection.execute(
            "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
            (email,)
        ).fetchone():
            raise EmailAlreadyRegisteredError
        connection.execute(
            "UPDATE users SET email = ?, password_hash = ? WHERE id = ?",
            (email, password_hash, user_id)
        )
    return True


def list_users_for_credential_setup() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, name, role, is_demo, email, password_hash
            FROM users
            WHERE is_demo = 0
            ORDER BY name COLLATE NOCASE, id
            """
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "is_demo": bool(row["is_demo"]),
            "has_credentials": (
                row["email"] is not None and row["password_hash"] is not None
            )
        }
        for row in rows
    ]


def update_user_name(user_id: str, name: str) -> dict | None:
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE users SET name = ?
            WHERE id = ? AND is_demo = 0
            """,
            (name, user_id)
        )
        if cursor.rowcount != 1:
            return None
        row = connection.execute(
            "SELECT id, name, role, is_demo FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
    return _user_record(row)


def get_demo_user(code_hash: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT users.id, users.name, users.role, users.is_demo
            FROM demo_codes
            JOIN users ON users.id = demo_codes.user_id
            WHERE demo_codes.code_hash = ?
            """,
            (code_hash,)
        ).fetchone()
    return _user_record(row)


def code_hash_exists(code_hash: str) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM invitations WHERE code_hash = ?
            UNION ALL
            SELECT 1 FROM demo_codes WHERE code_hash = ?
            LIMIT 1
            """,
            (code_hash, code_hash)
        ).fetchone()
    return row is not None


def add_invitation(
    code_hash: str,
    name: str,
    role: str,
    expires_at: int,
    care_circle_id: str | None = None,
    permissions: tuple[str, ...] = ()
) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO invitations (
                code_hash, name, role, care_circle_id, permissions_json,
                expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code_hash,
                name,
                role,
                care_circle_id,
                json.dumps(sorted(set(permissions))),
                expires_at,
                int(time.time())
            )
        )


def get_owned_care_circle(user_id: str) -> str | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id FROM care_circles WHERE owner_user_id = ?",
            (user_id,)
        ).fetchone()
    return row["id"] if row else None


def get_circle_scope(
    connection: sqlite3.Connection,
    user_id: str,
    care_circle_id: str
) -> dict | None:
    circle = connection.execute(
        """
        SELECT care_circles.id, care_circles.owner_user_id AS elder_user_id,
               owners.name AS elder_name, owners.role AS elder_role
        FROM care_circles
        JOIN users AS owners ON owners.id = care_circles.owner_user_id
        WHERE care_circles.id = ?
        """,
        (care_circle_id,)
    ).fetchone()
    if circle is None:
        return None

    if circle["elder_user_id"] == user_id:
        if circle["elder_role"] != "elder":
            return None
        return {
            "care_circle_id": circle["id"],
            "elder_user_id": circle["elder_user_id"],
            "elder_name": circle["elder_name"],
            "is_owner": True,
            "permissions": set(ALL_PERMISSIONS)
        }

    membership = connection.execute(
        """
        SELECT permissions_json
        FROM care_circle_members
        WHERE care_circle_id = ? AND user_id = ?
        """,
        (care_circle_id, user_id)
    ).fetchone()
    if membership is None:
        return None
    return {
        "care_circle_id": circle["id"],
        "elder_user_id": circle["elder_user_id"],
        "elder_name": circle["elder_name"],
        "is_owner": False,
        "permissions": set(json.loads(membership["permissions_json"]))
    }


def has_circle_permission(
    scope: dict | None,
    permission: str,
    manage: bool = False
) -> bool:
    if scope is None:
        return False
    if scope["is_owner"]:
        return True
    permissions = scope["permissions"]
    manage_permission = f"{permission}_manage"
    if manage:
        return manage_permission in permissions
    return permission in permissions or manage_permission in permissions


def get_user_circle_scopes(
    user_id: str,
    permission: str,
    manage: bool = False
) -> list[dict]:
    with connect() as connection:
        circles = connection.execute(
            """
            SELECT id FROM care_circles WHERE owner_user_id = ?
            UNION
            SELECT care_circle_id AS id
            FROM care_circle_members
            WHERE user_id = ?
            """,
            (user_id, user_id)
        ).fetchall()
        scopes = []
        for circle in circles:
            scope = get_circle_scope(connection, user_id, circle["id"])
            if has_circle_permission(scope, permission, manage):
                scopes.append(scope)
    return scopes


def record_timeline_event(
    connection: sqlite3.Connection,
    elder_user_id: str,
    actor_user_id: str,
    description: str,
    event_type: str
) -> None:
    connection.execute(
        """
        INSERT INTO care_activities (
            id, elder_user_id, description, occurred_at, actor_user_id, event_type
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            elder_user_id,
            description,
            datetime.now(timezone.utc).isoformat(),
            actor_user_id,
            event_type
        )
    )


def redeem_invitation(
    code_hash: str,
    now: int | None = None,
    email: str | None = None,
    password_hash: str | None = None
) -> dict | None:
    if (email is None) != (password_hash is None):
        raise ValueError("An email and password hash must be supplied together.")
    now = int(time.time()) if now is None else now
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        invitation = connection.execute(
            """
            SELECT name, role, care_circle_id, permissions_json, expires_at, used_at
            FROM invitations
            WHERE code_hash = ?
            """,
            (code_hash,)
        ).fetchone()
        if (
            invitation is None
            or invitation["used_at"] is not None
            or invitation["expires_at"] <= now
        ):
            return None

        if email is not None and connection.execute(
            "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
            (email,)
        ).fetchone():
            raise EmailAlreadyRegisteredError

        user_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO users (
                id, name, role, is_demo, created_at, email, password_hash
            ) VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            (
                user_id, invitation["name"], invitation["role"], now,
                email, password_hash
            )
        )

        if invitation["role"] == "elder":
            connection.execute(
                """
                INSERT INTO care_circles (id, owner_user_id, name)
                VALUES (?, ?, ?)
                """,
                (str(uuid.uuid4()), user_id, "Care circle")
            )
        else:
            if invitation["care_circle_id"] is None:
                raise ValueError("Non-elder invitations must belong to a care circle.")
            connection.execute(
                """
                INSERT INTO care_circle_members (
                    care_circle_id, user_id, permissions_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    invitation["care_circle_id"],
                    user_id,
                    invitation["permissions_json"],
                    now
                )
            )

        connection.execute(
            "UPDATE invitations SET used_at = ? WHERE code_hash = ?",
            (now, code_hash)
        )
        user = connection.execute(
            "SELECT id, name, role, is_demo FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
    return _user_record(user)


def get_dashboard_for_user(user_id: str) -> dict | None:
    user = get_user(user_id)
    if user is None:
        return None

    with connect() as connection:
        if user["role"] == "elder":
            circles = connection.execute(
                """
                SELECT care_circles.id AS circle_id,
                       users.id AS elder_id,
                       users.name AS elder_name,
                       COUNT(care_circle_members.user_id) + 1 AS member_count
                FROM care_circles
                JOIN users ON users.id = care_circles.owner_user_id
                LEFT JOIN care_circle_members
                    ON care_circle_members.care_circle_id = care_circles.id
                WHERE care_circles.owner_user_id = ?
                GROUP BY care_circles.id
                """,
                (user_id,)
            ).fetchall()
            circle_rows = [
                (circle, set(ALL_PERMISSIONS))
                for circle in circles
            ]
        else:
            circles = connection.execute(
                """
                SELECT care_circles.id AS circle_id,
                       owners.id AS elder_id,
                       owners.name AS elder_name,
                       members.permissions_json,
                       COUNT(all_members.user_id) + 1 AS member_count
                FROM care_circle_members AS members
                JOIN care_circles ON care_circles.id = members.care_circle_id
                JOIN users AS owners ON owners.id = care_circles.owner_user_id
                LEFT JOIN care_circle_members AS all_members
                    ON all_members.care_circle_id = care_circles.id
                WHERE members.user_id = ?
                GROUP BY care_circles.id, members.user_id
                """,
                (user_id,)
            ).fetchall()
            circle_rows = [
                (circle, set(json.loads(circle["permissions_json"])))
                for circle in circles
            ]

        care_circles = []
        for circle, permissions in circle_rows:
            circle_data = {
                "id": circle["circle_id"],
                "elder": {
                    "id": circle["elder_id"],
                    "name": circle["elder_name"]
                },
                "member_count": circle["member_count"],
                "permissions": sorted(permissions)
            }
            if user["is_demo"]:
                owner = connection.execute(
                    "SELECT is_demo FROM users WHERE id = ?",
                    (circle["elder_id"],)
                ).fetchone()
                if owner is not None and owner["is_demo"]:
                    members = connection.execute(
                        """
                        SELECT id, name, role
                        FROM users
                        WHERE id = ? AND is_demo = 1
                        UNION
                        SELECT users.id, users.name, users.role
                        FROM care_circle_members
                        JOIN users ON users.id = care_circle_members.user_id
                        WHERE care_circle_members.care_circle_id = ?
                          AND users.is_demo = 1
                        ORDER BY role, name
                        """,
                        (circle["elder_id"], circle["circle_id"])
                    ).fetchall()
                    circle_data["members"] = [dict(member) for member in members]
            for table, permission in (
                ("medications", "medications"),
                ("appointments", "appointments"),
                ("care_activities", "activities")
            ):
                can_view = permission in permissions or (
                    f"{permission}_manage" in permissions
                )
                if not can_view:
                    circle_data[permission] = []
                    continue
                rows = connection.execute(
                    f"""
                    SELECT id, elder_user_id, name, dosage, frequency,
                           start_date, end_date, instructions, created_at
                    FROM {table}
                    WHERE elder_user_id = ?
                    ORDER BY created_at DESC
                    """ if table == "medications" else f"""
                    SELECT * FROM {table}
                    WHERE elder_user_id = ?
                    ORDER BY {'starts_at' if table == 'appointments' else 'occurred_at'} DESC
                    """,
                    (circle["elder_id"],)
                ).fetchall()
                circle_data[permission] = [dict(row) for row in rows]
            care_circles.append(circle_data)

    return {"profile": user, "care_circles": care_circles}


def _medication_circle_access(
    connection: sqlite3.Connection,
    user_id: str,
    care_circle_id: str
) -> tuple[str, str, bool, bool] | None:
    circle = connection.execute(
        """
        SELECT care_circles.owner_user_id AS elder_user_id,
               owners.name AS elder_name,
               owners.role AS elder_role
        FROM care_circles
        JOIN users AS owners ON owners.id = care_circles.owner_user_id
        WHERE care_circles.id = ?
        """,
        (care_circle_id,)
    ).fetchone()
    if circle is None:
        return None

    if circle["elder_user_id"] == user_id and circle["elder_role"] == "elder":
        return circle["elder_user_id"], circle["elder_name"], True, True

    membership = connection.execute(
        """
        SELECT permissions_json
        FROM care_circle_members
        WHERE care_circle_id = ? AND user_id = ?
        """,
        (care_circle_id, user_id)
    ).fetchone()
    if membership is None:
        return None

    permissions = set(json.loads(membership["permissions_json"]))
    can_manage = "medications_manage" in permissions
    can_view = "medications" in permissions or can_manage
    if not can_view:
        return None
    return circle["elder_user_id"], circle["elder_name"], can_view, can_manage


def _medication_record(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    circle_id: str,
    elder_name: str,
    can_manage: bool
) -> dict:
    doses = connection.execute(
        """
        SELECT id, scheduled_at, status, created_at, updated_at
        FROM medication_doses
        WHERE medication_id = ?
        ORDER BY scheduled_at DESC, created_at DESC
        """,
        (row["id"],)
    ).fetchall()
    dose_records = []
    for dose in doses:
        history = connection.execute(
            """
            SELECT status, changed_at
            FROM medication_dose_history
            WHERE dose_id = ?
            ORDER BY changed_at DESC
            """,
            (dose["id"],)
        ).fetchall()
        dose_records.append({
            **dict(dose),
            "history": [dict(change) for change in history]
        })

    return {
        "id": row["id"],
        "care_circle_id": circle_id,
        "elder_user_id": row["elder_user_id"],
        "elder_name": elder_name,
        "can_manage": can_manage,
        "name": row["name"],
        "dosage": row["dosage"],
        "frequency": row["frequency"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "instructions": row["instructions"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "doses": dose_records
    }


def get_medications_for_user(user_id: str) -> dict:
    medications = []
    manageable_circles = []
    with connect() as connection:
        circles = connection.execute(
            """
            SELECT id FROM care_circles WHERE owner_user_id = ?
            UNION
            SELECT care_circle_id AS id
            FROM care_circle_members
            WHERE user_id = ?
            """,
            (user_id, user_id)
        ).fetchall()
        for circle in circles:
            access = _medication_circle_access(
                connection, user_id, circle["id"]
            )
            if access is None:
                continue
            elder_user_id, elder_name, _, can_manage = access
            if can_manage:
                manageable_circles.append({
                    "id": circle["id"],
                    "elder_user_id": elder_user_id,
                    "elder_name": elder_name
                })
            rows = connection.execute(
                """
                SELECT id, elder_user_id, name, dosage, frequency, start_date,
                       end_date, instructions, created_at, updated_at
                FROM medications
                WHERE elder_user_id = ?
                ORDER BY created_at DESC, id
                """,
                (elder_user_id,)
            ).fetchall()
            medications.extend(
                _medication_record(
                    connection, row, circle["id"], elder_name, can_manage
                )
                for row in rows
            )
    return {
        "medications": medications,
        "manageable_circles": manageable_circles
    }


def get_medication_for_user(user_id: str, medication_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, elder_user_id, name, dosage, frequency, start_date,
                   end_date, instructions, created_at, updated_at
            FROM medications
            WHERE id = ?
            """,
            (medication_id,)
        ).fetchone()
        if row is None:
            return None
        circle = connection.execute(
            "SELECT id FROM care_circles WHERE owner_user_id = ?",
            (row["elder_user_id"],)
        ).fetchone()
        if circle is None:
            return None
        access = _medication_circle_access(connection, user_id, circle["id"])
        if access is None:
            return None
        _, elder_name, _, can_manage = access
        return _medication_record(
            connection, row, circle["id"], elder_name, can_manage
        )


def create_medication_for_user(
    user_id: str,
    care_circle_id: str,
    name: str,
    dosage: str,
    frequency: str,
    start_date: str,
    end_date: str | None,
    instructions: str
) -> dict | None:
    record_id = str(uuid.uuid4())
    now = int(time.time())
    with connect() as connection:
        access = _medication_circle_access(
            connection, user_id, care_circle_id
        )
        if access is None or not access[3]:
            return None
        elder_user_id = access[0]
        connection.execute(
            """
            INSERT INTO medications (
                id, elder_user_id, name, dosage, frequency, start_date,
                end_date, instructions, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id, elder_user_id, name, dosage, frequency,
                start_date, end_date, instructions, now, now
            )
        )
        record_timeline_event(
            connection,
            elder_user_id,
            user_id,
            "A medication was added.",
            "medication_created"
        )
    return get_medication_for_user(user_id, record_id)


def update_medication_for_user(
    user_id: str,
    medication_id: str,
    name: str,
    dosage: str,
    frequency: str,
    start_date: str,
    end_date: str | None,
    instructions: str
) -> dict | None:
    now = int(time.time())
    with connect() as connection:
        row = connection.execute(
            "SELECT elder_user_id FROM medications WHERE id = ?",
            (medication_id,)
        ).fetchone()
        if row is None:
            return None
        circle = connection.execute(
            "SELECT id FROM care_circles WHERE owner_user_id = ?",
            (row["elder_user_id"],)
        ).fetchone()
        if circle is None:
            return None
        access = _medication_circle_access(connection, user_id, circle["id"])
        if access is None or not access[3]:
            return None
        connection.execute(
            """
            UPDATE medications
            SET name = ?, dosage = ?, frequency = ?, start_date = ?,
                end_date = ?, instructions = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name, dosage, frequency, start_date, end_date,
                instructions, now, medication_id
            )
        )
        record_timeline_event(
            connection,
            row["elder_user_id"],
            user_id,
            "A medication was updated.",
            "medication_updated"
        )
    return get_medication_for_user(user_id, medication_id)


def delete_medication_for_user(user_id: str, medication_id: str) -> bool:
    with connect() as connection:
        row = connection.execute(
            "SELECT elder_user_id FROM medications WHERE id = ?",
            (medication_id,)
        ).fetchone()
        if row is None:
            return False
        circle = connection.execute(
            "SELECT id FROM care_circles WHERE owner_user_id = ?",
            (row["elder_user_id"],)
        ).fetchone()
        if circle is None:
            return False
        access = _medication_circle_access(connection, user_id, circle["id"])
        if access is None or not access[3]:
            return False
        record_timeline_event(
            connection,
            row["elder_user_id"],
            user_id,
            "A medication was removed.",
            "medication_deleted"
        )
        connection.execute("DELETE FROM medications WHERE id = ?", (medication_id,))
        return True


def create_medication_dose_for_user(
    user_id: str,
    medication_id: str,
    scheduled_at: str,
    status: str
) -> dict | None:
    dose_id = str(uuid.uuid4())
    now = int(time.time())
    with connect() as connection:
        row = connection.execute(
            """
            SELECT elder_user_id, start_date, end_date
            FROM medications WHERE id = ?
            """,
            (medication_id,)
        ).fetchone()
        if row is None:
            return None
        circle = connection.execute(
            "SELECT id FROM care_circles WHERE owner_user_id = ?",
            (row["elder_user_id"],)
        ).fetchone()
        if circle is None:
            return None
        access = _medication_circle_access(connection, user_id, circle["id"])
        if access is None or not access[3]:
            return None
        dose_date = scheduled_at[:10]
        if row["start_date"] and dose_date < row["start_date"]:
            raise ValueError(
                "A dose cannot be scheduled before the medication start date."
            )
        if row["end_date"] and dose_date > row["end_date"]:
            raise ValueError(
                "A dose cannot be scheduled after the medication end date."
            )
        connection.execute(
            """
            INSERT INTO medication_doses (
                id, medication_id, scheduled_at, status,
                updated_by_user_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (dose_id, medication_id, scheduled_at, status, user_id, now, now)
        )
        connection.execute(
            """
            INSERT INTO medication_dose_history (
                id, dose_id, status, changed_by_user_id, changed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), dose_id, status, user_id, now)
        )
        record_timeline_event(
            connection,
            row["elder_user_id"],
            user_id,
            "A medication dose was recorded.",
            "medication_dose_recorded"
        )
    return get_medication_for_user(user_id, medication_id)


def update_medication_dose_for_user(
    user_id: str,
    medication_id: str,
    dose_id: str,
    status: str
) -> dict | None:
    now = int(time.time())
    with connect() as connection:
        row = connection.execute(
            "SELECT elder_user_id FROM medications WHERE id = ?",
            (medication_id,)
        ).fetchone()
        if row is None:
            return None
        circle = connection.execute(
            "SELECT id FROM care_circles WHERE owner_user_id = ?",
            (row["elder_user_id"],)
        ).fetchone()
        if circle is None:
            return None
        access = _medication_circle_access(connection, user_id, circle["id"])
        if access is None or not access[3]:
            return None
        dose = connection.execute(
            """
            SELECT id FROM medication_doses
            WHERE id = ? AND medication_id = ?
            """,
            (dose_id, medication_id)
        ).fetchone()
        if dose is None:
            return None
        connection.execute(
            """
            UPDATE medication_doses
            SET status = ?, updated_by_user_id = ?, updated_at = ?
            WHERE id = ? AND medication_id = ?
            """,
            (status, user_id, now, dose_id, medication_id)
        )
        connection.execute(
            """
            INSERT INTO medication_dose_history (
                id, dose_id, status, changed_by_user_id, changed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), dose_id, status, user_id, now)
        )
        record_timeline_event(
            connection,
            row["elder_user_id"],
            user_id,
            "A medication dose status was updated.",
            "medication_dose_updated"
        )
    return get_medication_for_user(user_id, medication_id)


def add_medication(
    elder_user_id: str,
    name: str,
    dosage: str = "",
    schedule: str = "",
    instructions: str = "",
    record_id: str | None = None
) -> str:
    record_id = record_id or str(uuid.uuid4())
    now = int(time.time())
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO medications (
                id, elder_user_id, name, dosage, frequency, start_date,
                instructions, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)
            """,
            (
                record_id,
                elder_user_id,
                name,
                dosage,
                schedule,
                instructions,
                now,
                now
            )
        )
    return record_id


def add_appointment(
    elder_user_id: str,
    title: str,
    starts_at: str,
    provider: str = "",
    location: str = "",
    record_id: str | None = None
) -> str:
    record_id = record_id or str(uuid.uuid4())
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO appointments (
                id, elder_user_id, title, provider, starts_at, location
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (record_id, elder_user_id, title, provider, starts_at, location)
        )
    return record_id


def add_care_activity(
    elder_user_id: str,
    description: str,
    occurred_at: str,
    record_id: str | None = None
) -> str:
    record_id = record_id or str(uuid.uuid4())
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO care_activities (id, elder_user_id, description, occurred_at)
            VALUES (?, ?, ?, ?)
            """,
            (record_id, elder_user_id, description, occurred_at)
        )
    return record_id
