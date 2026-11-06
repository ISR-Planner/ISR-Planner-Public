#!/usr/bin/env python3
import json, math, itertools, argparse, time, os
from pathlib import Path

# Verbose solver tracing OFF by default. The Held-Karp DP prints "NEW BEST" on
# every improvement and dumps solver-mode/airport debug on every call; an
# opportunistic solve invokes this ~150× and emitted ~9k lines, tripping
# Railway's 500 logs/sec limit. Set ISR_DEBUG=1 to restore the trace. Shadowing
# the module-level `print` silences all trace calls in this file at once.
_ISR_DEBUG = os.environ.get("ISR_DEBUG", "").strip() not in ("", "0", "false", "False")
_real_print = print


def print(*args, **kwargs):  # noqa: A001 - intentional module-level shadow
    if _ISR_DEBUG:
        _real_print(*args, **kwargs)


# ---------- Ordering-constraint helpers (LOCAL-index form) ----------
# A `constraints` dict has GLOBAL matrix-label form:
#   {"first": id|None, "last": id|None,
#    "before": [(a,b),...], "adjacent": [(y,x),...]}
# The DP works on LOCAL submatrix indices where local 0 = start airport.
# `_local_constraints(constraints, order, labels)` translates the global label
# constraints into cheap LOCAL structures over `order` (a list of GLOBAL matrix
# indices, order[0] == start). Only pairs whose BOTH endpoints are present in
# `order` are kept (a partial precedence/adjacency on a subset is vacuously
# satisfied for the missing endpoint). Returns:
#   (first_local:int|None, last_local:int|None,
#    before_local:list[(a,b)], adj_local:list[(y,x)])   (all LOCAL ints)
# When constraints is falsy, returns all-empty => zero pruning => old behavior.

def _local_constraints(constraints, order, labels):
    if not constraints:
        return None, None, [], []
    # global matrix index -> local index within `order`
    gi_to_local = {gi: li for li, gi in enumerate(order)}

    def _to_local(tid):
        if tid is None:
            return None
        try:
            gi = labels.index(tid)
        except ValueError:
            return None
        return gi_to_local.get(gi)  # None if not in this subset

    first_local = _to_local(constraints.get("first"))
    last_local = _to_local(constraints.get("last"))

    before_local = []
    for a, b in (constraints.get("before") or []):
        la, lb = _to_local(a), _to_local(b)
        if la is not None and lb is not None:
            before_local.append((la, lb))

    adj_local = []
    for y, x in (constraints.get("adjacent") or []):
        ly, lx = _to_local(y), _to_local(x)
        if ly is not None and lx is not None:
            adj_local.append((ly, lx))

    return first_local, last_local, before_local, adj_local


def _seed_allowed(k, first_local, before_local, adj_local):
    """Is singleton seed 0->k (k first visited node) allowed by the constraints?"""
    # FIRST: if a first node is pinned, only it may be the very first node.
    if first_local is not None and k != first_local:
        return False
    # ADJACENCY immediacy: x may only be entered from y. If y is not start(0),
    # x cannot be the first visited node.
    for y, x in adj_local:
        if k == x and y != 0:
            return False
    # PRECEDENCE (a,b): b may not be the first visited node — a must precede it
    # (both endpoints are guaranteed present in the subset).
    for a, b in before_local:
        if k == b:
            return False
    return True


def _transition_allowed(k, j, pm, first_local, before_local, adj_local):
    """Is edge k->j (append j to state whose prior mask is pm, last node k) legal?

    pm = mask BEFORE adding j (i.e. the set of already-visited nodes, includes k).
    All indices LOCAL. Enforces adjacency immediacy + precedence.
    """
    # ADJACENCY immediacy (y,x): x only entered from y; y only exited into x.
    for y, x in adj_local:
        if j == x and k != y:
            return False          # x must be entered from y
        if k == y and j != x:
            return False          # y must be exited into x
    # NOTE: if an adjacency (y,x) has y == start(0), then `j == x and k != y`
    # above rejects x after ANY non-start node k (k is never 0 in the DP loop),
    # so x is forced to be the first visited node — no extra rule needed.
    # PRECEDENCE (a,b): b may not be placed until a already visited.
    for a, b in before_local:
        if j == b and not ((pm >> a) & 1):
            return False
    return True


