"""Pruning queued routes must retain the router's exact path/tie semantics."""

import heapq
import random

import pytest

from path_planning_core.boundary_navigation import _dijkstra


def legacy_dijkstra(graph, start, goal):
    """Pre-optimization algorithm, kept independent as a differential oracle."""
    queue = [(0.0, start, [start])]
    visited = set()
    while queue:
        distance, node, path = heapq.heappop(queue)
        node_key = (round(node[0], 6), round(node[1], 6))
        if node_key in visited:
            continue
        visited.add(node_key)
        goal_key = (round(goal[0], 6), round(goal[1], 6))
        if node_key == goal_key:
            return path, distance
        for neighbor, edge_distance in graph.get(node, []):
            neighbor_key = (round(neighbor[0], 6), round(neighbor[1], 6))
            if neighbor_key not in visited:
                heapq.heappush(
                    queue, (distance + edge_distance, neighbor, path + [neighbor])
                )
    return [], float("inf")


def test_later_equal_cost_route_keeps_lexicographically_smallest_path():
    start, a, b, goal = (0, 0), (1, 0), (3, 0), (4, 0)
    # b reaches the queue first; a supplies the preferred equal-cost path later.
    graph = {start: [(b, 1), (a, 2)], a: [(goal, 1)], b: [(goal, 2)]}
    expected = ([start, a, goal], 3)
    assert legacy_dijkstra(graph, start, goal) == expected
    assert _dijkstra(graph, start, goal) == expected


def test_zero_cost_edges_keep_full_path_tie_break():
    start, a, b, goal = (0, 0), (1, 0), (2, 0), (3, 0)
    graph = {
        start: [(b, 0), (a, 0), (goal, 0)],
        a: [(b, 0), (goal, 0)],
        b: [(goal, 0)],
    }
    expected = ([start, a, b, goal], 0)
    assert legacy_dijkstra(graph, start, goal) == expected
    assert _dijkstra(graph, start, goal) == expected


@pytest.mark.parametrize("alias_has_exit", [False, True])
def test_distinct_nodes_sharing_rounded_identity_preserve_existing_semantics(
    alias_has_exit,
):
    start, node, alias, goal = (0, 0), (1, 0), (1.0000002, 0), (3, 0)
    graph = {
        start: [(alias, 0.5), (node, 1)],
        node: [] if alias_has_exit else [(goal, 1)],
        alias: [(goal, 1)] if alias_has_exit else [],
    }
    expected = ([start, alias, goal], 1.5) if alias_has_exit else ([], float("inf"))
    assert legacy_dijkstra(graph, start, goal) == expected
    assert _dijkstra(graph, start, goal) == expected


def test_missing_adjacency_and_rounded_goal_identity():
    start, goal = (0, 0), (3, 0)
    assert _dijkstra({}, start, goal) == ([], float("inf"))
    assert _dijkstra({start: [(goal, 2)]}, start, goal) == ([start, goal], 2)
    assert _dijkstra({}, start, (0.0000001, 0)) == ([start], 0)


def test_seeded_directed_graphs_match_legacy_exactly_without_mutation():
    rng = random.Random(927109)
    for case in range(1600):
        count = rng.randint(2, 30)
        mode = case % 4
        if mode == 0:
            nodes = [(float(i), float(i % 3)) for i in range(count)]
        elif mode == 1:
            nodes = [
                (float(i // 3) + (i % 3) * 2e-7, float((i // 3) % 4))
                for i in range(count)
            ]
        elif mode == 2:
            nodes = [
                (-float(i // 2) + (0.5e-6 if i % 2 else -0.5e-6), float(i % 3))
                for i in range(count)
            ]
        else:
            nodes = [(float(i) * 1e-7, float(i % 3)) for i in range(count)]
        density = [0.1, 0.45, 0.9, 1.0][mode]
        graph = {node: [] for node in nodes}
        for node in nodes:
            for neighbor in nodes:
                if node == neighbor or rng.random() > density:
                    continue
                weight = rng.choice([0.0, 0.0, 0.25, 0.5, 1.0, 1.0, 2.0, 3.0, 4.0])
                graph[node].append((neighbor, weight))
                if rng.random() < 0.025:
                    graph[node].append((neighbor, weight + rng.choice([0.0, 1.0, 2.0])))
            rng.shuffle(graph[node])
        start = rng.choice(nodes)
        goal = rng.choice(nodes) if case % 11 else (999.0, 999.0)
        before = {node: edges[:] for node, edges in graph.items()}
        assert _dijkstra(graph, start, goal) == legacy_dijkstra(
            graph, start, goal
        ), case
        assert graph == before


def test_dense_graph_avoids_quadratic_heap_work(monkeypatch):
    nodes = [(i, 0) for i in range(120)]
    graph = {node: [(other, 1.0) for other in nodes if other != node] for node in nodes}
    original_push = heapq.heappush
    counts = {"legacy": 0, "current": 0}
    phase = "legacy"

    def counted_push(queue, entry):
        counts[phase] += 1
        return original_push(queue, entry)

    monkeypatch.setattr(heapq, "heappush", counted_push)
    expected = legacy_dijkstra(graph, nodes[0], nodes[-1])
    phase = "current"
    assert _dijkstra(graph, nodes[0], nodes[-1]) == expected
    assert counts["legacy"] == len(nodes) * (len(nodes) - 1) // 2
    assert counts["current"] <= len(nodes) - 1
    assert counts["current"] * 40 < counts["legacy"]
