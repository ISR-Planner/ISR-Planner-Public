"""Ordering-constraint helpers for solver orchestration."""

from typing import Any, Dict, List, Optional, Set, Tuple


def _normalize_constraints(c: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Coerce a raw constraints dict into the canonical shape, or None if empty.

    Permissive: silently drops malformed entries. Used internally for
    already-trusted/partial data (e.g. per-leg subsets, which are expected
    to legitimately omit targets from the original constraint set — see
    _subset_constraints_for_leg). For validating FRESH user/LLM-supplied
    constraints, use validate_and_normalize_constraints instead, which
    raises on unknown target ids and logical contradictions rather than
    silently coercing them away.
    """
    if not c:
        return None
    first = c.get("first") or None
    last = c.get("last") or None
    before = [(str(a), str(b)) for a, b in (c.get("before") or []) if a and b]
    adjacent = [(str(y), str(x)) for y, x in (c.get("adjacent") or []) if y and x]
    if not (first or last or before or adjacent):
        return None
    return {
        "first": str(first) if first else None,
        "last": str(last) if last else None,
        "before": before,
        "adjacent": adjacent,
    }


def validate_and_normalize_constraints(
    raw: Optional[Dict[str, Any]], target_ids: Set[str]
) -> Dict[str, Any]:
    """Validate and normalize a FRESH, user/LLM-supplied ordering-constraints dict.

    Returns {"first":str|None, "last":str|None, "before":[(a,b)...],
    "adjacent":[(y,x)...]}. Raises ValueError(msg) on any malformed input,
    unknown target id, or cheap contradiction (first==last with >1 target,
    a cycle in `before`, adjacent (Y,X) where X is also required first, ...).

    This is the entry-point validator — call it once, at the boundary where
    raw constraints first arrive (from an LLM tool call or a parsed user
    request), before anything else touches them. Downstream solver code that
    re-normalizes already-validated/subsetted constraints should keep using
    the permissive _normalize_constraints above, not this function — it
    would incorrectly reject a legitimate per-leg subset that no longer
    references every original target.
    """
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError('constraints must be a dict (e.g. {"first":"T7"}).')

    def _check(tid: str, what: str) -> str:
        tid = str(tid).strip()
        if tid not in target_ids:
            raise ValueError(f"{what} references unknown target '{tid}'.")
        return tid

    first = raw.get("first")
    first = _check(first, "first") if first else None

    last = raw.get("last")
    last = _check(last, "last") if last else None

    def _pairs(key: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for pair in raw.get(key) or []:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"{key} entries must be [id, id] pairs; got {pair!r}.")
            a = _check(pair[0], key)
            b = _check(pair[1], key)
            if a == b:
                raise ValueError(f"{key} pair ({a},{b}) references the same target.")
            out.append((a, b))
        return out

    before = _pairs("before")
    adjacent = _pairs("adjacent")

    # ---- Cheap contradiction checks. ----
    if first is not None and last is not None and first == last:
        raise ValueError(
            f"'{first}' cannot be BOTH first and last unless it is the only target."
        )
    # adjacent (Y,X): X immediately follows Y, so X cannot ALSO be first.
    for y, x in adjacent:
        if first is not None and x == first:
            raise ValueError(
                f"adjacent ({y},{x}) makes '{x}' follow '{y}', but '{x}' is also "
                f"required first — contradiction."
            )
        if last is not None and y == last:
            # NOTE: the original reasoning_agent.py source guarded this branch
            # with `first is not None` instead of `last is not None` — a bug
            # that skipped this contradiction check whenever only `last` (no
            # `first`) was set. Fixed here during the port to server/solver/
            # constraints.py.
            raise ValueError(
                f"adjacent ({y},{x}) makes '{x}' follow '{y}', but '{y}' is also "
                f"required last — contradiction."
            )
    # Cycle detection in `before` (a tiny DFS topo check). first/last add
    # implied edges: first < everyone, everyone < last.
    edges = list(before)
    for y, x in adjacent:
        edges.append((y, x))  # y before x
    others = (
        {t for pair in edges for t in pair}
        | ({first} if first else set())
        | ({last} if last else set())
    )
    if first is not None:
        edges += [(first, t) for t in others if t != first]
    if last is not None:
        edges += [(t, last) for t in others if t != last]
    adj: Dict[str, List[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {}

    def _dfs(u: str) -> None:
        color[u] = GRAY
        for v in adj.get(u, []):
            if color.get(v, WHITE) == GRAY:
                raise ValueError(
                    "ordering constraints contradict each other (a cycle in "
                    "before/first/last/adjacent) — cannot satisfy all of them."
                )
            if color.get(v, WHITE) == WHITE:
                _dfs(v)
        color[u] = BLACK

    for node in list(adj.keys()):
        if color.get(node, WHITE) == WHITE:
            _dfs(node)

    return {"first": first, "last": last, "before": before, "adjacent": adjacent}


def _constraints_for_drone(
    constraints: Optional[Dict[str, Any]], did: str
) -> Optional[Dict[str, Any]]:
    """Resolve constraints that apply to a physical or virtual drone id."""
    if not constraints:
        return None
    if any(k in constraints for k in ("first", "last", "before", "adjacent")):
        return _normalize_constraints(constraints)

    try:
        from .virtual_drones import parent_of as _parent_of

        phys = _parent_of(did)
    except Exception:
        phys = did

    per = constraints.get(str(did)) or constraints.get(str(phys))
    return _normalize_constraints(per)


def _subset_constraints_for_leg(
    constraints: Optional[Dict[str, Any]],
    leg_target_ids,
    is_first_leg: bool,
    is_last_leg: bool,
) -> Optional[Dict[str, Any]]:
    """Restrict a drone's constraints to a single refuel leg."""
    if not constraints:
        return None
    ids = set(str(t) for t in (leg_target_ids or []))

    first = constraints.get("first") if is_first_leg else None
    if first and first not in ids:
        first = None
    last = constraints.get("last") if is_last_leg else None
    if last and last not in ids:
        last = None

    before = [(a, b) for a, b in constraints.get("before", []) if a in ids and b in ids]

    adjacent = []
    for y, x in constraints.get("adjacent", []):
        y_in, x_in = y in ids, x in ids
        if y_in and x_in:
            adjacent.append((y, x))
        elif y_in ^ x_in:
            raise ValueError(
                f"Cross-leg adjacency constraint ({y} right after {x}) cannot be "
                "split across a refuel boundary - both targets must be on the "
                "same leg."
            )
    return _normalize_constraints(
        {"first": first, "last": last, "before": before, "adjacent": adjacent}
    )


def _ordering_legal(route, constraints: Optional[Dict[str, Any]]) -> bool:
    """Return True when a full route list satisfies ordering constraints."""
    if not constraints:
        return True
    pos = {node: i for i, node in enumerate(route)}

    first = constraints.get("first")
    if first is not None:
        if len(route) < 2 or route[1] != first:
            return False

    last = constraints.get("last")
    if last is not None:
        if len(route) < 2 or route[-2] != last:
            return False

    for a, b in constraints.get("before", []):
        if a in pos and b in pos and pos[a] > pos[b]:
            return False

    for y, x in constraints.get("adjacent", []):
        if y in pos and x in pos and pos[x] != pos[y] + 1:
            return False

    return True
