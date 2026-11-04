from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.models import (
    AuditLog, Clip, CreditTransaction, OAuthCredential, PlatformMetadata, Project,
    PublishingPost, PublishingSchedule, PublishingTarget, SocialAccount,
    SocialOAuthState, SocialProvider, Transcript, UsageRecord,
)
from app.social.crypto import decrypt_secret, encrypt_secret
from app.social.providers import YouTubeProvider
from app.social.providers.base import ProviderAccount, ProviderError, PublishResult
from app.storage.local import storage


def _register(client: TestClient, email: str) -> str:
    response = client.post("/api/auth/register", json={
        "email": email, "password": "SecureLongPassword!23", "display_name": "Stage Five Creator",
    })
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = client.cookies.get("clipforge_csrf")
    return response.json()["id"]


def test_youtube_oauth_state_is_one_time_pkce_and_tokens_are_encrypted(client, monkeypatch):
    user_id = _register(client, "social-oauth@example.com")
    monkeypatch.setattr(settings, "google_oauth_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "test-client-secret")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://app.example.test/api/social/oauth/youtube/callback")
    monkeypatch.setattr(settings, "social_token_encryption_key", Fernet.generate_key().decode())
    started = client.post("/api/social/accounts/youtube/oauth/start", json={})
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    state = query["state"][0]
    factory = client.app.state.test_session_factory
    with factory() as db:
        state_row = db.query(SocialOAuthState).one()
        assert state_row.user_id == user_id and state_row.state_hash != state
        assert len(state_row.code_verifier) >= 43

    exchanges = []
    def exchange(self, code, client_id, client_secret, redirect_uri, verifier):
        exchanges.append((code, verifier))
        return {"access_token": "access-secret", "refresh_token": "refresh-secret", "expires_in": 3600, "scope": "youtube.upload"}
    monkeypatch.setattr(YouTubeProvider, "exchange_code", exchange)
    monkeypatch.setattr(YouTubeProvider, "accounts", lambda self, token: [
        ProviderAccount("channel-1", "Authorized test channel", "@test", {"google_email": "creator@example.com"})
    ])
    callback = client.get(f"/api/social/oauth/youtube/callback?state={state}&code=ok", follow_redirects=False)
    assert callback.status_code == 303 and "connection=youtube_connected" in callback.headers["location"]
    with factory() as db:
        account = db.query(SocialAccount).filter_by(user_id=user_id).one()
        credential = db.query(OAuthCredential).filter_by(social_account_id=account.id).one()
        assert account.status == "connected"
        assert decrypt_secret(credential.access_token_encrypted) == "access-secret"
        assert decrypt_secret(credential.refresh_token_encrypted) == "refresh-secret"
        assert db.query(SocialOAuthState).one().consumed_at is not None
    replay = client.get(f"/api/social/oauth/youtube/callback?state={state}&code=ok", follow_redirects=False)
    assert "connection=invalid_state" in replay.headers["location"] and len(exchanges) == 1


