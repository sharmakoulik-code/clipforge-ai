from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.core.config import settings
from app.main import app
from app.models.models import User
from app.core.security import SESSION_COOKIE


def register(client: TestClient, email: str = "creator@example.com") -> dict:
    response = client.post("/api/auth/register", json={"email": email, "password": "SecureLongPassword!23", "display_name": "Clip Creator"})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = client.cookies.get("clipforge_csrf")
    return response.json()


def test_registration_login_protected_me_and_logout(client: TestClient) -> None:
    user = register(client)
    assert user["email"] == "creator@example.com"
    assert "password_hash" not in user
    assert SESSION_COOKIE in client.cookies
    assert client.get("/api/auth/me").json()["id"] == user["id"]
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.patch("/api/auth/profile", json={"display_name": "CSRF blocked"}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.get("/api/admin/health").status_code == 403
    with app.state.test_session_factory() as db:
        account = db.query(User).filter_by(id=user["id"]).one()
        account.is_admin = True
        db.commit()
    assert client.get("/api/admin/health").json()["role"] == "admin"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    failed = client.post("/api/auth/login", json={"email": user["email"], "password": "wrong-password"})
    assert failed.status_code == 401
    signed_in = client.post("/api/auth/login", json={"email": user["email"], "password": "SecureLongPassword!23"})
    assert signed_in.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_password_reset_revokes_sessions_and_accepts_new_password(client: TestClient) -> None:
    register(client, "reset@example.com")
    reset = client.post("/api/auth/password-reset", json={"email": "reset@example.com"})
    assert reset.status_code == 202
    token = reset.json()["debug_token"]
    assert token
    changed = client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "AnotherSecurePass!45"})
    assert changed.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": "reset@example.com", "password": "SecureLongPassword!23"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "reset@example.com", "password": "AnotherSecurePass!45"}).status_code == 200
    assert client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "ThirdSecurePassword!89"}).status_code == 400


def test_production_password_reset_uses_mail_adapter_without_exposing_token(client: TestClient, monkeypatch) -> None:
    register(client, "mail@example.com")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(auth_api, "send_password_reset_email", lambda recipient, token: sent.append((recipient, token)))
    response = client.post("/api/auth/password-reset", json={"email": "mail@example.com"})
    assert response.status_code == 202
    assert response.json()["debug_token"] is None
    assert len(sent) == 1 and sent[0][0] == "mail@example.com"


def test_profile_update_is_authenticated(client: TestClient) -> None:
    register(client)
    response = client.patch("/api/auth/profile", json={"display_name": "New Creator Name"})
    assert response.status_code == 200
    assert response.json()["display_name"] == "New Creator Name"
