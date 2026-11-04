from decimal import Decimal

from fastapi.testclient import TestClient

from app.models.models import Clip, Project
from app.storage.local import storage


def _register(client: TestClient, email: str) -> str:
    response = client.post("/api/auth/register", json={"email": email, "password": "SecureLongPassword!23", "display_name": "Clip Tester"})
    assert response.status_code == 201
    client.headers["X-CSRF-Token"] = client.cookies.get("clipforge_csrf")
    return response.json()["id"]


def test_clip_results_media_edit_and_zip_are_owner_scoped(client: TestClient, monkeypatch) -> None:
    user_id = _register(client, "clips@example.com")
    with client.app.state.test_session_factory() as db:
        project = Project(owner_id=user_id, name="Results", source_filename="source.mp4", source_metadata={"duration_seconds": 120}, status="completed")
        db.add(project); db.flush()
        key = f"artifacts/{project.id}/clips/sample.mp4"
        file_path = storage.resolve(key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"test mp4 bytes")
        clip = Clip(project_id=project.id, title="Useful moment", start_seconds=Decimal("20.000"), end_seconds=Decimal("45.000"), score=Decimal("91.50"), status="completed", output_video_key=key,
                    metadata_json={"category": "Education", "hook": "Start here", "reason": "Strong explanation", "transcript": "This is a test clip.", "score_components": {"hook_strength": 92}, "thumbnail_key": None})
        db.add(clip); db.commit()
        project_id, clip_id = project.id, clip.id
    result = client.get(f"/api/projects/{project_id}/clips?sort=score")
    assert result.status_code == 200
    data = result.json()["clips"][0]
    assert data["viral_score"] == 91.5 and data["duration_seconds"] == 25
    assert data["category"] == "Education" and data["video_url"]
    media = client.get(f"/api/clips/{clip_id}/video?download=true")
    assert media.status_code == 200 and media.content == b"test mp4 bytes"
    changed = client.patch(f"/api/clips/{clip_id}", json={"title": "Renamed", "caption_style": "dynamic", "caption_options": {"font_size": 72, "position": "top", "animation": "fade"}})
    assert changed.status_code == 200 and changed.json()["title"] == "Renamed"
    assert changed.json()["caption_options"]["font_size"] == 72
    invalid = client.patch(f"/api/clips/{clip_id}", json={"caption_options": {"font_family": "Arial\nDialogue: injected"}})
    assert invalid.status_code == 422
    from app.workers.tasks import render_edited_clip
    queued = []
    monkeypatch.setattr(render_edited_clip, "apply_async", lambda args: queued.append(args) or None)
    rerender = client.post(f"/api/clips/{clip_id}/render")
    assert rerender.status_code == 202
    assert queued == [[clip_id, rerender.json()["job_id"]]]
    zipped = client.post(f"/api/projects/{project_id}/clips/download", json=[clip_id])
    assert zipped.status_code == 200 and zipped.headers["content-type"] == "application/zip"

    other_id = _register(client, "other-clips@example.com")
    assert other_id != user_id
    assert client.get(f"/api/clips/{clip_id}/video").status_code == 404
