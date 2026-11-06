"""Exercise Agents Send's real provider-selection path without provider calls."""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from server.agents import llm_factory, reasoning_agent
from server.agents.agent_session import clear_all_sessions
from server.api import agent_routes
from server.api.models import ReasoningAgentRequest


@pytest.fixture
def agent_client(monkeypatch):
    calls = []

    class FakeLLM:
        def bind_tools(self, tools):
            assert any(tool.name == "get_current_mission_status" for tool in tools)
            return self

        def invoke(self, _messages):
            return AIMessage(content="The mission has no targets.")

    def hosted_creator(provider):
        def create(model, api_key, temperature, **kwargs):
            calls.append(
                {
                    "provider": provider,
                    "model": model,
                    "api_key": api_key,
                    "temperature": temperature,
                    **kwargs,
                }
            )
            return FakeLLM()

        return create

    for provider in ("openai", "google", "groq", "zai"):
        monkeypatch.setenv(
            llm_factory.PROVIDERS[provider].env_key, f"test-server-{provider}-key"
        )
        monkeypatch.setattr(
            llm_factory, f"_create_{provider}_llm", hosted_creator(provider)
        )

    def create_ollama(model, base_url, temperature, **kwargs):
        calls.append(
            {
                "provider": "ollama",
                "model": model,
                "base_url": base_url,
                "temperature": temperature,
                **kwargs,
            }
        )
        return FakeLLM()

    monkeypatch.setattr(llm_factory, "_create_ollama_llm", create_ollama)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://server-ollama.test:11434")
    # Keep command memory, learned-mission retrieval and capture out of this
    # API test; the request, agent execution and LLM factory are all real.
    monkeypatch.setattr(reasoning_agent, "_store_mission_command", lambda *_: None)
    monkeypatch.setattr(reasoning_agent, "get_memory_for_prompt", lambda: "")
    monkeypatch.setattr(reasoning_agent, "_similar_missions_context", lambda *_: "")
    monkeypatch.setattr(reasoning_agent, "_capture_reasoning_run", lambda *_: None)
    clear_all_sessions()
    app = FastAPI()
    app.include_router(agent_routes.router)
    with TestClient(app) as client:
        yield client, calls
    clear_all_sessions()
    reasoning_agent.clear_solved_routes()


def send(client, config=None):
    payload = {
        "user_request": "How many targets are in the mission?",
        "env": {"airports": [], "targets": [], "sams": []},
        "drone_configs": {},
    }
    if config is not None:
        payload["llm_config"] = config
    response = client.post("/api/agents/reason", json=payload)
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "gpt-4o"),
        ("google", "gemini-2.0-flash"),
        ("groq", "llama-3.1-8b-instant"),
        ("zai", "glm-4.6-flash"),
    ],
)
def test_selected_hosted_provider_uses_only_its_server_credential(
    agent_client, provider, model
):
    client, calls = agent_client
    result = send(
        client,
        {
            "provider": provider,
            "model": model,
            "api_key": "test-client-key-must-not-be-used",
            "base_url": "https://client-host-must-not-be-used.invalid",
        },
    )

    assert result["success"] is True
    assert calls == [
        {
            "provider": provider,
            "model": model,
            "api_key": f"test-server-{provider}-key",
            "temperature": 0,
            "max_tokens": 8192,
        }
    ]


@pytest.mark.parametrize("config", [None, {}])
def test_absent_selection_preserves_server_configuration(agent_client, config):
    client, calls = agent_client
    assert send(client, config)["success"] is True
    assert calls[0]["provider"] == "openai"
    assert calls[0]["model"] == "gpt-4o-mini"


def test_absent_selection_retains_openai_default(agent_client, monkeypatch):
    client, calls = agent_client
    monkeypatch.delenv("LLM_PROVIDER")
    monkeypatch.delenv("LLM_MODEL")
    assert send(client)["success"] is True
    assert calls[0]["provider"] == "openai"
    assert calls[0]["model"] == "gpt-4o"


def test_new_provider_without_model_does_not_inherit_server_model(agent_client):
    client, calls = agent_client
    assert send(client, {"provider": "google"})["success"] is True
    assert calls[0]["model"] == "gemini-1.5-pro"


def test_selected_ollama_honors_local_host_without_credentials(agent_client):
    client, calls = agent_client
    assert (
        send(
            client,
            {
                "provider": "ollama",
                "model": "local-test-model",
                "base_url": "http://operator-ollama.test:11434/",
                "api_key": "test-client-key-must-not-be-used",
            },
        )["success"]
        is True
    )
    assert calls == [
        {
            "provider": "ollama",
            "model": "local-test-model",
            "base_url": "http://operator-ollama.test:11434",
            "temperature": 0,
            "max_tokens": 8192,
        }
    ]


def test_selection_is_per_request_and_does_not_change_server_defaults(agent_client):
    client, calls = agent_client
    assert (
        send(client, {"provider": "google", "model": "gemini-2.0-flash"})["success"]
        is True
    )
    assert send(client)["success"] is True
    assert [(call["provider"], call["model"]) for call in calls] == [
        ("google", "gemini-2.0-flash"),
        ("openai", "gpt-4o-mini"),
    ]
    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["LLM_MODEL"] == "gpt-4o-mini"


def test_invalid_provider_model_is_rejected_before_provider_call(agent_client):
    client, calls = agent_client
    assert send(client, {"provider": "google", "model": "gpt-4o"})["success"] is False
    assert calls == []


def test_request_model_discards_client_credentials():
    request = ReasoningAgentRequest(
        user_request="Status?",
        env={},
        llm_config={
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test-client-key-must-not-be-used",
            "openai_api_key": "test-client-key-must-not-be-used",
        },
    )
    assert request.llm_config.model_dump(exclude_none=True) == {
        "provider": "openai",
        "model": "gpt-4o",
    }
