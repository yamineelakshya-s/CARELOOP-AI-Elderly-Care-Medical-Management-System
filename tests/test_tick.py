import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from urllib.error import URLError

from app.config import settings
from app.database import connect
from app.main import app
from app.repositories.sqlite_repository import add_medication
from app.routes.auth import clear_rate_limit_state
from app.services import mailer_service, tick_service
from app.services.auth_service import create_invitation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "careloop.db"))
    monkeypatch.setattr(settings, "enable_demo_accounts", False)
    monkeypatch.setattr(settings, "brevo_api_key", None)
    monkeypatch.setattr(settings, "sender_email", None)
    monkeypatch.setattr(settings, "tick_secret", None)
    clear_rate_limit_state()
    with TestClient(app) as test_client:
        yield test_client
    clear_rate_limit_state()


def create_elder(client, name, email=None):
    code = create_invitation(name, "elder")
    credentials = (
        {"email": email, "password": "valid password 123"}
        if email else {}
    )
    response = client.post(
        "/api/auth/join",
        json={"join_code": code, **credentials}
    )
    assert response.status_code == 200
    user = response.json()["user"]
    circle = client.get("/api/dashboard").json()["care_circles"][0]
    return user, circle["id"]


def create_medication(elder_id, name="Daily medication"):
    return add_medication(
        elder_id,
        name,
        dosage="1 tablet",
        schedule="Once daily"
    )


def add_dose(client, medication_id, scheduled_at, status="Pending"):
    response = client.post(
        f"/api/medications/{medication_id}/doses",
        json={"scheduled_at": scheduled_at.isoformat(), "status": status}
    )
    assert response.status_code == 201
    return next(
        dose for dose in response.json()["doses"]
        if dose["scheduled_at"] == scheduled_at.isoformat()
    )["id"]


def configure_mail(monkeypatch):
    monkeypatch.setattr(settings, "brevo_api_key", SecretStr("test-api-key"))
    monkeypatch.setattr(settings, "sender_email", "careloop@example.test")
    monkeypatch.setattr(settings, "tick_secret", SecretStr("test-tick-secret"))


def test_tick_requires_correct_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "tick_secret", SecretStr("expected-secret"))

    assert client.post("/api/tick").status_code == 401
    assert client.post(
        "/api/tick",
        headers={"X-Tick-Secret": "wrong-secret"}
    ).status_code == 401
    success = client.post(
        "/api/tick",
        headers={"X-Tick-Secret": "expected-secret"}
    )
    assert success.status_code == 200
    assert success.json() == {
        "processed_events": 0,
        "emails_sent": 0,
        "failed_attempts": 0,
        "skipped_events": 0,
        "alerts_created": 0
    }


