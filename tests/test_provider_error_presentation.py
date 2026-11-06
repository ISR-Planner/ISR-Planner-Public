"""Offline infrastructure checks: no private dotenv files, provider SDKs, or network."""

from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.agents import llm_factory
from server.api import llm_routes

SYNTHETIC_SECRET = "synthetic-provider-credential"
SYNTHETIC_ERROR = f"SDK detail containing {SYNTHETIC_SECRET}"


@pytest.fixture(autouse=True)
def isolate_services(monkeypatch):
    for provider in llm_factory.PROVIDERS.values():
        if provider.requires_key:
            monkeypatch.delenv(provider.env_key, raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.delenv("ELEVENLABS_MODEL_ID", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    def unexpected(*args, **kwargs):
        pytest.fail("Unexpected provider or network call")

    monkeypatch.setattr(requests, "get", unexpected)
    monkeypatch.setattr(requests, "post", unexpected)
    monkeypatch.setattr(llm_factory, "get_llm", unexpected)


@pytest.fixture
def client():
    # Assemble only the infrastructure router; do not import server.main.
    app = FastAPI()
    app.include_router(llm_routes.router)
    with TestClient(app) as session:
        yield session


def assert_no_error_details(message, capsys):
    output = capsys.readouterr()
    for text in (message, output.out, output.err):
        assert SYNTHETIC_SECRET not in text
        assert "SDK detail" not in text


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ImportError(SYNTHETIC_ERROR), "Missing provider package"),
        (RuntimeError("401 " + SYNTHETIC_ERROR), "Invalid API key"),
        (RuntimeError("unauthorized " + SYNTHETIC_ERROR), "Invalid API key"),
        (RuntimeError("404 " + SYNTHETIC_ERROR), "not found or not accessible"),
        (RuntimeError("rate limited " + SYNTHETIC_ERROR), "Rate limited"),
        (RuntimeError(SYNTHETIC_ERROR), "Connection failed"),
        (requests.Timeout(SYNTHETIC_ERROR), "Connection failed"),
    ],
)
def test_llm_connection_errors_are_safe(client, monkeypatch, capsys, error, expected):
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", SYNTHETIC_SECRET)

    def get_llm(**kwargs):
        calls.append(kwargs)

        def invoke(_message):
            raise error

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(llm_factory, "get_llm", get_llm)
    response = client.post(
        "/api/llm/test",
        json={
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "ignored-client-value",
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert expected in response.json()["message"]
    assert len(calls) == 1  # No new retries.
    assert calls[0]["api_key"] == SYNTHETIC_SECRET
    assert_no_error_details(response.text, capsys)


def test_llm_constructor_import_error_is_safe(client, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SYNTHETIC_SECRET)

    def get_llm(**kwargs):
        raise ImportError(SYNTHETIC_ERROR)

    monkeypatch.setattr(llm_factory, "get_llm", get_llm)
    response = client.post(
        "/api/llm/test", json={"provider": "openai", "model": "gpt-4o"}
    )
    assert "Missing provider package" in response.json()["message"]
    assert_no_error_details(response.text, capsys)


@pytest.mark.parametrize("content", ["Connection successful", ""])
def test_llm_success_and_empty_response_contract(client, monkeypatch, content):
    monkeypatch.setenv("OPENAI_API_KEY", SYNTHETIC_SECRET)
    monkeypatch.setattr(
        llm_factory,
        "get_llm",
        lambda **_: SimpleNamespace(invoke=lambda _: SimpleNamespace(content=content)),
    )
    response = client.post(
        "/api/llm/test", json={"provider": "openai", "model": "gpt-4o"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "success": bool(content),
        "message": (
            "Connected to OpenAI (gpt-4o)"
            if content
            else "Received empty response from model"
        ),
    }


def test_missing_server_credential_never_uses_client_key(client):
    response = client.post(
        "/api/llm/test",
        json={
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "ignored-client-value",
        },
    )
    assert response.json() == {
        "success": False,
        "message": "OPENAI_API_KEY is not configured on the server",
    }


@pytest.mark.parametrize("failure", ["timeout", "http", "json"])
def test_ollama_discovery_failure_preserves_timeout_and_safe_envelope(
    client, monkeypatch, capsys, failure
):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if failure == "timeout":
            raise requests.Timeout(SYNTHETIC_ERROR)

        def raise_for_status():
            if failure == "http":
                raise requests.HTTPError(SYNTHETIC_ERROR)

        def json():
            raise ValueError(SYNTHETIC_ERROR)

        return SimpleNamespace(raise_for_status=raise_for_status, json=json)

    monkeypatch.setattr(requests, "get", get)
    response = client.get(
        "/api/llm/ollama/models", params={"base_url": "http://ollama.invalid/"}
    )
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["models"] == []
    assert "ollama serve" in response.json()["message"]
    assert calls == [("http://ollama.invalid/api/tags", {"timeout": 5})]
    assert_no_error_details(response.text, capsys)


def test_ollama_discovery_success_preserves_model_data(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_, **__: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "models": [{"name": "neutral-model", "details": {"family": "neutral"}}]
            },
        ),
    )
    response = client.get("/api/llm/ollama/models")
    assert response.json() == {
        "success": True,
        "models": [
            {"id": "neutral-model", "name": "neutral-model", "description": "neutral"}
        ],
    }


class AudioResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.closed = False

    @property
    def text(self):
        pytest.fail("Upstream error bodies must not be read into public diagnostics")

    def iter_content(self, chunk_size):
        assert chunk_size == 8192
        yield b"neutral-audio"
        yield b""

    def close(self):
        self.closed = True


@pytest.mark.parametrize("status", [200, 400, 401, 403, 429, 500])
def test_audio_response_preserves_status_stream_cleanup_and_timeout(
    client, monkeypatch, capsys, status
):
    monkeypatch.setenv("ELEVENLABS_API_KEY", SYNTHETIC_SECRET)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "environment-voice")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "environment-model")
    upstream = AudioResponse(status)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return upstream

    monkeypatch.setattr(requests, "post", post)
    response = client.post(
        "/api/tts/elevenlabs",
        json={"text": " neutral text ", "voice_id": "request-voice"},
    )
    assert response.status_code == status
    assert upstream.closed
    assert len(calls) == 1
    url, options = calls[0]
    assert "/request-voice/stream?output_format=mp3_44100_128" in url
    assert options["timeout"] == 30
    assert options["stream"] is True
    assert options["headers"]["xi-api-key"] == SYNTHETIC_SECRET
    assert options["json"]["model_id"] == "environment-model"
    assert options["json"]["text"] == "neutral text"
    if status == 200:
        assert response.content == b"neutral-audio"
        assert response.headers["content-type"] == "audio/mpeg"
    elif status in (401, 403):
        assert "authentication failed" in response.json()["detail"]
    elif status == 429:
        assert "rate limited" in response.json()["detail"]
    else:
        assert f"HTTP {status}" in response.json()["detail"]
    assert_no_error_details(response.text, capsys)


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError])
def test_audio_transport_failure_is_safe(client, monkeypatch, capsys, error):
    monkeypatch.setenv("ELEVENLABS_API_KEY", SYNTHETIC_SECRET)
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        raise error(SYNTHETIC_ERROR)

    monkeypatch.setattr(requests, "post", post)
    response = client.post("/api/tts/elevenlabs", json={"text": "neutral text"})
    assert response.status_code == 502
    assert response.json() == {
        "detail": "ElevenLabs request failed. Check service availability and try again."
    }
    assert len(calls) == 1
    assert_no_error_details(response.text, capsys)


@pytest.mark.parametrize("configured", [False, True])
def test_audio_missing_configuration_and_empty_text_skip_network(
    client, monkeypatch, configured
):
    if configured:
        monkeypatch.setenv("ELEVENLABS_API_KEY", SYNTHETIC_SECRET)
    response = client.post("/api/tts/elevenlabs", json={"text": " "})
    assert response.status_code == (400 if configured else 503)


def test_audio_stream_failure_still_closes_upstream(monkeypatch):
    import asyncio

    monkeypatch.setenv("ELEVENLABS_API_KEY", SYNTHETIC_SECRET)

    class InterruptedAudio(AudioResponse):
        def iter_content(self, chunk_size):
            yield b"neutral-audio"
            raise requests.Timeout("neutral stream interruption")

    upstream = InterruptedAudio(200)
    monkeypatch.setattr(requests, "post", lambda *_, **__: upstream)
    response = llm_routes.tts_elevenlabs(llm_routes.TTSRequest(text="neutral text"))

    async def consume():
        chunks = []
        with pytest.raises(requests.Timeout, match="neutral stream interruption"):
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        return chunks

    assert asyncio.run(consume()) == [b"neutral-audio"]
    assert upstream.closed