def test_youtube_resumable_upload_adapter_is_mocked_and_requires_audience_declaration(tmp_path, monkeypatch):
    from app.social.providers import youtube
    video = tmp_path / "authorized.mp4"
    video.write_bytes(b"mock video bytes")
    observed = {}
    def session(url, **kwargs):
        observed["url"], observed["body"], observed["headers"] = url, kwargs["json"], kwargs["headers"]
        return SimpleNamespace(status_code=200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=mock"})
    def upload(url, **kwargs):
        observed["upload_url"], observed["bytes"] = url, kwargs["content"].read()
        return SimpleNamespace(status_code=200, json=lambda: {"id": "mock-video-id"})
    monkeypatch.setattr(youtube.httpx, "post", session)
    monkeypatch.setattr(youtube.httpx, "put", upload)
    result = YouTubeProvider().publish(video, {"title": "Test upload", "description": "Local mock", "hashtags": ["#education"]},
        {"privacy_status": "private", "made_for_kids": False}, "mock-token", request_key="stable-target-key")
    assert result.external_post_id == "mock-video-id"
    assert observed["url"].startswith("https://www.googleapis.com/upload/youtube/v3/videos?")
    assert observed["body"]["status"]["selfDeclaredMadeForKids"] is False
    assert observed["headers"]["X-Upload-Content-Type"] == "video/mp4"
    assert observed["bytes"] == b"mock video bytes"
    with pytest.raises(ProviderError, match="made for kids"):
        YouTubeProvider.validate({"title": "Missing declaration"}, {"privacy_status": "private"})


def test_metadata_uses_clip_transcript_and_is_idempotently_charged_or_refunded(client, monkeypatch):
    user_id = _register(client, "social-metadata@example.com")
    factory = client.app.state.test_session_factory
    with factory() as db:
        project = Project(owner_id=user_id, name="Metadata project", status="completed")
        db.add(project); db.flush()
        transcript = Transcript(project_id=project.id, provider="fixture", status="completed", text="Whole transcript.", segments=[
            {"start": 0, "end": 5, "text": "Outside."},
            {"start": 10, "end": 15, "text": "Clip-specific transcript."},
            {"start": 20, "end": 25, "text": "Also outside."},
        ])
        db.add(transcript); db.flush()
        clip = Clip(project_id=project.id, transcript_id=transcript.id, title="Clip title", start_seconds=10, end_seconds=15,
                    status="completed", metadata_json={"hook": "Clip hook"})
        provider = db.query(SocialProvider).filter_by(slug="youtube").one()
        db.add(clip); db.flush()
        account = SocialAccount(user_id=user_id, provider_id=provider.id, external_account_id="metadata-channel",
                                display_name="Metadata channel", status="connected")
        db.add(account); db.flush()
        post = PublishingPost(user_id=user_id, clip_id=clip.id, status="draft", title=clip.title, timezone="UTC",
                              idempotency_key="metadata-draft-key-0001")
        db.add(post); db.flush()
        target = PublishingTarget(post_id=post.id, social_account_id=account.id, provider_id=provider.id, status="draft",
            metadata_json={}, platform_options={"privacy_status": "private", "made_for_kids": None},
            idempotency_key="metadata-target-key-0001")
        db.add(target); db.commit()
        post_id, target_id = post.id, target.id
    generated = {"title": "Reviewed title", "description": "Accurate copy", "caption": "Accurate copy",
                 "hashtags": ["#education"], "keywords": [], "call_to_action": "", "thumbnail_text_suggestions": []}
    observed = []
    def generate(**kwargs):
        observed.append(kwargs["transcript"])
        assert kwargs["clip"]["transcript"] == "Clip-specific transcript."
        return generated, "mock-model"
    monkeypatch.setattr("app.api.social_publishing.generate_platform_content", generate)
    payload = {"idempotency_key": "metadata-generation-key-0001", "audience": "educators", "language": "English"}
    response = client.post(f"/api/social/posts/{post_id}/metadata/generate", json=payload)
    assert response.status_code == 200 and response.json()["metadata_by_target"][target_id] == generated
    assert observed == ["Clip-specific transcript."]
    assert client.post(f"/api/social/posts/{post_id}/metadata/generate", json=payload).status_code == 200
    monkeypatch.setattr("app.api.social_publishing.generate_platform_content", lambda **kwargs: (_ for _ in ()).throw(
        ProviderError("fixture_failure", "Mocked metadata failure.")))
    failed = client.post(f"/api/social/posts/{post_id}/metadata/generate", json={**payload, "idempotency_key": "metadata-generation-key-0002"})
    assert failed.status_code == 502
    with factory() as db:
        assert db.query(PlatformMetadata).filter_by(target_id=target_id).count() == 1
        assert db.query(UsageRecord).filter_by(user_id=user_id, metric="social_ai_metadata").count() == 1
        assert db.query(CreditTransaction).filter_by(transaction_type="social_ai_metadata").count() == 2
        assert db.query(CreditTransaction).filter_by(transaction_type="refund").count() == 1


def test_schedule_quota_ownership_and_daylight_saving_validation(client, tmp_path, monkeypatch):
    user_id = _register(client, "social-schedule@example.com")
    with TestClient(client.app) as other_client:
        other_id = _register(other_client, "social-other@example.com")
    factory = client.app.state.test_session_factory
    monkeypatch.setattr(storage, "root", tmp_path / "social-storage")
    video_key = "clips/authorized-test.mp4"
    path = storage.resolve(video_key); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"mock clip")
    with factory() as db:
        project = Project(owner_id=user_id, name="Scheduling project", status="completed")
        db.add(project); db.flush()
        clip = Clip(project_id=project.id, title="Scheduled clip", start_seconds=0, end_seconds=12, status="completed",
            output_video_key=video_key, metadata_json={"render_validation": {"width": 1080, "height": 1920, "duration_seconds": 12}})
        provider = db.query(SocialProvider).filter_by(slug="youtube").one()
        account = SocialAccount(user_id=user_id, provider_id=provider.id, external_account_id="schedule-channel",
                                display_name="Scheduling channel", status="connected")
        db.add_all([clip, account]); db.flush()
        clip_id, account_id = clip.id, account.id
        future = datetime.now(timezone.utc) + timedelta(days=2)
        for i in range(5):
            post = PublishingPost(user_id=user_id, clip_id=clip.id, status="scheduled", title=clip.title,
                                  timezone="UTC", idempotency_key=f"scheduled-post-{i:04d}")
            db.add(post); db.flush()
            target = PublishingTarget(post_id=post.id, social_account_id=account.id, provider_id=provider.id, status="scheduled",
                metadata_json={"title": "Valid YouTube title", "description": "Description", "hashtags": []},
                platform_options={"privacy_status": "private", "made_for_kids": False}, idempotency_key=f"scheduled-target-{i:04d}")
            db.add(target); db.flush()
            db.add(PublishingSchedule(target_id=target.id, scheduled_at=future + timedelta(minutes=i), timezone="UTC", status="scheduled"))
        foreign = PublishingPost(user_id=other_id, clip_id=clip.id, status="draft", title=clip.title,
                                 timezone="UTC", idempotency_key="foreign-owner-post")
        db.add(foreign); db.flush(); foreign_id = foreign.id; db.commit()
    assert client.post(f"/api/social/posts/{foreign_id}/publish").status_code == 404
    draft = client.post("/api/social/drafts", json={"clip_id": clip_id, "account_ids": [account_id],
        "idempotency_key": "sixth-draft-key-00001", "timezone": "UTC"})
    assert draft.status_code == 200, draft.text
    rejected = client.post(f"/api/social/posts/{draft.json()['id']}/schedule", json={
        "scheduled_at": (future + timedelta(hours=1)).isoformat(), "timezone": "UTC"})
    assert rejected.status_code == 429
    from app.api.social_publishing import _utc_schedule
    with pytest.raises(HTTPException) as invalid:
        _utc_schedule(datetime(2027, 3, 14, 2, 30), "America/New_York")
    assert invalid.value.status_code == 422
    assert _utc_schedule(datetime(2027, 3, 14, 3, 30), "America/New_York").hour == 7


