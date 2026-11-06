"""Prompt context builders for the v4 ISR agent workflow."""

from typing import Any, List

from ..drone_config import resolve_fuel_budget
from .state import MissionState

HARD_CONSTRAINTS = """
═══════════════════════════════════════════════════════════════════════════════
                    HARD CONSTRAINTS - MUST NEVER BE VIOLATED
═══════════════════════════════════════════════════════════════════════════════

1. DRONE TARGET ELIGIBILITY (Sensor Type Restrictions):
   - Each drone has an "ELIGIBLE TARGETS" list based on its sensor configuration
   - A drone can ONLY be assigned targets from its eligible list
   - If D1's eligible targets are "T1, T3, T5" → D1 can ONLY visit T1, T3, T5
   - NEVER assign a target to a drone not in that drone's eligible list
   - This is a PHYSICAL constraint (sensor type) and CANNOT be overridden

2. EXCLUDED TARGETS (Inside SAM Zones):
   - Targets marked as "EXCLUDED (inside SAM zones)" are UNREACHABLE
   - No drone can visit excluded targets - they must be ignored entirely

3. FUEL BUDGET:
   - Each drone has a fuel budget that cannot be exceeded
   - Routes must respect fuel constraints

4. ALL ELIGIBLE TARGETS MUST BE COVERED:
   - Every target that is NOT excluded must be assigned to exactly one drone
   - The assigned drone MUST be eligible to visit that target

These constraints apply to ALL operations: allocation, optimization, reallocation,
and routing. No user request can override these physical constraints.
═══════════════════════════════════════════════════════════════════════════════
"""


def build_mission_context(state: MissionState) -> str:
    """Build a comprehensive context string for agents to reason about."""
    env = state.get("environment") or {}
    configs = state.get("drone_configs") or {}
    dist_matrix = state.get("distance_matrix") or {}
    excluded_targets = set(state.get("excluded_targets") or [])

    airports = env.get("airports", [])
    targets = env.get("targets", [])
    sams = env.get("sams", [])

    # Filter targets to only show accessible ones (not inside SAM zones)
    accessible_targets = [
        t for t in targets if t.get("id", t.get("label", "?")) not in excluded_targets
    ]

    lines = [
        "=" * 60,
        "MISSION CONTEXT",
        "=" * 60,
        "",
        "ENVIRONMENT:",
        f"  Airports: {len(airports)}",
    ]

    for ap in airports:
        ap_id = ap.get("id", ap.get("label", "?"))
        lines.append(f"    {ap_id}: pos=({ap.get('x', 0):.0f}, {ap.get('y', 0):.0f})")

    # Show excluded targets prominently if any exist
    if excluded_targets:
        lines.append(
            f"\n  ⚠️  EXCLUDED TARGETS (inside SAM zones - DO NOT ALLOCATE): {len(excluded_targets)}"
        )
        lines.append(f"      {', '.join(sorted(excluded_targets))}")

    lines.append(
        f"\n  ACCESSIBLE Targets: {len(accessible_targets)} (of {len(targets)} total)"
    )
    total_priority = 0
    for t in accessible_targets:
        t_id = t.get("id", t.get("label", "?"))
        priority = t.get("priority", t.get("value", 5))
        total_priority += priority
        lines.append(
            f"    {t_id}: priority={priority}, pos=({t.get('x', 0):.0f}, {t.get('y', 0):.0f})"
        )

    lines.append(f"  Total Priority: {total_priority}")

    if sams:
        lines.append(f"\n  SAMs/NFZs: {len(sams)}")
        for i, sam in enumerate(sams):
            pos = sam.get("pos", [0, 0])
            lines.append(
                f"    SAM{i+1}: range={sam.get('range', 0)}, pos=({pos[0]:.0f}, {pos[1]:.0f})"
            )

    lines.append("\nDRONE CONFIGURATIONS:")
    if not configs:
        lines.append("  (No drone configurations)")
    else:
        # Build a map of target types for eligibility calculation
        target_type_map = {}
        for t in accessible_targets:
            t_id = t.get("id", t.get("label", "?"))
            t_type = str(t.get("type", "a")).upper()
            target_type_map[t_id] = t_type

        for did in sorted(configs.keys()):
            cfg = configs[did]
            enabled = cfg.get("enabled", did == "1")
            if not enabled:
                continue
            fuel = resolve_fuel_budget(cfg, drone_id=str(did))
            airport = cfg.get("homeAirport", cfg.get("home_airport", "A1"))

            # Get allowed target types for this drone
            allowed_types = cfg.get(
                "accessibleTargets", cfg.get("accessible_targets", [])
            )
            if allowed_types:
                allowed_types_upper = {t.upper() for t in allowed_types}
            else:
                allowed_types_upper = None  # None means ALL types allowed

            # Calculate which specific targets this drone can visit
            if allowed_types_upper is None:
                eligible_targets = list(target_type_map.keys())
            else:
                eligible_targets = [
                    tid
                    for tid, ttype in target_type_map.items()
                    if ttype in allowed_types_upper
                ]

            lines.append(f"  D{did}:")
            lines.append(f"    Home: {airport}, Fuel: {fuel}")
            # Show refuel station if configured
            refuel = cfg.get("refuel_airport", "")
            if refuel == "-":
                lines.append("    Refuel: ANY airport (solver picks best)")
            elif refuel:
                lines.append(f"    Refuel: {refuel}")
            if allowed_types_upper:
                lines.append(
                    f"    Sensor types: {', '.join(sorted(allowed_types_upper))}"
                )
                lines.append(
                    f"    ⚠️  ELIGIBLE TARGETS (ONLY THESE): {', '.join(sorted(eligible_targets))}"
                )
            else:
                lines.append("    Sensor types: ALL")
                lines.append(
                    f"    Eligible targets: ALL ({len(eligible_targets)} targets)"
                )

    # Add distance info if available
    if dist_matrix:
        lines.append("\nDISTANCE MATRIX: Available (SAM-aware paths computed)")

    # Add policy rules if any (Learning v1)
    policy_rules = state.get("policy_rules", [])
    if policy_rules:
        lines.append(f"\nACTIVE POLICY RULES ({len(policy_rules)}):")
        for rule in policy_rules[:5]:  # Show first 5 rules
            title = rule.get("title", "Untitled")
            category = rule.get("category", "unknown")
            lines.append(f"  - [{category}] {title}")
        if len(policy_rules) > 5:
            lines.append(f"  ... and {len(policy_rules) - 5} more rules")

    lines.append("=" * 60)
    return "\n".join(lines)


