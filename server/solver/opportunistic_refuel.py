"""Opportunistic refueling — solver-decided refuel plan.

Replaces the prescriptive forced-leg model (refuel_stops -> fixed virtual legs).
Here the user designates which refuel points a drone MAY use (refuel_options) and
a max-refuel cap; THIS module decides whether/when/where/how-many times to refuel
to maximize collected points.

Model (validated in scripts/opp_refuel_prototype.py): the drone alternates
between BASES (refuel points = start airport + compatible airports + tankers). At
a base it has a full tank. It flies a SORTIE (base -> targets -> same base) within
budget, refuels (counts vs max_refuels), and may sortie again from the same base
OR transit to another base. It ends at a real airport. A sortie that earns no
target is dominated (never taken) — so no pointless refuels, no empty sorties.

Each sortie / transit / final leg is solved by the EXISTING Held-Karp solver via a
`leg_solver` callback (so SAM-aware distances + optimal in-leg ordering are reused;
no brute force). This module only chooses the refuel STRUCTURE.
"""

from __future__ import annotations

from itertools import combinations, permutations
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# A leg_solver solves one leg: (start, end, target_ids, budget, flexible_end) ->
# (route_ids, visited_target_ids, distance). end is None when flexible_end=True.
LegSolver = Callable[
    [str, Optional[str], List[str], float, bool],
    Tuple[List[str], List[str], float],
]


def _real_airport_ends(refuel_options: List[str], tankers: Set[str]) -> List[str]:
    """Refuel options that are REAL airports (valid mission endpoints — not tankers)."""
    return [o for o in refuel_options if o not in tankers]


