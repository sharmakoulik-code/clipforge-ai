from fastapi.testclient import TestClient


def test_root_is_available(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_is_available(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_database_and_redis(client: TestClient, monkeypatch) -> None:
    from app.api import health

    class FakeRedis:
        def ping(self):
            return True

        def close(self):
            return None

    monkeypatch.setattr(health.Redis, "from_url", lambda *args, **kwargs: FakeRedis())
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok", "redis": "ok"}


def test_readiness_fails_when_redis_is_unavailable(client: TestClient, monkeypatch) -> None:
    from redis.exceptions import ConnectionError
    from app.api import health

    class FakeRedis:
        def ping(self):
            raise ConnectionError("unavailable")

        def close(self):
            return None

    monkeypatch.setattr(health.Redis, "from_url", lambda *args, **kwargs: FakeRedis())
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "Redis is unavailable"


def test_cors_allows_csrf_header_from_configured_frontend(client: TestClient) -> None:
    response = client.options(
        "/api/auth/profile",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_does_not_allow_unconfigured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/auth/profile",
        headers={
            "Origin": "https://untrusted.example.invalid",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_api_rejects_untrusted_host(client: TestClient) -> None:
    response = client.get("/health", headers={"Host": "untrusted.example.invalid"})
    assert response.status_code == 400
