from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _stable_hash(obj: Any) -> str:
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_hashes(env: Dict[str, Any], drone_configs: Dict[str, Any]) -> Tuple[str, str]:
    env_hash = _stable_hash(
        {
            "airports": env.get("airports", []),
            "targets": env.get("targets", []),
            "sams": env.get("sams", []),
        }
    )
    cfg_hash = _stable_hash(drone_configs or {})
    return env_hash, cfg_hash


# Mission lifecycle states (§1a-0). A mission stays ACTIVE until the officer
# ends it explicitly; env/cfg edits mid-mission must NOT change identity.
STATUS_ACTIVE = "active"
STATUS_ENDED = "ended"


@dataclass
class MissionRecord:
    """The Mission as a first-class object (see docs/ISR_PLANNER_DESCRIPTION.md
    §1a-0). The Mission owns its environment + drone configs and carries an
    explicit identity (``name``) and lifecycle (``status`` / ``ended_at``).

    Field order keeps the original positional fields first so existing callers
    are unaffected; the Phase-0 additions all have defaults. Maps 1:1 onto the
    ``sessions`` row defined in
    docs/architecture/migrations/001_sessions_to_missions.sql.
    """

    mission_id: str
    env: Dict[str, Any]
    drone_configs: Dict[str, Any]
    env_hash: str
    cfg_hash: str
    routes: Dict[str, Any]
    allocation: Dict[str, Any]
    mission_metrics: Dict[str, Any]
    attempts: List[Dict[str, Any]]
    updated_at: str

    # --- Phase 0 additions (§1a-0): identity + explicit lifecycle ----------
    name: Optional[str] = None  # officer-declared mission name/number
    status: str = STATUS_ACTIVE  # STATUS_ACTIVE until explicitly ended
    created_at: str = field(default_factory=utc_now_iso)
    ended_at: Optional[str] = None  # set when status -> STATUS_ENDED

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    def end(self) -> None:
        """Explicitly end the mission (§1a-0: ends by officer action only)."""
        self.status = STATUS_ENDED
        self.ended_at = utc_now_iso()
        self.updated_at = self.ended_at


# In-memory store (RAM only). Resets on Railway restart / redeploy.
# NOTE (Phase 0): durable persistence will move to the `sessions` table via
# server/memory/session_store.py; this dict remains the dev/fallback backend.
MISSION_STORE: Dict[str, MissionRecord] = {}
