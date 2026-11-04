import pytest

from app.core.config import settings
from app.video.analysis import AnalysisError, _provider_request, _request_openai


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": '{"clips": []}'}}]}


def test_openai_compatible_endpoint_and_configured_timeout(monkeypatch):
    observed = {}

    def post(url, headers, json, timeout):
        observed.update(url=url, headers=headers, timeout=timeout.read, connect=timeout.connect, model=json["model"])
        return Response()

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://llm.example.invalid/v1/")
    monkeypatch.setattr("app.video.analysis.httpx.post", post)

    result = _provider_request("Analyze this transcript", "openai-compatible", "fixture-model", 37)
    assert result == '{"clips": []}'
    assert observed == {
        "url": "https://llm.example.invalid/v1/chat/completions",
        "headers": {"Authorization": "Bearer test-key"},
        "timeout": 37,
        "connect": 9,
        "model": "fixture-model",
    }


def test_openai_compatible_endpoint_requires_https(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "http://insecure.example.invalid/v1")
    with pytest.raises(AnalysisError) as error:
        _request_openai("prompt", "model")
    assert error.value.code == "analysis_endpoint_invalid"
