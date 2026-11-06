"""
LLM Factory - Provider-agnostic LLM instantiation

Supports multiple AI providers with a unified interface:
- OpenAI (GPT)
- Google (Gemini)
- Groq (Llama, Mixtral)

Usage:
    from server.agents.llm_factory import get_llm, get_available_providers

    # Get LLM with specific provider/model
    llm = get_llm(provider="google", model="gemini-1.5-pro", api_key="xxx")

    # Get available providers and models
    providers = get_available_providers()
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# =============================================================================
# Provider Configuration
# =============================================================================


@dataclass
class ModelInfo:
    """Information about a specific model."""

    id: str
    name: str
    description: str
    context_window: int = 128000
    supports_tools: bool = True


@dataclass
class ProviderInfo:
    """Information about an AI provider."""

    id: str
    name: str
    env_key: str  # Environment variable name for API key (or base URL for local)
    models: List[ModelInfo] = field(default_factory=list)
    requires_key: bool = True  # False for local providers like Ollama
    dynamic_models: bool = False  # True if models are discovered at runtime
    default_base_url: Optional[str] = None  # For OpenAI-compatible/local providers

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "env_key": self.env_key,
            "requires_key": self.requires_key,
            "dynamic_models": self.dynamic_models,
            "default_base_url": self.default_base_url,
            "models": [
                {
                    "id": m.id,
                    "name": m.name,
                    "description": m.description,
                    "context_window": m.context_window,
                    "supports_tools": m.supports_tools,
                }
                for m in self.models
            ],
        }


# Define supported providers and their models
PROVIDERS: Dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        id="openai",
        name="OpenAI",
        env_key="OPENAI_API_KEY",
        models=[
            ModelInfo("gpt-4o", "GPT-4o", "Most capable GPT model"),
            ModelInfo("gpt-4o-mini", "GPT-4o Mini", "Fast and affordable"),
            ModelInfo(
                "gpt-4-turbo", "GPT-4 Turbo", "Previous generation, 128K context"
            ),
            ModelInfo("o1", "o1", "Reasoning model for complex problems"),
            ModelInfo("o1-mini", "o1 Mini", "Faster reasoning model"),
        ],
    ),
    "google": ProviderInfo(
        id="google",
        name="Google",
        env_key="GOOGLE_API_KEY",
        models=[
            ModelInfo(
                "gemini-1.5-pro", "Gemini 1.5 Pro", "Best for complex tasks, 1M context"
            ),
            ModelInfo("gemini-1.5-flash", "Gemini 1.5 Flash", "Fast and versatile"),
            ModelInfo(
                "gemini-2.0-flash", "Gemini 2.0 Flash", "Latest generation, multimodal"
            ),
        ],
    ),
    "groq": ProviderInfo(
        id="groq",
        name="Groq",
        env_key="GROQ_API_KEY",
        models=[
            ModelInfo(
                "llama-3.3-70b-versatile", "Llama 3.3 70B", "Most capable open model"
            ),
            ModelInfo("llama-3.1-8b-instant", "Llama 3.1 8B", "Ultra-fast responses"),
            ModelInfo("mixtral-8x7b-32768", "Mixtral 8x7B", "32K context, fast"),
        ],
    ),
    "zai": ProviderInfo(
        id="zai",
        name="Z.AI",
        env_key="ZAI_API_KEY",
        models=[
            ModelInfo(
                "glm-4.6", "GLM-4.6", "Most capable Z.AI model", context_window=128000
            ),
            ModelInfo(
                "glm-4.6-flash",
                "GLM-4.6 Flash",
                "Faster, cheaper variant of GLM-4.6",
                context_window=128000,
            ),
            ModelInfo("glm-4.5", "GLM-4.5", "Previous flagship", context_window=128000),
            ModelInfo(
                "glm-4.5-flash",
                "GLM-4.5 Flash",
                "Fast and economical",
                context_window=128000,
            ),
            ModelInfo(
                "glm-4.5-air",
                "GLM-4.5 Air",
                "Lightweight, lower cost",
                context_window=128000,
            ),
        ],
    ),
    "ollama": ProviderInfo(
        id="ollama",
        name="Ollama (local)",
        env_key="OLLAMA_BASE_URL",  # a host URL, not a secret
        requires_key=False,
        dynamic_models=True,  # discovered from the running Ollama server
        default_base_url="http://localhost:11434",
        models=[],  # populated at runtime via list_ollama_models()
    ),
}

# Default Ollama server if none is configured.
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


def get_ollama_base_url(base_url: Optional[str] = None) -> str:
    """Resolve the Ollama host: explicit arg > OLLAMA_BASE_URL env > localhost."""
    url = base_url or os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
    return url.rstrip("/")


def list_ollama_models(base_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List models installed on a running Ollama server via its /api/tags endpoint.

    Returns a list of {id, name, description} dicts. Raises on connection errors
    so callers can surface a helpful "is Ollama running?" message.
    """
    import requests

    host = get_ollama_base_url(base_url)
    resp = requests.get(f"{host}/api/tags", timeout=5)
    resp.raise_for_status()
    data = resp.json() or {}
    models = []
    for m in data.get("models", []):
        name = m.get("name") or m.get("model")
        if not name:
            continue
        details = m.get("details") or {}
        param_size = details.get("parameter_size", "")
        family = details.get("family", "")
        desc = " ".join(x for x in [family, param_size] if x) or "Local Ollama model"
        models.append({"id": name, "name": name, "description": desc})
    return models


