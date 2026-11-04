from fastapi.testclient import TestClient


def test_login_is_rate_limited_per_account(client: TestClient) -> None:
    payload = {"email": "limited@example.com", "password": "not-the-password"}
    responses = [client.post("/api/auth/login", json=payload) for _ in range(11)]
    assert all(response.status_code == 401 for response in responses[:10])
    assert responses[-1].status_code == 429
    assert responses[-1].headers["retry-after"] == "60"
