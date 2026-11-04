import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.models import Clip, ProcessingJob, Project, User
from app.storage.local import storage
from app.video.analysis import analyze_transcript, resolve_analysis_model
from app.video.captions import generate_ass
from app.video.ffmpeg import extract_audio, probe_media
from app.video.render import render_clip
from app.video.transcription import _normalize
from app.video.vision import track_speakers_and_crops


def test_transcript_normalization_keeps_only_provider_word_timestamps():
    raw = {
        "language": "en", "segments": [{"start": 1.0, "end": 3.0, "text": "Hello there."}],
        "words": [{"word": "Hello", "start": 1.0, "end": 1.5}, {"word": "there.", "start": 1.6, "end": 2.1}],
    }
    value = _normalize(raw, "openai", "whisper-1", 10)
    assert value["language"] == "en"
    assert value["segments"][0]["words"] == raw["words"]
    phrase_only = _normalize({"segments": [{"start": 0, "end": 2, "text": "No word timing."}]}, "test", "test", 10)
    assert phrase_only["segments"][0]["words"] is None


def test_analysis_model_defaults_follow_selected_provider(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_model", "gpt-4o-mini")
    assert resolve_analysis_model("gemini", None) == "gemini-2.5-flash"
    assert resolve_analysis_model("openai", settings.ai_model) == "gpt-4o-mini"
    observed = {}

    def request(prompt, provider, model):
        observed["model"] = model
        return '{"clips": []}'

    monkeypatch.setattr("app.video.analysis._provider_request", request)
    monkeypatch.setattr("app.video.analysis._validate", lambda *args: [])
    analyze_transcript({"segments": []}, 30, 20, 60, 1, provider="gemini")
    assert observed["model"] == "gemini-2.5-flash"


def test_analysis_scores_weight_components_and_filters_bad_boundaries(monkeypatch):
    transcript = {"segments": [
        {"start": 0, "end": 10, "text": "A strong hook begins."},
        {"start": 10, "end": 21, "text": "Here is the useful explanation."},
        {"start": 21, "end": 30, "text": "And that is the memorable ending."},
    ]}
    response = {"clips": [
        {"title": "The explanation", "start_time": 0, "end_time": 30, "hook": "A strong hook", "reason": "Useful context", "category": "Education",
         "score_components": {"hook_strength": 100, "emotional_engagement": 80, "information_value": 90, "storytelling": 70, "standalone_context": 90, "retention_potential": 80, "ending_impact": 90}},
        {"title": "Bad timestamp", "start_time": -1, "end_time": 20, "hook": "bad", "reason": "bad", "category": "Other", "score_components": {key: 100 for key in ["hook_strength", "emotional_engagement", "information_value", "storytelling", "standalone_context", "retention_potential", "ending_impact"]}},
    ]}
    monkeypatch.setattr("app.video.analysis._provider_request", lambda *args: json.dumps(response))
    result = analyze_transcript(transcript, 30, 20, 60, 2, provider="openai", model="test")
    clip = result["clips"][0]
    assert clip["viral_score"] == pytest.approx(86.5)
    assert clip["start_time"] == 0 and clip["end_time"] == 30
    assert clip["transcript"] == "A strong hook begins. Here is the useful explanation. And that is the memorable ending."
    assert result["score_type"] == "estimated_engagement_not_probability"


def test_caption_word_highlight_and_style_customization(tmp_path):
    transcript = {"segments": [{"start": 1, "end": 3, "text": "This works.", "words": [
        {"word": "This", "start": 1, "end": 1.5}, {"word": "works.", "start": 1.5, "end": 2.2},
    ]}]}
    output = tmp_path / "word-captions.ass"
    result = generate_ass(transcript, output, 0, 5, "custom", {"font_family": "Arial", "font_size": 72, "text_color": "#ffffff", "active_word_color": "#ff00aa", "background_color": "#101010", "background_opacity": 60, "position": "top", "animation": "fade", "words_per_line": 3}, width=1080, height=1920)
    text = output.read_text()
    assert result["uses_word_timing"] is True
    assert result["caption_count"] == 2
    assert r"\1c&haa00ff&" in text.lower() and r"\fad(120,120)" in text.lower()
    assert "Dialogue: 0,0:00:01.00,0:00:01.50" in text
    assert "&H66" in text and ",8,72,72," in text


def _make_video(path: Path, duration: float = 21.0) -> None:
    os.environ["PATH"] = f"{Path(__file__).resolve().parents[2] / '.venv' / 'bin'}:{os.environ.get('PATH', '')}"
    import subprocess
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=teal:s=640x360:r=15",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}", "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True, capture_output=True)


def test_real_ffmpeg_vertical_render_and_audio_extraction(tmp_path):
    source = tmp_path / "authorized-generated-sample.mp4"
    _make_video(source)
    info = probe_media(source)
    assert info["audio_streams"] == 1 and info["duration_seconds"] >= 20
    audio = tmp_path / "audio.wav"
    audio_info = extract_audio(source, audio)
    assert audio_info["has_audio"] and audio.stat().st_size > 10_000
    crop = track_speakers_and_crops(source, 0, 20, output_width=180, output_height=320, sample_interval=1)
    assert crop["samples"] and all(0 <= row["x"] <= info["width"] for row in crop["samples"])
    transcript = {"segments": [{"start": 0, "end": 20, "text": "A generated authorized sample, used to verify phrase-caption timing."}]}
    subtitles = tmp_path / "captions.ass"
    caption_info = generate_ass(transcript, subtitles, 0, 20, "creator", width=180, height=320)
    assert caption_info["caption_count"] == 1 and "Dialogue:" in subtitles.read_text()
    output, thumbnail, preview = tmp_path / "clip.mp4", tmp_path / "clip.jpg", tmp_path / "preview.mp4"
    result = render_clip(source, output, 0, 20, crop, subtitles, thumbnail, preview, width=180, height=320, fps=15, bitrate="500k")
    assert result["validated"] and result["width"] == 180 and result["height"] == 320
    assert result["width"] < result["height"] and result["video_codec"] == "h264"
    assert thumbnail.is_file() and preview.is_file()
    moving_crop = {**crop, "method": "opencv_face_track", "samples": [
        {"time": 0, "x": 50, "y": 0, "width": crop["samples"][0]["width"], "height": crop["samples"][0]["height"]},
        {"time": 3, "x": 400, "y": 0, "width": crop["samples"][0]["width"], "height": crop["samples"][0]["height"]},
    ]}
    moving = render_clip(source, tmp_path / "moving.mp4", 0, 3, moving_crop, subtitles, tmp_path / "moving.jpg", tmp_path / "moving-preview.mp4", width=180, height=320, fps=15, bitrate="500k")
    assert moving["validated"] and moving["width"] < moving["height"]
    full_size = render_clip(source, tmp_path / "default-vertical.mp4", 0, 2, crop, subtitles,
                            tmp_path / "default-vertical.jpg", tmp_path / "default-preview.mp4", fps=15, bitrate="1M")
    assert (full_size["width"], full_size["height"]) == (1080, 1920)


def test_audio_extraction_reports_video_without_audio(tmp_path):
    import subprocess
    os.environ["PATH"] = f"{Path(__file__).resolve().parents[2] / '.venv' / 'bin'}:{os.environ.get('PATH', '')}"
    source = tmp_path / "silent-video.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, capture_output=True)
    result = extract_audio(source, tmp_path / "silent.wav")
    assert result["has_audio"] is False and result["reason"] == "source_has_no_audio"
    assert not (tmp_path / "silent.wav").exists()


def test_pipeline_completes_with_mocked_model_providers_and_real_ffmpeg(tmp_path, monkeypatch):
    from app.core import config
    from app.processing import pipeline

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    old_root = storage.root
    storage.root = tmp_path / "storage"
    storage.root.mkdir(parents=True)
    source_key = "source/integration.mp4"
    source = storage.resolve(source_key)
    source.parent.mkdir(parents=True, exist_ok=True)
    _make_video(source, 21)
    monkeypatch.setattr(config.settings, "render_width", 180)
    monkeypatch.setattr(config.settings, "render_height", 320)
    monkeypatch.setattr(config.settings, "render_fps", 15)
    monkeypatch.setattr(config.settings, "render_video_bitrate", "500k")
    monkeypatch.setattr(config.settings, "video_sample_interval_seconds", 2)
    transcript = {"language": "en", "provider": "test", "model": "fixture", "text": "This is a generated test transcript.", "segments": [
        {"start": 0.0, "end": 7.0, "text": "This is the opening thought."},
        {"start": 7.0, "end": 14.0, "text": "Here is a complete useful explanation."},
        {"start": 14.0, "end": 20.0, "text": "And this is a natural conclusion."},
    ]}
    monkeypatch.setattr(pipeline, "transcribe", lambda *args: transcript)
    analysis = {"provider": "test", "model": "fixture", "score_type": "estimated_engagement_not_probability", "clips": [{
        "title": "Generated sample clip", "start_time": 0.0, "end_time": 20.0, "viral_score": 72.0,
        "score_components": {"hook_strength": 70, "emotional_engagement": 65, "information_value": 75, "storytelling": 72, "standalone_context": 80, "retention_potential": 68, "ending_impact": 70},
        "hook": "This is the opening thought.", "reason": "Fixture for an authorized local render test.", "category": "Education", "transcript": transcript["text"],
    }]}
    monkeypatch.setattr(pipeline, "analyze_transcript", lambda *args, **kwargs: analysis)
    with sessions() as db:
        user = User(email="stage3@example.test", password_hash="fixture")
        db.add(user); db.flush()
        project = Project(owner_id=user.id, name="Authorized generated test", source_video_key=source_key,
                          source_filename="generated.mp4", source_metadata={"duration_seconds": 21, "width": 640, "height": 360}, status="processing")
        db.add(project); db.flush()
        job = ProcessingJob(project_id=project.id, requested_by_id=user.id, status="queued", options={"num_clips": 1, "min_clip_seconds": 20, "max_clip_seconds": 20, "transcription_provider": "faster-whisper", "ai_provider": "openai", "caption_style": "classic"})
        db.add(job); db.commit()
        job_id = job.id
    try:
        pipeline.run_processing_job(job_id, sessions)
        with sessions() as db:
            job = db.get(ProcessingJob, job_id)
            clip = db.query(Clip).filter_by(project_id=job.project_id).one()
            assert job.status == "completed" and job.progress == 100
            assert clip.status == "completed" and clip.output_video_key
            output = storage.resolve(clip.output_video_key)
            info = probe_media(output)
            assert info["width"] == 180 and info["height"] == 320 and info["width"] < info["height"]
            stages = {row.name: row.status for row in job.stages} if hasattr(job, "stages") else {}
            from app.models.models import ProcessingStage
            assert db.query(ProcessingStage).filter_by(job_id=job_id, status="completed").count() >= 8
        monkeypatch.setattr(pipeline, "transcribe", lambda *args: pytest.fail("cached transcript was transcribed again"))
        monkeypatch.setattr(pipeline, "analyze_transcript", lambda *args, **kwargs: pytest.fail("cached analysis was requested again"))
        monkeypatch.setattr(pipeline, "render_clip", lambda *args, **kwargs: pytest.fail("validated clip was rendered again"))
        pipeline.run_processing_job(job_id, sessions)
        with sessions() as db:
            assert db.get(ProcessingJob, job_id).status == "completed"
    finally:
        storage.root = old_root
        Base.metadata.drop_all(engine)
        engine.dispose()
