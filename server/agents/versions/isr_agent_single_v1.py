"""Retired single-agent v1 module kept only for compatibility imports.

Use `server.agents.reasoning_agent` or `server.agents.isr_agent_multi_v4`
for maintained implementations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class RetiredAgentError(RuntimeError):
    """Raised when code tries to run a retired agent module."""


def _raise_retired() -> None:
    raise RetiredAgentError(
        "This retired agent module is no longer runnable. "
        "Use server.agents.reasoning_agent or server.agents.isr_agent_multi_v4 instead."
    )


def load_memory() -> List[Dict[str, Any]]:
    return []


def save_memory(memories: List[Dict[str, Any]]) -> None:
    return None


def add_memory(content: str, category: str = "correction") -> Dict[str, Any]:
    _raise_retired()


def get_active_memories() -> List[Dict[str, Any]]:
    return []


def clear_memory() -> int:
    return 0


def delete_memory(memory_id: int) -> bool:
    return False


def set_context(
    environment: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Optional[Any] = None,
) -> None:
    _raise_retired()


def create_isr_agent() -> Any:
    _raise_retired()


def get_isr_workflow() -> Any:
    _raise_retired()


def run_isr_agent(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    _raise_retired()
