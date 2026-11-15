import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import connect, initialize_database
from app.main import app
from app.repositories.sqlite_repository import add_medication
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


def create_elder(client, name):
    code = create_invitation(name=name, role="elder")
    response = client.post("/api/auth/join", json={"join_code": code})
    assert response.status_code == 200
    circle = client.get("/api/dashboard").json()["care_circles"][0]
    return response.json()["user"], circle["id"]


def medication_payload(circle_id, **overrides):
    payload = {
        "care_circle_id": circle_id,
        "name": "Daily medication",
        "dosage": "1 tablet",
        "frequency": "Once daily",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "instructions": "Take with food"
    }
    payload.update(overrides)
    return payload


def invite_member(client, name, role, permissions):
    response = client.post(
        "/api/auth/invitations",
        json={"name": name, "role": role, "permissions": permissions}
    )
    assert response.status_code == 200
    return response.json()["join_code"]


def test_medication_crud_and_dose_status_history(client):
    _, circle_id = create_elder(client, "Account Owner")
    created = client.post(
        "/api/medications",
        json=medication_payload(circle_id)
    )

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    medication = created.json()
    assert medication["name"] == "Daily medication"
    assert medication["frequency"] == "Once daily"
    assert medication["start_date"] == "2026-01-01"
    assert medication["end_date"] == "2026-12-31"
    assert medication["instructions"] == "Take with food"
    assert medication["can_manage"] is True
    assert medication["doses"] == []

    dose_response = client.post(
        f"/api/medications/{medication['id']}/doses",
        json={"scheduled_at": "2026-01-01T08:00:00", "status": "Pending"}
    )
    assert dose_response.status_code == 201
    dose = dose_response.json()["doses"][0]
    assert dose["scheduled_at"] == "2026-01-01T08:00:00+00:00"
    assert dose["status"] == "Pending"
    assert [change["status"] for change in dose["history"]] == ["Pending"]

    updated_dose = client.patch(
        f"/api/medications/{medication['id']}/doses/{dose['id']}",
        json={"status": "Taken"}
    )
    assert updated_dose.status_code == 200
    dose = updated_dose.json()["doses"][0]
    assert dose["status"] == "Taken"
    assert {change["status"] for change in dose["history"]} == {
        "Pending", "Taken"
    }

    missed = client.post(
        f"/api/medications/{medication['id']}/doses",
        json={"scheduled_at": "2026-01-01T20:00:00", "status": "Missed"}
    )
    assert missed.status_code == 201
    assert any(dose["status"] == "Missed" for dose in missed.json()["doses"])

    changed = client.put(
        f"/api/medications/{medication['id']}",
        json=medication_payload(
            circle_id,
            name="Updated medication",
            dosage="2 tablets",
            frequency="Twice daily",
            end_date=None
        )
    )
    assert changed.status_code == 200
    assert changed.json()["name"] == "Updated medication"
    assert changed.json()["dosage"] == "2 tablets"
    assert changed.json()["end_date"] is None

    listing = client.get("/api/medications")
    assert listing.status_code == 200
    assert len(listing.json()["medications"]) == 1
    assert listing.json()["manageable_circles"][0]["id"] == circle_id

    deleted = client.delete(f"/api/medications/{medication['id']}")
    assert deleted.status_code == 204
    assert deleted.headers["cache-control"] == "no-store"
    assert client.get("/api/medications").json()["medications"] == []
    with connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM medication_doses"
        ).fetchone()[0] == 0


def test_medication_requires_authentication_and_valid_date_range(client):
    assert client.get("/api/medications").status_code == 401
    assert client.post(
        "/api/medications",
        json=medication_payload("circle")
    ).status_code == 401

    create_elder(client, "Date Check Elder")
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]
    invalid = client.post(
        "/api/medications",
        json=medication_payload(
            circle_id,
            start_date="2026-05-01",
            end_date="2026-04-30"
        )
    )
    assert invalid.status_code == 422


def test_dose_schedule_must_fall_within_medication_dates(client):
    _, circle_id = create_elder(client, "Dose Date Elder")
    medication = client.post(
        "/api/medications",
        json=medication_payload(
            circle_id,
            start_date="2026-03-01",
            end_date="2026-03-31"
        )
    ).json()

    before = client.post(
        f"/api/medications/{medication['id']}/doses",
        json={"scheduled_at": "2026-02-28T08:00:00"}
    )
    after = client.post(
        f"/api/medications/{medication['id']}/doses",
        json={"scheduled_at": "2026-04-01T08:00:00"}
    )

    assert before.status_code == after.status_code == 422
    assert client.get(
        f"/api/medications/{medication['id']}"
    ).json()["doses"] == []


def test_medication_workflow_events_are_persisted_in_timeline(client):
    _, circle_id = create_elder(client, "Medication Timeline Elder")
    medication = client.post(
        "/api/medications",
        json=medication_payload(circle_id)
    ).json()
    medication = client.put(
        f"/api/medications/{medication['id']}",
        json=medication_payload(circle_id, name="Updated medication")
    ).json()
    dose = client.post(
        f"/api/medications/{medication['id']}/doses",
        json={"scheduled_at": "2026-06-01T08:00:00"}
    ).json()["doses"][0]
    client.patch(
        f"/api/medications/{medication['id']}/doses/{dose['id']}",
        json={"status": "Taken"}
    )

    event_types = {
        event["event_type"]
        for event in client.get("/api/timeline").json()["events"]
    }
    assert {
        "medication_created",
        "medication_updated",
        "medication_dose_recorded",
        "medication_dose_updated"
    } <= event_types


