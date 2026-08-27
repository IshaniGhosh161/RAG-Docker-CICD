import os
import uuid

import pytest
import requests


BASE_URL = os.getenv("API_BASE_URL", "http://localhost:5000")


def api_request(method, path, **kwargs):
    timeout = kwargs.pop("timeout", 10)
    return requests.request(method, f"{BASE_URL}{path}", timeout=timeout, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def api_available():
    try:
        response = api_request("GET", "/api/health")
    except requests.RequestException as error:
        pytest.skip(f"API is not running at {BASE_URL}: {error}")
    if response.status_code != 200:
        pytest.skip(f"API health check failed: {response.status_code}")


@pytest.fixture
def user_and_session():
    suffix = uuid.uuid4().hex[:8]
    user = {
        "username": f"testuser_{suffix}",
        "password": "password123",
        "email": f"test_{suffix}@example.com",
    }
    register = api_request("POST", "/api/register", json=user)
    assert register.status_code == 201, register.text

    login = api_request(
        "POST",
        "/api/login",
        json={"username": user["username"], "password": user["password"]},
    )
    assert login.status_code == 200, login.text

    session = api_request(
        "POST",
        "/api/sessions",
        json={"username": user["username"], "session_name": "Test Session"},
    )
    assert session.status_code == 201, session.text
    return user, session.json()["session_id"]


def test_health_check():
    response = api_request("GET", "/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_prometheus_metrics_endpoint():
    response = api_request("GET", "/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_frontend_and_static_assets_are_served():
    frontend = api_request("GET", "/")
    stylesheet = api_request("GET", "/frontend/style.css")
    javascript = api_request("GET", "/frontend/app.js")

    assert frontend.status_code == 200
    assert "RAG Chat" in frontend.text
    assert stylesheet.status_code == 200
    assert javascript.status_code == 200


def test_unknown_route_returns_json_error():
    response = api_request("GET", "/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": "Endpoint not found"}


def test_cors_headers_are_present():
    response = api_request(
        "OPTIONS",
        "/api/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_validation_errors():
    assert api_request("POST", "/api/register", json={}).status_code == 400
    assert api_request("POST", "/api/login", json={}).status_code == 400
    assert api_request("POST", "/api/sessions", json={}).status_code == 400
    assert api_request("POST", "/api/sessions/unknown/messages", json={}).status_code == 400
    assert api_request("POST", "/api/sessions/unknown/generate", json={}).status_code == 400
    assert api_request("POST", "/api/sessions/unknown/generate-stream", json={}).status_code == 400


def test_duplicate_registration_and_invalid_login():
    suffix = uuid.uuid4().hex[:8]
    user = {
        "username": f"duplicate_{suffix}",
        "password": "password123",
        "email": f"duplicate_{suffix}@example.com",
    }
    first_registration = api_request("POST", "/api/register", json=user)
    duplicate_registration = api_request("POST", "/api/register", json=user)
    invalid_login = api_request(
        "POST",
        "/api/login",
        json={"username": user["username"], "password": "wrong-password"},
    )

    assert first_registration.status_code == 201
    assert duplicate_registration.status_code == 409
    assert invalid_login.status_code == 401


def test_session_and_message_lifecycle(user_and_session):
    user, session_id = user_and_session
    sessions = api_request("GET", "/api/sessions", params={"username": user["username"]})
    assert sessions.status_code == 200
    assert any(item["session_id"] == session_id for item in sessions.json())

    message = {"username": user["username"], "message": "Hello"}
    saved = api_request("POST", f"/api/sessions/{session_id}/messages", json=message)
    assert saved.status_code == 201

    messages = api_request("GET", f"/api/sessions/{session_id}/messages")
    assert messages.status_code == 200
    assert messages.json()[-1]["message"] == "Hello"

    deleted = api_request("DELETE", f"/api/sessions/{session_id}")
    assert deleted.status_code == 200
    assert api_request("GET", f"/api/sessions/{session_id}/messages").json() == []
    assert api_request("DELETE", f"/api/sessions/{session_id}").status_code == 404


def test_account_deletion_requires_correct_password_and_cascades_data():
    suffix = uuid.uuid4().hex[:8]
    user = {
        "username": f"delete_{suffix}",
        "password": "password123",
        "email": f"delete_{suffix}@example.com",
    }
    assert api_request("POST", "/api/register", json=user).status_code == 201
    session = api_request(
        "POST",
        "/api/sessions",
        json={"username": user["username"], "session_name": "Delete me"},
    )
    session_id = session.json()["session_id"]
    assert api_request(
        "POST",
        f"/api/sessions/{session_id}/messages",
        json={"username": user["username"], "message": "Sensitive message"},
    ).status_code == 201

    wrong_password = api_request(
        "DELETE",
        f"/api/users/{user['username']}",
        json={"password": "wrong-password"},
    )
    assert wrong_password.status_code == 401
    assert api_request(
        "DELETE",
        f"/api/users/{user['username']}",
        json={"password": user["password"]},
    ).status_code == 200
    assert api_request(
        "POST",
        "/api/login",
        json={"username": user["username"], "password": user["password"]},
    ).status_code == 401
    assert api_request("GET", f"/api/sessions/{session_id}/messages").json() == []


def test_delete_unknown_user_returns_not_found():
    response = api_request(
        "DELETE",
        f"/api/users/unknown_{uuid.uuid4().hex}",
        json={"password": "password123"},
    )
    assert response.status_code == 404


def test_generation_endpoint(user_and_session):
    user, session_id = user_and_session
    response = api_request(
        "POST",
        f"/api/sessions/{session_id}/generate",
        json={"username": user["username"], "message": "What is RAG?"},
        timeout=120,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("bot_response")


def test_streaming_generation_endpoint(user_and_session):
    user, session_id = user_and_session
    response = api_request(
        "POST",
        f"/api/sessions/{session_id}/generate-stream",
        json={"username": user["username"], "message": "Explain embeddings."},
        stream=True,
        timeout=120,
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("Content-Type", "")

    events = [line for line in response.iter_lines(decode_unicode=True) if line]
    assert any('"started": true' in line for line in events)
    assert any('"content"' in line for line in events)
    assert any('"done": true' in line for line in events)
