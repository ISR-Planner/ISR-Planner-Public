import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from .models import MissionResponse

MissionRecord = Dict[str, Any]
MISSION_STORE: Dict[str, MissionRecord] = {}

STATUS_ACTIVE = "active"
STATUS_ENDED = "ended"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(obj: Any) -> str:
    """Deterministic short hash for JSON-serializable objects."""
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def compute_env_hash(env: Dict[str, Any]) -> str:
    """Hash only the environment components that affect solving/feasibility."""
    payload = {
        "airports": env.get("airports", []),
        "targets": env.get("targets", []),
        "sams": env.get("sams", []),
        "polygons": env.get("polygons", env.get("obstacles", [])),
        "boundary": env.get("boundary", None),
        "synthetic_starts": env.get("synthetic_starts", {}),
    }
    return _stable_hash(payload)


def compute_cfg_hash(drone_configs: Optional[Dict[str, Any]]) -> str:
    return _stable_hash(drone_configs or {})


def _known_waypoint_ids(env: Dict[str, Any]) -> set:
    ids = set()
    for a in env.get("airports", []):
        aid = a.get("id") or a.get("label")
        if aid:
            ids.add(str(aid))
    for t in env.get("targets", []):
        tid = t.get("id") or t.get("label")
        if tid:
            ids.add(str(tid))
    for sid in (env.get("synthetic_starts") or {}).keys():
        ids.add(str(sid))
    for c in env.get("checkpoints", []):
        cid = c.get("id") or c.get("label")
        if cid:
            ids.add(str(cid))
    return ids


