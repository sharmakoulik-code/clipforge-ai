import json
import subprocess

from app.video import metadata


def test_ffprobe_metadata_is_normalized(tmp_path, monkeypatch) -> None:
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"fixture")
    result = {
        "format": {"duration": "12.5", "format_name": "mov,mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "avg_frame_rate": "30000/1001"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    monkeypatch.setattr(metadata.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(metadata.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(result), ""))
    details = metadata.extract_video_metadata(video)
    assert details["duration_seconds"] == 12.5
    assert details["aspect_ratio"] == "9:16"
    assert details["audio_streams"] == 1
    assert details["frame_rate"] > 29.9
    assert details["file_size_bytes"] == 7


def test_ffprobe_missing_is_actionable(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException
    import pytest

    monkeypatch.setattr(metadata.shutil, "which", lambda _name: None)
    with pytest.raises(HTTPException) as error:
        metadata.extract_video_metadata(tmp_path / "not-created.mp4")
    assert error.value.status_code == 503
