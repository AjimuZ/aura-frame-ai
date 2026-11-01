"""Tests for the API request guard (session token, Host allowlist, no CORS) and the health route.

No photo is read; the app is exercised in memory through the FastAPI test client.
"""

import pytest
from fastapi.testclient import TestClient

from api import API_VERSION
from api.app import create_app, create_app_from_env
from api.models import ApiModel
from api.security import (
    ALLOWED_HOSTS_ENV_VAR,
    TOKEN_ENV_VAR,
    is_allowed_host,
    is_valid_token,
)

TOKEN = "test-session-token-0123456789"
HOSTS = ("127.0.0.1", "localhost")
GOOD_HOST = "127.0.0.1:8000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# Collects every response header whose name starts with access-control- (CORS).
def _cors_headers(response) -> list[str]:
    return [name for name in response.headers if name.lower().startswith("access-control-")]


class _Payload(ApiModel):
    count: int


@pytest.fixture()
def client() -> TestClient:
    app = create_app(TOKEN, HOSTS)

    # Test-only route to prove request bodies are validated by Pydantic behind the guard.
    @app.post("/echo")
    def echo(payload: _Payload) -> _Payload:
        return payload

    return TestClient(app, base_url=f"http://{GOOD_HOST}")


def test_health_with_token_and_local_host(client: TestClient) -> None:
    response = client.get("/health", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": API_VERSION}


def test_missing_token_is_refused(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer wrong-token-0123456789",
        "Bearer ",
        "Basic " + TOKEN,
        TOKEN,
        "Bearer " + TOKEN + "x",
    ],
)
def test_wrong_token_is_refused(client: TestClient, header: str) -> None:
    response = client.get("/health", headers={"Authorization": header})
    assert response.status_code == 401


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", "0.0.0.0:8000"])
def test_foreign_host_is_refused_even_with_token(client: TestClient, host: str) -> None:
    response = client.get("/health", headers={**AUTH, "Host": host})
    assert response.status_code == 403
    assert response.json()["code"] == "foreign_host"


def test_localhost_name_is_allowed(client: TestClient) -> None:
    response = client.get("/health", headers={**AUTH, "Host": "localhost:3000"})
    assert response.status_code == 200


def test_unknown_path_is_guarded(client: TestClient) -> None:
    assert client.get("/nope").status_code == 401
    assert client.get("/nope", headers=AUTH).status_code == 404


def test_docs_and_openapi_are_disabled(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers=AUTH).status_code == 404


def test_no_cors_header_on_any_response(client: TestClient) -> None:
    origin = {"Origin": "http://evil.example"}
    responses = [
        client.get("/health", headers={**AUTH, **origin}),
        client.get("/health", headers=origin),
        client.get("/health", headers={**AUTH, **origin, "Host": "evil.example"}),
        client.options(
            "/health",
            headers={**origin, "Access-Control-Request-Method": "GET"},
        ),
        client.options(
            "/health",
            headers={**AUTH, **origin, "Access-Control-Request-Method": "GET"},
        ),
    ]
    for response in responses:
        assert _cors_headers(response) == []


def test_invalid_body_gives_422_behind_the_guard(client: TestClient) -> None:
    assert client.post("/echo", json={"count": 1}).status_code == 401
    assert client.post("/echo", json={"count": 1}, headers=AUTH).json() == {"count": 1}
    assert client.post("/echo", json={"count": "many"}, headers=AUTH).status_code == 422
    assert client.post("/echo", json={"count": 1, "extra": True}, headers=AUTH).status_code == 422


def test_short_token_is_rejected_at_startup() -> None:
    with pytest.raises(ValueError):
        create_app("short", HOSTS)


def test_app_from_env_requires_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError):
        create_app_from_env()


def test_app_from_env_uses_token_and_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN)
    monkeypatch.setenv(ALLOWED_HOSTS_ENV_VAR, "gateway.internal")
    client = TestClient(create_app_from_env(), base_url="http://gateway.internal")
    assert client.get("/health", headers=AUTH).status_code == 200
    other = TestClient(create_app_from_env(), base_url="http://127.0.0.1:8000")
    assert other.get("/health", headers=AUTH).status_code == 403


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("LOCALHOST:80", True),
        (None, False),
        ("", False),
        ("localhost:99999", False),
        ("localhost@evil.example", False),
        ("localhost/evil", False),
        ("[::1]:8000", False),
    ],
)
def test_is_allowed_host(host: str | None, expected: bool) -> None:
    assert is_allowed_host(host, HOSTS) is expected


def test_is_valid_token_requires_bearer_scheme() -> None:
    assert is_valid_token(f"bearer {TOKEN}", TOKEN) is True
    assert is_valid_token(None, TOKEN) is False
    assert is_valid_token("Bearer", TOKEN) is False