def verify_solution_basic(
    env: Dict[str, Any],
    routes: Dict[str, Any],
    drone_configs: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Lightweight, fast validation to prevent returning self-inconsistent results."""
    errors: List[str] = []
    known_ids = _known_waypoint_ids(env)

    if not env.get("airports"):
        errors.append("env.airports missing/empty")

    if not isinstance(routes, dict):
        errors.append("routes is not a dict")
        return errors

    for drone_id, r in routes.items():
        if not isinstance(r, dict):
            errors.append(f"route[{drone_id}] is not an object")
            continue

        if drone_configs:
            cfg = drone_configs.get(drone_id) or drone_configs.get(str(drone_id))
            if cfg and not cfg.get("enabled", True):
                continue

        if r.get("error"):
            continue

        seq = r.get("route") or r.get("waypoints") or r.get("sequence")
        if isinstance(seq, str):
            seq = [s.strip() for s in seq.split(",") if s.strip()]
        if not isinstance(seq, list) or not seq:
            errors.append(f"route[{drone_id}].route missing/empty")
            continue

        for wid in seq:
            if str(wid) not in known_ids:
                errors.append(f"route[{drone_id}] contains unknown waypoint id: {wid}")
                break

        dist = r.get("distance")
        budget = r.get("fuel_budget")
        if isinstance(dist, (int, float)) and isinstance(budget, (int, float)):
            validation_dist = float(dist)
            sync_wait = r.get("sync_wait_fuel")
            if isinstance(sync_wait, (int, float)):
                validation_dist = max(0.0, validation_dist - float(sync_wait))
            if validation_dist >= 99999:
                errors.append(
                    f"route[{drone_id}] has blocked path (SAM obstruction between waypoints)"
                )
            elif isinstance(r.get("leg_distances"), list) and r.get("leg_distances"):
                cfg = (
                    (drone_configs or {}).get(str(drone_id))
                    or (drone_configs or {}).get(drone_id)
                    or {}
                )
                full_tank = float(
                    cfg.get("fuel_budget", cfg.get("fuel_capacity", budget)) or budget
                )
                cfg_start_fuel = cfg.get("start_fuel")
                start_fuel = float(
                    full_tank if cfg_start_fuel is None else cfg_start_fuel
                )
                for i, leg in enumerate(r["leg_distances"]):
                    cap = start_fuel if i == 0 else full_tank
                    if float(leg or 0.0) > cap + 1e-6:
                        errors.append(
                            f"route[{drone_id}] leg {i + 1} exceeds fuel budget: "
                            f"{float(leg):.2f} > {cap:.2f}"
                        )
            elif r.get("refuel_airport"):
                leg1 = r.get("leg1_distance", 0.0)
                leg2 = r.get("leg2_distance", 0.0)
                cfg = (
                    (drone_configs or {}).get(str(drone_id))
                    or (drone_configs or {}).get(drone_id)
                    or {}
                )
                full_tank = float(
                    cfg.get("fuel_budget", cfg.get("fuel_capacity", budget)) or budget
                )
                cfg_start_fuel = cfg.get("start_fuel")
                start_fuel = float(budget if cfg_start_fuel is None else cfg_start_fuel)
                if leg1 > start_fuel + 1e-6:
                    errors.append(
                        f"route[{drone_id}] leg 1 exceeds fuel budget: {leg1:.2f} > {start_fuel:.2f}"
                    )
                if leg2 > full_tank + 1e-6:
                    errors.append(
                        f"route[{drone_id}] leg 2 exceeds fuel budget: {leg2:.2f} > {full_tank:.2f}"
                    )
            else:
                cfg = (
                    (drone_configs or {}).get(str(drone_id))
                    or (drone_configs or {}).get(drone_id)
                    or {}
                )
                cfg_start_fuel = cfg.get("start_fuel")
                cap = float(budget if cfg_start_fuel is None else cfg_start_fuel)
                if validation_dist > cap + 1e-6:
                    errors.append(
                        f"route[{drone_id}] exceeds fuel budget: {validation_dist:.2f} > {cap:.2f}"
                    )

    return errors


def enforce_mission_continuity(
    mission_id: Optional[str],
    env: Dict[str, Any],
    drone_configs: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], bool, str, str, Optional[str]]:
    """Return (mission_id, continuity, env_hash, cfg_hash, mismatch_reason)."""
    env_hash = compute_env_hash(env)
    cfg_hash = compute_cfg_hash(drone_configs)

    if not mission_id:
        return None, False, env_hash, cfg_hash, None

    rec = MISSION_STORE.get(mission_id)
    if not rec:
        return None, False, env_hash, cfg_hash, "mission_id not found"

    stored_env_hash = rec.get("env_hash")
    stored_cfg_hash = rec.get("cfg_hash")
    if stored_env_hash and stored_env_hash != env_hash:
        return None, False, env_hash, cfg_hash, "env_hash mismatch"
    if stored_cfg_hash and stored_cfg_hash != cfg_hash:
        return None, False, env_hash, cfg_hash, "cfg_hash mismatch"

    return mission_id, True, env_hash, cfg_hash, None


def new_mission_id() -> str:
    """Single source of truth for minting a mission id."""
    return f"m_{uuid4().hex}"


def mission_is_active(mission_id: Optional[str]) -> bool:
    """True iff an existing stored mission is still active."""
    if not mission_id:
        return False
    rec = MISSION_STORE.get(mission_id)
    if not rec:
        return False
    return rec.get("status", STATUS_ACTIVE) == STATUS_ACTIVE


def get_mission_record(mission_id: Optional[str]) -> Optional[MissionRecord]:
    """Return a mission record by id, or None if missing."""
    if not mission_id:
        return None
    return MISSION_STORE.get(mission_id)


def set_mission_name(mission_id: str, name: str) -> Optional[MissionRecord]:
    """Set a mission's display name and return the updated record."""
    rec = get_mission_record(mission_id)
    if rec is None:
        return None
    rec["name"] = name
    rec["updated_at"] = _utc_now_iso()
    return rec


def end_mission_record(mission_id: str) -> Optional[MissionRecord]:
    """Mark a mission ended and return the updated record."""
    rec = get_mission_record(mission_id)
    if rec is None:
        return None
    if rec.get("status") != STATUS_ENDED:
        now = _utc_now_iso()
        rec["status"] = STATUS_ENDED
        rec["ended_at"] = now
        rec["updated_at"] = now
    return rec


def get_existing_solution_if_fresh(
    mission_id: Optional[str],
    env: Dict[str, Any],
    drone_configs: Optional[Dict[str, Any]],
) -> Tuple[Optional[MissionRecord], Optional[Dict[str, Any]], Optional[str]]:
    """Return stored solution data only when env/config hashes still match."""
    rec = get_mission_record(mission_id)
    if rec is None:
        return None, None, "mission_id not found" if mission_id else None

    incoming_env_hash = compute_env_hash(env)
    incoming_cfg_hash = compute_cfg_hash(drone_configs)
    stored_env_hash = rec.get("env_hash")
    stored_cfg_hash = rec.get("cfg_hash")

    if stored_env_hash and stored_env_hash != incoming_env_hash:
        return rec, None, "env_hash mismatch"
    if stored_cfg_hash and stored_cfg_hash != incoming_cfg_hash:
        return rec, None, "cfg_hash mismatch"

    return (
        rec,
        {
            "routes": rec.get("routes") or {},
            "allocation": rec.get("allocation") or {},
        },
        None,
    )


def upsert_mission(
    mission_id: str,
    *,
    env: Dict[str, Any],
    drone_configs: Optional[Dict[str, Any]],
    env_hash: str,
    cfg_hash: str,
    routes: Dict[str, Any],
    allocation: Any = None,
    total_points: Optional[int] = None,
    total_fuel: Optional[float] = None,
) -> Dict[str, Any]:
    """Create or update a mission record, preserving identity and lifecycle fields."""
    if total_points is None:
        total_points = (
            sum(r.get("points", 0) for r in routes.values())
            if isinstance(routes, dict)
            else 0
        )
    if total_fuel is None:
        total_fuel = (
            sum(r.get("distance", 0) for r in routes.values())
            if isinstance(routes, dict)
            else 0.0
        )

    now = _utc_now_iso()
    prior = MISSION_STORE.get(mission_id) or {}

    rec = {
        "env": env,
        "drone_configs": drone_configs,
        "env_hash": env_hash,
        "cfg_hash": cfg_hash,
        "routes": routes,
        "allocation": allocation,
        "total_points": total_points,
        "total_fuel": total_fuel,
        "updated_at": now,
        "name": prior.get("name"),
        "status": prior.get("status", STATUS_ACTIVE),
        "created_at": prior.get("created_at", now),
    }
    MISSION_STORE[mission_id] = rec
    return rec


def get_or_create_mission(
    mission_id: Optional[str],
    env: Dict[str, Any],
    drone_configs: Optional[Dict[str, Any]],
) -> Tuple[str, bool, str, str, Optional[str]]:
    """Resolve the mission for a request, always returning a usable mission_id."""
    env_hash = compute_env_hash(env)
    cfg_hash = compute_cfg_hash(drone_configs)

    if mission_id and mission_id in MISSION_STORE and not mission_is_active(mission_id):
        return new_mission_id(), False, env_hash, cfg_hash, "mission ended"

    resolved, continuity, env_hash, cfg_hash, mismatch_reason = (
        enforce_mission_continuity(mission_id, env, drone_configs)
    )
    if resolved is not None:
        return resolved, continuity, env_hash, cfg_hash, mismatch_reason

    if mismatch_reason and mission_is_active(mission_id):
        return mission_id, False, env_hash, cfg_hash, mismatch_reason  # type: ignore[return-value]

    return new_mission_id(), False, env_hash, cfg_hash, mismatch_reason


def mission_to_response(mission_id: str, rec: Dict[str, Any]) -> MissionResponse:
    """Project a mission record into the public MissionResponse shape."""
    return MissionResponse(
        mission_id=mission_id,
        name=rec.get("name"),
        status=rec.get("status", STATUS_ACTIVE),
        created_at=rec.get("created_at"),
        updated_at=rec.get("updated_at"),
        ended_at=rec.get("ended_at"),
        total_points=rec.get("total_points"),
        total_fuel=rec.get("total_fuel"),
        has_solution=bool(rec.get("routes")),
    )