# =============================================================================
# LLM Factory Functions
# =============================================================================


def get_available_providers() -> List[Dict[str, Any]]:
    """
    Get list of all available providers and their models.

    Returns:
        List of provider info dicts with models
    """
    return [p.to_dict() for p in PROVIDERS.values()]


def get_provider_info(provider_id: str) -> Optional[ProviderInfo]:
    """Get info for a specific provider."""
    return PROVIDERS.get(provider_id)


def get_default_model(provider_id: str) -> Optional[str]:
    """Get the default (first) model for a provider."""
    provider = PROVIDERS.get(provider_id)
    if provider and provider.models:
        return provider.models[0].id
    return None


# Short aliases for model IDs.
MODEL_ALIASES: Dict[str, str] = {}


def resolve_model_alias(model_id: str) -> str:
    """Resolve a short model alias to a full model ID."""
    return MODEL_ALIASES.get(model_id, model_id)


def validate_provider_model(provider_id: str, model_id: str) -> Tuple[bool, str]:
    """
    Validate that a provider/model combination is supported.

    Returns:
        (is_valid, error_message)
    """
    if provider_id not in PROVIDERS:
        return (
            False,
            f"Unknown provider: {provider_id}. Supported: {list(PROVIDERS.keys())}",
        )

    provider = PROVIDERS[provider_id]

    # Providers with runtime-discovered models (e.g. Ollama) accept any
    # non-empty model id — whatever the user has pulled locally.
    if provider.dynamic_models:
        if not model_id or not str(model_id).strip():
            return False, f"No model specified for {provider.name}"
        return True, ""

    model_ids = [m.id for m in provider.models]

    # Resolve alias before checking
    resolved = resolve_model_alias(model_id)
    if resolved not in model_ids:
        return (
            False,
            f"Unknown model '{model_id}' for {provider.name}. Supported: {model_ids}",
        )

    return True, ""


def get_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0,
    base_url: Optional[str] = None,
    **kwargs,
) -> Any:
    """
    Get a LangChain chat model for the specified provider.

    Args:
        provider: Provider ID (openai, google, groq, zai, ollama)
        model: Model ID (e.g., "gpt-4o")
        api_key: API key (if not provided, reads from environment)
        temperature: Model temperature (default 0 for deterministic)
        **kwargs: Additional model-specific arguments

    Returns:
        LangChain BaseChatModel instance

    Raises:
        ValueError: If provider/model invalid or API key missing
        ImportError: If required package not installed
    """
    # Default to OpenAI if not specified.
    provider = provider or os.getenv("LLM_PROVIDER", "openai")

    # Validate provider
    if provider not in PROVIDERS:
        raise ValueError(
            f"Unknown provider: {provider}. Supported: {list(PROVIDERS.keys())}"
        )

    provider_info = PROVIDERS[provider]

    # Default to first model if not specified.
    model_name = model or os.getenv("LLM_MODEL") or get_default_model(provider)
    if not model_name:
        raise ValueError(f"No model configured for {provider_info.name}")

    # Resolve short aliases.
    model_name = resolve_model_alias(model_name)

    # Validate model
    is_valid, error = validate_provider_model(provider, model_name)
    if not is_valid:
        raise ValueError(error)

    # Local/keyless providers (Ollama) short-circuit the API-key requirement.
    if provider == "ollama":
        host = get_ollama_base_url(base_url)
        return _create_ollama_llm(model_name, host, temperature, **kwargs)

    # Get API key from parameter, environment, or raise error
    if not api_key:
        api_key = os.getenv(provider_info.env_key)  # type: ignore[assignment]

    if not api_key:
        raise ValueError(
            f"No API key for {provider_info.name}. "
            f"Set {provider_info.env_key} environment variable or pass api_key parameter."
        )

    # Create the appropriate LLM
    if provider == "openai":
        return _create_openai_llm(model_name, api_key, temperature, **kwargs)
    elif provider == "google":
        return _create_google_llm(model_name, api_key, temperature, **kwargs)
    elif provider == "groq":
        return _create_groq_llm(model_name, api_key, temperature, **kwargs)
    elif provider == "zai":
        return _create_zai_llm(model_name, api_key, temperature, **kwargs)
    else:
        raise ValueError(f"Provider {provider} not implemented")


