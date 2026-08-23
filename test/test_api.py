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


def test_validation_errors():
    assert api_request("POST", "/api/register", json={}).status_code == 400
    assert api_request("POST", "/api/login", json={}).status_code == 400
    assert api_request("POST", "/api/sessions", json={}).status_code == 400
    assert api_request("POST", "/api/sessions/unknown/messages", json={}).status_code == 400
    assert api_request("POST", "/api/sessions/unknown/generate", json={}).status_code == 400
    assert api_request("POST", "/api/sessions/unknown/generate-stream", json={}).status_code == 400


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
    assert api_request("DELETE", f"/api/sessions/{session_id}").status_code == 404


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
