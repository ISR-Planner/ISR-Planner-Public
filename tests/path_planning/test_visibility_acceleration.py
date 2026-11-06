"""Batched visibility must preserve the scalar router's edges and paths."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import numpy as np
import pytest

import path_planning_core.boundary_navigation as bn

POLYGONS = [
    [(1, -1), (3, -1), (3, 1), (1, 1)],
    [(5, 0), (7, 0), (7, 2), (5, 2)],
    [(8, -2), (10, -2), (10, -1), (9, -1), (9, 1), (8, 1)],
]


def scalar_graph(polygons):
    nodes = []
    seen = set()
    for poly in polygons:
        for point in poly:
            key = tuple(round(x, 6) for x in point)
            if key not in seen:
                nodes.append(point)
                seen.add(key)
    graph = {node: [] for node in nodes}
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            if bn._segment_clear_of_all_polygons(a, b, polygons):
                distance = bn._distance(a, b)
                graph[a].append((b, distance))
                graph[b].append((a, distance))
    for poly in polygons:
        for a, b in zip(poly, poly[1:] + poly[:1]):
            graph.setdefault(a, [])
            graph.setdefault(b, [])
            if b not in [point for point, _ in graph[a]]:
                distance = bn._distance(a, b)
                graph[a].append((b, distance))
                graph[b].append((a, distance))
    return graph


@pytest.mark.parametrize("polygon", POLYGONS + [list(reversed(POLYGONS[0]))])
def test_batch_predicate_matches_scalar_on_boundaries_and_random_segments(polygon):
    rng = np.random.default_rng(47)
    special = polygon + [
        (2, -1),
        (2, 0),
        (2, 1),
        (0, 0),
        (11, 0),
        (1 + 1e-10, -1),
        (1 - 1e-10, -1),
        (1, -1 + 1e-10),
    ]
    pairs = [(a, b) for a in special for b in special]
    pairs += [(tuple(a), tuple(b)) for a, b in rng.uniform(-3, 12, (400, 2, 2))]
    starts = np.array([a for a, _ in pairs], dtype=float)
    ends = np.array([b for _, b in pairs], dtype=float)
    expected = [bn._segment_crosses_polygon(a, b, polygon) for a, b in pairs]
    actual = bn._segments_cross_polygon_batch(
        starts, ends, bn._PreparedPolygon(polygon)
    )
    np.testing.assert_array_equal(actual, expected)


def test_chunked_graph_preserves_exact_edges_weights_and_order(monkeypatch):
    monkeypatch.setattr(bn, "_VISIBILITY_BATCH_SIZE", 7)
    monkeypatch.setattr(bn, "_VISIBILITY_EDGE_ELEMENTS", 12)
    assert bn._compute_base_visibility_graph(POLYGONS) == scalar_graph(POLYGONS)


def test_endpoint_batches_preserve_query_graph_and_shortest_path(monkeypatch):
    bn._BASE_GRAPH_CACHE.clear()
    fast = bn._build_visibility_graph((0, 0), (12, 0), POLYGONS)

    def scalar_clear(starts, ends, polygons):
        raw = [p.vertices.tolist() for p in polygons]
        return np.array(
            [
                bn._segment_clear_of_all_polygons(tuple(a), tuple(b), raw)
                for a, b in zip(starts.tolist(), ends.tolist())
            ]
        )

    monkeypatch.setattr(bn, "_segments_clear_batch", scalar_clear)
    bn._BASE_GRAPH_CACHE.clear()
    slow = bn._build_visibility_graph((0, 0), (12, 0), POLYGONS)
    assert fast == slow
    assert bn._dijkstra(fast, (0, 0), (12, 0)) == bn._dijkstra(slow, (0, 0), (12, 0))


def test_concurrent_same_geometry_builds_once(monkeypatch):
    bn._BASE_GRAPH_CACHE.clear()
    entered, release = Event(), Event()
    counter_lock = Lock()
    count = 0

    def build(polygons):
        nonlocal count
        with counter_lock:
            count += 1
        entered.set()
        assert release.wait(3)
        return {("same", "graph"): []}

    monkeypatch.setattr(bn, "_compute_base_visibility_graph", build)
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(bn._base_visibility_graph, POLYGONS)
        assert entered.wait(3)
        others = [pool.submit(bn._base_visibility_graph, POLYGONS) for _ in range(3)]
        release.set()
        result = first.result(3)
        assert all(future.result(3) is result for future in others)
    assert count == 1
    assert not bn._BASE_GRAPH_INFLIGHT
    bn._BASE_GRAPH_CACHE.clear()


def test_failed_build_is_not_cached_and_can_retry(monkeypatch):
    bn._BASE_GRAPH_CACHE.clear()
    attempts = 0

    def build(polygons):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("test failure")
        return {}

    monkeypatch.setattr(bn, "_compute_base_visibility_graph", build)
    with pytest.raises(RuntimeError, match="test failure"):
        bn._base_visibility_graph(POLYGONS)
    assert not bn._BASE_GRAPH_INFLIGHT
    assert bn._base_visibility_graph(POLYGONS) == {}
    assert attempts == 2
    bn._BASE_GRAPH_CACHE.clear()


def test_repeated_query_endpoints_reuse_connections_without_sharing_mutations(
    monkeypatch,
):
    bn._BASE_GRAPH_CACHE.clear()
    bn._ENDPOINT_CONTEXT_CACHE.clear()
    bn._base_visibility_graph(POLYGONS)
    original = bn._segments_clear_batch
    calls = 0

    def count_calls(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(bn, "_segments_clear_batch", count_calls)
    first = bn._build_visibility_graph((0, 0), (12, 0), POLYGONS)
    assert calls == 2
    expected = {point: list(edges) for point, edges in first.items()}
    first[(0, 0)].clear()
    first[POLYGONS[0][0]].clear()
    assert bn._build_visibility_graph((0, 0), (12, 0), POLYGONS) == expected
    assert calls == 2
    bn._build_visibility_graph((0, 0), (12, 1), POLYGONS)
    assert calls == 3


def test_endpoint_cache_uses_exact_polygon_and_endpoint_coordinates():
    bn._ENDPOINT_CONTEXT_CACHE.clear()
    polygons = [[(1, 1), (3, 1), (3, 3), (1, 3)]]
    shifted = [[(x + 4e-10, y + 4e-10) for x, y in polygons[0]]]
    assert bn._polygons_key(polygons) == bn._polygons_key(shifted)
    base = {(-1, -1): [], (3, 3): []}
    first = bn._endpoint_visibility_context(base, polygons)
    second = bn._endpoint_visibility_context(base, shifted)
    assert first is not second
    assert bn._endpoint_visibility_context(dict(base), polygons) is not first
    point = (1 + 1.1e-9, 1 + 1.1e-9)
    for context, shapes in ((first, polygons), (second, shifted)):
        expected = tuple(
            (vertex, bn._distance(point, vertex))
            for vertex in base
            if bn._segment_clear_of_all_polygons(point, vertex, shapes)
        )
        assert bn._endpoint_connections(point, context) == expected
    assert first.connections[point] != second.connections[point]
    near_point = (point[0] + 1e-10, point[1])
    bn._endpoint_connections(near_point, first)
    assert len(first.connections) == 2


def test_endpoint_connection_and_context_caches_are_bounded(monkeypatch):
    bn._ENDPOINT_CONTEXT_CACHE.clear()
    monkeypatch.setattr(bn, "_BASE_GRAPH_CACHE_SIZE", 2)
    monkeypatch.setattr(bn, "_ENDPOINT_CONNECTION_CACHE_SIZE", 2)
    base = {(0, 0): [], (1, 1): []}
    context = bn._endpoint_visibility_context(base, POLYGONS)
    for point in ((-1, -1), (-2, -2), (-3, -3)):
        bn._endpoint_connections(point, context)
    assert len(context.connections) == 2
    for _ in range(3):
        bn._endpoint_visibility_context(dict(base), POLYGONS)
    assert len(bn._ENDPOINT_CONTEXT_CACHE) == 2
    bn._ENDPOINT_CONTEXT_CACHE.clear()
