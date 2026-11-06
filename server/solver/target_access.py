"""Which target categories a drone may visit (its `target_access`).

The ONE rule every solver path uses. Categories are ids (the letter stored on
each target: "a", "b", ...); their number is not fixed (catalog
"target_categories", a mission may add its own).

- No `target_access` at all: every category is allowed.
- Otherwise: the categories set to true. A category missing from the dict is
  not visited (the app writes every category explicitly).
"""

from typing import Any, Dict, Optional, Set


def allowed_types(cfg: Dict[str, Any]) -> Optional[Set[str]]:
    """The categories this drone may visit, or None = all of them."""
    access = (cfg or {}).get("target_access") or {}
    if not access:
        return None
    return {str(t).lower() for t, on in access.items() if on and isinstance(t, str)}


def type_allowed(cfg: Dict[str, Any], target: Dict[str, Any]) -> bool:
    """Whether this drone may visit this target (by its category)."""
    allowed = allowed_types(cfg)
    return allowed is None or str(target.get("type", "a")).lower() in allowed
