"""Retired multi-agent v2 module kept only for compatibility imports.

Use `server.agents.isr_agent_multi_v4` for the maintained implementation.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class RetiredAgentError(RuntimeError):
    """Raised when code tries to run a retired agent module."""


def _raise_retired() -> None:
    raise RetiredAgentError(
        "This retired agent module is no longer runnable. "
        "Use server.agents.isr_agent_multi_v4 instead."
    )


def set_context(
    environment: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Optional[Any] = None,
) -> None:
    _raise_retired()


def create_multi_agent_workflow() -> Any:
    _raise_retired()


def get_multi_agent_workflow() -> Any:
    _raise_retired()


def run_isr_agent(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    _raise_retired()