# ---------- Held–Karp (exact) solvers ----------
def held_karp_cycle(D, constraints_local=None):
    """Shortest cycle visiting all nodes, start/end at index 0.

    constraints_local: (first_local, last_local, before_local, adj_local) in
    LOCAL indices, or None for the unconstrained (original) behavior.
    """
    n = len(D)
    if n == 1:
        return 0.0, [0,0]
    first_local = last_local = None
    before_local = adj_local = []
    if constraints_local is not None:
        first_local, last_local, before_local, adj_local = constraints_local

    # SEED: singletons 0->k. Under FIRST/adjacency, drop illegal seeds.
    DP = {}
    for k in range(1, n):
        if constraints_local is not None and not _seed_allowed(k, first_local, before_local, adj_local):
            continue
        DP[(1<<k, k)] = (D[0][k], 0)
    for r in range(2, n):
        for subset in itertools.combinations(range(1,n), r):
            mask = sum(1<<k for k in subset)
            for j in subset:
                pm = mask ^ (1<<j)
                best = (math.inf, None)
                for k in subset:
                    if k == j:
                        continue
                    if (pm, k) not in DP:
                        continue
                    if constraints_local is not None and not _transition_allowed(
                        k, j, pm, first_local, before_local, adj_local
                    ):
                        continue
                    cand = DP[(pm,k)][0] + D[k][j]
                    if cand < best[0]:
                        best = (cand, k)
                if best[1] is not None:
                    DP[(mask,j)] = best
    mask = (1<<n) - 2
    best = (math.inf, None)
    for j in range(1,n):
        # LAST: if pinned, only that node may be the final visited node before 0.
        if constraints_local is not None and last_local is not None and j != last_local:
            continue
        if (mask, j) not in DP:
            continue
        cand = DP[(mask,j)][0] + D[j][0]
        if cand < best[0]:
            best = (cand, j)
    cost, j = best
    if j is None:
        return math.inf, None
    # reconstruct
    path = [0, j]
    m = (1<<n) - 2
    while j != 0:
        _, p = DP[(m,j)]
        m ^= (1<<j)
        j = p
        if j != 0:
            path.append(j)
    path.append(0)
    return cost, path[::-1]

def held_karp_path(D, end_idx, constraints_local=None):
    """
    Shortest Hamiltonian path from node 0 to 'end_idx' visiting all nodes once.
    'end_idx' is an index in D (e.g., last node).

    constraints_local: (first_local, last_local, before_local, adj_local) in
    LOCAL indices, or None. NOTE: end_idx is the END AIRPORT node — `last_local`
    (if set) is the last TARGET before end_idx.
    """
    n = len(D)
    if n == 1:
        return 0.0, [0]
    first_local = last_local = None
    before_local = adj_local = []
    if constraints_local is not None:
        first_local, last_local, before_local, adj_local = constraints_local

    # SEED
    DP = {}
    for k in range(1, n):
        if k == end_idx:
            # end airport is never a seed unless it is the only remaining node;
            # standard held_karp seeds it too (it is inside 1..n-1). Keep it so
            # the fixed-end reconstruction works, but gate by first/adjacency.
            pass
        if constraints_local is not None and not _seed_allowed(k, first_local, before_local, adj_local):
            continue
        DP[(1<<k, k)] = (D[0][k], 0)
    for r in range(2, n):
        for subset in itertools.combinations(range(1,n), r):
            mask = sum(1<<k for k in subset)
            for j in subset:
                pm = mask ^ (1<<j)
                best = (math.inf, None)
                for k in subset:
                    if k == j:
                        continue
                    if (pm, k) not in DP:
                        continue
                    if constraints_local is not None:
                        # LAST: only `last_local` may immediately precede end_idx.
                        if j == end_idx and last_local is not None and k != last_local:
                            continue
                        if not _transition_allowed(
                            k, j, pm, first_local, before_local, adj_local
                        ):
                            continue
                    cand = DP[(pm,k)][0] + D[k][j]
                    if cand < best[0]:
                        best = (cand, k)
                if best[1] is not None:
                    DP[(mask,j)] = best
    # reconstruct for fixed end_idx
    mask = (1 << n) - 2
    if (mask, end_idx) not in DP:
        return math.inf, None
    cost = DP[(mask, end_idx)][0]
    path = [end_idx]
    m = mask
    cur = end_idx
    while cur != 0:
        _, p = DP[(m, cur)]
        m ^= (1 << cur)
        cur = p
        path.append(cur)
    path = list(reversed(path))
    return cost, path

# ---------- Graph contraction for first/last/adjacent ordering ----------
# Instead of DP-pruning first/last/adjacent, we CONTRACT each fixed sub-chain
# into a single directed super-node with an INPUT port (chain[0]) and an OUTPUT
# port (chain[-1]). The solver then optimizes the ordering of {super-nodes +
# free targets}; the internal chain order is fixed by construction so it can
# NEVER be violated. After solving we EXPAND every super-node back into its real
# node chain to recover the full route.
#
# `before` precedence is OUT OF SCOPE for contraction — it is still handled by
# the DP-prune path (_transition_allowed). When contraction runs it carries any
# `before` pairs through as unit-level precedence so both mechanisms compose.

