from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.rate_limit import _requests, _lock
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    from app.social.providers import seed_provider_catalog
    with test_sessions() as seed_db:
        seed_provider_catalog(seed_db)
        seed_db.commit()

    def override_get_db():
        session = test_sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.state.test_session_factory = test_sessions
    with _lock:
        _requests.clear()
    from app.api import projects as projects_api
    monkeypatch.setattr(projects_api.storage, "root", tmp_path / "uploads")
    projects_api.storage.root.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()
