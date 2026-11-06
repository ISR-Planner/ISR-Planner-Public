"""Opportunistic refueling prototype v3 — SORTIES FROM MULTIPLE BASES.

Model: the drone alternates between BASES (refuel points: start airport, other
compatible airports, tankers). At a base it has a full tank. It flies a SORTIE
(base -> targets -> same base) within tank budget, refuels (counts against
max_refuels), and may sortie again from the same base or transit to another base.
It ends at a real airport. Maximize points, then minimize fuel.

This captures: A1-sorties (grab targets near A1) AND TK1-sorties (grab targets
near TK1), in one mission — which the linear-chain v2 missed.
"""
import json, math, itertools, sys, contextlib, io
sys.path.insert(0, '/Users/kamalali/isr-planner')

F = sys.argv[1] if len(sys.argv) > 1 else '/Users/kamalali/Downloads/isr_env_20260624-1455_N1_3.json'
MAX_REFUELS = int(sys.argv[2]) if len(sys.argv) > 2 else 6

e = json.load(open(F)); env = e.get('env', e)
prio = {str(t['id']): int(t.get('priority', 5)) for t in env['targets']}
tankers = {str(a['id']) for a in env['airports'] if a.get('type') == 'tanker'}
real_airports = {str(a['id']) for a in env['airports'] if a.get('type') != 'tanker'}
cfg = next(c for c in e.get('drone_configs', env.get('drone_configs', {})).values() if c.get('enabled'))
B = float(cfg['fuel_budget']); START = cfg['start_airport']
targets = list(prio)

with contextlib.redirect_stdout(io.StringIO()):
    from server.solver.solver_bridge import prepare_distance_matrix
    dm = prepare_distance_matrix(env)
labels = dm['labels']; M = dm['matrix']; idx = {l: i for i, l in enumerate(labels)}
def d(a, b): return M[idx[a]][idx[b]] if a in idx and b in idx else float('inf')

bases = sorted(tankers | real_airports)  # refuel options = potential sortie bases

def best_sortie(base, avail, budget):
    """Best base->targets->base loop within budget. Returns (points, fuel, [targets])."""
    best = (0, 0.0, [])
    for r in range(1, len(avail) + 1):
        for combo in itertools.permutations(avail, r):
            seq = [base] + list(combo) + [base]
            dist = sum(d(seq[i], seq[i+1]) for i in range(len(seq)-1))
            if dist <= budget + 1e-9:
                p = sum(prio[t] for t in combo)
                if p > best[0] or (p == best[0] and dist < best[1]):
                    best = (p, dist, list(combo))
    return best

def best_transit(base, end, avail, budget):
    """Best base->targets->end (one-way, can carry targets) within budget."""
    best = (0, 0.0, [])
    for r in range(0, len(avail) + 1):
        for combo in itertools.permutations(avail, r):
            seq = [base] + list(combo) + [end]
            dist = sum(d(seq[i], seq[i+1]) for i in range(len(seq)-1))
            if dist <= budget + 1e-9:
                p = sum(prio[t] for t in combo)
                if p > best[0] or (p == best[0] and dist < best[1]):
                    best = (p, dist, list(combo))
    return best

# DFS over (current_base, visited_targets, refuels_used). At each state:
#  - do a sortie from current_base (refuel after), OR
#  - transit to another base (refuel there), OR
#  - finish: transit current_base -> best real-airport end.
# Memo on (base, frozenset(visited), refuels) -> best (points, fuel, route_tail)
best_overall = [None]

def finish(base, visited, refuels_used, fuel_so_far, route):
    avail = [t for t in targets if t not in visited]
    fin = None
    for endap in real_airports:
        p, f, ts = best_transit(base, endap, avail, B)
        cand = (sum(prio[t] for t in visited) + p, -(fuel_so_far + f), ts, endap, f)
        if fin is None or cand > fin:
            fin = cand
    tp, negf, ts, endap, f = fin
    total_pts = tp; total_fuel = -negf
    full_route = route + ts + [endap]
    key = (total_pts, -total_fuel)
    if best_overall[0] is None or key > best_overall[0][0]:
        best_overall[0] = (key, total_pts, total_fuel, full_route)

def dfs(base, visited, refuels_used, fuel_so_far, route):
    # Option 1: finish now
    finish(base, visited, refuels_used, fuel_so_far, route)
    if refuels_used >= MAX_REFUELS:
        return
    avail = [t for t in targets if t not in visited]
    if not avail:
        return
    # Option 2: sortie from current base (then refuel here)
    p, f, ts = best_sortie(base, avail, B)
    if ts:  # only if it earns targets
        dfs(base, visited | set(ts), refuels_used + 1, fuel_so_far + f, route + ts + [base])
    # Option 3: transit to another base (refuel there), optionally carrying targets
    for nb in bases:
        if nb == base: continue
        if d(base, nb) > B + 1e-9: continue  # unreachable transit
        # simple transit (no targets en route, keep it tractable) then refuel at nb
        dfs(nb, visited, refuels_used + 1, fuel_so_far + d(base, nb), route + [nb])

# Start: full tank at START (no refuel consumed to be there)
dfs(START, set(), 0, 0.0, [START])

key, pts, fuel, route = best_overall[0]
print(f'{F.split("/")[-1]}: budget={B} start={START} max_refuels={MAX_REFUELS}')
print(f'  bases(refuel options)={bases}')
print(f'  BEST: points={pts}  fuel={fuel:.1f}')
print(f'  route: {route}')
# verify legs (split at any base)
legs = []; cur = [route[0]]
for w in route[1:]:
    cur.append(w)
    if w in bases and len(cur) > 1:
        legs.append(cur); cur = [w]
if len(cur) > 1: legs.append(cur)
allok = True
for lg in legs:
    dd = sum(d(lg[i], lg[i+1]) for i in range(len(lg)-1))
    ok = dd <= B + 1e-9; allok &= ok
    print(f'    leg {lg}: {dd:.1f} {"OK" if ok else "OVER!"}')
print(f'  ALL LEGS WITHIN BUDGET: {allok}')
tvis = sum(1 for w in route if w in prio)
print(f'  targets visited: {tvis}/{len(targets)}')
