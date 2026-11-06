"""Retired reasoning-agent module kept only for compatibility imports.

Use `server.agents.reasoning_agent` for the maintained implementation.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class RetiredAgentError(RuntimeError):
    """Raised when code tries to run a retired agent module."""


def _raise_retired() -> None:
    raise RetiredAgentError(
        "This retired agent module is no longer runnable. "
        "Use server.agents.reasoning_agent instead."
    )


def set_mission_context(
    environment: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Optional[Any] = None,
) -> None:
    _raise_retired()


def get_solved_routes() -> Dict[str, Dict[str, Any]]:
    _raise_retired()


def store_solved_route(
    drone_id: str,
    route_data: Dict[str, Any],
    append: bool = False,
) -> None:
    _raise_retired()


def clear_solved_routes() -> None:
    _raise_retired()


def get_targets() -> str:
    _raise_retired()


def get_airports() -> str:
    _raise_retired()


def get_drones() -> str:
    _raise_retired()


def get_sams() -> str:
    _raise_retired()


def allocate_targets(strategy: str = "insertion") -> str:
    _raise_retired()


def solve_route(
    drone_id: str,
    start: str,
    targets: list[str],
    end: str,
    append: bool = False,
) -> str:
    _raise_retired()


def solve_full_mission(
    strategy: str = "geographic",
    start_with: Optional[str] = None,
    only_targets: Optional[list[str]] = None,
) -> str:
    _raise_retired()


def run_reasoning_agent(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    _raise_retired()


def handle_reasoning_request(request_data: Dict[str, Any]) -> Dict[str, Any]:
    _raise_retired()