def build_ordering_chains(constraints, present_labels):
    """Compose first/last/adjacent into directed chains over the present targets.

    Returns a dict:
      {
        "chains":     [[id0, id1, ...], ...]   # each an ordered real-id chain
        "first_chain": [ids] | None            # the chain pinned to lead (first)
        "last_chain":  [ids] | None            # the chain pinned to trail (last)
        "in_chain":   {id: chain_index}        # membership lookup
      }
    or raises ValueError on a conflicting adjacency (a target with two different
    predecessors/successors, or an adjacency cycle).

    `present_labels` is the set of real ids available in THIS subset. Adjacency
    pairs are honored only when BOTH endpoints are present (a partial adjacency
    on a subset is vacuously fine — the missing endpoint simply isn't visited).
    first/last are honored only when the named target is present.
    """
    present = set(present_labels)
    first = constraints.get("first") if constraints else None
    last = constraints.get("last") if constraints else None
    if first is not None and first not in present:
        first = None
    if last is not None and last not in present:
        last = None

    # Adjacency edges y -> x (x immediately follows y), only when both present.
    succ = {}   # y -> x
    pred = {}   # x -> y
    for y, x in (constraints.get("adjacent") or []) if constraints else []:
        if y not in present or x not in present:
            continue
        if y == x:
            raise ValueError(f"adjacency ({y} -> {x}) is a self-loop")
        if y in succ and succ[y] != x:
            raise ValueError(
                f"conflicting adjacency: {y} is required immediately before both "
                f"{succ[y]} and {x}"
            )
        if x in pred and pred[x] != y:
            raise ValueError(
                f"conflicting adjacency: {x} is required immediately after both "
                f"{pred[x]} and {y}"
            )
        succ[y] = x
        pred[x] = y

    # Build chains by walking each edge-graph component from its head (a node
    # with no predecessor). Detect cycles.
    heads = [y for y in succ if y not in pred]
    chains = []
    seen = set()
    for h in heads:
        chain = [h]
        seen.add(h)
        cur = h
        while cur in succ:
            nxt = succ[cur]
            if nxt in seen:
                raise ValueError(f"adjacency cycle detected at {nxt}")
            chain.append(nxt)
            seen.add(nxt)
            cur = nxt
        chains.append(chain)
    # Any node that is part of an adjacency edge but not reached => pure cycle.
    for y in succ:
        if y not in seen:
            raise ValueError(f"adjacency cycle detected involving {y}")

    in_chain = {}
    for ci, ch in enumerate(chains):
        for nid in ch:
            in_chain[nid] = ci

    # Resolve first/last to their containing chain (a first/last target may be
    # the head/tail of an adjacency chain, e.g. first=T2 & adjacent T2->T5).
    first_chain = None
    last_chain = None
    if first is not None:
        if first in in_chain:
            fch = chains[in_chain[first]]
            # `first` must be the HEAD of its chain (nothing may precede it).
            if fch[0] != first:
                raise ValueError(
                    f"first={first} conflicts with adjacency: {first} is required "
                    f"to follow {fch[fch.index(first) - 1]}"
                )
            first_chain = fch
        else:
            first_chain = [first]
    if last is not None:
        if last in in_chain:
            lch = chains[in_chain[last]]
            # `last` must be the TAIL of its chain (nothing may follow it).
            if lch[-1] != last:
                raise ValueError(
                    f"last={last} conflicts with adjacency: {last} is required "
                    f"to precede {lch[lch.index(last) + 1]}"
                )
            last_chain = lch
        else:
            last_chain = [last]
    if (
        first_chain is not None
        and last_chain is not None
        and first_chain is last_chain
    ):
        # first and last name endpoints of the SAME chain: only OK if the chain
        # is the whole route (single chain, no free targets) — otherwise it can
        # be both first and last only when it is the only unit. We allow it; the
        # solver will simply place it as the single leading==trailing unit.
        pass

    return {
        "chains": chains,
        "first_chain": first_chain,
        "last_chain": last_chain,
        "in_chain": in_chain,
    }


def _has_contractible(constraints):
    """True when first/last/adjacent are present (contraction applies)."""
    if not constraints:
        return False
    return bool(
        constraints.get("first")
        or constraints.get("last")
        or constraints.get("adjacent")
    )


