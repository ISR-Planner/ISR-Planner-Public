"""Shared state and LLM helpers for the v4 ISR agent workflow."""

import operator
import os
import time
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph.message import add_messages

from ..llm_factory import get_llm


class MissionState(TypedDict):
    """
    State shared across all reasoning agents.
    Each agent can read and write to this state.
    """

    # Conversation
    messages: Annotated[list, add_messages]

    # Mission data
    environment: Optional[Dict[str, Any]]
    drone_configs: Optional[Dict[str, Any]]
    distance_matrix: Optional[Dict[str, Any]]
    excluded_targets: Optional[List[str]]  # Targets inside SAM zones - DO NOT allocate

    # User request analysis
    user_request: str
    request_type: str  # "question", "optimize", "command"
    commands: Optional[List[Dict[str, Any]]]  # Explicit commands extracted

    # Coordinator v4 fields (injected by pre-pass)
    intent: Optional[str]  # plan|replan|explain|what_if|debug|unknown
    policy: Optional[Dict[str, Any]]  # allocation_strategy, solver_mode, etc.
    guardrails: Optional[Dict[str, Any]]  # validation results
    drone_contracts: Optional[
        Dict[str, Dict[str, Any]]
    ]  # per-drone start/home/endpoint contracts
    allocation_strategy: Optional[str]  # efficient|greedy|balanced|geographic|exclusive

    # Constraint program fields (from memory.constraints)
    constraint_program: Optional[Dict[str, Any]]  # Parsed constraint operations
    sequencing_hints: Optional[
        Dict[str, Any]
    ]  # Hints for visit ordering (Strategist interprets)

    # RAG Phase 2: pre-formatted advisory text about similar past missions,
    # computed once in isr_agent_multi_v4.py (server.database.mission_retrieval).
    # "" (not None) when no similar mission was found or retrieval is
    # unconfigured — Strategist can unconditionally concatenate it.
    similar_missions_context: Optional[str]

    # Agent reasoning outputs
    strategy_analysis: Optional[str]  # Strategist's analysis
    mission_plan: Optional[str]  # Mission Planner's orchestration plan
    allocation_reasoning: Optional[str]  # Allocator's reasoning
    route_analysis: Optional[str]  # Router's analysis
    optimizer_analysis: Optional[str]  # Optimizer's analysis
    critic_review: Optional[str]  # Critic's review
    suggestions: Optional[List[str]]  # Improvement suggestions

    # Mission orchestration fields
    requires_segmentation: Optional[bool]  # Multi-segment mission needed?
    segments: Optional[List[Dict[str, Any]]]  # Segment definitions
    constraint_operations: Optional[List[Dict[str, Any]]]  # Constraint ops to apply

    # Retry counters
    allocator_retry_count: Optional[int]
    router_retry_count: Optional[int]
    optimizer_retry_count: Optional[int]

    # Solution data
    allocation: Optional[Dict[str, List[str]]]
    routes: Optional[Dict[str, Dict[str, Any]]]
    phase1_target_ids: Optional[
        List[str]
    ]  # Phase 1 targets (LLM determined from user request)
    phase2_target_ids: Optional[
        List[str]
    ]  # Targets deferred for Phase 2 (phased mission)

    # Solver metadata (for Decision Trace v1)
    solver_type: Optional[str]
    solver_runtime_ms: Optional[int]

    # Algorithm tracking (for Algorithm Awareness)
    algorithms_used: Optional[
        List[Dict[str, Any]]
    ]  # List of AlgorithmExecution records
    algorithm_overrides: Optional[Dict[str, Any]]  # User-specified algorithm choices
    asks_about_algorithms: Optional[bool]  # User wants algorithm explanation

    # LLM Configuration (provider-agnostic)
    llm_config: Optional[Dict[str, Any]]  # provider, model, api_key

    # Policy rules (Learning v1)
    policy_rules: Optional[List[Dict[str, Any]]]

    # Final response
    final_response: Optional[str]

    # Node-level trace events (accumulated by each graph node)
    node_trace: Annotated[list, operator.add]

    # Count of llm.invoke calls across the graph (each LLM-calling node returns 1)
    llm_calls: Annotated[int, operator.add]

    # Error handling
    error: Optional[str]


def trace_event(node: str, msg: str, **data) -> Dict[str, Any]:
    """Create a trace event dict for the node_trace list."""
    evt = {"t": f"node.{node}", "msg": msg, "ts_ms": int(time.time() * 1000)}
    if data:
        evt["data"] = data
    return evt


# Global state reference for tool callbacks.
_current_state: Optional[MissionState] = None


def get_state() -> MissionState:
    if _current_state is None:
        raise ValueError("No mission state set")
    return _current_state


def set_state(state: MissionState) -> None:
    global _current_state
    _current_state = state


def get_llm_for_state(state: MissionState):
    """
    Get LLM instance based on state's llm_config.

    Resolution order:
    1. state.llm_config selects the provider/model only
    2. The matching server environment variable supplies the API key
    """
    llm_config = state.get("llm_config")

    # Local keyless providers (Ollama): no api_key, but honor an optional host.
    if llm_config and llm_config.get("provider") == "ollama":
        return get_llm(
            provider="ollama",
            model=llm_config.get("model"),
            base_url=llm_config.get("base_url"),
            temperature=0,
        )

    if llm_config and llm_config.get("provider"):
        return get_llm(
            provider=llm_config["provider"],
            model=llm_config.get("model"),
            temperature=0,
        )

    # Fall back to env-driven config (LLM_PROVIDER, LLM_MODEL, *_API_KEY).
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL")
    if not model:
        defaults = {
            "openai": "gpt-4o",
            "zai": "glm-4.6",
            "google": "gemini-1.5-pro",
            "groq": "llama-3.3-70b-versatile",
        }
        model = defaults.get(provider, "gpt-4o")

    return get_llm(
        provider=provider,
        model=model,
        temperature=0,
    )


def describe_llm(llm: Any) -> str:
    """Return a human-readable 'provider/model' label for a LangChain chat model.

    Used so log lines reflect the actual backend being called instead of a
    hardcoded provider name. ZAI is detected from ChatOpenAI's base_url because
    we reuse the OpenAI-compatible client for it (see llm_factory._create_zai_llm).
    """
    cls_name = type(llm).__name__
    model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or "unknown"

    if cls_name == "ChatGoogleGenerativeAI":
        provider = "google"
    elif cls_name == "ChatGroq":
        provider = "groq"
    elif cls_name == "ChatOpenAI":
        base = (
            getattr(llm, "openai_api_base", None)
            or getattr(llm, "base_url", None)
            or ""
        )
        provider = "zai" if "z.ai" in str(base).lower() else "openai"
    else:
        provider = cls_name

    return f"{provider}/{model}"
