import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import connect
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


def create_elder(client, name):
    code = create_invitation(name, "elder")
    response = client.post("/api/auth/join", json={"join_code": code})
    assert response.status_code == 200
    circle = client.get("/api/dashboard").json()["care_circles"][0]
    return response.json()["user"], circle["id"]


def invite_member(client, role, permissions, name="Circle member"):
    response = client.post(
        "/api/auth/invitations",
        json={"name": name, "role": role, "permissions": permissions}
    )
    assert response.status_code == 200
    return response.json()["join_code"]


def appointment_payload(circle_id, **overrides):
    payload = {
        "care_circle_id": circle_id,
        "title": "Care review",
        "starts_at": "2026-11-20T10:30:00",
        "provider": "Care team",
        "location": "Clinic"
    }
    payload.update(overrides)
    return payload


def test_appointment_crud_is_scoped_and_persisted(client):
    _, circle_id = create_elder(client, "Appointment Owner")
    created = client.post(
        "/api/appointments",
        json=appointment_payload(circle_id)
    )

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    appointment = created.json()
    assert appointment["care_circle_id"] == circle_id
    assert appointment["can_manage"] is True
    assert appointment["title"] == "Care review"
    assert appointment["starts_at"] == "2026-11-20T10:30:00+00:00"

    listing = client.get("/api/appointments")
    assert listing.status_code == 200
    assert len(listing.json()["appointments"]) == 1
    assert listing.json()["manageable_circles"][0]["id"] == circle_id
    assert client.get(
        f"/api/appointments/{appointment['id']}"
    ).json()["id"] == appointment["id"]

    updated = client.put(
        f"/api/appointments/{appointment['id']}",
        json={
            "title": "Updated review",
            "starts_at": "2026-11-21T11:00:00",
            "provider": "Updated provider",
            "location": "Updated location"
        }
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated review"

    deleted = client.delete(f"/api/appointments/{appointment['id']}")
    assert deleted.status_code == 204
    assert deleted.headers["cache-control"] == "no-store"
    assert client.get("/api/appointments").json()["appointments"] == []
    with connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM appointments"
        ).fetchone()[0]
    assert count == 0


def test_appointment_view_and_manage_permissions_are_distinct(client):
    elder, circle_id = create_elder(client, "Appointment Elder")
    created = client.post(
        "/api/appointments",
        json=appointment_payload(circle_id)
    ).json()
    view_code = invite_member(client, "caregiver", ["appointments"], "Viewer")
    manage_code = invite_member(
        client,
        "family",
        ["appointments_manage"],
        "Appointment Manager"
    )

    with TestClient(app) as viewer:
        assert viewer.post(
            "/api/auth/join", json={"join_code": view_code}
        ).status_code == 200
        rows = viewer.get("/api/appointments").json()
        assert [row["id"] for row in rows["appointments"]] == [created["id"]]
        assert rows["appointments"][0]["can_manage"] is False
        assert rows["manageable_circles"] == []
        assert viewer.get(f"/api/appointments/{created['id']}").status_code == 200
        assert viewer.put(
            f"/api/appointments/{created['id']}",
            json={
                "title": "Not allowed",
                "starts_at": "2026-11-22T10:00:00",
                "provider": "",
                "location": ""
            }
        ).status_code == 404
        assert viewer.delete(
            f"/api/appointments/{created['id']}"
        ).status_code == 404
        assert viewer.post(
            "/api/appointments",
            json=appointment_payload(circle_id)
        ).status_code == 404

    with TestClient(app) as manager:
        assert manager.post(
            "/api/auth/join", json={"join_code": manage_code}
        ).status_code == 200
        rows = manager.get("/api/appointments").json()
        assert rows["appointments"][0]["can_manage"] is True
        assert manager.put(
            f"/api/appointments/{created['id']}",
            json={
                "title": "Managed update",
                "starts_at": "2026-11-23T12:00:00",
                "provider": "Provider",
                "location": "Room 1"
            }
        ).status_code == 200

    assert client.get(f"/api/appointments/{created['id']}").status_code == 200
    assert client.get("/api/dashboard").json()["care_circles"][0]["elder"]["id"] == elder["id"]


def test_appointment_ids_cannot_access_unlinked_circles(client):
    _, circle_one = create_elder(client, "First appointment account")
    record = client.post(
        "/api/appointments",
        json=appointment_payload(circle_one, title="Private appointment")
    ).json()
    client.cookies.clear()
    _, circle_two = create_elder(client, "Second appointment account")
    assert circle_one != circle_two

    assert client.get("/api/appointments").json()["appointments"] == []
    assert client.get(
        f"/api/appointments/{record['id']}"
    ).status_code == 404
    assert client.put(
        f"/api/appointments/{record['id']}",
        json={
            "title": "Tampered",
            "starts_at": "2026-11-24T12:00:00",
            "provider": "",
            "location": ""
        }
    ).status_code == 404
    assert client.delete(
        f"/api/appointments/{record['id']}"
    ).status_code == 404


def test_appointment_input_and_authentication_validation(client):
    assert client.get("/api/appointments").status_code == 401
    assert client.post(
        "/api/appointments",
        json=appointment_payload("unknown")
    ).status_code == 401

    create_elder(client, "Date validation account")
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]
    response = client.post(
        "/api/appointments",
        json=appointment_payload(circle_id, starts_at="not-a-date")
    )
    assert response.status_code == 422