def test_view_permission_allows_read_but_not_medication_management(client):
    elder, circle_id = create_elder(client, "Elder With Viewer")
    medication = add_medication(
        elder["id"],
        "Private medicine",
        dosage="1 tablet",
        schedule="Once daily"
    )
    code = invite_member(
        client,
        "Read Only Caregiver",
        "caregiver",
        ["medications"]
    )

    with TestClient(app) as caregiver:
        assert caregiver.post(
            "/api/auth/join", json={"join_code": code}
        ).status_code == 200
        listed = caregiver.get("/api/medications").json()
        assert [item["id"] for item in listed["medications"]] == [medication]
        assert listed["medications"][0]["frequency"] == "Once daily"
        assert listed["medications"][0]["can_manage"] is False
        assert listed["manageable_circles"] == []

        assert caregiver.get(
            f"/api/medications/{medication}"
        ).status_code == 200
        assert caregiver.post(
            "/api/medications",
            json=medication_payload(circle_id)
        ).status_code == 404
        payload = medication_payload(circle_id)
        assert caregiver.put(
            f"/api/medications/{medication}", json=payload
        ).status_code == 404
        assert caregiver.delete(
            f"/api/medications/{medication}"
        ).status_code == 404
        assert caregiver.post(
            f"/api/medications/{medication}/doses",
            json={"scheduled_at": "2026-01-01T08:00:00"}
        ).status_code == 404

    assert client.get(f"/api/medications/{medication}").status_code == 200


def test_manage_permission_is_explicit_and_scoped_to_linked_circle(client):
    elder_one, circle_one = create_elder(client, "First Elder")
    code = invite_member(
        client,
        "Medication Manager",
        "family",
        ["medications_manage"]
    )
    client.cookies.clear()
    elder_two, circle_two = create_elder(client, "Second Elder")
    second_elder_session = client.cookies.get("careloop_session")
    second_medication = client.post(
        "/api/medications",
        json=medication_payload(circle_two, name="Second circle medicine")
    ).json()

    with TestClient(app) as manager:
        assert manager.post(
            "/api/auth/join", json={"join_code": code}
        ).status_code == 200
        visible = manager.get("/api/medications").json()
        assert visible["medications"] == []
        assert [item["id"] for item in visible["manageable_circles"]] == [
            circle_one
        ]

        created = manager.post(
            "/api/medications",
            json=medication_payload(circle_one, name="Managed medicine")
        )
        assert created.status_code == 201
        managed_id = created.json()["id"]
        assert created.json()["can_manage"] is True

        dose = manager.post(
            f"/api/medications/{managed_id}/doses",
            json={"scheduled_at": "2026-02-01T09:00:00"}
        )
        assert dose.status_code == 201
        dose_id = dose.json()["doses"][0]["id"]
        assert manager.patch(
            f"/api/medications/{managed_id}/doses/{dose_id}",
            json={"status": "Missed"}
        ).status_code == 200
        assert manager.put(
            f"/api/medications/{second_medication['id']}",
            json=medication_payload(circle_two)
        ).status_code == 404
        assert manager.get(
            f"/api/medications/{second_medication['id']}"
        ).status_code == 404
        assert manager.delete(
            f"/api/medications/{second_medication['id']}"
        ).status_code == 404
        assert len(manager.get("/api/medications").json()["medications"]) == 1

    assert elder_one["id"] != elder_two["id"]
    client.cookies.set("careloop_session", second_elder_session)

def test_dose_ids_cannot_be_reused_across_medications(client):
    _, circle_id = create_elder(client, "Dose Isolation Elder")
    first = client.post(
        "/api/medications", json=medication_payload(circle_id, name="First")
    ).json()
    second = client.post(
        "/api/medications", json=medication_payload(circle_id, name="Second")
    ).json()
    dose = client.post(
        f"/api/medications/{first['id']}/doses",
        json={"scheduled_at": "2026-01-01T08:00:00"}
    ).json()["doses"][0]

    response = client.patch(
        f"/api/medications/{second['id']}/doses/{dose['id']}",
        json={"status": "Taken"}
    )
    assert response.status_code == 404


def test_existing_medication_schedule_is_preserved_by_database_migration(
    tmp_path,
    monkeypatch
):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE medications (
                id TEXT PRIMARY KEY,
                elder_user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                dosage TEXT NOT NULL DEFAULT '',
                schedule TEXT NOT NULL DEFAULT '',
                instructions TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO medications
                (id, elder_user_id, name, dosage, schedule, instructions, created_at)
            VALUES ('legacy-med', 'elder-id', 'Existing medicine', '1 tablet',
                    'Every morning', '', 1)
            """
        )

    monkeypatch.setattr(settings, "database_path", str(path))
    initialize_database()

    with connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(medications)")
        }
        row = connection.execute(
            "SELECT frequency FROM medications WHERE id = 'legacy-med'"
        ).fetchone()
        dose_table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'medication_doses'
            """
        ).fetchone()

    assert {"frequency", "start_date", "end_date", "updated_at"} <= columns
    assert row["frequency"] == "Every morning"
    assert dose_table is not None

