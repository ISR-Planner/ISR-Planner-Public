"""Per-mission agent session — the conversation thread + solution the reasoning
agent carries ACROSS turns, so followup questions work.

The reasoning agent is otherwise stateless: every /api/agents/reason call rebuilt
the thread from scratch (system + the one new message) and wiped _solved_routes.
That makes "why did D2 skip T14?" impossible — the agent has no memory of the
prior solve or its own reasoning.

A session holds, keyed by mission_id:
  - messages:      the FULL LangChain thread (System, Human, AI, Tool messages)
                   accumulated over every turn of the mission
  - solved_routes: the agent's last committed solution (seeds the tools' route
                   accumulator so a followup edits the existing plan, not a blank)
  - last_turn:     compact facts about the most recently completed turn, including
                   whether it changed routes and the route delta.

Lifecycle (officer-driven, matches MissionRecord §1a-0): a session CONTINUES
across edits / cuts / followups and is cleared only on explicit End Mission
(end_session) — NOT on env/cfg change. The whole mission arc stays in the thread.

In-memory only (RAM), mirroring MISSION_STORE; resets on restart. Durable
persistence lands later alongside the Phase-0 sessions table. Single-process,
single-threaded use assumed (same as the reasoning agent's module globals).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentSession:
    """The agent's running memory for one mission."""

    mission_id: str
    messages: List[Any] = field(default_factory=list)  # LangChain BaseMessage list
    solved_routes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_turn: Dict[str, Any] = field(default_factory=dict)
    turns: int = 0  # how many user turns this session has seen

    @property
    def is_fresh(self) -> bool:
        """True if no turn has run yet (no thread to continue)."""
        return not self.messages


# mission_id -> AgentSession. RAM only.
_SESSIONS: Dict[str, AgentSession] = {}


def get_session(mission_id: Optional[str]) -> Optional[AgentSession]:
    """Return the existing session for a mission_id, or None.

    None mission_id (or unknown id) → None, so the caller runs a fresh,
    sessionless turn exactly as before.
    """
    if not mission_id:
        return None
    return _SESSIONS.get(mission_id)


def get_or_create_session(mission_id: Optional[str]) -> Optional[AgentSession]:
    """Return the session for a mission_id, creating an empty one if needed.

    Returns None only when mission_id is falsy (a sessionless one-off turn).
    """
    if not mission_id:
        return None
    sess = _SESSIONS.get(mission_id)
    if sess is None:
        sess = AgentSession(mission_id=mission_id)
        _SESSIONS[mission_id] = sess
    return sess


def save_session(
    mission_id: Optional[str],
    messages: List[Any],
    solved_routes: Dict[str, Dict[str, Any]],
    last_turn: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist the thread + solution back onto the session after a turn."""
    if not mission_id:
        return
    sess = get_or_create_session(mission_id)
    if sess is None:
        return
    sess.messages = messages
    sess.solved_routes = solved_routes
    if last_turn is not None:
        sess.last_turn = last_turn
    sess.turns += 1


def end_session(mission_id: Optional[str]) -> bool:
    """Drop a mission's session (explicit End Mission). True if one existed."""
    if not mission_id:
        return False
    return _SESSIONS.pop(mission_id, None) is not None


def clear_all_sessions() -> None:
    """Test/admin helper — wipe every session."""
    _SESSIONS.clear()
