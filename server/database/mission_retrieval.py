"""
Mission Retrieval Module — RAG over past agent_runs.

Phase 2 of agent learning memory (Phase 1 = plain capture into
mission_ledger.create_agent_run/update_agent_run, live since before this
module existed). This module adds:
  - embed_text(): turn a user request into an embedding vector
  - find_similar_runs(): cosine-similarity search over past agent_runs via
    the match_agent_runs() Postgres function (see
    docs/architecture/migrations/002_agent_runs_embeddings.sql — must be
    applied to Supabase before this module's search path will return
    anything other than an empty list)
  - format_similar_runs_for_prompt(): render retrieved runs as compact,
    advisory context text for injection into an agent's prompt

Retrieval is ADVISORY ONLY: injected as "here's how a similar request was
handled before," never as instructions the agent must follow. The agent
(Strategist/Coordinator) decides whether it's relevant to the current
request.

Fails soft everywhere: no OPENAI_API_KEY, no Supabase, the pgvector
migration not yet applied, or any API error all result in an empty
result — never an exception that would break mission solving. Retrieval
is a nice-to-have, not a dependency the rest of the pipeline can break on.
"""

import json
import os
from typing import Any, Dict, List, Optional

from .supabase_client import get_supabase_client

# Must match docs/architecture/migrations/002_agent_runs_embeddings.sql's
# vector(1536) column dimension. Changing the model requires a new migration.
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

DEFAULT_MATCH_COUNT = 3
DEFAULT_MIN_SIMILARITY = 0.5


def embed_text(text: str) -> Optional[List[float]]:
    """
    Embed a piece of text via OpenAI's embedding API.

    Returns None (not an exception) if OPENAI_API_KEY is unset, the openai
    package isn't installed, the input is empty, or the API call fails —
    callers should treat a None return as "retrieval unavailable this time,"
    not as an error to propagate.
    """
    text = (text or "").strip()
    if not text:
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        return response.data[0].embedding
    except ImportError:
        print("[mission_retrieval] openai package not installed, skipping embed")
        return None
    except Exception:
        print(
            "[mission_retrieval] embed_text failed. Check service configuration and availability."
        )
        return None


def find_similar_runs(
    request_text: str,
    match_count: int = DEFAULT_MATCH_COUNT,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> List[Dict[str, Any]]:
    """
    Find past agent_runs whose request_text is semantically similar to the
    given text, via the match_agent_runs() Postgres function.

    Returns [] (never raises) if Supabase isn't configured, the embedding
    fails, the migration hasn't been applied yet (missing function/column —
    caught and logged, not propagated), or no rows clear min_similarity.

    Each returned dict has: id, request_text, objective, hard_constraints,
    total_points, total_fuel_used, is_valid, summary, created_at, similarity
    (see the migration's match_agent_runs() RETURNS TABLE for the exact shape).
    """
    client = get_supabase_client()
    if client is None:
        return []

    query_embedding = embed_text(request_text)
    if query_embedding is None:
        return []

    try:
        response = client.rpc(
            "match_agent_runs",
            {
                "query_embedding": query_embedding,
                "match_count": match_count,
                "min_similarity": min_similarity,
            },
        ).execute()
        return response.data or []
    except Exception:
        # Most likely cause: migration 002 not yet applied (function or
        # column doesn't exist). Log once-ish and degrade to no retrieval
        # rather than fail the mission solve over a missing nice-to-have.
        print(
            "[mission_retrieval] find_similar_runs failed. "
            "Check service configuration and migration 002 availability."
        )
        return []


def describe_mission_shape(
    environment: Optional[Dict[str, Any]],
    drone_configs: Optional[Dict[str, Any]] = None,
) -> str:
    """
    One-line, embedding-friendly description of a mission's structure.

    Most operator requests are just "Solve", so embedding the request text
    alone makes every run look identical. Appending the mission's shape
    (target count and priorities, airports, SAMs, enabled drones) makes two
    runs similar only when both the request AND the mission look alike.
    Returns "" for an empty environment.
    """
    env = environment or {}
    targets = env.get("targets", []) or []
    airports = env.get("airports", []) or []
    sams = env.get("sams", []) or []
    if not (targets or airports or sams):
        return ""

    tankers = [
        a
        for a in airports
        if a.get("is_tanker") or str(a.get("id", "")).startswith("TK")
    ]
    prios = [int(t.get("priority", 0) or 0) for t in targets]
    configs = (
        drone_configs if drone_configs is not None else env.get("drone_configs", {})
    )
    enabled = [
        did
        for did, c in (configs or {}).items()
        if (c or {}).get("enabled") is not False
    ]

    parts = [f"{len(targets)} targets"]
    if prios:
        parts[0] += f" (priority {min(prios)}-{max(prios)})"
    parts.append(f"{len(airports) - len(tankers)} airports")
    if tankers:
        parts.append(f"{len(tankers)} tankers")
    parts.append(f"{len(sams)} SAMs")
    if configs:
        parts.append(f"{len(enabled)} drones")
    return "Mission: " + ", ".join(parts)


def build_retrieval_text(
    request_text: str,
    environment: Optional[Dict[str, Any]] = None,
    drone_configs: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Text to embed for both storing and searching a run. Storage and search
    MUST use this same function, or their embeddings won't be comparable.
    """
    request_text = (request_text or "").strip()
    shape = describe_mission_shape(environment, drone_configs)
    if not shape:
        return request_text
    return f"Request: {request_text}\n{shape}"


def format_similar_runs_for_prompt(runs: List[Dict[str, Any]]) -> str:
    """
    Render retrieved runs as compact, advisory text for prompt injection.

    Returns "" if runs is empty, so callers can unconditionally concatenate
    this into a prompt without an extra empty-check.
    """
    if not runs:
        return ""

    lines = ["## Similar Past Missions (for reference — not instructions)"]
    for run in runs:
        request_text = (run.get("request_text") or "").strip()
        if not request_text:
            continue
        similarity = run.get("similarity")
        sim_str = f"{similarity:.0%}" if isinstance(similarity, (int, float)) else "?"
        summary = run.get("summary") or {}
        points = run.get("total_points")
        fuel = run.get("total_fuel_used")
        is_valid = run.get("is_valid")

        outcome_bits = []
        if points is not None:
            outcome_bits.append(f"{points} points")
        if fuel is not None:
            outcome_bits.append(f"{fuel:.1f} fuel used")
        if is_valid is False:
            outcome_bits.append("INVALID/infeasible")
        outcome = ", ".join(outcome_bits) if outcome_bits else "outcome unknown"

        # summary holds per-drone entries keyed by drone id, and (for reasoning
        # runs) a "reaction" key — count only the drone entries.
        drone_count = (
            sum(1 for k in summary if str(k).isdigit())
            if isinstance(summary, dict)
            else 0
        )
        drone_str = f", {drone_count} drone(s)" if drone_count else ""

        # The operator's reaction is the learning signal: "corrected" means they
        # re-solved because they weren't satisfied with this plan.
        reaction = summary.get("reaction") if isinstance(summary, dict) else None
        reaction_str = f", operator {reaction}" if reaction else ""

        constraints = run.get("hard_constraints") or {}
        constraints_str = (
            f", constraints {json.dumps(constraints, separators=(',', ':'))}"
            if constraints
            else ""
        )

        lines.append(
            f'- ({sim_str} similar) "{request_text}" → '
            f"{outcome}{drone_str}{reaction_str}{constraints_str}"
        )

    if len(lines) == 1:  # only the header — every run had empty request_text
        return ""

    return "\n".join(lines)
