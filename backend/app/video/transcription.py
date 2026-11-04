from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


class TranscriptionError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _openai_transcribe(path: Path, model: str | None) -> dict[str, Any]:
    if not settings.openai_api_key:
        raise TranscriptionError("transcription_credentials_missing", "OPENAI_API_KEY is required for OpenAI transcription.")
    try:
        with path.open("rb") as audio:
            response = httpx.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={"model": model or "whisper-1", "response_format": "verbose_json", "timestamp_granularities[]": ["segment", "word"]},
                files={"file": (path.name, audio, "audio/wav")}, timeout=httpx.Timeout(120, connect=15),
            )
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        raise TranscriptionError("transcription_timeout", "The transcription provider timed out.", retryable=True) from exc
    except httpx.HTTPStatusError as exc:
        retryable = exc.response.status_code >= 500 or exc.response.status_code == 429
        message = "The transcription provider rejected the audio or request."
        try:
            message = exc.response.json().get("error", {}).get("message", message)[:300]
        except (ValueError, AttributeError):
            pass
        raise TranscriptionError("transcription_provider_error", message, retryable=retryable) from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError("transcription_unavailable", "Could not connect to the transcription provider.", retryable=True) from exc


@lru_cache(maxsize=2)
def _load_whisper_model(model_name: str, device: str, compute_type: str, model_cache: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError("transcription_engine_missing", "Install faster-whisper or configure TRANSCRIPTION_PROVIDER=openai.") from exc
    return WhisperModel(model_name, device=device, compute_type=compute_type, download_root=model_cache)


def _faster_whisper_transcribe(path: Path, model_name: str | None) -> dict[str, Any]:
    model_name = model_name or settings.whisper_model
    try:
        model = _load_whisper_model(model_name, settings.whisper_device, settings.whisper_compute_type, settings.whisper_model_cache)
        segments, info = model.transcribe(str(path), language=None, word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
        normalized = []
        for segment in segments:
            words = []
            for word in (segment.words or []):
                if word.start is None or word.end is None:
                    continue
                words.append({"word": word.word, "start": float(word.start), "end": float(word.end)})
            normalized.append({"start": float(segment.start), "end": float(segment.end), "text": segment.text.strip(), "speaker_id": None, "words": words or None})
        return {"language": info.language, "segments": normalized, "provider": "faster-whisper", "model": model_name}
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError("transcription_failed", "Local Whisper could not transcribe the extracted audio. Confirm the model is cached and the worker has sufficient memory.", retryable=False) from exc


def _normalize(raw: dict[str, Any], provider: str, model: str, duration: float) -> dict[str, Any]:
    raw_segments = raw.get("segments") or []
    raw_words = raw.get("words") or []
    words_by_segment: list[list[dict[str, Any]]] = [[] for _ in raw_segments]
    valid_words = []
    for word in raw_words:
        try:
            start, end = float(word["start"]), float(word["end"])
            text = str(word.get("word", word.get("text", ""))).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if text and 0 <= start < end <= duration + 1:
            valid_words.append({"word": text, "start": start, "end": min(end, duration)})
    for word in valid_words:
        midpoint = (word["start"] + word["end"]) / 2
        idx = next((i for i, segment in enumerate(raw_segments) if float(segment.get("start", 0)) <= midpoint <= float(segment.get("end", 0))), None)
        if idx is not None:
            words_by_segment[idx].append(word)
    segments = []
    for idx, item in enumerate(raw_segments):
        try:
            start, end = float(item["start"]), float(item["end"])
            text = str(item.get("text", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not text or start < 0 or start >= duration or start >= end or end > duration + 1:
            continue
        end = min(end, duration)
        if end <= start:
            continue
        words = item.get("words")
        if isinstance(words, list):
            clean_words = []
            for word in words:
                try:
                    ws, we = float(word["start"]), float(word["end"])
                    token = str(word.get("word", word.get("text", ""))).strip()
                except (KeyError, TypeError, ValueError):
                    continue
                if token and start <= ws < we <= min(end, duration) + .05:
                    clean_words.append({"word": token, "start": ws, "end": we})
            words = clean_words or words_by_segment[idx] or None
        else:
            words = words_by_segment[idx] or None
        speaker = item.get("speaker_id") or item.get("speaker")
        segments.append({"start": start, "end": min(end, duration), "text": text, "speaker_id": str(speaker) if speaker else None, "words": words})
    segments.sort(key=lambda segment: segment["start"])
    if not segments:
        raise TranscriptionError("transcript_empty", "The transcription provider returned no valid timestamped segments.")
    return {"language": str(raw.get("language") or "und"), "segments": segments, "provider": provider, "model": model, "text": " ".join(s["text"] for s in segments)}


def transcribe(audio_path: Path, duration: float, provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    provider = (provider or settings.transcription_provider).lower()
    selected_model = model or ("whisper-1" if provider == "openai" else settings.whisper_model)
    if provider == "openai":
        raw = _openai_transcribe(audio_path, model or "whisper-1")
    elif provider in {"faster-whisper", "local"}:
        raw = _faster_whisper_transcribe(audio_path, model)
    else:
        raise TranscriptionError("transcription_provider_unsupported", f"Unsupported transcription provider: {provider}.")
    return _normalize(raw, provider, selected_model, duration)


def save_transcript(transcript: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(transcript, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
