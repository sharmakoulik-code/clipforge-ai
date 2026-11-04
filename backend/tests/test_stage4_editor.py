from decimal import Decimal

from fastapi.testclient import TestClient

from app.models.models import Clip, ClipRenderVersion, CreditReservation, CreditWallet, ProcessingJob, Project, Transcript
from app.storage.local import storage


def _login(client: TestClient, email: str) -> str:
    result = client.post("/api/auth/register", json={"email": email, "password": "SecureLongPassword!23", "display_name": "Editor"})
    assert result.status_code == 201, result.text
    client.headers["X-CSRF-Token"] = client.cookies.get("clipforge_csrf")
    return result.json()["id"]


def test_editor_patch_source_preview_caption_corrections_and_version_restore(client: TestClient, tmp_path):
    user_id = _login(client, "editor-stage4@example.com")
    with client.app.state.test_session_factory() as db:
        source_key = "source/editor-test.mp4"
        source_file = storage.resolve(source_key)
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(b"fake source video")
        transcript = Transcript(project_id="pending", provider="test", status="completed", segments=[
            {"start": 10, "end": 12, "text": "original caption", "words": [
                {"word": "original", "start": 10, "end": 10.8}, {"word": "caption", "start": 10.8, "end": 12}
            ]}
        ])
        project = Project(owner_id=user_id, name="Editor project", source_video_key=source_key,
                          source_filename="source.mp4", source_metadata={"duration_seconds": 100})
        db.add(project); db.flush()
        transcript.project_id = project.id
        db.add(transcript); db.flush()
        old_key, current_key = f"artifacts/{project.id}/clips/old.mp4", f"artifacts/{project.id}/clips/current.mp4"
        for key in (old_key, current_key):
            path = storage.resolve(key); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"video bytes")
        clip = Clip(project_id=project.id, transcript_id=transcript.id, title="Original", start_seconds=Decimal("10"),
                    end_seconds=Decimal("30"), status="completed", output_video_key=current_key,
                    metadata_json={"original_start_time": 10, "original_end_time": 30, "caption_style": "creator"})
        db.add(clip); db.flush()
        db.add_all([
            ClipRenderVersion(clip_id=clip.id, version=1, output_key=old_key, status="completed",
                              render_settings={"start_time": 9, "end_time": 31, "thumbnail_key": "old.jpg",
                                               "crop_settings": {"mode": "manual", "center_x": 0.6, "center_y": 0.5, "zoom": 1.2},
                                               "caption_style": "minimal", "caption_options": {"font_size": 44},
                                               "caption_segments": [{"start": 10, "end": 12, "text": "Old caption"},
                                                                    {"start": 13, "end": 14, "text": "Second caption"}],
                                               "title": "Restored title", "description": "Restored description", "hashtags": ["#restored"],
                                               "export_filename": "restored.mp4", "hide_captions": False, "watermark": True}),
            ClipRenderVersion(clip_id=clip.id, version=2, output_key=current_key, status="completed", render_settings={}),
        ])
        db.commit()
        project_id, clip_id = project.id, clip.id

    source = client.get(f"/api/projects/{project_id}/source")
    assert source.status_code == 200 and source.content == b"fake source video"
    captions = client.get(f"/api/clips/{clip_id}/captions")
    assert captions.status_code == 200 and captions.json()["segments"][0]["words"][0]["word"] == "original"
    patched = client.patch(f"/api/clips/{clip_id}", json={
        "description": "Human-approved description", "hashtags": ["#education", "#ai"],
        "export_filename": "highlight-final", "start_time": 11, "end_time": 28,
        "caption_segments": [{"start": 11, "end": 13, "text": "Corrected words!", "words": [
            {"word": "Corrected", "start": 11, "end": 12}, {"word": "words!", "start": 12, "end": 13}
        ]}], "hide_captions": False,
        "crop_settings": {"mode": "manual", "center_x": 0.7, "center_y": 0.4, "zoom": 1.5},
    })
    assert patched.status_code == 200
    assert patched.json()["description"] == "Human-approved description"
    assert patched.json()["export_filename"] == "highlight-final.mp4"
    assert patched.json()["crop_settings"]["mode"] == "manual"
    assert client.get(f"/api/clips/{clip_id}/captions").json()["segments"][0]["text"] == "Corrected words!"
    assert client.patch(f"/api/clips/{clip_id}", json={"caption_segments": [{"start": -5, "end": 9, "text": "bad"}]}).status_code == 422
    restored = client.post(f"/api/clips/{clip_id}/versions/1/restore")
    assert restored.status_code == 200 and restored.json()["clip"]["video_url"]
    with client.app.state.test_session_factory() as db:
        current = db.get(Clip, clip_id)
        assert current.output_video_key == old_key and current.status == "completed"
        assert current.start_seconds == Decimal("9") and current.end_seconds == Decimal("31")
        assert current.title == "Restored title"
        assert current.metadata_json["caption_style"] == "minimal"
        assert current.metadata_json["caption_segments"][0]["text"] == "Old caption"
        assert current.metadata_json["description"] == "Restored description"
        assert current.metadata_json["crop_settings"]["zoom"] == 1.2
        assert current.metadata_json["watermark"] is True
    assert client.post(f"/api/clips/{clip_id}/restore-ai-crop").status_code == 200
    assert client.post(f"/api/clips/{clip_id}/restore-ai-boundaries").json()["start_time"] == 10


