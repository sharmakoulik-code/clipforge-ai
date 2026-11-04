from fastapi.testclient import TestClient

from app.api import jobs as jobs_api
from app.api import projects as projects_api
from app.services import job_queue


def test_failed_job_can_be_retried_and_status_is_persisted(client: TestClient, monkeypatch) -> None:
    registered = client.post("/api/auth/register", json={"email": "retry@example.com", "password": "SecureLongPassword!23", "display_name": "Retry User"})
    assert registered.status_code == 201
    client.headers["X-CSRF-Token"] = client.cookies.get("clipforge_csrf")
    monkeypatch.setattr(projects_api, "extract_video_metadata", lambda _path: {
        "duration_seconds": 10.0, "width": 1280, "height": 720, "frame_rate": 25.0,
        "codec": "h264", "container": "mp4", "audio_streams": 0, "audio_codecs": [],
        "aspect_ratio": "1280:720", "file_size_bytes": 20,
    })
    monkeypatch.setattr(job_queue, "enqueue_uploaded_project", lambda _job_id: False)
    response = client.post("/api/projects/upload", files={"file": ("retry.mp4", b"\x00\x00\x00\x18ftypisom" + b"a" * 8, "video/mp4")})
    assert response.status_code == 201, response.text
    job_id = response.json()["job"]["id"]
    assert response.json()["job"]["status"] == "failed"
    assert response.json()["job"]["error_code"] == "queue_unavailable"

    monkeypatch.setattr(jobs_api, "dispatch_job", lambda job, db: True)
    retried = client.post(f"/api/jobs/{job_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    assert retried.json()["attempt"] == 1
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
