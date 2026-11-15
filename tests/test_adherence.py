from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
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


def invite_member(client, permissions):
    response = client.post(
        "/api/auth/invitations",
        json={
            "name": "Linked Member",
            "role": "caregiver",
            "permissions": permissions
        }
    )
    assert response.status_code == 200
    return response.json()["join_code"]


def test_weekly_adherence_counts_taken_missed_and_pending_doses(client):
    elder, circle_id = create_elder(client, "Adherence Elder")
    medication = client.post(
        "/api/medications",
        json={
            "care_circle_id": circle_id,
            "name": "Scheduled medication",
            "dosage": "1 tablet",
            "frequency": "Daily",
            "start_date": "2026-03-01",
            "end_date": None,
            "instructions": ""
        }
    ).json()
    monday = datetime(2026, 3, 16, 8, tzinfo=timezone.utc)
    statuses = ["Taken"] * 6 + ["Pending"]
    for day, status in enumerate(statuses):
        response = client.post(
            f"/api/medications/{medication['id']}/doses",
            json={
                "scheduled_at": (monday + timedelta(days=day)).isoformat(),
                "status": status
            }
        )
        assert response.status_code == 201

    response = client.get(
        "/api/medications/adherence?week_start=2026-03-18"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["week_start"] == "2026-03-16"
    assert response.json()["taken"] == 6
    assert response.json()["missed"] == 0
    assert response.json()["pending"] == 1
    assert response.json()["scheduled"] == 7
    assert response.json()["adherence_percent"] == 85.7
    assert response.json()["care_circles"][0]["elder_name"] == elder["name"]


def test_adherence_is_scoped_to_medication_permissions(client):
    elder, circle_id = create_elder(client, "Scoped Adherence Elder")
    medication = client.post(
        "/api/medications",
        json={
            "care_circle_id": circle_id,
            "name": "Private schedule",
            "dosage": "1 tablet",
            "frequency": "Daily",
            "start_date": "2026-03-01",
            "instructions": ""
        }
    ).json()
    dose_time = datetime(2026, 3, 16, 8, tzinfo=timezone.utc)
    assert client.post(
        f"/api/medications/{medication['id']}/doses",
        json={"scheduled_at": dose_time.isoformat(), "status": "Taken"}
    ).status_code == 201
    medication_viewer_code = invite_member(client, ["medications"])
    no_access_code = invite_member(client, [])

    with TestClient(app) as viewer:
        viewer.post("/api/auth/join", json={"join_code": medication_viewer_code})
        viewer_summary = viewer.get(
            "/api/medications/adherence?week_start=2026-03-16"
        ).json()
        assert viewer_summary["scheduled"] == 1
        assert viewer_summary["taken"] == 1
    with TestClient(app) as no_access:
        no_access.post("/api/auth/join", json={"join_code": no_access_code})
        private_summary = no_access.get(
            "/api/medications/adherence?week_start=2026-03-16"
        ).json()
        assert private_summary["care_circles"] == []
        assert private_summary["scheduled"] == 0
        assert private_summary["adherence_percent"] is None


def test_zero_dose_week_has_no_percentage_and_requires_authentication(client):
    elder, _ = create_elder(client, "Empty Adherence Elder")

    response = client.get(
        "/api/medications/adherence?week_start=2026-03-16"
    )
    assert response.status_code == 200
    assert response.json()["scheduled"] == 0
    assert response.json()["adherence_percent"] is None
    client.post("/api/auth/logout")
    assert client.get(
        "/api/medications/adherence?week_start=2026-03-16"
    ).status_code == 401
    assert elder["role"] == "elder"