def test_worker_and_beat_dispatch_are_idempotent_and_audited(client, tmp_path, monkeypatch):
    user_id = _register(client, "social-worker@example.com")
    monkeypatch.setattr(settings, "social_token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(storage, "root", tmp_path / "worker-storage")
    video_key = "clips/worker-test.mp4"
    path = storage.resolve(video_key); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"mock mp4 data")
    factory = client.app.state.test_session_factory
    with factory() as db:
        project = Project(owner_id=user_id, name="Worker project", status="completed")
        db.add(project); db.flush()
        clip = Clip(project_id=project.id, title="Worker clip", start_seconds=0, end_seconds=12, status="completed",
            output_video_key=video_key, metadata_json={"render_validation": {"width": 1080, "height": 1920, "duration_seconds": 12}})
        provider = db.query(SocialProvider).filter_by(slug="youtube").one()
        account = SocialAccount(user_id=user_id, provider_id=provider.id, external_account_id="worker-channel",
                                display_name="Worker channel", status="connected")
        db.add_all([clip, account]); db.flush()
        db.add(OAuthCredential(social_account_id=account.id, access_token_encrypted=encrypt_secret("test-access"),
            refresh_token_encrypted=encrypt_secret("test-refresh"), expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=["youtube.upload"]))
        post = PublishingPost(user_id=user_id, clip_id=clip.id, status="scheduled", title=clip.title, timezone="UTC",
                              idempotency_key="worker-post-idempotency-0001")
        db.add(post); db.flush()
        target = PublishingTarget(post_id=post.id, social_account_id=account.id, provider_id=provider.id, status="scheduled",
            metadata_json={"title": "Approved mock video", "description": "Local test", "hashtags": []},
            platform_options={"privacy_status": "private", "made_for_kids": False}, idempotency_key="worker-target-key-0001")
        db.add(target); db.flush()
        schedule = PublishingSchedule(target_id=target.id, scheduled_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                                      timezone="UTC", status="scheduled")
        db.add(schedule); db.commit(); target_id = target.id
    from app.workers import tasks
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    enqueued = []
    monkeypatch.setattr(tasks.publish_social_target, "apply_async", lambda *, args: enqueued.extend(args))
    dispatched = tasks.dispatch_due_social_publications.run()
    assert dispatched["dispatched"] == 1 and enqueued == [target_id]
    calls = []
    monkeypatch.setattr(YouTubeProvider, "publish", lambda self, *args, **kwargs: (
        calls.append(kwargs["request_key"]) or PublishResult("mock-video-id", "https://www.youtube.com/watch?v=mock-video-id", "mock-request")
    ))
    tasks.publish_social_target.run(target_id)
    tasks.publish_social_target.run(target_id)
    with factory() as db:
        target = db.get(PublishingTarget, target_id)
        schedule = db.query(PublishingSchedule).filter_by(target_id=target_id).one()
        assert target.status == "published" and schedule.status == "published"
        assert target.external_post_url.endswith("mock-video-id")
        assert calls == ["worker-target-key-0001"]
        assert db.query(UsageRecord).filter_by(user_id=user_id, metric="social_published_target").count() == 1
        assert db.query(AuditLog).filter_by(actor_user_id=user_id, action="social.post.published").count() == 1
