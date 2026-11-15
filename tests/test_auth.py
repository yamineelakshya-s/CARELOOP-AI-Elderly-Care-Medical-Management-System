from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings, settings
from app.database import connect, initialize_database
from app.main import app
from app.repositories.sqlite_repository import (
    add_appointment,
    add_care_activity,
    add_medication
)
from app.routes import auth as auth_routes
from app.routes.auth import clear_rate_limit_state
from app.services import auth_service
from app.services.auth_service import (
    SESSION_HOURS,
    create_invitation,
    hash_join_code,
    redeem_join_code
)


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
    return response.json()["user"], code


def test_users_redeem_unique_single_use_invitation_codes(client):
    first_code = create_invitation("Elder One", "elder")
    second_code = create_invitation("Elder Two", "elder")

    assert len(first_code) == 8
    assert len(second_code) == 8
    assert first_code != second_code

    first_response = client.post(
        "/api/auth/join",
        json={"join_code": first_code}
    )
    first_user = first_response.json()["user"]
    client.cookies.clear()
    second_response = client.post(
        "/api/auth/join",
        json={"join_code": second_code}
    )
    second_user = second_response.json()["user"]

    assert first_response.status_code == second_response.status_code == 200
    assert first_user["id"] != second_user["id"]
    assert first_user["name"] == "Elder One"
    assert second_user["name"] == "Elder Two"
    assert first_user["role"] == second_user["role"] == "elder"

    with TestClient(app) as replay_client:
        replay = replay_client.post(
            "/api/auth/join",
            json={"join_code": first_code}
        )
    assert replay.status_code == 401

    with connect() as connection:
        stored_hashes = {
            row["code_hash"]
            for row in connection.execute("SELECT code_hash FROM invitations")
        }
    assert hash_join_code(first_code) in stored_hashes
    assert first_code not in stored_hashes


def test_invitation_code_can_only_be_redeemed_once_concurrently(client):
    code = create_invitation("Concurrent Account", "elder")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(redeem_join_code, (code, code)))

    assert sum(result is not None for result in results) == 1


def test_demo_accounts_are_opt_in_and_have_no_seeded_health_records(client, monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_accounts", True)
    initialize_database()

    response = client.post(
        "/api/auth/join",
        json={"join_code": "LAKSHMI1"}
    )

    assert response.status_code == 200
    assert response.json()["user"]["is_demo"] is True
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["profile"]["name"] == "Lakshmi"
    assert dashboard["care_circles"][0]["medications"] == []

    client.cookies.clear()
    meena = client.post(
        "/api/auth/join",
        json={"join_code": "MEENA001"}
    )
    assert meena.status_code == 200
    assert client.get("/api/dashboard").json()["care_circles"] == []

    monkeypatch.setattr(settings, "enable_demo_accounts", False)
    assert client.get("/api/auth/me").status_code == 401


def test_demo_accounts_are_seeded_on_startup_and_isolated_from_real_data(
    tmp_path,
    monkeypatch
):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "startup-demo.db"))
    monkeypatch.setattr(settings, "enable_demo_accounts", True)
    clear_rate_limit_state()

    with TestClient(app) as client:
        assert "LAKSHMI1" in client.get("/login").text

        real_user_code = create_invitation("Real User", "elder")
        real_user_response = client.post(
            "/api/auth/join",
            json={"join_code": real_user_code}
        )
        assert real_user_response.status_code == 200
        real_user = real_user_response.json()["user"]
        real_dashboard = client.get("/api/dashboard").json()
        assert len(real_dashboard["care_circles"]) == 1
        medication_id = add_medication(
            real_user["id"],
            "Private real-user medication",
            dosage="1 tablet"
        )
        appointment_id = add_appointment(
            real_user["id"],
            "Private real-user appointment",
            "2030-01-02T10:00:00+00:00"
        )

        expected_demo_accounts = (
            ("LAKSHMI1", "Lakshmi", "elder", 1),
            ("MEENA001", "Meena", "caregiver", 0),
            ("FAMILY01", "Arjun", "family", 0)
        )
        for code, name, role, circle_count in expected_demo_accounts:
            client.cookies.clear()
            login = client.post("/api/auth/join", json={"join_code": code})
            assert login.status_code == 200
            assert login.json()["user"]["is_demo"] is True
            dashboard = client.get("/api/dashboard")
            assert dashboard.status_code == 200
            assert dashboard.json()["profile"]["name"] == name
            assert dashboard.json()["profile"]["role"] == role
            assert len(dashboard.json()["care_circles"]) == circle_count
            assert client.get("/api/medications").json()["medications"] == []
            assert client.get("/api/appointments").json()["appointments"] == []
            assert client.get(
                f"/api/medications/{medication_id}"
            ).status_code == 404
            assert client.get(
                f"/api/appointments/{appointment_id}"
            ).status_code == 404

        client.cookies.clear()
        assert client.post(
            "/api/auth/join",
            json={"join_code": real_user_code}
        ).status_code == 401
        with connect() as connection:
            assert connection.execute(
                "SELECT 1 FROM medications WHERE id = ? AND elder_user_id = ?",
                (medication_id, real_user["id"])
            ).fetchone()
            assert connection.execute(
                "SELECT 1 FROM appointments WHERE id = ? AND elder_user_id = ?",
                (appointment_id, real_user["id"])
            ).fetchone()

    clear_rate_limit_state()


