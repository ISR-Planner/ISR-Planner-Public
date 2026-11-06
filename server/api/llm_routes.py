import os
from typing import Any, Optional

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from server.agents.llm_factory import (
    get_available_providers,
    get_configured_providers,
    list_ollama_models,
)
from server.agents.llm_factory import test_connection as test_llm_connection
from server.agents.llm_factory import (
    validate_provider_model,
)

from .models import LLMTestRequest, LLMTestResponse, TTSRequest

router = APIRouter()

ELEVENLABS_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
ELEVENLABS_DEFAULT_MODEL = "eleven_turbo_v2_5"


@router.get("/api/llm/providers")
def get_llm_providers():
    """
    Get list of available LLM providers and their models.
    Also indicates which providers have API keys configured in environment.
    """
    providers = get_available_providers()
    configured = get_configured_providers()

    for p in providers:
        p["configured"] = p["id"] in configured

    return {"providers": providers, "configured_providers": configured}


@router.post("/api/llm/test", response_model=LLMTestResponse)
def test_llm_provider(request: LLMTestRequest):
    """Test connection to an LLM provider with given credentials."""
    is_valid, error = validate_provider_model(request.provider, request.model)
    if not is_valid:
        return LLMTestResponse(success=False, message=error)

    success, message = test_llm_connection(
        provider=request.provider,
        model=request.model,
        api_key="",
        base_url=request.base_url,
    )

    return LLMTestResponse(success=success, message=message)


@router.get("/api/llm/ollama/models")
def get_ollama_models(base_url: Optional[str] = None):
    """
    List models installed on a running Ollama server.

    Returns {success, models, message}. On failure, success=False with a
    friendly message instead of a 500.
    """
    try:
        models = list_ollama_models(base_url)
        return {"success": True, "models": models}
    except Exception:  # noqa: BLE001 - return a fixed hint, not SDK exception text
        return {
            "success": False,
            "models": [],
            "message": (
                "Could not reach Ollama. Is it running? "
                "Start it with `ollama serve` and pull a model "
                "(e.g. `ollama pull llama3.1`)."
            ),
        }


@router.get("/api/tts/engines")
def get_tts_engines():
    """Report which TTS engines the client may offer in the voice dropdown."""
    has_eleven = bool(os.getenv("ELEVENLABS_API_KEY", "").strip())
    engines = [
        {"id": "elevenlabs", "label": "ElevenLabs (cloud)", "configured": has_eleven},
        {"id": "kokoro", "label": "Kokoro (local neural)", "configured": True},
        {"id": "browser", "label": "Browser (built-in)", "configured": True},
    ]
    return {"engines": engines, "default": "elevenlabs" if has_eleven else "kokoro"}


@router.post("/api/tts/elevenlabs")
def tts_elevenlabs(req: TTSRequest):
    """Proxy a single text chunk to ElevenLabs and stream the MP3 back."""
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs is not configured (set ELEVENLABS_API_KEY).",
        )
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided.")

    voice_id = (
        req.voice_id
        or os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        or ELEVENLABS_DEFAULT_VOICE
    )
    model_id = os.getenv("ELEVENLABS_MODEL_ID", "").strip() or ELEVENLABS_DEFAULT_MODEL

    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        "?output_format=mp3_44100_128"
    )
    headers = {
        "xi-api-key": key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload: dict[str, Any] = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    try:
        upstream = requests.post(
            url, headers=headers, json=payload, stream=True, timeout=30
        )
    except requests.RequestException:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail="ElevenLabs request failed. Check service availability and try again.",
        ) from None

    if upstream.status_code != 200:
        status_code = upstream.status_code
        upstream.close()
        if status_code in (401, 403):
            detail = "ElevenLabs authentication failed. Check the server credential and access."
        elif status_code == 429:
            detail = "ElevenLabs rate limited the request. Try again later."
        else:
            detail = f"ElevenLabs request failed (HTTP {status_code}). Try again later."
        raise HTTPException(status_code=status_code, detail=detail)

    def stream_audio():
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(stream_audio(), media_type="audio/mpeg")