def solve_contracted(D_full, labels, start_idx, subset_idxs, end_idx, constraints):
    """Solve one subset via GRAPH CONTRACTION of first/last/adjacent chains.

    Collapses each fixed chain into one super-node (in_port=chain[0],
    out_port=chain[-1], internal_cost = sum of consecutive real-node distances,
    a constant). Builds a reduced distance matrix over [start] + units (+ [end]
    for a path), solves it with the SAME Held-Karp routine, and expands the
    chosen unit order back into the full real-node route.

    end_idx is None for a cycle (round trip to start) or the LOCAL... no — here
    end_idx is a GLOBAL matrix index of the end airport, or None for a cycle.

    Returns (cost, route_labels) or (inf, []) when no legal ordering exists.
    `before` precedence pairs are carried through as unit-level pins.
    """
    subset_labels = [labels[i] for i in subset_idxs]
    present = set(subset_labels)
    plan = build_ordering_chains(constraints, present)
    chains = plan["chains"]
    in_chain = plan["in_chain"]

    # Units = adjacency chains + every free (non-chained) target as a singleton.
    units = [list(ch) for ch in chains]
    for lab in subset_labels:
        if lab not in in_chain:
            units.append([lab])

    def gidx(lab):
        return labels.index(lab)

    def in_port(u):
        return u[0]

    def out_port(u):
        return u[-1]

    def internal(u):
        return sum(D_full[gidx(u[i])][gidx(u[i + 1])] for i in range(len(u) - 1))

    # Reduced node order: 0 = start, 1..U = units, (U+1) = end (path only).
    U = len(units)
    is_cycle = end_idx is None
    n = U + 1 + (0 if is_cycle else 1)
    end_local = None if is_cycle else n - 1

    INF = math.inf
    RD = [[0.0] * n for _ in range(n)]

    def unit_local(k):
        return 1 + k  # local index of unit k

    start_g = start_idx
    # start -> unit v : travel to v's in_port + v's internal cost (counted once
    # on the incoming edge so each unit's internal cost is added exactly once).
    for v in range(U):
        RD[0][unit_local(v)] = D_full[start_g][gidx(in_port(units[v]))] + internal(units[v])
    # unit u -> unit v : out_port(u) -> in_port(v) + internal(v)
    for u in range(U):
        for v in range(U):
            if u == v:
                RD[unit_local(u)][unit_local(v)] = 0.0
                continue
            RD[unit_local(u)][unit_local(v)] = (
                D_full[gidx(out_port(units[u]))][gidx(in_port(units[v]))]
                + internal(units[v])
            )
    if is_cycle:
        # unit u -> start (close the cycle): out_port(u) -> start
        for u in range(U):
            RD[unit_local(u)][0] = D_full[gidx(out_port(units[u]))][start_g]
        RD[0][0] = 0.0
    else:
        end_g = end_idx
        # start -> end direct (no units) handled by held_karp only if U==0.
        RD[0][end_local] = D_full[start_g][end_g]
        for u in range(U):
            RD[unit_local(u)][end_local] = D_full[gidx(out_port(units[u]))][end_g]
        # end has no outgoing edges that matter; fill zeros (already 0).

    # Unit-level constraints: first/last chain -> its unit index; before pairs
    # translated to the units that contain each endpoint.
    first_chain = plan["first_chain"]
    last_chain = plan["last_chain"]

    def _find_unit(chain):
        for k, u in enumerate(units):
            if u == chain:
                return k
        return None

    first_unit = _find_unit(first_chain) if first_chain is not None else None
    last_unit = _find_unit(last_chain) if last_chain is not None else None

    # before pairs (a,b) at the UNIT level: unit(a) must precede unit(b). If both
    # ids live in the SAME chain their relative order is already fixed — skip.
    def _unit_of_label(lab):
        for k, u in enumerate(units):
            if lab in u:
                return k
        return None

    before_units = []
    for a, b in (constraints.get("before") or []):
        if a not in present or b not in present:
            continue
        ua, ub = _unit_of_label(a), _unit_of_label(b)
        if ua is None or ub is None or ua == ub:
            continue
        before_units.append((ua, ub))

    # Translate to LOCAL held-karp indices (unit_local shifts by +1).
    first_local = None if first_unit is None else unit_local(first_unit)
    last_local = None if last_unit is None else unit_local(last_unit)
    before_local = [(unit_local(a), unit_local(b)) for a, b in before_units]
    constraints_local = (first_local, last_local, before_local, [])

    if is_cycle:
        cost, idx_path = held_karp_cycle(RD, constraints_local=constraints_local)
    else:
        cost, idx_path = held_karp_path(
            RD, end_local, constraints_local=constraints_local
        )
    if idx_path is None:
        return INF, []

    # EXPAND: map reduced-local indices back to real node labels.
    route_labels = []
    for li in idx_path:
        if li == 0:
            route_labels.append(labels[start_g])
        elif end_local is not None and li == end_local:
            route_labels.append(labels[end_idx])
        else:
            u = units[li - 1]
            route_labels.extend(u)
    return cost, route_labels


# ---------- Core orienteering over provided matrix ----------
def is_airport(label, airports):
    return any(a["id"] == label for a in airports)