def test_synthetic_demo_accounts_are_disabled_by_default(client):
    response = client.post(
        "/api/auth/join",
        json={"join_code": "LAKSHMI1"}
    )

    assert settings.enable_demo_accounts is False
    assert response.status_code == 401
    assert "LAKSHMI1" not in client.get("/login").text


def test_relative_database_path_is_anchored_to_project_root():
    configured = Settings(
        _env_file=None,
        database_path="data/example.sqlite3"
    )

    assert configured.database_path == str(
        PROJECT_ROOT / "data" / "example.sqlite3"
    )


def test_valid_join_returns_authenticated_profile(client):
    invited_user, _ = create_elder(client, "Asha Rao")

    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["user"] == invited_user
    assert response.json()["user"]["name"] == "Asha Rao"
    assert response.json()["user"]["role"] == "elder"
    assert response.json()["user"]["is_demo"] is False


def test_registration_logout_and_password_relogin_preserve_account(client):
    code = create_invitation("Returning Elder", "elder")
    email = "returning@example.test"
    password = "correct horse battery staple 42"
    registration = client.post(
        "/api/auth/join",
        json={"join_code": code, "email": email, "password": password}
    )
    assert registration.status_code == 200
    user = registration.json()["user"]
    assert user["has_credentials"] is True
    assert "password" not in user
    assert "password_hash" not in user
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]

    with connect() as connection:
        stored = connection.execute(
            "SELECT email, password_hash FROM users WHERE id = ?",
            (user["id"],)
        ).fetchone()
    assert stored["email"] == email
    assert stored["password_hash"] != password
    assert stored["password_hash"].startswith("pbkdf2_sha256$")

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    wrong_password = client.post(
        "/api/auth/login",
        json={"email": email, "password": "not the right password"}
    )
    assert wrong_password.status_code == 401
    signed_in = client.post(
        "/api/auth/login",
        json={"email": email.upper(), "password": password}
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["id"] == user["id"]
    assert signed_in.json()["user"]["has_credentials"] is True
    assert client.get("/api/dashboard").json()["care_circles"][0]["id"] == circle_id

    with TestClient(app) as replay_client:
        replay = replay_client.post(
            "/api/auth/join",
            json={"join_code": code, "email": email, "password": password}
        )
    assert replay.status_code == 401


def test_password_registration_requires_eight_characters(client):
    code = create_invitation("Minimum Password Account", "elder")
    too_short = client.post(
        "/api/auth/join",
        json={
            "join_code": code,
            "email": "minimum@example.test",
            "password": "short7"
        }
    )
    assert too_short.status_code == 422

    accepted = client.post(
        "/api/auth/join",
        json={
            "join_code": code,
            "email": "minimum@example.test",
            "password": "exactly8"
        }
    )
    assert accepted.status_code == 200


def test_five_consecutive_login_failures_trigger_fifteen_minute_lockout(
    client,
    monkeypatch
):
    code = create_invitation("Locked Account", "elder")
    email = "locked@example.test"
    password = "valid password 123"
    assert client.post(
        "/api/auth/join",
        json={"join_code": code, "email": email, "password": password}
    ).status_code == 200
    client.post("/api/auth/logout")

    for _ in range(4):
        response = client.post(
            "/api/auth/login",
            json={"email": email, "password": "incorrect password"}
        )
        assert response.status_code == 401

    locked = client.post(
        "/api/auth/login",
        json={"email": email, "password": "incorrect password"}
    )
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "900"

    client_locked_until = auth_routes.login_locked_until["testclient"]
    assert client_locked_until > time.time()
    retry = client.post(
        "/api/auth/login",
        json={"email": email, "password": password}
    )
    assert retry.status_code == 429

    monkeypatch.setattr(
        auth_routes,
        "time",
        SimpleNamespace(time=lambda: client_locked_until + 1)
    )
    signed_in = client.post(
        "/api/auth/login",
        json={"email": email, "password": password}
    )
    assert signed_in.status_code == 200
    assert auth_routes.login_locked_until == {}
    assert auth_routes.login_attempts == {}


def test_successful_login_resets_consecutive_failure_count(client):
    code = create_invitation("Reset Account", "elder")
    email = "reset@example.test"
    password = "valid password 123"
    assert client.post(
        "/api/auth/join",
        json={"join_code": code, "email": email, "password": password}
    ).status_code == 200
    client.post("/api/auth/logout")

    assert client.post(
        "/api/auth/login",
        json={"email": email, "password": "incorrect password"}
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": email, "password": password}
    ).status_code == 200
    assert auth_routes.login_attempts == {}


def test_legacy_signed_in_user_can_set_credentials_once(client):
    user, _ = create_elder(client, "Legacy Account")
    assert client.get("/api/auth/me").json()["user"]["has_credentials"] is False

    response = client.post(
        "/api/auth/credentials",
        json={
            "email": "legacy@example.test",
            "password": "a secure legacy password 123"
        }
    )
    assert response.status_code == 201
    assert response.json()["has_credentials"] is True
    assert client.get("/api/auth/me").json()["user"]["has_credentials"] is True
    assert client.post(
        "/api/auth/credentials",
        json={
            "email": "another@example.test",
            "password": "another secure password 123"
        }
    ).status_code == 409

    client.post("/api/auth/logout")
    signed_in = client.post(
        "/api/auth/login",
        json={
            "email": "legacy@example.test",
            "password": "a secure legacy password 123"
        }
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["id"] == user["id"]


def test_expired_password_session_is_rejected(client, monkeypatch):
    code = create_invitation("Session Expiry Elder", "elder")
    joined = client.post(
        "/api/auth/join",
        json={
            "join_code": code,
            "email": "expiry@example.test",
            "password": "a secure expiry password 123"
        }
    )
    assert joined.status_code == 200
    expired_at = int(time.time()) + SESSION_HOURS * 3600 + 1
    monkeypatch.setattr(
        auth_service,
        "time",
        SimpleNamespace(time=lambda: expired_at)
    )

    assert client.get("/api/auth/me").status_code == 401
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login?expired=1"


def test_email_collision_does_not_consume_invitation(client):
    first_code = create_invitation("First Owner", "elder")
    second_code = create_invitation("Second Owner", "elder")
    credentials = {
        "email": "unique@example.test",
        "password": "a secure email password 123"
    }
    assert client.post(
        "/api/auth/join",
        json={"join_code": first_code, **credentials}
    ).status_code == 200
    client.post("/api/auth/logout")

    collision = client.post(
        "/api/auth/join",
        json={"join_code": second_code, **credentials}
    )
    assert collision.status_code == 409
    assert client.post(
        "/api/auth/join",
        json={
            "join_code": second_code,
            "email": "second@example.test",
            "password": credentials["password"]
        }
    ).status_code == 200


def test_auth_migration_preserves_existing_users(tmp_path, monkeypatch):
    database_path = tmp_path / "existing-users.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO users (id, name, role, is_demo, created_at)
            VALUES ('existing-user', 'Existing Elder', 'elder', 0, 123)
            """
        )

    monkeypatch.setattr(settings, "database_path", str(database_path))
    initialize_database()

    with connect() as connection:
        row = connection.execute(
            "SELECT id, name, email, password_hash FROM users WHERE id = ?",
            ("existing-user",)
        ).fetchone()
    assert row["name"] == "Existing Elder"
    assert row["email"] is None
    assert row["password_hash"] is None


def test_join_sets_eight_hour_http_only_cookie(client):
    code = create_invitation("Account Owner", "elder")
    response = client.post("/api/auth/join", json={"join_code": code})

    cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "max-age=28800" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_invalid_and_malformed_join_codes_are_rejected(client):
    invalid = client.post(
        "/api/auth/join",
        json={"join_code": "INVALID1"}
    )
    malformed = client.post(
        "/api/auth/join",
        json={"join_code": "short"}
    )

    assert invalid.status_code == 401
    assert "invalid, expired, or has already been used" in invalid.json()["detail"]
    assert malformed.status_code == 422


def test_expired_invitation_cannot_be_redeemed(client):
    code = create_invitation("Expired Account", "elder")
    with connect() as connection:
        connection.execute(
            "UPDATE invitations SET expires_at = 0 WHERE code_hash = ?",
            (hash_join_code(code),)
        )

    response = client.post("/api/auth/join", json={"join_code": code})

    assert response.status_code == 401


def test_unauthenticated_dashboard_redirects_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/api/auth/me").status_code == 401


def test_invalid_or_expired_session_redirects_with_expiry_notice(client):
    client.cookies.set("careloop_session", "expired-or-invalid-token")

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?expired=1"


def test_login_page_has_eight_character_invitation_form(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert 'id="join-code"' in response.text
    assert 'maxlength="8"' in response.text
    assert "LAKSHMI1" not in response.text


def test_dashboard_profile_and_content_are_loaded_for_current_user(client):
    user, _ = create_elder(client, "Nora Shah")
    page = client.get("/")
    dashboard = client.get("/api/dashboard")
    script = client.get("/static/js/app.js")

    assert page.status_code == 200
    assert 'id="profile-name">Nora Shah</strong>' in page.text
    assert "Lakshmi" not in page.text
    assert "Metformin" not in page.text
    assert dashboard.json()["profile"]["id"] == user["id"]
    assert dashboard.headers["cache-control"] == "no-store"
    assert dashboard.json()["care_circles"] == [
        {
            "id": dashboard.json()["care_circles"][0]["id"],
            "elder": {"id": user["id"], "name": "Nora Shah"},
            "member_count": 1,
            "permissions": ["activities", "appointments", "medications"],
            "medications": [],
            "appointments": [],
            "activities": []
        }
    ]
    assert 'fetch("/api/auth/me")' in script.text
    assert 'fetch("/api/dashboard")' in script.text


def test_elder_can_invite_caregiver_with_limited_access(client):
    elder, _ = create_elder(client, "Elder One")
    invite = client.post(
        "/api/auth/invitations",
        json={
            "name": "Caregiver One",
            "role": "caregiver",
            "permissions": ["medications"]
        }
    )

    assert invite.status_code == 200
    assert invite.headers["cache-control"] == "no-store"
    code = invite.json()["join_code"]
    assert len(code) == 8

    with TestClient(app) as caregiver_client:
        joined = caregiver_client.post(
            "/api/auth/join",
            json={"join_code": code}
        )
        dashboard = caregiver_client.get("/api/dashboard").json()

    assert joined.status_code == 200
    assert joined.json()["user"]["name"] == "Caregiver One"
    assert dashboard["profile"]["role"] == "caregiver"
    assert len(dashboard["care_circles"]) == 1
    assert dashboard["care_circles"][0]["elder"]["id"] == elder["id"]
    assert dashboard["care_circles"][0]["permissions"] == ["medications"]


def test_caregiver_and_family_only_receive_explicitly_authorized_circle_data(client):
    elder_one, _ = create_elder(client, "Elder One")
    medication_one = "Account One Medication"
    add_medication(elder_one["id"], medication_one, dosage="1 tablet")
    add_appointment(
        elder_one["id"],
        "Account One Appointment",
        "2030-01-02T10:00:00Z"
    )
    add_care_activity(
        elder_one["id"],
        "Account One Activity",
        "2030-01-01T10:00:00Z"
    )
    caregiver_code = client.post(
        "/api/auth/invitations",
        json={
            "name": "Linked Caregiver",
            "role": "caregiver",
            "permissions": ["medications", "appointments"]
        }
    ).json()["join_code"]
    family_code = client.post(
        "/api/auth/invitations",
        json={"name": "Limited Family", "role": "family", "permissions": []}
    ).json()["join_code"]

    client.cookies.clear()
    elder_two, _ = create_elder(client, "Elder Two")
    add_medication(elder_two["id"], "Account Two Medication")

    with TestClient(app) as caregiver_client:
        caregiver_client.post(
            "/api/auth/join",
            json={"join_code": caregiver_code}
        )
        caregiver_data = caregiver_client.get("/api/dashboard").json()

    assert len(caregiver_data["care_circles"]) == 1
    caregiver_circle = caregiver_data["care_circles"][0]
    assert caregiver_circle["elder"]["name"] == "Elder One"
    assert [item["name"] for item in caregiver_circle["medications"]] == [medication_one]
    assert [item["title"] for item in caregiver_circle["appointments"]] == [
        "Account One Appointment"
    ]
    assert caregiver_circle["activities"] == []
    assert "Account Two Medication" not in str(caregiver_data)

    with TestClient(app) as family_client:
        family_client.post("/api/auth/join", json={"join_code": family_code})
        family_data = family_client.get("/api/dashboard").json()
    assert family_data["care_circles"][0]["elder"]["name"] == "Elder One"
    assert family_data["care_circles"][0]["medications"] == []
    assert family_data["care_circles"][0]["appointments"] == []

    elder_two_data = client.get("/api/dashboard").json()
    assert len(elder_two_data["care_circles"]) == 1
    assert [item["name"] for item in elder_two_data["care_circles"][0]["medications"]] == [
        "Account Two Medication"
    ]
    assert medication_one not in str(elder_two_data)


def test_non_elder_cannot_create_care_circle_invitations(client):
    _, _ = create_elder(client, "Elder")
    code = client.post(
        "/api/auth/invitations",
        json={"name": "Caregiver", "role": "caregiver", "permissions": []}
    ).json()["join_code"]
    client.cookies.clear()
    client.post("/api/auth/join", json={"join_code": code})

    response = client.post(
        "/api/auth/invitations",
        json={"name": "Another Member", "role": "family", "permissions": []}
    )

    assert response.status_code == 403


def test_logout_clears_session_and_dashboard_access(client):
    create_elder(client, "Account Owner")

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303