def test_editor_rerender_reserves_credits_and_rejects_duplicate_dispatch(client: TestClient, monkeypatch):
    user_id = _login(client, "render-charge@example.com")
    with client.app.state.test_session_factory() as db:
        project = Project(owner_id=user_id, name="Render project", source_video_key="source/render.mp4",
                          source_filename="source.mp4", source_metadata={"duration_seconds": 30})
        db.add(project); db.flush()
        transcript = Transcript(project_id=project.id, provider="test", status="completed", segments=[])
        db.add(transcript); db.flush()
        clip = Clip(project_id=project.id, transcript_id=transcript.id, title="Render me",
                    start_seconds=Decimal("0"), end_seconds=Decimal("20"), status="needs_render")
        db.add(clip); db.commit()
        clip_id = clip.id

    from app.workers.tasks import render_edited_clip
    dispatched = []
    monkeypatch.setattr(render_edited_clip, "apply_async", lambda args: dispatched.append(args))
    response = client.post(f"/api/clips/{clip_id}/render")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["reserved_credits"] == 2
    assert dispatched == [[clip_id, body["job_id"]]]
    assert client.post(f"/api/clips/{clip_id}/render").status_code == 409
    with client.app.state.test_session_factory() as db:
        wallet = db.query(CreditWallet).filter_by(user_id=user_id).one()
        reservation = db.query(CreditReservation).filter_by(job_id=body["job_id"]).one()
        job = db.get(ProcessingJob, body["job_id"])
        assert wallet.reserved == 2 and reservation.status == "reserved"
        assert job.job_type == "clip_render" and job.options["clip_id"] == clip_id


def test_edited_render_worker_settles_credit_and_saves_version(client: TestClient, tmp_path, monkeypatch):
    user_id = _login(client, "render-worker@example.com")
    source_key = "source/worker-render.mp4"
    source = storage.resolve(source_key)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source fixture")
    with client.app.state.test_session_factory() as db:
        wallet = db.query(CreditWallet).filter_by(user_id=user_id).one()
        wallet.balance, wallet.reserved = wallet.balance - 2, 2
        project = Project(owner_id=user_id, name="Worker render", source_video_key=source_key,
                          source_filename="source.mp4", source_metadata={"duration_seconds": 30})
        db.add(project); db.flush()
        transcript = Transcript(project_id=project.id, provider="test", status="completed", segments=[])
        db.add(transcript); db.flush()
        clip = Clip(project_id=project.id, transcript_id=transcript.id, title="Output", start_seconds=Decimal("0"),
                    end_seconds=Decimal("20"), status="rendering", metadata_json={"crop_settings": {"mode": "manual"}})
        db.add(clip); db.flush()
        job = ProcessingJob(project_id=project.id, requested_by_id=user_id, job_type="clip_render", status="queued",
                            options={"clip_id": clip.id, "reserved_credits": 2})
        db.add(job); db.flush()
        reservation = CreditReservation(job_id=job.id, user_id=user_id, amount=2, consumed=0, status="reserved")
        db.add(reservation); db.commit()
        clip_id, job_id = clip.id, job.id

    from app.workers import tasks
    monkeypatch.setattr(tasks, "SessionLocal", client.app.state.test_session_factory)
    monkeypatch.setattr(tasks, "get_user_plan", lambda db, owner_id: ("free", {"export_width": 720, "export_height": 1280, "watermark": True}))
    monkeypatch.setattr(tasks, "manual_crop_plan", lambda *args: {"mode": "manual"})

    def fake_captions(_transcript, output_path, *args, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("captions")
        return {"caption_count": 0}

    def fake_render(_source, output_path, _start, _end, _crop, _caption, thumbnail_path, preview_path, **kwargs):
        for path in (output_path, thumbnail_path, preview_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"rendered")
        return {"validated": True, "duration_seconds": 20, "width": 720, "height": 1280}

    monkeypatch.setattr(tasks, "generate_ass", fake_captions)
    monkeypatch.setattr(tasks, "render_clip", fake_render)
    tasks.render_edited_clip.run(clip_id, job_id)

    with client.app.state.test_session_factory() as db:
        wallet = db.query(CreditWallet).filter_by(user_id=user_id).one()
        reservation = db.query(CreditReservation).filter_by(job_id=job_id).one()
        job = db.get(ProcessingJob, job_id)
        clip = db.get(Clip, clip_id)
        version = db.query(ClipRenderVersion).filter_by(clip_id=clip_id, version=1).one()
        assert wallet.reserved == 0 and wallet.balance == 58
        assert reservation.status == "settled" and reservation.consumed == 2
        assert job.status == "completed" and clip.status == "completed"
        assert clip.output_video_key == version.output_key
        assert version.validation_data["validated"] is True