def build_submatrix(D_full, node_order):
    """Return submatrix of D_full over 'node_order' indices, in that order."""
    k = len(node_order)
    M = [[0.0]*k for _ in range(k)]
    for r in range(k):
        fr = node_order[r]
        row_fr = D_full[fr]
        for c in range(k):
            M[r][c] = row_fr[node_order[c]]
    return M

def evaluate_cycle(D_full, labels, start_idx, subset_idxs, constraints=None):
    """Order: [start] + subset; shortest cycle via Held-Karp."""
    # first/last/adjacent are enforced STRUCTURALLY via graph contraction; only
    # `before` remains on the DP-prune path.
    if _has_contractible(constraints):
        return solve_contracted(
            D_full, labels, start_idx, subset_idxs, None, constraints
        )
    order = [start_idx] + list(subset_idxs)
    M = build_submatrix(D_full, order)
    cl = _local_constraints(constraints, order, labels) if constraints else None
    cost, idx_path = held_karp_cycle(M, constraints_local=cl)
    if idx_path is None:
        return math.inf, []  # no legal ordering for this subset
    route_labels = [labels[order[i]] for i in idx_path]
    return cost, route_labels

def evaluate_path(D_full, labels, start_idx, subset_idxs, end_idx, constraints=None):
    """Order: [start] + subset + [end]; shortest path via Held-Karp path."""
    if end_idx in subset_idxs:
        return math.inf, []  # invalid; end cannot be inside subset
    # first/last/adjacent enforced by contraction; `before` stays on DP-prune.
    if _has_contractible(constraints):
        return solve_contracted(
            D_full, labels, start_idx, subset_idxs, end_idx, constraints
        )
    order = [start_idx] + list(subset_idxs) + [end_idx]
    M = build_submatrix(D_full, order)
    end_local = len(order) - 1
    cl = _local_constraints(constraints, order, labels) if constraints else None
    cost, idx_path = held_karp_path(M, end_local, constraints_local=cl)
    if idx_path is None:
        return math.inf, []  # no legal ordering for this subset
    route_labels = [labels[order[i]] for i in idx_path]
    return cost, route_labels

def choose_best_end(D_full, labels, start_idx, subset_idxs, airport_idxs, fuel_cap, constraints=None):
    """Try all airport ends (INCLUDING start for round-trip); pick best feasible."""
    best = {"len": math.inf, "route": None, "end_idx": None}

    for aidx in airport_idxs:
        # Don't skip start - we want to consider round trips too!
        if aidx == start_idx:
            # For round trip, use evaluate_cycle instead
            cost, route = evaluate_cycle(D_full, labels, start_idx, subset_idxs, constraints=constraints)
        else:
            cost, route = evaluate_path(D_full, labels, start_idx, subset_idxs, aidx, constraints=constraints)

        if cost <= fuel_cap and route:
            if cost < best["len"] or (best["route"] is None):
                best.update({"len": cost, "route": route, "end_idx": aidx})

    return best if best["route"] else None

