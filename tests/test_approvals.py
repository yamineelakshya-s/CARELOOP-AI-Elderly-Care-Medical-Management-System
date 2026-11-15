import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import connect
from app.database import initialize_database
from app.main import app
from app.routes.auth import clear_rate_limit_state
from app.services.auth_service import create_invitation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "careloop.db"))
    monkeypatch.setattr(settings, "enable_demo_accounts", False)
    clear_rate_limit_state()
    with TestClient(app) as test_client:
        yield test_client
    clear_rate_limit_state()


def invite_member(client, role, permissions, name):
    response = client.post(
        "/api/auth/invitations",
        json={"name": name, "role": role, "permissions": permissions}
    )
    assert response.status_code == 200
    return response.json()["join_code"]


def join_member(code):
    with TestClient(app) as member:
        joined = member.post("/api/auth/join", json={"join_code": code})
        assert joined.status_code == 200
        token = member.cookies.get("careloop_session")
        return joined.json()["user"], token


def create_medication(client, circle_id):
    response = client.post(
        "/api/medications",
        json={
            "care_circle_id": circle_id,
            "name": "Current medication",
            "dosage": "1 tablet",
            "frequency": "Once daily",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "instructions": "Take with food"
        }
    )
    assert response.status_code == 201
    return response.json()


def proposed_change(**overrides):
    values = {
        "name": "Updated medication",
        "dosage": "2 tablets",
        "frequency": "Twice daily",
        "start_date": "2026-02-01",
        "end_date": "2026-12-31",
        "instructions": "Take after meals"
    }
    values.update(overrides)
    return values


def submit_proposal(caregiver, circle_id, medication_id, change=None):
    return caregiver.post(
        "/api/approvals",
        json={
            "care_circle_id": circle_id,
            "request_type": "medication",
            "title": "Proposed medication change",
            "details": "Please review before applying.",
            "medication_id": medication_id,
            "proposed_changes": change or proposed_change()
        }
    )


