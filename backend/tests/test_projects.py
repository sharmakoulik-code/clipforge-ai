from pathlib import Path

from fastapi.testclient import TestClient

from app.api import projects as projects_api
from app.main import app
from app.storage.local import storage


def _register(client: TestClient, email: str) -> None:
    response = client.post("/api/auth/register", json={"email": email, "password": "SecureLongPassword!23", "display_name": "Test Creator"})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = client.cookies.get("clipforge_csrf")


def test_upload_project_metadata_jobs_dashboard_and_delete_are_owner_scoped(client: TestClient, monkeypatch) -> None:
    _register(client, "owner@example.com")
    monkeypatch.setattr(projects_api, "extract_video_metadata", lambda _path: {
        "duration_seconds": 32.5, "width": 1920, "height": 1080, "frame_rate": 29.97,
        "codec": "h264", "container": "mov,mp4,m4a,3gp,3g2,mj2", "audio_streams": 1,
        "audio_codecs": ["aac"], "aspect_ratio": "1920:1080", "file_size_bytes": 23,
    })
    dispatched: list[str] = []
    monkeypatch.setattr(projects_api, "dispatch_job", lambda job, _db: dispatched.append(job.id) or True)
    payload = b"\x00\x00\x00\x18ftypisom" + b"x" * 16
    response = client.post("/api/projects/upload", data={"project_name": "Launch interview"}, files={"file": ("source.mp4", payload, "video/mp4")})
    assert response.status_code == 201, response.text
    uploaded = response.json()
    project_id = uploaded["project"]["id"]
    job_id = uploaded["job"]["id"]
    assert uploaded["project"]["source_metadata"]["duration_seconds"] == 32.5
    assert uploaded["job"]["status"] == "queued"
    assert dispatched == [job_id]
    saved_source = next(storage.root.glob("source/*"))
    assert saved_source.exists()

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    assert len(detail.json()["jobs"]) == 1
    assert detail.json()["clip_count"] == 0
    assert client.get(f"/api/projects/{project_id}/status").json()["id"] == job_id
    assert client.get(f"/api/jobs/{job_id}").json()["project_id"] == project_id
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["total_projects"] == 1
    assert dashboard["processing_videos"] == 1

    with TestClient(app) as other_client:
        _register(other_client, "other@example.com")
        assert other_client.get(f"/api/projects/{project_id}").status_code == 404
        assert other_client.get(f"/api/jobs/{job_id}").status_code == 404
        assert other_client.delete(f"/api/projects/{project_id}").status_code == 404

    assert client.patch(f"/api/projects/{project_id}", json={"name": "Renamed interview"}).json()["name"] == "Renamed interview"
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert not saved_source.exists()
    assert client.get("/api/projects").json() == []


def test_upload_rejects_oversized_requests_before_parsing(client: TestClient) -> None:
    response = client.post("/api/projects/upload", content=b"", headers={"Content-Length": str(3 * 1024 * 1024 * 1024)})
    assert response.status_code == 413


def test_upload_rejects_unauthorized_extension_and_bad_container(client: TestClient) -> None:
    assert client.post("/api/projects/upload", files={"file": ("clip.mp4", b"bad", "video/mp4")}).status_code == 401
    _register(client, "upload@example.com")
    unsupported = client.post("/api/projects/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert unsupported.status_code == 415
    invalid_signature = client.post("/api/projects/upload", files={"file": ("fake.mp4", b"not a video", "video/mp4")})
    assert invalid_signature.status_code == 415
    assert client.get("/api/projects").json() == []


def test_youtube_reference_requires_permission_and_never_downloads(client: TestClient, monkeypatch) -> None:
    _register(client, "youtube@example.com")
    monkeypatch.setattr(projects_api, "_youtube_oembed", lambda _url: {"title": "Public demo", "author_name": "Channel"})
    no_consent = client.post("/api/projects/youtube", json={"url": "https://youtu.be/abcdefghijk", "user_confirms_rights": False})
    assert no_consent.status_code == 422
    result = client.post("/api/projects/youtube", json={"url": "https://youtu.be/abcdefghijk", "user_confirms_rights": True})
    assert result.status_code == 201
    body = result.json()
    assert body["import_status"] == "metadata_retrieved"
    assert body["requires_authorized_upload"] is True
    assert body["project"]["source_metadata"]["downloaded"] is False
    assert client.get("/api/projects").json()[0]["source_url"] == "https://www.youtube.com/watch?v=abcdefghijk"


def test_youtube_rejects_non_youtube_hosts(client: TestClient) -> None:
    _register(client, "urlcheck@example.com")
    response = client.post("/api/projects/youtube", json={"url": "https://example.com/watch?v=abcdefghijk", "user_confirms_rights": True})
    assert response.status_code == 422
