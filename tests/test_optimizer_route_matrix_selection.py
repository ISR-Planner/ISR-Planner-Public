import importlib
import sys
import types
from types import SimpleNamespace


class _FakeRouter:
    def post(self, *args, **kwargs):
        return lambda func: func

    def get(self, *args, **kwargs):
        return lambda func: func


def _load_optimizer_routes(monkeypatch):
    fake_fastapi = types.ModuleType("fastapi")
    fake_fastapi.APIRouter = _FakeRouter
    fake_fastapi.Request = object
    monkeypatch.setitem(sys.modules, "fastapi", fake_fastapi)
    sys.modules.pop("server.api.optimizer_routes", None)

    return importlib.import_module("server.api.optimizer_routes")


def test_optimizer_route_matrix_selection_prefers_solution_matrix(monkeypatch):
    optimizer_routes = _load_optimizer_routes(monkeypatch)
    explicit = {"labels": ["solution"]}

    def fail_if_called():
        raise AssertionError("cache should not be used when solution has matrix")

    monkeypatch.setattr(optimizer_routes, "get_current_matrix", fail_if_called)

    assert (
        optimizer_routes._select_distance_matrix({"distance_matrix": explicit})
        is explicit
    )


def test_optimizer_route_matrix_selection_uses_cache_fallback(monkeypatch):
    optimizer_routes = _load_optimizer_routes(monkeypatch)
    cached = {"labels": ["cached"]}

    monkeypatch.setattr(optimizer_routes, "get_current_matrix", lambda: cached)

    assert optimizer_routes._select_distance_matrix({}) is cached


def test_optimizer_request_builds_routing_context(monkeypatch):
    optimizer_routes = _load_optimizer_routes(monkeypatch)
    matrix = {
        "labels": ["A1", "T1"],
        "env_hash": "env123",
        "routing_model_hash": "routing123",
    }
    req = SimpleNamespace(
        env={"airports": [{"id": "A1"}]},
        drone_configs={"1": {"enabled": True}},
        solution={"distance_matrix": matrix},
    )

    context = optimizer_routes._routing_context_from_optimizer_request(req)

    assert context.env is req.env
    assert context.drone_configs is req.drone_configs
    assert context.distance_matrix is matrix
    assert context.labels == ["A1", "T1"]
    assert context.env_hash == "env123"
    assert context.routing_model_hash == "routing123"
