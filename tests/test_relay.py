from contextlib import ExitStack
import sqlite3
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import connect
from app.database import initialize_database
from app.main import app
from app.repositories.sqlite_repository import add_medication
from app.routes.auth import clear_rate_limit_state
from app.services.auth_service import create_invitation
from app.services.tick_service import process_tick


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "careloop.db"))
    monkeypatch.setattr(settings, "enable_demo_accounts", False)
    monkeypatch.setattr(settings, "brevo_api_key", None)
    monkeypatch.setattr(settings, "sender_email", None)
    clear_rate_limit_state()
    with TestClient(app) as test_client:
        yield test_client
    clear_rate_limit_state()


def create_member(client, role, permissions, name):
    response = client.post(
        "/api/auth/invitations",
        json={"name": name, "role": role, "permissions": permissions}
    )
    assert response.status_code == 200
    return response.json()["join_code"]


def join_member(code, stack):
    member = stack.enter_context(TestClient(app))
    joined = member.post(
        "/api/auth/join",
        json={"join_code": code}
    )
    assert joined.status_code == 200
    return member, joined.json()["user"]


def create_dose_alert(client, elder_id):
    medication_id = add_medication(
        elder_id,
        "Scheduled medication",
        dosage="1 tablet",
        schedule="Once daily"
    )
    now = int(time.time())
    scheduled_at = datetime.fromtimestamp(now - 16 * 60, timezone.utc)
    dose_response = client.post(
        f"/api/medications/{medication_id}/doses",
        json={"scheduled_at": scheduled_at.isoformat(), "status": "Pending"}
    )
    assert dose_response.status_code == 201
    dose_id = next(
        dose["id"]
        for dose in dose_response.json()["doses"]
        if dose["scheduled_at"] == scheduled_at.isoformat()
    )
    process_tick(now)
    with connect() as connection:
        return connection.execute(
            "SELECT id FROM relay_items WHERE medication_dose_id = ?",
            (dose_id,)
        ).fetchone()["id"]