def solve_orienteering_with_matrix(env, start_id=None, mode=None, fuel_cap=None, end_id=None, constraints=None):
    """
    mode:
      - 'return'  : start=end (cycle)
      - 'end'     : fixed end airport
      - 'best_end': pick best airport end (excl. start)
    If any of start_id / end_id / fuel_cap / mode are None, they are derived from JSON.

    constraints: optional ordering-constraint dict (global matrix-label ids):
      {"first": id|None, "last": id|None,
       "before": [(a,b),...], "adjacent": [(y,x),...]}
      When None/empty, the DP behaves byte-for-byte like the original solver.
      May also be supplied via env["constraints"].
    """
    if constraints is None:
        constraints = env.get("constraints")
    # Normalize an empty dict to None so the fast path is taken.
    if constraints and not (
        constraints.get("first")
        or constraints.get("last")
        or constraints.get("before")
        or constraints.get("adjacent")
    ):
        constraints = None
    labels = env["matrix_labels"]
    D_full = env["distance_matrix"]
    airports = env["airports"]
    targets = env["targets"]
    prio = {t["id"]: int(t.get("priority", 1)) for t in targets}

    # =========================================================================
    # DEBUG: Dump raw inputs to diagnose D1_START / synthetic label issues
    # =========================================================================
    print("\n" + "="*70)
    print("🔬 RAW SOLVER INPUTS (for debugging)")
    print("="*70)
    print(f"  matrix_labels ({len(labels)} items): {labels}")
    print(f"  airports ({len(airports)} items): {[a.get('id', '?') for a in airports]}")
    print(f"  targets ({len(targets)} items): {[t.get('id', '?') for t in targets]}")
    print(f"  start_id param: {start_id}")
    print(f"  end_id param: {end_id}")
    print(f"  mode param: {mode}")
    print("="*70 + "\n")

    # Defaults from JSON if not provided
    if start_id is None:
        start_id = env.get("start_airport")
    json_end = env.get("end_airport")
    
    print(f"🔍 SOLVER MODE DETERMINATION:")
    print(f"  - start_id: {start_id}")
    print(f"  - json_end from env: {json_end}")
    print(f"  - end_id parameter: {end_id}")
    print(f"  - mode parameter: {mode}")
    
    if mode is None:
        if json_end is None:
            mode = "best_end"  # choose best end if JSON doesn't fix it
            print(f"  → Mode selected: 'best_end' (no end airport specified)")
        elif json_end == start_id:
            mode = "return"
            print(f"  → Mode selected: 'return' (end = start)")
        else:
            mode = "end"
            print(f"  → Mode selected: 'end' (fixed end airport: {json_end})")
    if end_id is None and json_end is not None:
        end_id = json_end
    # Handle fuel_cap parameter with proper precedence
    if fuel_cap is not None:
        # Explicit parameter takes precedence
        fuel_cap = float(fuel_cap)
        print(f"🔧 Using EXPLICIT fuel_cap parameter: {fuel_cap}")
    else:
        # Fall back to JSON value
        fuel_cap = float(env.get("fuel_budget", float("inf")))
        print(f"🔧 Using JSON fuel_budget: {fuel_cap}")
    
    print(f"🔧 ORIENTEERING SOLVER DEBUG:")
    print(f"  - Final fuel limit: {fuel_cap}")
    print(f"  - Environment has {len(labels)} locations")
    print(f"  - Mode: {mode}")
    print(f"  - End airport: {end_id if mode == 'end' else 'TO BE DETERMINED' if mode == 'best_end' else start_id}")

    # Validate basic fields
    if start_id not in labels:
        print(f"❌ ERROR: Start airport '{start_id}' not in matrix_labels", flush=True)
        print(f"   Available labels ({len(labels)}): {labels[:20]}{'...' if len(labels) > 20 else ''}", flush=True)
        raise ValueError(f"Start airport '{start_id}' not found in matrix_labels.")
    start_idx = labels.index(start_id)

    # =========================================================================
    # FIX: Use ID-based classification instead of "not airport => target"
    # This ensures synthetic nodes (D1_START, CUT_1, etc.) are never targets
    # =========================================================================

    # Build explicit ID sets for airports and targets
    airport_ids = {a["id"] for a in airports if "id" in a}
    target_ids = {t["id"] for t in targets if "id" in t}

    # Build indices using explicit ID membership (not negation)
    airport_idxs = [i for i, lab in enumerate(labels) if lab in airport_ids]
    cand_target_idxs = [i for i, lab in enumerate(labels) if lab in target_ids and i != start_idx]

    # =========================================================================
    # DEBUG: Diagnose D1_START / synthetic label misclassification
    # =========================================================================

    # Labels that look like airports but are NOT in env["airports"]
    suspicious_airport_like = [lab for lab in labels if lab.startswith("A") or lab.endswith("_START")]
    missing_airport_like = [lab for lab in suspicious_airport_like if lab not in airport_ids]

    print("🔍 AIRPORT DEBUG")
    print("  airport_ids:", sorted(list(airport_ids))[:50], ("..." if len(airport_ids) > 50 else ""))
    print("  labels airports (by is_airport):", [labels[i] for i in airport_idxs])
    print("  suspicious airport-like labels:", suspicious_airport_like)
    print("  airport-like labels missing from env['airports']:", missing_airport_like)
    print(f"  start_id={start_id}, is_airport(start_id)={is_airport(start_id, airports)}")

    # STRICT GUARD: Reject unknown labels (neither airport nor target)
    # This catches bugs where synthetic starts aren't properly added to airports list
    unknown_labels = [lab for lab in labels if lab not in airport_ids and lab not in target_ids]
    if unknown_labels:
        raise ValueError(
            f"matrix_labels contains unknown nodes (neither airport nor target): {unknown_labels}. "
            f"Fix env['airports'] (include synthetic starts like D1_START) or env['targets']. "
            f"Current airports: {sorted(airport_ids)}, targets: {sorted(target_ids)}"
        )

    # =========================================================================
    # Endpoint candidates for best_end: airports only, optionally filtered
    # Note: We do NOT exclude start_idx here - returning to start can be valid!
    # If start_id is a synthetic cut node, it won't be in airport_ids anyway.
    # =========================================================================
    best_end_airport_idxs = airport_idxs  # Default: all airports

    valid_end_airport_ids = env.get("valid_end_airports", None)
    if mode == "best_end" and valid_end_airport_ids:
        # Filter to only valid endpoints, but ensure they're real airports
        valid_end_airport_ids = [aid for aid in valid_end_airport_ids if aid in airport_ids]
        valid_end_idxs = [i for i in airport_idxs if labels[i] in valid_end_airport_ids]
        print(f"🔧 Filtering endpoints: {len(airport_idxs)} total airports → {len(valid_end_idxs)} valid endpoints")
        print(f"🔧 Valid endpoint airports: {[labels[i] for i in valid_end_idxs]}")
        # Use filtered list for best_end mode, fallback to all airports if empty
        best_end_airport_idxs = valid_end_idxs if valid_end_idxs else airport_idxs

    print(f"🔧 Available airports (by ID): {[labels[i] for i in airport_idxs]}")
    print(f"🔧 Start index: {start_idx} ({start_id})")
    print(f"🔧 Target candidates (by ID, excludes start): {[labels[i] for i in cand_target_idxs]}")

    # For mode=end, validate the provided/JSON end_id
    end_idx = None
    if mode == "end":
        if end_id is None:
            raise ValueError("mode 'end' requires an end airport (from JSON or --end).")
        if end_id not in labels:
            raise ValueError(f"End airport '{end_id}' not in matrix_labels.")
        if not is_airport(end_id, airports):
            raise ValueError(f"End node '{end_id}' is not an airport.")
        end_idx = labels.index(end_id)

    best = {
        "points": -1,
        "len": math.inf,
        "subset": [],
        "route": [start_id],  # trivial fallback
        "end_airport": start_id if mode == "return" else (end_id if end_id else None)
    }

    # Constrained targets that MUST be present in any accepted subset.
    # A named ordering constraint implies its named targets are wanted, so a
    # subset that silently drops them (making the constraint vacuously true) is
    # not a valid honoring of the constraint. `first`/`last` are single-target
    # pins; before/adjacent name pairs — require BOTH endpoints present.
    required_target_labels = set()
    if constraints:
        if constraints.get("first"):
            required_target_labels.add(constraints["first"])
        if constraints.get("last"):
            required_target_labels.add(constraints["last"])
        for a, b in (constraints.get("before") or []):
            required_target_labels.add(a); required_target_labels.add(b)
        for y, x in (constraints.get("adjacent") or []):
            required_target_labels.add(y); required_target_labels.add(x)
        # Only keep those that are actual targets in this env (airports/absent skip)
        required_target_labels &= target_ids

    # Enumerate all non-empty subsets of targets
    N = len(cand_target_idxs)
    for r in range(1, N+1):
        for combo in itertools.combinations(cand_target_idxs, r):
            if required_target_labels:
                combo_labels = {labels[i] for i in combo}
                if not required_target_labels.issubset(combo_labels):
                    continue  # subset drops a constrained target — skip
            pts = sum(prio.get(labels[i], 0) for i in combo)

            if mode == "return":
                cost, route = evaluate_cycle(D_full, labels, start_idx, combo, constraints=constraints)
            elif mode == "end":
                cost, route = evaluate_path(D_full, labels, start_idx, combo, end_idx, constraints=constraints)
            else:  # best_end
                # Use filtered airport list to exclude synthetic starts from endpoints
                end_choice = choose_best_end(D_full, labels, start_idx, combo, best_end_airport_idxs, fuel_cap, constraints=constraints)
                if end_choice:
                    cost, route = end_choice["len"], end_choice["route"]
                else:
                    cost, route = math.inf, []

            # STRICT FUEL CONSTRAINT CHECK
            feasible = (cost <= fuel_cap) and route and (cost is not None) and (cost != math.inf)
            # Only print for feasible solutions or when we find a new best (reduce verbosity)
            if feasible:
                better = (pts > best["points"]) or \
                         (pts == best["points"] and cost < best["len"]) or \
                         (pts == best["points"] and cost == best["len"] and r > len(best["subset"]))
                if better:
                    # TRIPLE CHECK: Absolutely ensure constraint is satisfied
                    if cost > fuel_cap or cost is None or cost == math.inf:
                        print(f"🚨 CRITICAL BUG: Trying to accept infeasible solution!")
                        print(f"🚨 cost={cost}, fuel_cap={fuel_cap}, cost > fuel_cap = {cost > fuel_cap}")
                        continue  # Skip this solution
                        
                    print(f"    ✅ NEW BEST: {pts} points, {cost:.1f} distance (within {fuel_cap} budget)")
                    best.update({
                        "points": pts,
                        "len": cost,
                        "subset": list(combo),
                        "route": route,
                        "end_airport": route[-1] if mode in ("end","best_end") else start_id
                    })
            # Removed verbose rejected logging - only show feasible solutions

    # Nothing fit: return start->end route (0 points, no targets)
    if best["points"] < 0:
        actual_end = start_id if mode=="return" else (end_id if end_id else start_id)
        # Route must always have at least [start, end] for trajectory generation
        if actual_end and actual_end != start_id:
            route_for_no_targets = [start_id, actual_end]
            # Calculate the direct distance for start->end
            start_idx_for_dist = labels.index(start_id)
            end_idx_for_dist = labels.index(actual_end) if actual_end in labels else start_idx_for_dist
            direct_dist = D_full[start_idx_for_dist][end_idx_for_dist] if end_idx_for_dist != start_idx_for_dist else 0.0
        else:
            route_for_no_targets = [start_id]
            direct_dist = 0.0
        return {
            "sequence": " ".join(route_for_no_targets),
            "route": route_for_no_targets,
            "distance": direct_dist,
            "total_points": 0,
            "visited_targets": [],
            "start_airport": start_id,
            "end_airport": actual_end,
        }

    final_solution = {
        "sequence": " ".join(best["route"]),
        "route": best["route"],
        "distance": best["len"],
        "total_points": best["points"],
        "visited_targets": [labels[i] for i in best["subset"]],
        "start_airport": start_id,
        "end_airport": best["end_airport"]
    }
    
    print(f"🔧 FINAL SOLUTION CHECK:")
    print(f"  - Distance: {final_solution['distance']}")
    print(f"  - Fuel cap: {fuel_cap}")
    print(f"  - CONSTRAINT VIOLATION: {final_solution['distance'] > fuel_cap}")
    
    if final_solution['distance'] > fuel_cap:
        print(f"🚨 CRITICAL BUG: Orienteering solver is returning infeasible solution!")
        print(f"🚨 This should NEVER happen - solver has a bug!")
        print(f"🚨 FORCING FALLBACK TO MINIMAL SOLUTION...")
        
        # Force a minimal solution that definitely fits
        minimal_distance = D_full[start_idx][labels.index(best["end_airport"])] if best["end_airport"] in labels else 0
        if minimal_distance > fuel_cap:
            # Even start->end is too far, return start only
            return {
                "sequence": start_id,
                "route": [start_id],
                "distance": 0.0,
                "total_points": 0,
                "visited_targets": [],
                "start_airport": start_id,
                "end_airport": start_id
            }
        else:
            # Return just start->end
            return {
                "sequence": f"{start_id} {best['end_airport']}",
                "route": [start_id, best['end_airport']],
                "distance": minimal_distance,
                "total_points": 0,
                "visited_targets": [],
                "start_airport": start_id,
                "end_airport": best['end_airport']
            }
    
    return final_solution

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(
        description="Maximize points within fuel using a SAM-aware distance matrix (single drone)."
    )
    ap.add_argument("input_json", help="Path to env JSON (must include matrix_labels + distance_matrix)")
    # All flags are OPTIONAL; JSON provides defaults:
    ap.add_argument("--start", help="Override start airport id (default: from JSON)")
    ap.add_argument("--end", help="Override end airport id (default: from JSON; omit for best_end)")
    ap.add_argument("--mode", choices=["return","end","best_end"],
                    help="Override mode (default inferred from JSON start/end)")
    ap.add_argument("--fuel", type=float, help="Override fuel budget (default: from JSON)")
    ap.add_argument("--outdir", help="Output folder (default: alongside input JSON)")
    args = ap.parse_args()

    with open(args.input_json) as f:
        env = json.load(f)

    res = solve_orienteering_with_matrix(
        env,
        start_id=args.start,
        mode=args.mode,
        fuel_cap=args.fuel,
        end_id=args.end
    )

    # Print copy/paste summary
    print(",".join(res["route"]))
    print(f"Distance: {round(res['distance'], 3)}")
    print(f"Total points: {res['total_points']}")
    print(f"Visited targets: {res['visited_targets']}")
    print(f"Start: {res['start_airport']}  End: {res['end_airport']}")

    # Save solved JSON
    p = Path(args.input_json)
    outdir = Path(args.outdir) if args.outdir else p.parent
    outdir.mkdir(parents=True, exist_ok=True)
    out_path = outdir / f"{p.stem}_OPT_SOLVED_{time.strftime('%Y%m%d_%H%M%S')}.json"

    out_env = dict(env)
    out_env["sequence"] = res["sequence"]
    out_env["path"] = res["route"]
    out_env["path_distance"] = res["distance"]
    out_env["total_points"] = res["total_points"]
    out_env["visited_targets_orienteering"] = res["visited_targets"]
    out_env["start_airport"] = res["start_airport"]
    out_env["end_airport"] = res["end_airport"]

    with open(out_path, "w") as f:
        json.dump(out_env, f, indent=2)
    print(f"Saved: {out_path}")

if __name__ == "__main__":
    main()
