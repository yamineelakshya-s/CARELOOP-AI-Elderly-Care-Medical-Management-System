import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import settings
from app.main import app
from app.repositories.sqlite_repository import add_medication
from app.routes.auth import clear_rate_limit_state
from app.services import gemini_service
from app.services.auth_service import create_invitation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "careloop.db"))
    monkeypatch.setattr(settings, "enable_demo_accounts", False)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    clear_rate_limit_state()
    with TestClient(app) as test_client:
        yield test_client
    clear_rate_limit_state()


def create_elder(client, name):
    code = create_invitation(name, "elder")
    response = client.post("/api/auth/join", json={"join_code": code})
    assert response.status_code == 200
    profile = response.json()["user"]
    circle_id = client.get("/api/dashboard").json()["care_circles"][0]["id"]
    return profile, circle_id


def invite_member(client, role, permissions, name):
    response = client.post(
        "/api/auth/invitations",
        json={"name": name, "role": role, "permissions": permissions}
    )
    assert response.status_code == 200
    return response.json()["join_code"]


def test_relay_crud_and_circle_permissions(client):
    elder, circle_id = create_elder(client, "Relay Elder")
    manager_code = invite_member(
        client, "caregiver", ["relay_manage"], "Relay Manager"
    )
    viewer_code = invite_member(
        client, "family", ["relay"], "Relay Viewer"
    )

    with TestClient(app) as manager:
        joined = manager.post(
            "/api/auth/join", json={"join_code": manager_code}
        )
        member_id = joined.json()["user"]["id"]
        manager_session = manager.cookies.get("careloop_session")
        created = manager.post(
            "/api/relay",
            json={
                "care_circle_id": circle_id,
                "title": "Check in",
                "body": "Call the care circle",
                "assigned_to_user_id": member_id
            }
        )
        assert created.status_code == 201
        item = created.json()
        assert item["can_manage"] is True
        assert item["assigned_to_user_id"] == member_id
        invalid_assignment = manager.post(
            "/api/relay",
            json={
                "care_circle_id": circle_id,
                "title": "Invalid assignment",
                "body": "",
                "assigned_to_user_id": "unlinked-user-id"
            }
        )
        assert invalid_assignment.status_code == 404
        assert len(manager.get("/api/relay").json()["items"]) == 1
        updated = manager.put(
            f"/api/relay/{item['id']}",
            json={
                "title": "Follow-up completed",
                "body": "",
                "assigned_to_user_id": None,
                "status": "completed"
            }
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "completed"
        assert manager.get(f"/api/relay/{item['id']}").status_code == 200

    with TestClient(app) as viewer:
        assert viewer.post(
            "/api/auth/join", json={"join_code": viewer_code}
        ).status_code == 200
        item = viewer.get("/api/relay").json()["items"][0]
        assert item["can_manage"] is False
        assert viewer.put(
            f"/api/relay/{item['id']}",
            json={
                "title": "Unauthorized edit",
                "body": "",
                "assigned_to_user_id": None,
                "status": "open"
            }
        ).status_code == 404
        assert viewer.delete(f"/api/relay/{item['id']}").status_code == 404

    assert client.get("/api/relay").json()["items"][0]["elder_name"] == "Relay Elder"
    assert elder["id"] != member_id
    assert client.get("/api/notifications").json()["notifications"][0]["category"] == "relay"
    assert client.get("/api/timeline").json()["events"]
    with TestClient(app) as manager:
        manager.cookies.set("careloop_session", manager_session)
        item_id = manager.get("/api/relay").json()["items"][0]["id"]
        assert manager.delete(f"/api/relay/{item_id}").status_code == 204
        assert manager.get("/api/relay").json()["items"] == []


def test_approval_workflow_notifies_requester_and_is_private(client):
    _, circle_id = create_elder(client, "Approval Elder")
    requester_code = invite_member(
        client, "family", ["approvals"], "Requesting Family Member"
    )
    observer_code = invite_member(
        client, "caregiver", ["approvals"], "Other Family Observer"
    )

    with TestClient(app) as requester:
        requester_user = requester.post(
            "/api/auth/join", json={"join_code": requester_code}
        ).json()["user"]
        submitted = requester.post(
            "/api/approvals",
            json={
                "care_circle_id": circle_id,
                "request_type": "appointment",
                "title": "Request appointment review",
                "details": "Please review this request."
            }
        )
        assert submitted.status_code == 201
        request_id = submitted.json()["id"]
        assert requester.get("/api/approvals").json()["requests"][0]["id"] == request_id
        requester_session = requester.cookies.get("careloop_session")

    with TestClient(app) as observer:
        observer.post("/api/auth/join", json={"join_code": observer_code})
        assert observer.get("/api/approvals").json()["requests"] == []
        assert observer.get(f"/api/approvals/{request_id}").status_code == 404

    other_elder_code = create_invitation("Unlinked Elder", "elder")
    with TestClient(app) as other_elder:
        other_elder.post(
            "/api/auth/join", json={"join_code": other_elder_code}
        )
        assert other_elder.get(
            f"/api/approvals/{request_id}"
        ).status_code == 404
        assert other_elder.post(
            f"/api/approvals/{request_id}/decision",
            json={"status": "approved"}
        ).status_code == 404

    owner_notifications = client.get("/api/notifications").json()["notifications"]
    approval_notification = next(
        item for item in owner_notifications if item["category"] == "approval"
    )
    assert client.get(f"/api/approvals/{request_id}").json()["can_decide"] is True
    decision = client.post(
        f"/api/approvals/{request_id}/decision",
        json={"status": "approved", "decision_note": "Reviewed"}
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"
    assert client.post(
        f"/api/approvals/{request_id}/decision",
        json={"status": "rejected"}
    ).status_code == 404

    with TestClient(app) as requester:
        requester.cookies.set("careloop_session", requester_session)
        notifications = requester.get("/api/notifications").json()["notifications"]
        assert any(item["category"] == "approval" for item in notifications)
        assert requester.post(
            f"/api/notifications/{approval_notification['id']}/read"
        ).status_code == 404
        assert requester.get(f"/api/approvals/{request_id}").json()["status"] == "approved"
        assert requester.get("/api/timeline").json()["events"] == []

    assert client.get("/api/timeline").json()["events"]
    assert requester_user["role"] == "family"


def test_timeline_is_persistent_and_access_checked(client):
    _, circle_id = create_elder(client, "Timeline Elder")
    viewer_code = invite_member(
        client, "caregiver", ["activities"], "Timeline Viewer"
    )
    editor_code = invite_member(
        client, "family", ["activities_manage"], "Timeline Editor"
    )

    with TestClient(app) as viewer:
        viewer.post("/api/auth/join", json={"join_code": viewer_code})
        viewer_session = viewer.cookies.get("careloop_session")
        assert viewer.get("/api/timeline").json()["events"] == []
        assert viewer.post(
            "/api/timeline",
            json={"care_circle_id": circle_id, "description": "Not authorized"}
        ).status_code == 404

    with TestClient(app) as editor:
        editor.post("/api/auth/join", json={"join_code": editor_code})
        added = editor.post(
            "/api/timeline",
            json={"care_circle_id": circle_id, "description": "Care update recorded"}
        )
        assert added.status_code == 201
        assert editor.get("/api/timeline").json()["events"][0]["description"] == (
            "Care update recorded"
        )

    with TestClient(app) as viewer:
        viewer.cookies.set("careloop_session", viewer_session)
        assert viewer.get("/api/timeline").json()["events"][0]["actor_name"] == "Timeline Editor"

    other_elder_code = create_invitation("Private Timeline Elder", "elder")
    with TestClient(app) as other_elder:
        other_elder.post(
            "/api/auth/join", json={"join_code": other_elder_code}
        )
        assert other_elder.get("/api/timeline").json()["events"] == []


def test_profile_is_user_specific_and_ai_summary_stays_local(client):
    profile, _ = create_elder(client, "Original Name")
    updated = client.patch("/api/profile", json={"name": "Updated Name"})
    assert updated.status_code == 200
    assert updated.json()["profile"]["name"] == "Updated Name"
    assert client.get("/api/auth/me").json()["user"]["name"] == "Updated Name"
    assert client.get("/api/dashboard").json()["profile"]["id"] == profile["id"]

    summary = client.get("/api/ai/summary")
    assert summary.status_code == 200
    assert summary.json()["care_records_shared_with_gemini"] is False
    assert summary.json()["ai_status"] == "not_configured"
    assert "Updated Name" not in summary.text


def test_gemini_receives_only_generic_guidance_prompt(client, monkeypatch):
    profile, _ = create_elder(client, "Private Account Name")
    add_medication(
        profile["id"],
        "Private medicine name",
        dosage="Private dosage"
    )
    monkeypatch.setattr(
        gemini_service.settings,
        "gemini_api_key",
        SecretStr("test-only-placeholder")
    )
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{
                    "content": {
                        "parts": [{"text": "Coordinate with the care team."}]
                    }
                }]
            }

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(gemini_service.httpx, "post", fake_post)
    response = client.get("/api/ai/summary")

    prompt_body = str(captured["body"])
    assert response.status_code == 200
    assert "Private Account Name" not in prompt_body
    assert "Private medicine name" not in prompt_body
    assert "Private dosage" not in prompt_body
    assert response.json()["general_guidance"] == "Coordinate with the care team."
    assert response.json()["care_records_shared_with_gemini"] is False


def test_workflow_apis_require_authentication(client):
    endpoints = (
        "/api/relay",
        "/api/approvals",
        "/api/notifications",
        "/api/profile",
        "/api/timeline",
        "/api/ai/summary"
    )
    for endpoint in endpoints:
        assert client.get(endpoint).status_code == 401