def test_approval_applies_valid_medication_change_only_after_elder_approval(client):
    elder_code = create_invitation("Approval Owner", "elder")
    client.post("/api/auth/join", json={"join_code": elder_code})
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]
    medication = create_medication(client, circle_id)
    caregiver_code = invite_member(
        client,
        "caregiver",
        ["approvals", "medications"],
        "Medication Caregiver"
    )
    caregiver, token = join_member(caregiver_code)
    with TestClient(app) as requester:
        requester.cookies.set("careloop_session", token)
        submitted = submit_proposal(
            requester, circle_id, medication["id"]
        )
        assert submitted.status_code == 201
        request_id = submitted.json()["id"]
        assert submitted.json()["status"] == "pending"
        assert submitted.json()["proposed_changes"]["dosage"] == "2 tablets"
        unchanged = client.get(
            f"/api/medications/{medication['id']}"
        ).json()
        assert unchanged["dosage"] == "1 tablet"
        assert unchanged["frequency"] == "Once daily"

    decision = client.post(
        f"/api/approvals/{request_id}/decision",
        json={"status": "approved", "decision_note": "Reviewed"}
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"
    updated = client.get(f"/api/medications/{medication['id']}").json()
    assert updated["name"] == "Updated medication"
    assert updated["dosage"] == "2 tablets"
    assert updated["frequency"] == "Twice daily"
    assert updated["start_date"] == "2026-02-01"
    assert updated["instructions"] == "Take after meals"
    assert client.post(
        f"/api/approvals/{request_id}/decision",
        json={"status": "approved"}
    ).status_code == 404
    events = client.get("/api/timeline").json()["events"]
    assert any(event["event_type"] == "medication_change_approved" for event in events)
    assert any(event["event_type"] == "approval_decided" for event in events)
    assert caregiver["role"] == "caregiver"


def test_rejected_proposal_leaves_medication_unchanged(client):
    elder_code = create_invitation("Rejection Owner", "elder")
    client.post("/api/auth/join", json={"join_code": elder_code})
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]
    medication = create_medication(client, circle_id)
    caregiver_code = invite_member(
        client,
        "family",
        ["approvals", "medications"],
        "Requesting Family Member"
    )
    _, token = join_member(caregiver_code)
    with TestClient(app) as requester:
        requester.cookies.set("careloop_session", token)
        submitted = submit_proposal(
            requester, circle_id, medication["id"]
        )
        assert submitted.status_code == 201

    rejected = client.post(
        f"/api/approvals/{submitted.json()['id']}/decision",
        json={"status": "rejected", "decision_note": "Keep current plan"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    unchanged = client.get(f"/api/medications/{medication['id']}").json()
    assert unchanged["dosage"] == "1 tablet"
    assert unchanged["frequency"] == "Once daily"
    with connect() as connection:
        event_types = {
            row["event_type"]
            for row in connection.execute(
                """
                SELECT event_type FROM care_activities
                WHERE elder_user_id = ?
                """,
                (client.get("/api/auth/me").json()["user"]["id"],)
            )
        }
    assert "medication_change_approved" not in event_types
    assert "approval_decided" in event_types


def test_proposal_requires_medication_permission_and_same_circle(client):
    elder_code = create_invitation("Private Approval Owner", "elder")
    client.post("/api/auth/join", json={"join_code": elder_code})
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]
    medication = create_medication(client, circle_id)
    approvals_only_code = invite_member(
        client, "caregiver", ["approvals"], "Approvals Only"
    )
    _, token = join_member(approvals_only_code)
    with TestClient(app) as requester:
        requester.cookies.set("careloop_session", token)
        denied = submit_proposal(
            requester, circle_id, medication["id"]
        )
        assert denied.status_code == 404
        missing = requester.post(
            "/api/approvals",
            json={
                "care_circle_id": circle_id,
                "request_type": "medication",
                "title": "Incomplete proposal"
            }
        )
        assert missing.status_code == 422

    authorized_code = invite_member(
        client,
        "family",
        ["approvals", "medications"],
        "Authorized Requester"
    )
    _, authorized_token = join_member(authorized_code)
    with TestClient(app) as requester:
        requester.cookies.set("careloop_session", authorized_token)
        submitted = submit_proposal(
            requester, circle_id, medication["id"]
        )
        assert submitted.status_code == 201

    other_elder_code = create_invitation("Other Approval Owner", "elder")
    with TestClient(app) as other_elder:
        other_elder.post("/api/auth/join", json={"join_code": other_elder_code})
        assert other_elder.post(
            f"/api/approvals/{submitted.json()['id']}/decision",
            json={"status": "approved"}
        ).status_code == 404


def test_invalid_persisted_proposal_cannot_be_applied(client):
    elder_code = create_invitation("Validation Owner", "elder")
    client.post("/api/auth/join", json={"join_code": elder_code})
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]
    medication = create_medication(client, circle_id)
    caregiver_code = invite_member(
        client,
        "caregiver",
        ["approvals", "medications"],
        "Validation Requester"
    )
    _, token = join_member(caregiver_code)
    with TestClient(app) as requester:
        requester.cookies.set("careloop_session", token)
        submitted = submit_proposal(
            requester, circle_id, medication["id"]
        )
        assert submitted.status_code == 201

    with connect() as connection:
        connection.execute(
            """
            UPDATE approval_requests
            SET proposed_data_json = ?
            WHERE id = ?
            """,
            (
                '{"name":"Corrupt","dosage":"","frequency":"daily",'
                '"start_date":"2026-02-01","end_date":"2026-01-01",'
                '"instructions":""}',
                submitted.json()["id"]
            )
        )
    response = client.post(
        f"/api/approvals/{submitted.json()['id']}/decision",
        json={"status": "approved"}
    )

    assert response.status_code == 409
    assert client.get(f"/api/medications/{medication['id']}").json()[
        "dosage"
    ] == "1 tablet"
    assert client.get(
        f"/api/approvals/{submitted.json()['id']}"
    ).json()["status"] == "pending"


def test_approval_migration_preserves_existing_requests(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy-approvals.db"
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
            CREATE TABLE approval_requests (
                id TEXT PRIMARY KEY,
                care_circle_id TEXT NOT NULL REFERENCES care_circles(id),
                requested_by_user_id TEXT NOT NULL REFERENCES users(id),
                request_type TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                decision_note TEXT NOT NULL DEFAULT '',
                decided_by_user_id TEXT REFERENCES users(id),
                created_at INTEGER NOT NULL,
                decided_at INTEGER
            );
            INSERT INTO approval_requests (
                id, care_circle_id, requested_by_user_id, request_type,
                title, details, status, created_at
            ) VALUES (
                'legacy-request', 'legacy-circle', 'legacy-elder',
                'other', 'Existing request', 'Keep this row', 'pending', 2
            );
            """
        )

    monkeypatch.setattr(settings, "database_path", str(database_path))
    monkeypatch.setattr(settings, "enable_demo_accounts", False)
    initialize_database()

    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, title, details, status, medication_id,
                   proposed_data_json
            FROM approval_requests WHERE id = 'legacy-request'
            """
        ).fetchone()
    assert row["id"] == "legacy-request"
    assert row["title"] == "Existing request"
    assert row["details"] == "Keep this row"
    assert row["status"] == "pending"
    assert row["medication_id"] is None
    assert row["proposed_data_json"] is None