def _create_openai_llm(model: str, api_key: str, temperature: float, **kwargs):
    """Create OpenAI (GPT) LLM."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise ImportError("Install langchain-openai: pip install langchain-openai")

    return ChatOpenAI(model=model, temperature=temperature, api_key=api_key, **kwargs)  # type: ignore[arg-type]


def _create_google_llm(model: str, api_key: str, temperature: float, **kwargs):
    """Create Google (Gemini) LLM."""
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        raise ImportError(
            "Install langchain-google-genai: pip install langchain-google-genai"
        )

    return ChatGoogleGenerativeAI(
        model=model, temperature=temperature, google_api_key=api_key, **kwargs
    )


def _create_groq_llm(model: str, api_key: str, temperature: float, **kwargs):
    """Create Groq LLM."""
    try:
        from langchain_groq import ChatGroq
    except ImportError:
        raise ImportError("Install langchain-groq: pip install langchain-groq")

    return ChatGroq(model=model, temperature=temperature, api_key=api_key, **kwargs)  # type: ignore[arg-type]


def _create_zai_llm(model: str, api_key: str, temperature: float, **kwargs):
    """Create Z.AI LLM via the OpenAI-compatible endpoint.

    Z.AI's chat completions API is OpenAI-compatible, so we reuse ChatOpenAI
    with a custom base_url. See https://docs.z.ai/guides/overview/quick-start
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise ImportError(
            "Install langchain-openai: pip install langchain-openai (Z.AI uses the OpenAI-compatible client)"
        )

    base_url = kwargs.pop("base_url", "https://api.z.ai/api/paas/v4")
    return ChatOpenAI(  # type: ignore[call-arg]
        model=model,
        temperature=temperature,
        api_key=api_key,  # type: ignore[arg-type]
        base_url=base_url,
        **kwargs,
    )


def _create_ollama_llm(model: str, base_url: str, temperature: float, **kwargs):
    """Create an LLM backed by a local Ollama server.

    Ollama exposes an OpenAI-compatible API at ``{host}/v1``, so we reuse the
    ChatOpenAI client (already installed) with a dummy API key — Ollama ignores
    it. This lets users run open-source models (Llama, Qwen, Mistral, etc.)
    with no paid key. Note: tool-calling quality varies by model.
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise ImportError(
            "Install langchain-openai: pip install langchain-openai "
            "(Ollama uses the OpenAI-compatible client)"
        )

    host = base_url.rstrip("/")
    v1_url = host if host.endswith("/v1") else f"{host}/v1"
    return ChatOpenAI(  # type: ignore[call-arg]
        model=model,
        temperature=temperature,
        api_key="ollama",  # type: ignore[arg-type]  # placeholder; Ollama ignores it
        base_url=v1_url,
        **kwargs,
    )


# =============================================================================
# Connection Testing
# =============================================================================


def test_connection(
    provider: str, model: str, api_key: Optional[str], base_url: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Test if we can connect to a provider with given credentials.

    Args:
        provider: Provider ID
        model: Model ID
        api_key: API key to test (ignored for keyless providers like Ollama)
        base_url: Optional host override (used by Ollama)

    Returns:
        (success, message) tuple
    """
    try:
        # Validate inputs
        is_valid, error = validate_provider_model(provider, model)
        if not is_valid:
            return False, error

        provider_info = PROVIDERS.get(provider)
        keyless = provider_info is not None and not provider_info.requires_key

        if not keyless and not api_key:
            api_key = os.getenv(provider_info.env_key) if provider_info else None
        if not keyless and (not api_key or len(api_key.strip()) < 10):
            return False, (
                f"{provider_info.env_key} is not configured on the server"
                if provider_info
                else "Provider credentials are not configured on the server"
            )

        # Create LLM and make a simple test call
        llm = get_llm(
            provider=provider, model=model, api_key=api_key, base_url=base_url
        )

        # Simple test message
        response = llm.invoke("Say 'Connection successful' and nothing else.")

        # Check we got a response
        if response and hasattr(response, "content") and response.content:
            return True, f"Connected to {PROVIDERS[provider].name} ({model})"
        else:
            return False, "Received empty response from model"

    except ImportError:
        return (
            False,
            "Missing provider package. Check the server provider dependencies.",
        )
    except Exception as e:
        error_msg = str(e)
        # Clean up common error messages
        if "401" in error_msg or "unauthorized" in error_msg.lower():
            return False, "Invalid API key"
        elif "404" in error_msg:
            return False, f"Model '{model}' not found or not accessible"
        elif "rate" in error_msg.lower():
            return False, "Rate limited - try again later"
        else:
            return False, (
                "Connection failed. Check provider configuration and service availability."
            )


# =============================================================================
# Utility Functions
# =============================================================================


def get_env_key_for_provider(provider_id: str) -> Optional[str]:
    """Get the environment variable name for a provider's API key."""
    provider = PROVIDERS.get(provider_id)
    return provider.env_key if provider else None


def check_provider_configured(provider_id: str) -> bool:
    """Check if a provider is ready to use.

    Keyless providers (e.g. Ollama) are always considered configured — no API
    key is needed; reachability is checked separately via the models endpoint /
    Test Connection. Key-based providers need their env var set.
    """
    provider = PROVIDERS.get(provider_id)
    if not provider:
        return False
    if not provider.requires_key:
        return True
    return bool(os.getenv(provider.env_key))


def get_configured_providers() -> List[str]:
    """Get list of provider IDs that have API keys configured."""
    return [pid for pid in PROVIDERS if check_provider_configured(pid)]
