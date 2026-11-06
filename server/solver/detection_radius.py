"""
Detection radius — fuel from the path actually flown, and use of the fuel it saves.

A drone with a detection radius only has to pass within r of a target, so its
path is shortened (taut string, `_relax_positions_to_detection_radii`). The
planner chooses and orders targets on centre-to-centre distances, so on its own
it (a) reports the centre-to-centre distance as fuel, although the drone flies
the shorter path, and (b) never uses the fuel that shortening saves.

`apply_detection_radius` runs once on a finished solution, for drones that have
a radius (docs/DETECTION_RADIUS_TRAJECTORY_OPTIMIZATION.md "Fuel impact"):

  (a) every such route is re-evaluated with the shared route evaluator (the same
      code as the sequence bar and the agent's route edits): trajectory, distance,
      per-hop and per-leg distances all come from the flown path;
  (b) unassigned targets are then inserted where they add the least flown
      distance, while every leg still fits its tank — checked on the real path.
      The existing order is kept; this can only add targets.

Drones without a radius are left exactly as the planner made them.
"""

from typing import Any, Dict, List, Optional, Tuple

from .route_evaluation import evaluate_route


def radius_active(cfg: Optional[Dict[str, Any]]) -> bool:
    radii = (cfg or {}).get("detection_radius") or {}
    return isinstance(radii, dict) and any(
        float(v or 0.0) > 0.0 for v in radii.values()
    )


def _legs_from_spans(spans: List[float], refuel_indices: List[int]) -> List[float]:
    """Per-leg distances (refuel to refuel) from per-hop distances."""
    bounds = (
        [0] + sorted(i for i in refuel_indices if 0 < i < len(spans)) + [len(spans)]
    )
    return [sum(spans[bounds[k] : bounds[k + 1]]) for k in range(len(bounds) - 1)]


def _leg_budgets(cfg: Dict[str, Any], n_legs: int) -> List[float]:
    """Leg 1 flies on the fuel left (start_fuel, a numeric 0 is real); later legs
    start with a full tank after refuelling."""
    tank = float(cfg.get("fuel_budget") or 0.0)
    start_fuel = cfg.get("start_fuel")
    first = tank if start_fuel is None else float(start_fuel)
    return [first] + [tank] * (n_legs - 1)


def _fits(legs: List[float], cfg: Dict[str, Any]) -> bool:
    return all(
        leg <= budget + 1e-6 for leg, budget in zip(legs, _leg_budgets(cfg, len(legs)))
    )


def _target_allowed(target: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    access = cfg.get("target_access") or {}
    ttype = str(target.get("type", "a")).lower()
    return bool(access.get(ttype, access.get(ttype.upper(), True))) if access else True


def _evaluate(route: List[str], env, did, cfg) -> Dict[str, Any]:
    """The flown path (taut string within the radii); its distance is fuel,
    loiter included (route_evaluation.evaluate_route)."""
    return evaluate_route(route, env, did, drone_config=cfg)


def _apply_evaluation(
    rd: Dict[str, Any], ev: Dict[str, Any], refuel_indices: List[int]
) -> None:
    rd["route"] = ev["route"]
    rd["sequence"] = ",".join(ev["route"])
    rd["trajectory"] = ev["trajectory"]
    rd["waypoint_indices"] = ev["waypoint_indices"]
    rd["span_distances"] = ev["span_distances"]
    rd["distance"] = ev["distance"]
    rd["fuel_used"] = ev["distance"]
    rd["loiter_fuel"] = ev["loiter_fuel"]
    rd["points"] = ev["points"]
    if refuel_indices:
        rd["refuel_indices"] = refuel_indices
        rd["leg_distances"] = _legs_from_spans(ev["fuel_spans"], refuel_indices)
        rd["refuel_stops"] = [ev["route"][i] for i in refuel_indices]
    elif rd.get("leg_distances"):
        rd["leg_distances"] = [ev["distance"]]


def apply_detection_radius(
    result: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    constraints: Optional[Dict[str, Any]] = None,
    insert_targets: bool = True,
) -> Tuple[Dict[str, Any], List[str]]:
    """Return (result, log lines). Mutates result["routes"] in place.

    insert_targets=False runs only (a) — for a caller that plans a single drone
    and cannot see which targets the other drones already have.
    """
    routes = result.get("routes") or {}
    log: List[str] = []
    radius_drones = [
        str(did)
        for did, rd in routes.items()
        if radius_active((drone_configs or {}).get(str(did)))
        and (drone_configs or {}).get(str(did), {}).get("enabled") is not False
        and len((rd or {}).get("route") or []) >= 2
    ]
    if not radius_drones:
        return result, log

    # (a) fuel from the flown path.
    for did in radius_drones:
        rd = routes[did]
        cfg = drone_configs[did]
        before = float(rd.get("distance") or 0.0)
        ev = _evaluate(list(rd["route"]), env, did, cfg)
        if not ev["success"]:
            log.append(
                f"D{did}: flown-path evaluation failed ({ev['error']}); kept planner values"
            )
            continue
        _apply_evaluation(rd, ev, list(rd.get("refuel_indices") or []))
        log.append(
            f"D{did}: detection radius — flown {ev['distance']:.1f} km (centre-to-centre {before:.1f})"
        )

    # (b) use the saved fuel: insert unassigned targets, cheapest flown increase first.
    # Drones with ordering constraints are left alone (an insert could break them).
    constrained = set()
    if constraints:
        if any(k in constraints for k in ("first", "last", "before", "adjacent")):
            constrained = set(radius_drones)
        else:
            constrained = {str(d) for d in constraints}
    candidates_drones = (
        [d for d in radius_drones if d not in constrained] if insert_targets else []
    )
    in_routes = {
        str(w) for rd in routes.values() for w in (rd or {}).get("route") or []
    }
    free = [
        t for t in env.get("targets", []) or [] if str(t.get("id")) not in in_routes
    ]

    while candidates_drones and free:
        best = (
            None  # ((-priority, added km), drone, refuel indices, evaluation, target)
        )
        for t in free:
            tid = str(t.get("id"))
            for did in candidates_drones:
                cfg = drone_configs[did]
                if not _target_allowed(t, cfg):
                    continue
                rd = routes[did]
                route = list(rd["route"])
                refuel = list(rd.get("refuel_indices") or [])
                for pos in range(1, len(route)):
                    cand = route[:pos] + [tid] + route[pos:]
                    cand_refuel = [i + 1 if i >= pos else i for i in refuel]
                    ev = _evaluate(cand, env, did, cfg)
                    if not ev["success"]:
                        continue
                    if not _fits(_legs_from_spans(ev["fuel_spans"], cand_refuel), cfg):
                        continue
                    added = ev["distance"] - float(rd.get("distance") or 0.0)
                    key = (-int(t.get("priority", 5) or 0), added)
                    if best is None or key < best[0]:
                        best = (key, did, cand_refuel, ev, t)
        if best is None:
            break
        _, did, cand_refuel, ev, t = best
        _apply_evaluation(routes[did], ev, cand_refuel)
        free = [x for x in free if x is not t]
        log.append(
            f"D{did}: detection radius saved fuel — added {t.get('id')} "
            f"(route now {ev['distance']:.1f} km)"
        )
    return result, log