def test_dose_alert_claim_contact_resolve_reopen_and_permission_isolation(client):
    code = create_invitation("Relay Workflow Elder", "elder")
    elder = client.post("/api/auth/join", json={"join_code": code}).json()["user"]
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]

    first_code = create_member(
        client,
        "caregiver",
        ["relay_manage", "medications"],
        "First Caregiver"
    )
    second_code = create_member(
        client,
        "family",
        ["relay_manage"],
        "Second Caregiver"
    )
    viewer_code = create_member(
        client, "family", ["relay"], "Relay Viewer"
    )
    medication_viewer_code = create_member(
        client, "caregiver", ["medications"], "Medication Viewer"
    )
    no_access_code = create_member(
        client, "family", [], "No Access Member"
    )
    with ExitStack() as stack:
        first, first_user = join_member(first_code, stack)
        second, second_user = join_member(second_code, stack)
        viewer, _ = join_member(viewer_code, stack)
        medication_viewer, _ = join_member(medication_viewer_code, stack)
        no_access, _ = join_member(no_access_code, stack)
        relay_id = create_dose_alert(client, elder["id"])

        assert first.get("/api/relay").json()["items"][0]["medication_dose_id"]
        assert first.get("/api/relay").json()["items"][0]["claimant_name"] is None
        assert viewer.get(f"/api/relay/{relay_id}").status_code == 200
        assert medication_viewer.get(f"/api/relay/{relay_id}").status_code == 200
        assert medication_viewer.post(f"/api/relay/{relay_id}/claim").status_code == 404
        assert no_access.get("/api/relay").json()["items"] == []

        claimed = first.post(f"/api/relay/{relay_id}/claim")
        assert claimed.status_code == 200
        assert claimed.json()["status"] == "claimed"
        assert claimed.json()["claimed_by_user_id"] == first_user["id"]
        assert claimed.json()["claimant_name"] == "First Caregiver"
        assert claimed.json()["can_claim"] is False
        assert second.post(f"/api/relay/{relay_id}/claim").status_code == 409
        assert first.post(f"/api/relay/{relay_id}/contact").json()["status"] == "contacted"
        assert first.post(f"/api/relay/{relay_id}/resolve").json()["status"] == "resolved"
        assert first.post(f"/api/relay/{relay_id}/resolve").status_code == 409

        reopened = second.post(f"/api/relay/{relay_id}/reopen")
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "open"
        assert reopened.json()["claimed_by_user_id"] is None
        assert second.post(f"/api/relay/{relay_id}/claim").json()[
            "claimed_by_user_id"
        ] == second_user["id"]

        assert viewer.post(f"/api/relay/{relay_id}/claim").status_code == 404
        assert no_access.post(f"/api/relay/{relay_id}/claim").status_code == 404
        assert client.get("/api/relay").json()["items"][0]["care_circle_id"] == circle_id
    with connect() as connection:
        event_types = {
            row["event_type"]
            for row in connection.execute(
                """
                SELECT event_type FROM care_activities
                WHERE elder_user_id = ? AND event_type LIKE 'relay_alert_%'
                """,
                (elder["id"],)
            )
        }
        claim_count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM relay_items
            WHERE medication_dose_id IS NOT NULL AND care_circle_id = ?
            """,
            (circle_id,)
        ).fetchone()["count"]
    assert event_types == {
        "relay_alert_claimed",
        "relay_alert_contacted",
        "relay_alert_resolved",
        "relay_alert_reopened"
    }
    assert claim_count == 1


def test_general_relay_items_keep_existing_completion_flow(client):
    code = create_invitation("General Relay Elder", "elder")
    client.post("/api/auth/join", json={"join_code": code})
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]

    created = client.post(
        "/api/relay",
        json={
            "care_circle_id": circle_id,
            "title": "General follow-up",
            "body": "Existing relay task"
        }
    )
    assert created.status_code == 201
    relay_id = created.json()["id"]
    updated = client.put(
        f"/api/relay/{relay_id}",
        json={
            "title": "General follow-up",
            "body": "Existing relay task",
            "assigned_to_user_id": None,
            "status": "completed"
        }
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"


def test_legacy_relay_migration_preserves_existing_items(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy-relay.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );
            INSERT INTO users (id, name, role, created_at)
            VALUES ('legacy-elder', 'Legacy Elder', 'elder', 1);
            CREATE TABLE care_circles (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
                name TEXT NOT NULL
            );
            INSERT INTO care_circles (id, owner_user_id, name)
            VALUES ('legacy-circle', 'legacy-elder', 'Legacy circle');
            CREATE TABLE relay_items (
                id TEXT PRIMARY KEY,
                care_circle_id TEXT NOT NULL REFERENCES care_circles(id),
                created_by_user_id TEXT NOT NULL REFERENCES users(id),
                assigned_to_user_id TEXT REFERENCES users(id),
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            INSERT INTO relay_items (
                id, care_circle_id, created_by_user_id, title, body,
                status, created_at, updated_at
            ) VALUES (
                'legacy-relay', 'legacy-circle', 'legacy-elder',
                'Existing follow-up', 'Keep this row', 'completed', 2, 3
            );
            """
        )

    monkeypatch.setattr(settings, "database_path", str(database_path))
    monkeypatch.setattr(settings, "enable_demo_accounts", False)
    initialize_database()

    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, status, title, body, medication_dose_id,
                   claimed_by_user_id, claimed_at
            FROM relay_items WHERE id = 'legacy-relay'
            """
        ).fetchone()
    assert row["id"] == "legacy-relay"
    assert row["status"] == "completed"
    assert row["title"] == "Existing follow-up"
    assert row["body"] == "Keep this row"
    assert row["medication_dose_id"] is None
    assert row["claimed_by_user_id"] is None
    assert row["claimed_at"] is None