def test_healthz_reports_application_health(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_upcoming_email_only_sends_for_pending_doses_and_is_idempotent(
    client,
    monkeypatch
):
    elder, _ = create_elder(client, "Reminder Elder", "elder@example.test")
    medication_id = create_medication(elder["id"])
    now = int(time.time())
    scheduled_at = datetime.fromtimestamp(now + 600, timezone.utc)
    add_dose(client, medication_id, scheduled_at)
    add_dose(
        client,
        medication_id,
        scheduled_at + timedelta(minutes=5),
        status="Taken"
    )
    configure_mail(monkeypatch)
    sent = []
    monkeypatch.setattr(
        mailer_service,
        "send_transactional_email",
        lambda email, subject, body: sent.append((email, subject, body))
    )

    first = tick_service.process_tick(now)
    second = tick_service.process_tick(now)

    assert first["processed_events"] == 1
    assert first["emails_sent"] == 1
    assert first["failed_attempts"] == 0
    assert len(sent) == 1
    assert sent[0][0] == "elder@example.test"
    assert second["emails_sent"] == 0
    assert len(sent) == 1
    with connect() as connection:
        logs = connection.execute(
            "SELECT status, attempt_count FROM email_notification_log"
        ).fetchall()
    assert [(row["status"], row["attempt_count"]) for row in logs] == [
        ("sent", 1)
    ]


def test_missing_configuration_is_skipped_then_retried(client, monkeypatch):
    elder, _ = create_elder(client, "Retry Elder", "retry@example.test")
    medication_id = create_medication(elder["id"])
    now = int(time.time())
    add_dose(
        client,
        medication_id,
        datetime.fromtimestamp(now + 600, timezone.utc)
    )

    skipped = tick_service.process_tick(now)
    assert skipped["skipped_events"] == 1
    with connect() as connection:
        row = connection.execute(
            "SELECT status, attempt_count FROM email_notification_log"
        ).fetchone()
    assert (row["status"], row["attempt_count"]) == ("skipped", 1)

    configure_mail(monkeypatch)
    sent = []
    monkeypatch.setattr(
        mailer_service,
        "send_transactional_email",
        lambda email, subject, body: sent.append(email)
    )
    retried = tick_service.process_tick(now)
    assert retried["emails_sent"] == 1
    assert sent == ["retry@example.test"]
    with connect() as connection:
        row = connection.execute(
            "SELECT status, attempt_count FROM email_notification_log"
        ).fetchone()
    assert (row["status"], row["attempt_count"]) == ("sent", 2)


def test_failed_email_is_retried_without_logging_private_error(
    client,
    monkeypatch
):
    elder, _ = create_elder(client, "Network Elder", "network@example.test")
    medication_id = create_medication(elder["id"])
    now = int(time.time())
    add_dose(
        client,
        medication_id,
        datetime.fromtimestamp(now + 600, timezone.utc)
    )
    configure_mail(monkeypatch)
    outcomes = iter((URLError("private response details"), None))

    def send(*_args):
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(mailer_service, "send_transactional_email", send)
    first = tick_service.process_tick(now)
    second = tick_service.process_tick(now)

    assert first["failed_attempts"] == 1
    assert second["emails_sent"] == 1
    with connect() as connection:
        row = connection.execute(
            "SELECT status, attempt_count, last_error FROM email_notification_log"
        ).fetchone()
    assert (row["status"], row["attempt_count"]) == ("sent", 2)
    assert row["last_error"] is None


def test_overdue_pending_dose_creates_one_private_relay_alert(
    client,
    monkeypatch
):
    elder, circle_id = create_elder(client, "Overdue Elder")
    authorized_code = client.post(
        "/api/auth/invitations",
        json={
            "name": "Authorized Caregiver",
            "role": "caregiver",
            "permissions": ["medications", "relay"]
        }
    ).json()["join_code"]
    unauthorized_code = client.post(
        "/api/auth/invitations",
        json={
            "name": "Unlinked Data Viewer",
            "role": "family",
            "permissions": []
        }
    ).json()["join_code"]
    with TestClient(app) as authorized:
        member = authorized.post(
            "/api/auth/join",
            json={
                "join_code": authorized_code,
                "email": "authorized@example.test",
                "password": "valid password 123"
            }
        ).json()["user"]
        authorized_session = authorized.cookies.get("careloop_session")
    with TestClient(app) as unauthorized:
        unauthorized_user = unauthorized.post(
            "/api/auth/join",
            json={
                "join_code": unauthorized_code,
                "email": "unauthorized@example.test",
                "password": "valid password 123"
            }
        ).json()["user"]
        unauthorized_session = unauthorized.cookies.get("careloop_session")

    medication_id = create_medication(elder["id"])
    now = int(time.time())
    dose_id = add_dose(
        client,
        medication_id,
        datetime.fromtimestamp(now - 16 * 60, timezone.utc)
    )
    configure_mail(monkeypatch)
    sent = []
    monkeypatch.setattr(
        mailer_service,
        "send_transactional_email",
        lambda email, subject, body: sent.append((email, subject, body))
    )

    first = tick_service.process_tick(now)
    second = tick_service.process_tick(now)

    assert first["alerts_created"] == 1
    assert first["emails_sent"] == 1
    assert second["alerts_created"] == 0
    assert second["emails_sent"] == 0
    assert len(sent) == 1
    assert sent[0][0] == "authorized@example.test"
    assert "unconfirmed" in sent[0][2].lower()
    assert "may not have been taken" in sent[0][2].lower()
    with connect() as connection:
        alert = connection.execute(
            """
            SELECT medication_dose_id, body, status
            FROM relay_items WHERE medication_dose_id = ?
            """,
            (dose_id,)
        ).fetchone()
        timeline_count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM care_activities
            WHERE elder_user_id = ? AND event_type = ?
            """,
            (elder["id"], "medication_dose_unconfirmed_alert")
        ).fetchone()["count"]
    assert alert["medication_dose_id"] == dose_id
    assert alert["status"] == "open"
    assert "unconfirmed" in alert["body"].lower()
    assert "may not have been taken" in alert["body"].lower()
    assert timeline_count == 1

    with TestClient(app) as authorized:
        authorized.cookies.set("careloop_session", authorized_session)
        notifications = authorized.get("/api/notifications").json()["notifications"]
        assert any(item["category"] == "medication" for item in notifications)
        assert len(authorized.get("/api/relay").json()["items"]) == 1
    with TestClient(app) as unauthorized:
        unauthorized.cookies.set("careloop_session", unauthorized_session)
        assert unauthorized.get("/api/relay").json()["items"] == []
        assert unauthorized.get("/api/notifications").json()["notifications"] == []
    assert member["id"] != unauthorized_user["id"]
    assert any(
        event["event_type"] == "medication_dose_unconfirmed_alert"
        for event in client.get("/api/timeline").json()["events"]
    )
    assert circle_id == client.get("/api/dashboard").json()["care_circles"][0]["id"]


def test_appointment_reminder_goes_only_to_circle_members_with_permission(
    client,
    monkeypatch
):
    elder, circle_id = create_elder(
        client, "Appointment Elder", "appointment-elder@example.test"
    )
    appointment_code = client.post(
        "/api/auth/invitations",
        json={
            "name": "Appointment Viewer",
            "role": "family",
            "permissions": ["appointments"]
        }
    ).json()["join_code"]
    private_code = client.post(
        "/api/auth/invitations",
        json={
            "name": "No Appointment Access",
            "role": "caregiver",
            "permissions": ["relay"]
        }
    ).json()["join_code"]
    with TestClient(app) as viewer:
        viewer_user = viewer.post(
            "/api/auth/join",
            json={
                "join_code": appointment_code,
                "email": "appointment-viewer@example.test",
                "password": "valid password 123"
            }
        ).json()["user"]
    with TestClient(app) as private_member:
        private_user = private_member.post(
            "/api/auth/join",
            json={
                "join_code": private_code,
                "email": "private-member@example.test",
                "password": "valid password 123"
            }
        ).json()["user"]

    now = int(time.time())
    starts_at = datetime.fromtimestamp(now + 24 * 60 * 60, timezone.utc)
    created = client.post(
        "/api/appointments",
        json={
            "care_circle_id": circle_id,
            "title": "Care review",
            "starts_at": starts_at.isoformat(),
            "provider": "Care team",
            "location": "Clinic"
        }
    )
    assert created.status_code == 201
    configure_mail(monkeypatch)
    sent = []
    monkeypatch.setattr(
        mailer_service,
        "send_transactional_email",
        lambda email, subject, body: sent.append(email)
    )

    result = tick_service.process_tick(now)

    assert result["processed_events"] == 1
    assert result["emails_sent"] == 2
    assert set(sent) == {
        "appointment-elder@example.test",
        "appointment-viewer@example.test"
    }
    assert "private-member@example.test" not in sent
    assert viewer_user["id"] != private_user["id"]


def test_concurrent_ticks_claim_the_same_email_event_once(client, monkeypatch):
    elder, _ = create_elder(client, "Concurrent Tick Elder", "tick@example.test")
    medication_id = create_medication(elder["id"])
    now = int(time.time())
    add_dose(
        client,
        medication_id,
        datetime.fromtimestamp(now + 600, timezone.utc)
    )
    configure_mail(monkeypatch)
    started = Event()
    release = Event()
    sent = []

    def send(email, subject, body):
        sent.append(email)
        started.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(mailer_service, "send_transactional_email", send)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(tick_service.process_tick, now)
        assert started.wait(timeout=5)
        second = executor.submit(tick_service.process_tick, now)
        release.set()
        first_result = first.result(timeout=10)
        second_result = second.result(timeout=10)

    assert first_result["emails_sent"] + second_result["emails_sent"] == 1
    assert first_result["skipped_events"] + second_result["skipped_events"] == 1
    assert sent == ["tick@example.test"]
    with connect() as connection:
        row = connection.execute(
            "SELECT status, attempt_count FROM email_notification_log"
        ).fetchone()
    assert (row["status"], row["attempt_count"]) == ("sent", 1)