def build_current_solution_context(state: Any) -> str:
    """
    Build a textual summary of the CURRENT solution for the LLM.

    Uses mission_metrics when available (fuel, margins, points, unvisited).
    """
    routes = state.get("routes") or {}
    allocation = state.get("allocation") or {}
    metrics = state.get("mission_metrics") or {}

    if not routes:
        return "CURRENT SOLUTION: None computed yet.\n\n"

    lines: List[str] = []
    lines.append("CURRENT SOLUTION SUMMARY:")

    total_points = metrics.get("total_points")
    total_fuel = metrics.get("total_fuel")
    per_drone = metrics.get("per_drone") or {}
    unvisited = metrics.get("unvisited_targets") or []

    # Totals
    if total_points is not None and total_fuel is not None:
        lines.append(f"- Total points collected: {total_points}")
        lines.append(f"- Total fuel used: {total_fuel:.1f}")
    else:
        # Fallback if metrics missing
        lines.append("- Total points collected: (not available)")
        lines.append("- Total fuel used: (not available)")

    # Unvisited
    if unvisited:
        lines.append(f"- Unvisited targets: {', '.join(unvisited)}")
    else:
        lines.append("- All targets are visited.")

    # Per-drone metrics
    if per_drone:
        lines.append("")
        lines.append("PER-DRONE BREAKDOWN:")
        # Sort by drone id numerically when possible
        for did in sorted(per_drone.keys(), key=lambda d: int(d)):
            d = per_drone[did]
            fuel_used = d.get("fuel_used", 0.0) or 0.0
            fuel_budget = d.get("fuel_budget", 0.0) or 0.0
            fuel_margin = d.get("fuel_margin", 0.0)
            points = d.get("points", 0) or 0
            n_targets = len(d.get("visited_targets", []))

            lines.append(
                f"- D{did}: fuel_used={fuel_used:.1f}, "
                f"fuel_budget={fuel_budget:.1f}, "
                f"margin={fuel_margin:.1f}, "
                f"points={points}, targets={n_targets}"
            )

    # Allocation summary (optional, but helpful)
    if allocation:
        lines.append("")
        lines.append("ALLOCATION SUMMARY (targets per drone):")
        for did in sorted(allocation.keys(), key=lambda d: int(d)):
            tlist = allocation[did]
            lines.append(f"- D{did}: {len(tlist)} targets → {', '.join(tlist)}")

    lines.append("")  # final blank line
    return "\n".join(lines)