def solve_opportunistic(
    *,
    start: str,
    refuel_options: List[str],
    tanker_ids: Set[str],
    target_ids: List[str],
    target_priority: Dict[str, int],
    budget: float,
    max_refuels: int,
    leg_solver: LegSolver,
    valid_end_airports: List[str],
    first_target: Optional[str] = None,
    last_target: Optional[str] = None,
    start_budget: Optional[float] = None,
    max_search_states: int = 250,
    base_progress: Optional[Dict[str, float]] = None,
    use_corridor_planner: bool = True,
) -> Dict[str, Any]:
    """Return the best opportunistic route for ONE drone.

    Args:
        start: start airport id (full tank here on a fresh solve; a mid-flight cut
            node on a dynamic replan, where the drone starts with REDUCED fuel).
        refuel_options: ids the drone MAY refuel at (bases) — airports + tankers,
            already runway-filtered by the caller. May include `start`.
        tanker_ids: which option ids are tankers (never an endpoint).
        target_ids: target ids assigned to this drone.
        target_priority: id -> priority points.
        budget: tank (km) — full after every refuel. On a fresh solve this is also
            the leg-1 fuel; on a replan, leg 1 uses `start_budget` instead.
        start_budget: leg-1 fuel (km) available at `start`. Defaults to `budget`
            (fresh solve = full tank). On a dynamic replan the drone has already
            burned part of its tank, so leg 1 flies on this REDUCED fuel while every
            post-refuel leg still gets the full `budget`. Without this split the DFS
            capped EVERY leg at the reduced fuel, so after a deep cut only one nearby
            target fit even though a refuel would restore the full tank — the drone
            parked at the first post-cut target instead of finishing the mission.
        max_refuels: cap on refuels (0 = no refuel allowed).
        leg_solver: callback to solve one leg with the real Held-Karp solver.
        valid_end_airports: real airports the drone may END at (runway-filtered).

    Returns dict: {route, visited_targets, distance, refuel_indices, leg_distances,
                   points}. `route` includes start, every refuel/target waypoint,
                   and the chosen end airport.
    """
    # Leg-1 fuel: reduced on a replan (drone already burned part of the tank before
    # the cut), full tank on a fresh solve. Every leg AFTER a refuel gets `budget`.
    if start_budget is None:
        start_budget = budget

    # BASES = the real, LANDABLE refuel nodes (airports + tankers) the drone may fly
    # TO, land at, and refuel. A base is where a leg may END so the drone can refuel
    # and start the next leg. This is exactly `refuel_options` — which the caller has
    # already filtered to real refuel nodes.
    #
    # `start` is NOT automatically a base. On a fresh solve `start` is a real airport
    # (A1); if the caller listed it as a refuel option it is already in `refuel_options`
    # and thus a base. On a REPLAN `start` is a CUT-POINT — a *virtual* airport at the
    # freeze coordinates. A cut-point is a seam the drone FLIES THROUGH, never lands on:
    # it is the End Virtual Airport of the leg before it and the Start Virtual Airport
    # of the leg after it, but virtual either way. No drone can land or refuel there, so
    # it must NEVER be a base. The DFS still LAUNCHES from `start` (dfs() root) — that is
    # the Start Virtual Airport of leg 1 — but no leg may END at it and the mission may
    # not finish there.
    #
    # Including the cut-point in bases (the historical bug, since opportunistic refuel
    # shipped) let the DFS route a leg back to the cut-point and "refuel"/"finish"
    # there, so the route ended at the virtual airport (drone parked mid-air) instead
    # of a real airport.
    bases = list(dict.fromkeys(list(refuel_options)))

    # MEMOIZE the leg solver — SPEED ONLY, cannot change the answer. The DFS
    # reaches the same (base, end, available-target-set) sub-problem along many
    # different refuel orderings and re-runs the (expensive) Held-Karp leg solve
    # each time. The result depends ONLY on (base, end, frozenset(avail)) — budget
    # is constant per drone — so cache it and reuse. Same route out, fewer solver
    # calls in. This is the dominant cost of the ~176s solve.
    _leg_cache: Dict[Any, Any] = {}

    def leg(base_id, end_id, avail, flexible, leg_budget):
        # leg_budget varies (start_budget for the pre-refuel leg 1, budget after a
        # refuel), so it is part of the cache key — otherwise a leg-1 solve at the
        # reduced fuel would be reused for the same (base,end,avail) post-refuel leg
        # that actually has the full tank, and vice versa.
        key = (base_id, end_id, frozenset(avail), flexible, leg_budget)
        hit = _leg_cache.get(key)
        if hit is None:
            hit = leg_solver(base_id, end_id, list(avail), leg_budget, flexible)
            _leg_cache[key] = hit
        return hit

    def corridor_sequences() -> List[List[str]]:
        """Generate deliberate refuel-base corridors instead of arbitrary bounces."""
        ends = list(
            dict.fromkeys(valid_end_airports or _real_airport_ends(bases, tanker_ids))
        )
        if not ends:
            ends = [start]

        progress = base_progress or {}
        seqs: List[List[str]] = []
        seen: Set[Tuple[str, ...]] = set()

        def add(seq: List[str]) -> None:
            if len(seq) < 2:
                return
            # Intermediate nodes are refuel events; final landing is not.
            if len(seq) - 2 > max_refuels:
                return
            key = tuple(seq)
            if key not in seen:
                seen.add(key)
                seqs.append(seq)

        for end in ends:
            start_p = float(progress.get(start, 0.0))
            end_p = float(progress.get(end, start_p))
            same_end = start == end

            if same_end:
                # Home-base missions may use repeated same-base fuel legs ONLY
                # when the home airport is explicitly allowed as a refuel base.
                # Start/end permission alone means launch/land there; it does
                # not grant a mid-mission fuel reset.
                max_repeats = max_refuels if start in bases else 0
                for repeats in range(max_repeats + 1):
                    add([start] + [start] * repeats + [end])

                # A closed-loop mission has no meaningful start->end corridor: the
                # line collapses to a point. Still, allowed tankers/airports can be
                # the best refuel bases. Generate bounded loop corridors through
                # non-home bases and let the normal candidate scorer choose by
                # priority first, then fuel.
                loop_bases = sorted(
                    [b for b in bases if b != start],
                    key=lambda b: (float(progress.get(b, 0.0)), b),
                )
                max_middle = min(max_refuels, len(loop_bases))
                for n in range(1, max_middle + 1):
                    for middle in permutations(loop_bases, n):
                        add([start] + list(middle) + [end])
                continue

            direction = 1.0 if end_p >= start_p else -1.0
            lo = min(start_p, end_p) - 1e-6
            hi = max(start_p, end_p) + 1e-6
            ordered = sorted(
                [
                    b
                    for b in bases
                    if b != start and b in progress and lo <= float(progress[b]) <= hi
                ],
                key=lambda b: (direction * float(progress[b]), b),
            )
            if end in bases and end not in ordered:
                ordered.append(end)

            max_middle = min(max_refuels, len(ordered))
            for n in range(max_middle + 1):
                for middle in combinations(ordered, n):
                    add([start] + list(middle) + [end])

        seqs.sort(
            key=lambda seq: (
                len(seq),
                sum(
                    abs(
                        (base_progress or {}).get(seq[i + 1], i + 1)
                        - (base_progress or {}).get(seq[i], i)
                    )
                    for i in range(len(seq) - 1)
                ),
                seq,
            )
        )
        return seqs

    def solve_corridor(seq: List[str]) -> None:
        visited: Set[str] = set()
        route = [seq[0]]
        refuel_idx: List[int] = []
        leg_dists: List[float] = []
        total_dist = 0.0

        def add_extra_sorties(
            base: str,
            *,
            must_continue: bool,
            reserve_refuels: int = 0,
        ) -> bool:
            """Spend spare refuels on target-earning same-base sorties."""
            nonlocal total_dist, route, visited

            has_fresh_fuel = must_continue
            while base in bases:
                if not has_fresh_fuel:
                    if len(refuel_idx) >= max_refuels - reserve_refuels:
                        return True
                    refuel_idx.append(len(route) - 1)
                    has_fresh_fuel = True

                avail = [t for t in target_ids if t not in visited]
                if not avail:
                    return True
                sroute, svis, sdist = leg(base, base, avail, False, budget)
                if not sroute or not svis or sdist > budget + 1e-6:
                    return True

                # Intermediate sorties must leave enough refuel capacity for:
                # 1) the fresh tank after returning to this base, and
                # 2) any mandatory corridor refuels still ahead.
                if must_continue and len(refuel_idx) >= max_refuels - reserve_refuels:
                    return True

                route = route + sroute[1:]
                visited |= set(svis)
                total_dist += sdist
                leg_dists.append(sdist)
                has_fresh_fuel = False

                if must_continue:
                    refuel_idx.append(len(route) - 1)
                    has_fresh_fuel = True

            return True

        for leg_no, (s, e) in enumerate(zip(seq, seq[1:])):
            leg_budget = start_budget if leg_no == 0 else budget
            avail = [t for t in target_ids if t not in visited]
            lroute, lvis, ldist = leg(s, e, avail, False, leg_budget)
            if not lroute or ldist > leg_budget + 1e-6:
                return
            if lroute[-1] != e:
                return

            end_index = len(route) - 1 + len(lroute) - 1
            route = route + lroute[1:]
            visited |= set(lvis)
            total_dist += ldist
            leg_dists.append(ldist)

            if leg_no < len(seq) - 2:
                if len(refuel_idx) >= max_refuels:
                    return
                refuel_idx.append(end_index)
                remaining_required_refuels = max(0, (len(seq) - 2) - (leg_no + 1))
                if not add_extra_sorties(
                    e,
                    must_continue=True,
                    reserve_refuels=remaining_required_refuels,
                ):
                    return
            else:
                if not add_extra_sorties(e, must_continue=False):
                    return

        record_candidate(
            visited=visited,
            fuel_so_far=total_dist,
            route=route,
            refuel_idx=refuel_idx,
            leg_dists=leg_dists,
        )

    # DFS state: (current_base, visited frozenset, refuels_used). At each node we
    # may (a) finish -> transit current_base to the best end airport (carrying
    # targets), (b) sortie from current_base, or (c) transit to another base.
    # We keep the best full solution found (points desc, fuel asc).
    best: Dict[str, Any] = {"points": -1, "distance": float("inf"), "route": None}
    valid_end_set = set(valid_end_airports or [])
    seen_labels: Dict[Tuple[str, int], List[Tuple[frozenset, float]]] = {}
    search_states = 0
    search_exhausted = False

    def record_candidate(
        *,
        visited: Set[str],
        fuel_so_far: float,
        route: List[str],
        refuel_idx: List[int],
        leg_dists: List[float],
    ) -> None:
        """Score a complete mission route.

        A route may already be sitting on a valid final airport/refuel boundary. In
        that case the last boundary is a final landing, not a refuel event, so a
        trailing refuel marker at the final waypoint is removed before recording.
        """
        if not route:
            return
        end_ap = route[-1]
        if valid_end_set and end_ap not in valid_end_set:
            return

        final_refuel_idx = list(refuel_idx)
        if final_refuel_idx and final_refuel_idx[-1] == len(route) - 1:
            final_refuel_idx = final_refuel_idx[:-1]

        # first/last-TARGET gate. "first=Tx" means Tx must be the first TARGET in the
        # whole route — nothing (not even a refuel) is visited/landed on a target
        # before it; "last=Ty" the final target. A refuel STOP before the first target
        # is allowed only if it carries no target. We enforce on the target subsequence
        # of the complete route: reject any candidate whose first/last target is wrong.
        # If NO candidate satisfies it, best stays None -> caller reports infeasible
        # ("cannot reach Tx first without refueling; allow a refuel before it or drop
        # the constraint"), matching the respect-or-report policy.
        if first_target is not None or last_target is not None:
            _route_targets = [w for w in route if w in target_priority]
            if first_target is not None and (
                not _route_targets or _route_targets[0] != first_target
            ):
                return
            if last_target is not None and (
                not _route_targets or _route_targets[-1] != last_target
            ):
                return

        pts = sum(target_priority.get(t, 0) for t in visited)
        better = (pts > best["points"]) or (
            pts == best["points"] and fuel_so_far < best["distance"]
        )
        if better:
            best.update(
                points=pts,
                distance=fuel_so_far,
                route=list(route),
                visited=sorted(visited),
                refuel_indices=final_refuel_idx,
                leg_distances=list(leg_dists),
            )

    if use_corridor_planner and base_progress:
        seqs = corridor_sequences()
        for seq in seqs[:max_search_states]:
            solve_corridor(seq)
        print(
            f"   ⛽ Refuel corridor planner evaluated "
            f"{min(len(seqs), max_search_states)}/{len(seqs)} corridors",
            flush=True,
        )
        if best["route"] is not None:
            return {
                "route": best["route"],
                "visited_targets": best["visited"],
                "distance": best["distance"],
                "refuel_indices": best["refuel_indices"],
                "leg_distances": best["leg_distances"],
                "points": best["points"],
            }

    def consider_finish(
        base: str,
        visited: Set[str],
        fuel_so_far: float,
        route: List[str],
        refuel_idx: List[int],
        leg_dists: List[float],
        refuels_used: int,
    ) -> None:
        # If we are already at a valid final airport/refuel boundary, the current
        # route is a complete feasible mission. This is essential for A1->...->A1
        # home-refuel cases: after a target-earning fuel leg returns to A1, the
        # mission may simply end there. The old code only tried to append another
        # flexible final leg; if that leg chose another airport, the target-earning
        # route was rejected and the solver collapsed to ["A1"].
        record_candidate(
            visited=visited,
            fuel_so_far=fuel_so_far,
            route=route,
            refuel_idx=refuel_idx,
            leg_dists=leg_dists,
        )

        # Budget for THIS final leg: reduced start fuel if we never refueled (the
        # whole route is still on leg 1), full tank once any refuel has happened.
        leg_budget = start_budget if refuels_used == 0 else budget
        avail = [t for t in target_ids if t not in visited]
        # Final leg: base -> (targets) -> best real-airport end (flexible).
        froute, fvis, fdist = leg(base, None, avail, True, leg_budget)
        if not froute:
            return
        end_ap = froute[-1]
        if end_ap in tanker_ids:
            # never end on a tanker (safety; leg_solver should already exclude)
            return
        # A mission ENDS only at a REAL end airport. Reject a finish that lands on any
        # non-real node — a cut-point is a virtual airport the drone cannot land on.
        # (Defense-in-depth: _solve_one_leg's flexible path already resolves a real end,
        # but never let a virtual endpoint through here.) NOTE: ending at `start` is only
        # illegal when `start` is NOT a real end airport — a fresh solve legitimately
        # starts AND ends at its home airport (A0 -> ... -> A0); a replan's start is a
        # cut-point, which is not in valid_end_airports and so is correctly rejected.
        if valid_end_set and end_ap not in valid_end_set:
            return
        new_visited = visited | set(fvis)
        total_dist = fuel_so_far + fdist
        # Build the merged route: drop the duplicated `base` joint.
        merged = route + froute[1:]
        record_candidate(
            visited=new_visited,
            fuel_so_far=total_dist,
            route=merged,
            refuel_idx=refuel_idx,
            leg_dists=leg_dists + [fdist],
        )

    def dfs(
        base: str,
        visited: Set[str],
        refuels_used: int,
        fuel_so_far: float,
        route: List[str],
        refuel_idx: List[int],
        leg_dists: List[float],
    ) -> None:
        nonlocal search_states, search_exhausted
        if search_states >= max_search_states:
            search_exhausted = True
            return
        search_states += 1

        visited_key = frozenset(visited)
        state_key = (base, refuels_used)
        labels = seen_labels.setdefault(state_key, [])
        for seen_targets, seen_fuel in labels:
            if seen_fuel <= fuel_so_far + 1e-6 and seen_targets.issuperset(visited_key):
                return
        labels[:] = [
            (seen_targets, seen_fuel)
            for seen_targets, seen_fuel in labels
            if not (
                fuel_so_far <= seen_fuel + 1e-6 and visited_key.issuperset(seen_targets)
            )
        ]
        labels.append((visited_key, fuel_so_far))

        # Budget for any leg DEPARTING here: reduced start fuel while still on leg 1
        # (no refuel yet), full tank once we've refueled at least once.
        cur_budget = start_budget if refuels_used == 0 else budget

        # (a) finish here
        consider_finish(
            base, visited, fuel_so_far, route, refuel_idx, leg_dists, refuels_used
        )

        avail = [t for t in target_ids if t not in visited]
        if not avail:
            return

        # A same-base sortie (b) and a transit (c) both END by refueling at `base`.
        # That is only legal if `base` is a real refuel point. On a replan the DFS
        # root is the cut node (not a base): it may only fly OUT and land elsewhere
        # (option d relocating sortie) or finish at a real airport (option a) — never
        # loop back to refuel at the cut node. Skip (b)/(c) when base isn't refuelable.
        base_is_refuelable = base in set(bases)

        # (b) sortie from current base: base -> targets -> base (then refuel here).
        sroute, svis, sdist = (
            leg(base, base, avail, False, cur_budget)
            if base_is_refuelable
            else ([], [], 0.0)
        )
        if svis:  # only if it earns >=1 target (else dominated — no empty sorties)
            ridx = len(route) - 1 + len(sroute) - 1  # index of the returned base
            merged = route + sroute[1:]
            # Same-base target leg can be the FINAL leg: A1 -> targets -> A1.
            # Do not count a refuel at the final landing.
            record_candidate(
                visited=visited | set(svis),
                fuel_so_far=fuel_so_far + sdist,
                route=merged,
                refuel_idx=refuel_idx,
                leg_dists=leg_dists + [sdist],
            )

        # From here down, every move ends by consuming another refuel event. If
        # the cap is spent, the drone may still fly a final leg on its current
        # tank (handled above), but it may not create another refueled state.
        if refuels_used >= max_refuels:
            return

        moves = []
        if svis:
            moves.append(
                (
                    -sum(target_priority.get(t, 0) for t in svis),
                    sdist,
                    base,
                    visited | set(svis),
                    fuel_so_far + sdist,
                    merged,
                    refuel_idx + [ridx],
                    leg_dists + [sdist],
                )
            )

        # (c) RELOCATING SORTIE: base -> targets -> a DIFFERENT base (refuel there).
        # This is the move a same-base sortie (b) + empty transit (c) cannot express:
        # collect targets EN ROUTE to the next refuel base, instead of flying home,
        # refuelling, and hopping out empty. Without it the solver fragments into
        # wasteful home-round-trips (e.g. A1->targets->A1 + empty A1->TK1) and never
        # finds the through-path optimum A1->targets->TK1->targets->A1. Each such leg
        # ends by refuelling at nb, so it counts as one refuel.
        for nb in bases:
            if nb == base:
                continue
            rroute, rvis, rdist = leg(base, nb, avail, False, cur_budget)
            if not rroute or not rvis or rdist > cur_budget + 1e-6:
                continue  # must earn >=1 target and fit the tank
            ridx = len(route) - 1 + len(rroute) - 1  # index of nb (leg end)
            moves.append(
                (
                    -sum(target_priority.get(t, 0) for t in rvis),
                    rdist,
                    nb,
                    visited | set(rvis),
                    fuel_so_far + rdist,
                    route + rroute[1:],  # drop duplicated leading base
                    refuel_idx + [ridx],
                    leg_dists + [rdist],
                )
            )

        # (d) transit to another base (refuel there). Keep transit simple: no
        # targets carried on the hop (targets are grabbed via sorties), so the hop
        # is just base -> nb if within budget. This is intentionally explored after
        # target-earning moves because it is only a positioning move.
        for nb in bases:
            if nb == base:
                continue
            troute, _tvis, tdist = leg(base, nb, [], False, cur_budget)
            if not troute or tdist > cur_budget + 1e-6:
                continue
            ridx = len(route)  # nb sits at this index after append
            moves.append(
                (
                    0,
                    tdist,
                    nb,
                    visited,
                    fuel_so_far + tdist,
                    route + [nb],
                    refuel_idx + [ridx],
                    leg_dists + [tdist],
                )
            )

        for (
            _,
            _,
            next_base,
            next_visited,
            next_fuel,
            next_route,
            next_ridx,
            next_legd,
        ) in sorted(moves):
            dfs(
                next_base,
                next_visited,
                refuels_used + 1,
                next_fuel,
                next_route,
                next_ridx,
                next_legd,
            )

    dfs(start, set(), 0, 0.0, [start], [], [])

    if search_exhausted:
        print(
            f"   ⏱️ Opportunistic refuel search hit state budget "
            f"({max_search_states}); returning best candidate found",
            flush=True,
        )

    if best["route"] is None:
        # No TARGET-earning route fits the fuel. The drone must still FLY HOME to a
        # real end airport — a cut can strand it with fuel to reach an airport but not
        # to visit any more targets (e.g. the last leg after the refuel cap is spent).
        # Return the empty go-home leg (start → best real airport, 0 targets) so the
        # mission ends cleanly at an airport and the frontend has a real trajectory to
        # animate. Parking at `start` (the cut node) left the drone stuck mid-air and
        # froze the accept flow. Only if even the go-home leg is infeasible (can't
        # reach any airport) do we stay put.
        _budget = start_budget if start_budget is not None else budget
        home, _hv, hdist = leg(start, None, [], True, _budget)
        if (
            home
            and len(home) >= 2
            and home[-1] != start
            and (not valid_end_airports or home[-1] in set(valid_end_airports))
        ):
            return {
                "route": home,
                "visited_targets": [],
                "distance": hdist,
                "refuel_indices": [],
                "leg_distances": [hdist],
                "points": 0,
            }
        # Truly stranded — cannot even reach an airport.
        return {
            "route": [start],
            "visited_targets": [],
            "distance": 0.0,
            "refuel_indices": [],
            "leg_distances": [],
            "points": 0,
        }
    return {
        "route": best["route"],
        "visited_targets": best["visited"],
        "distance": best["distance"],
        "refuel_indices": best["refuel_indices"],
        "leg_distances": best["leg_distances"],
        "points": best["points"],
    }
