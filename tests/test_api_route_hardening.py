import importlib
from types import SimpleNamespace

import pytest

ROUTER_AND_FACADE_MODULES = [
    "server.main",
    "server.api.agent_routes",
    "server.api.debug_routes",
    "server.api.environment_routes",
    "server.api.executive_routes",
    "server.api.legacy_agent_routes",
    "server.api.llm_routes",
    "server.api.matrix_routes",
    "server.api.mission_routes",
    "server.api.optimizer_routes",
    "server.api.solve_routes",
    "server.agents.isr_agent_multi_v4",
    "server.agents.v4.allocator",
    "server.agents.v4.context",
    "server.agents.v4.critic",
    "server.agents.v4.metrics",
    "server.agents.v4.mission_planner",
    "server.agents.v4.optimizer",
    "server.agents.v4.optimizer_tools",
    "server.agents.v4.responder",
    "server.agents.v4.route_optimizer",
    "server.agents.v4.state",
    "server.agents.v4.strategist",
    "server.agents.v4.workflow",
    "server.solver.post_optimizer",
    "server.solver.solver_bridge",
]


@pytest.mark.parametrize("module_name", ROUTER_AND_FACADE_MODULES)
def test_router_and_facade_modules_import(module_name):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name in {"fastapi", "crewai"}:
            pytest.skip(f"{exc.name} is not installed in this environment")
        raise
    assert module


def test_environment_router_import_export_flow():
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from server.api.environment_routes import create_environment_router

    calls = {"saved": None, "refreshed": None}

    class FakeEnvironmentService:
        def save_environment(self, env):
            calls["saved"] = env

        def refresh_distance_matrix(self, env):
            calls["refreshed"] = env

        def environment_response(self):
            return {"environment": calls["saved"], "matrix_cached": False}

        def build_export(self):
            return "ise-env-test.json", {
                "environment": calls["saved"],
                "matrix_cached": False,
            }

        def read_environment_text(self):
            return '{"ok": true}'

    class FakeRunLedger:
        def snapshot_environment_update(self, env):
            return {
                "run_id": "run-1",
                "env_version_id": "env-1",
                "event_type": "ENV_IMPORTED",
            }

    app = fastapi.FastAPI()
    app.include_router(
        create_environment_router(FakeEnvironmentService(), FakeRunLedger())
    )
    client = testclient.TestClient(app)

    invalid = client.post("/api/environment", json={"environment": []})
    assert invalid.status_code == 200
    assert invalid.json() == {"success": False, "error": "Invalid payload"}

    env = {"airports": [{"id": "A1"}], "targets": [], "sams": []}
    update = client.post("/api/environment", json={"environment": env})
    assert update.status_code == 200
    assert update.json() == {
        "success": True,
        "run_id": "run-1",
        "env_version_id": "env-1",
        "event_type": "ENV_IMPORTED",
    }
    assert calls == {"saved": env, "refreshed": env}

    current = client.get("/api/environment")
    assert current.status_code == 200
    assert current.json()["environment"] == env

    exported = client.get("/api/export_environment")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == (
        'attachment; filename="ise-env-test.json"'
    )
    assert exported.json()["environment"] == env

    debug = client.get("/debug/env")
    assert debug.status_code == 200
    assert debug.text == '{"ok": true}'


def test_matrix_status_and_clear_routes(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api import matrix_routes

    cleared = {"called": False}
    matrix = {
        "labels": ["A1", "T1"],
        "paths": {"A1->T1": [[0, 0], [1, 1]]},
        "excluded_targets": ["T2"],
    }

    monkeypatch.setattr(matrix_routes, "get_current_matrix", lambda: matrix)
    monkeypatch.setattr(
        matrix_routes,
        "clear_cached_matrix",
        lambda: cleared.__setitem__("called", True),
    )

    app = fastapi.FastAPI()
    app.include_router(matrix_routes.router)
    client = testclient.TestClient(app)

    status = client.get("/api/matrix_status")
    assert status.status_code == 200
    assert status.json() == {
        "cached": True,
        "num_waypoints": 2,
        "num_paths": 1,
        "excluded_targets": ["T2"],
    }

    cleared_response = client.post("/api/clear_matrix")
    assert cleared_response.status_code == 200
    assert cleared_response.json() == {"success": True}
    assert cleared["called"] is True


def test_debug_routes_health_and_supabase_probe(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api import debug_routes

    events = []
    monkeypatch.setattr(
        debug_routes,
        "create_mission_run",
        lambda **kwargs: "run-debug",
    )
    monkeypatch.setattr(
        debug_routes,
        "log_event",
        lambda run_id, event_type, payload: events.append((run_id, event_type, payload))
        or True,
    )

    app = fastapi.FastAPI()
    app.include_router(debug_routes.router)
    client = testclient.TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    probe = client.post("/api/debug/supabase-test")
    assert probe.status_code == 200
    assert probe.json() == {"ok": True, "run_id": "run-debug"}
    assert events == [("run-debug", "SUPABASE_TEST_EVENT", {"ok": True})]


def test_debug_supabase_probe_reports_run_creation_failure(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api import debug_routes

    monkeypatch.setattr(debug_routes, "create_mission_run", lambda **kwargs: None)

    app = fastapi.FastAPI()
    app.include_router(debug_routes.router)
    client = testclient.TestClient(app)

    probe = client.post("/api/debug/supabase-test")
    assert probe.status_code == 200
    assert probe.json() == {
        "ok": False,
        "error": "Failed to create mission run (check Railway logs)",
    }


def test_executive_tick_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api import executive_routes

    captured = {}

    class FakeExecutive:
        def tick(self, request):
            captured["request"] = request
            return SimpleNamespace(
                action=SimpleNamespace(value="PAUSE"),
                mission_id=request.mission_id or "mission-generated",
                draft_plan={"draft": True},
                joined_plan=None,
                visited_targets=["T1"],
                markers={"green": ["T1"], "red": []},
                message="Paused",
            )

    monkeypatch.setattr(executive_routes, "get_executive", lambda: FakeExecutive())

    app = fastapi.FastAPI()
    app.include_router(executive_routes.router)
    client = testclient.TestClient(app)

    response = client.post(
        "/api/executive/tick",
        json={
            "mission_id": "mission-1",
            "ui_state": {"phase": "ANIMATING"},
            "events": [{"type": "HUMAN_COMMAND", "command": "PAUSE"}],
            "env": {"airports": []},
            "drone_configs": {"1": {"enabled": True}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "action": "PAUSE",
        "mission_id": "mission-1",
        "draft_plan": {"draft": True},
        "joined_plan": None,
        "visited_targets": ["T1"],
        "markers": {"green": ["T1"], "red": []},
        "message": "Paused",
    }
    tick_request = captured["request"]
    assert tick_request.ui_state == {"phase": "ANIMATING"}
    assert tick_request.events == [{"type": "HUMAN_COMMAND", "command": "PAUSE"}]


def test_reasoning_agent_route_preserves_memory_followup_flags(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api import agent_routes

    captured = {}

    def fake_handle_reasoning_request(payload):
        captured["payload"] = payload
        return {
            "response": "D1 has visited 2 targets.",
            "reasoning": ["Called get_current_mission_status"],
            "iterations": 2,
            "routes": {
                "1": {
                    "route": ["A1", "T2", "T1", "A1"],
                    "points": 15,
                    "distance": 24.0,
                }
            },
            "routes_changed": False,
        }

    import server.agents.reasoning_agent as reasoning_agent_module

    monkeypatch.setattr(
        reasoning_agent_module,
        "handle_reasoning_request",
        fake_handle_reasoning_request,
    )

    app = fastapi.FastAPI()
    app.include_router(agent_routes.router)
    client = testclient.TestClient(app)

    response = client.post(
        "/api/agents/reason",
        json={
            "user_request": "How many targets have you visited?",
            "env": {"airports": [{"id": "A1"}], "targets": [{"id": "T1"}]},
            "drone_configs": {"1": {"enabled": True}},
            "mission_id": "mission-memory",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mission_id"] == "mission-memory"
    assert body["routes_changed"] is False
    assert body["routes"]["1"]["route"] == ["A1", "T2", "T1", "A1"]
    assert captured["payload"]["mission_id"] == "mission-memory"


def test_reasoning_agent_applies_prompt_selected_time_sync(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api import agent_routes

    captured = {}

    def fake_handle_reasoning_request(payload):
        captured["payload"] = payload
        return {
            "response": "Mission ready.",
            "reasoning": ["Called solve_full_mission"],
            "iterations": 1,
            "routes": {
                "1": {
                    "route": ["A1", "T2", "A1"],
                    "points": 5,
                    "distance": 20.0,
                    "fuel_budget": 100.0,
                    "trajectory": [[0.0, 0.0], [0.0, 0.1], [0.0, 0.0]],
                    "waypoint_indices": {0: 0, 1: 1, 2: 2},
                },
                "2": {
                    "route": ["A2", "T1", "A2"],
                    "points": 5,
                    "distance": 40.0,
                    "fuel_budget": 100.0,
                    "trajectory": [[1.0, 0.0], [1.0, 0.2], [1.0, 0.0]],
                    "waypoint_indices": {0: 0, 1: 1, 2: 2},
                },
            },
            "routes_changed": True,
        }

    import server.agents.reasoning_agent as reasoning_agent_module

    monkeypatch.setattr(
        reasoning_agent_module,
        "handle_reasoning_request",
        fake_handle_reasoning_request,
    )

    app = fastapi.FastAPI()
    app.include_router(agent_routes.router)
    client = testclient.TestClient(app)

    response = client.post(
        "/api/agents/reason",
        json={
            "user_request": "Begin the mission with syncing drone 1 to T2 and drone 2 to T1.",
            "env": {
                "airports": [{"id": "A1"}, {"id": "A2"}],
                "targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}],
                "sams": [],
            },
            "drone_configs": {
                "1": {"enabled": True, "speed": 2.0, "fuel_budget": 100.0},
                "2": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
            },
            "distance_matrix": {
                "labels": ["A1", "A2", "T1", "T2"],
                "matrix": [
                    [0, 0, 0, 10],
                    [0, 0, 20, 0],
                    [0, 20, 0, 0],
                    [10, 0, 0, 0],
                ],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert captured["payload"]["time_sync"]["mode"] == "selected_targets"
    assert captured["payload"]["time_sync"]["members"] == [
        {"drone_id": "1", "target_id": "T2"},
        {"drone_id": "2", "target_id": "T1"},
    ]
    assert body["time_sync"]["status"] == "synchronized"
    assert body["time_sync"]["mode"] == "selected_targets"
    assert body["time_sync"]["sync_time"] == 20.0
    assert body["routes"]["1"]["arrival_times"]["T2"] == 19.0
    assert body["routes"]["1"]["launch_delay"] == 14.0
    assert body["routes"]["1"]["anim_frame"]["total_distance"] > 0


def test_legacy_agent_run_route_no_environment():
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api.legacy_agent_routes import create_legacy_agent_router

    class FakeEnvironmentService:
        def load_environment(self):
            return None

    app = fastapi.FastAPI()
    app.include_router(create_legacy_agent_router(FakeEnvironmentService()))
    client = testclient.TestClient(app)

    response = client.post("/api/agent/run", json={"drone_configs": {}})
    assert response.status_code == 200
    assert response.json() == {"success": False, "error": "No environment loaded"}


def test_legacy_agent_run_route_writes_heuristic_outputs():
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api.legacy_agent_routes import create_legacy_agent_router

    captured = {}
    env = {
        "airports": [{"id": "A1", "x": 0, "y": 0}],
        "targets": [{"id": "T1", "x": 3, "y": 4}],
        "sams": [],
    }

    class FakeEnvironmentService:
        def load_environment(self):
            return env

        def write_heuristic_outputs(self, *, sequences, drone_configs):
            captured["sequences"] = sequences
            captured["drone_configs"] = drone_configs

    drone_configs = {
        "1": {"start_airport": "A1", "end_airport": "A1", "fuel_budget": 20},
        "2": {"start_airport": "A1", "end_airport": "A1", "fuel_budget": 20},
    }

    app = fastapi.FastAPI()
    app.include_router(create_legacy_agent_router(FakeEnvironmentService()))
    client = testclient.TestClient(app)

    response = client.post(
        "/api/agent/run",
        json={
            "drone_configs": drone_configs,
            "enabled_drones": {"1": True, "2": False},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["routes"]["1"]["route"] == ["A1", "T1", "A1"]
    assert body["routes"]["2"]["route"] == ["A1", "A1"]
    assert captured == {
        "sequences": {"1": "A1,T1,A1", "2": "A1,A1"},
        "drone_configs": drone_configs,
    }


def test_solve_with_allocation_reports_missing_required_fields():
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from server.api.run_ledger_service import RunLedgerService
    from server.api.solve_routes import create_solve_router

    app = fastapi.FastAPI()
    app.include_router(create_solve_router(RunLedgerService()))
    client = testclient.TestClient(app)

    response = client.post(
        "/api/solve_with_allocation",
        json={"env": {"airports": []}, "drone_configs": {}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["routes"] == {}
    assert body["sequences"] == {}
    assert body["errors"] == [
        "Missing required fields: env.airports, env.targets, drone_configs"
    ]


def test_solve_with_allocation_applies_time_sync(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api import solve_routes

    class FakeRunLedger:
        def create_solver_run(self, **kwargs):
            return None

        def log_event(self, *args, **kwargs):
            raise AssertionError("no run id means log_event should not be called")

    def fake_solve_mission_with_allocation(**kwargs):
        return {
            "routes": {
                "1": {
                    "route": ["A1", "T1", "A1"],
                    "sequence": "A1,T1,A1",
                    "points": 5,
                    "distance": 20.0,
                    "fuel_budget": 100.0,
                    "trajectory": [[0.0, 0.0], [0.0, 0.1], [0.0, 0.0]],
                    "waypoint_indices": {0: 0, 1: 1, 2: 2},
                },
                "2": {
                    "route": ["A2", "T2", "A2"],
                    "sequence": "A2,T2,A2",
                    "points": 5,
                    "distance": 40.0,
                    "fuel_budget": 100.0,
                    "trajectory": [[1.0, 0.0], [1.0, 0.2], [1.0, 0.0]],
                    "waypoint_indices": {0: 0, 1: 1, 2: 2},
                },
            },
            "sequences": {"1": "A1,T1,A1", "2": "A2,T2,A2"},
            "allocations": {"1": ["T1"], "2": ["T2"]},
            "distance_matrix": {
                "labels": ["A1", "A2", "T1", "T2"],
                "matrix": [
                    [0, 0, 10, 0],
                    [0, 0, 0, 20],
                    [10, 0, 0, 0],
                    [0, 20, 0, 0],
                ],
            },
        }

    monkeypatch.setattr(
        solve_routes,
        "solve_mission_with_allocation",
        fake_solve_mission_with_allocation,
    )

    app = fastapi.FastAPI()
    app.include_router(solve_routes.create_solve_router(FakeRunLedger()))
    client = testclient.TestClient(app)

    response = client.post(
        "/api/solve_with_allocation",
        json={
            "env": {
                "airports": [{"id": "A1"}, {"id": "A2"}],
                "targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}],
                "sams": [],
            },
            "drone_configs": {
                "1": {"enabled": True, "speed": 2.0, "fuel_budget": 100.0},
                "2": {"enabled": True, "speed": 1.0, "fuel_budget": 100.0},
            },
            "time_sync": {"enabled": True, "tolerance": 0.1},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["time_sync"]["status"] == "synchronized"
    assert body["time_sync"]["wait_strategy"] == "launch_delay"
    assert body["time_sync"]["sync_time"] == 20.0
    assert body["routes"]["1"]["arrival_times"]["T1"] == 19.9
    assert body["routes"]["1"]["launch_delay"] == 14.9
    assert body["routes"]["1"]["wait_times"] == {}
    assert body["routes"]["1"]["sync_wait_fuel"] == 0.0
    assert body["routes"]["1"]["distance"] == 20.0
    fast_frame = body["routes"]["1"]["anim_frame"]
    slow_frame = body["routes"]["2"]["anim_frame"]
    fast_target_idx = fast_frame["waypoint_indices"]["1"]
    slow_target_idx = slow_frame["waypoint_indices"]["1"]
    assert fast_frame["cumulative_distances"][fast_target_idx] / 2.0 == 5.0
    assert (
        fast_frame["cumulative_distances"][fast_target_idx] / 2.0
        + body["routes"]["1"]["launch_delay"]
        == 19.9
    )
    assert slow_frame["cumulative_distances"][slow_target_idx] / 1.0 == 20.0


def test_mission_lifecycle_routes(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.agents import agent_session
    from server.api import mission_state
    from server.api.mission_routes import router as mission_router

    mission_state.MISSION_STORE.clear()
    ended_sessions = []
    monkeypatch.setattr(
        agent_session,
        "end_session",
        lambda mission_id: ended_sessions.append(mission_id) or True,
    )

    app = fastapi.FastAPI()
    app.include_router(mission_router)
    client = testclient.TestClient(app)

    env = {"airports": [{"id": "A1"}], "targets": [{"id": "T1"}], "sams": []}
    drone_configs = {"1": {"enabled": True, "fuel_budget": 10}}

    created = client.post(
        "/api/missions",
        json={"name": "phase-eight", "env": env, "drone_configs": drone_configs},
    )
    assert created.status_code == 200
    created_body = created.json()
    mission_id = created_body["mission_id"]
    assert created_body["name"] == "phase-eight"
    assert created_body["status"] == "active"
    assert created_body["has_solution"] is False

    fetched = client.get(f"/api/missions/{mission_id}")
    assert fetched.status_code == 200
    assert fetched.json()["mission_id"] == mission_id

    ended = client.post(f"/api/missions/{mission_id}/end")
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert ended_sessions == [mission_id]

    missing = client.get("/api/missions/not-a-real-mission")
    assert missing.status_code == 404


def test_insert_missed_endpoint_uses_cached_matrix_fallback(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    from server.api import optimizer_routes

    cached_matrix = {"labels": ["A1", "T1"], "matrix": [[0, 1], [1, 0]], "paths": {}}
    captured = {}

    def fake_insert_missed_optimize(
        *,
        solution,
        env,
        drone_configs,
        distance_matrix,
    ):
        captured["solution"] = solution
        captured["env"] = env
        captured["drone_configs"] = drone_configs
        captured["distance_matrix"] = distance_matrix
        return {"success": True, "routes": solution["routes"], "insertions": []}

    monkeypatch.setattr(optimizer_routes, "get_current_matrix", lambda: cached_matrix)
    monkeypatch.setattr(
        optimizer_routes,
        "insert_missed_optimize",
        fake_insert_missed_optimize,
    )

    app = fastapi.FastAPI()
    app.include_router(optimizer_routes.router)
    client = testclient.TestClient(app)

    env = {"airports": [{"id": "A1", "x": 0, "y": 0}], "targets": [], "sams": []}
    drone_configs = {"1": {"enabled": True, "fuel_budget": 10}}
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "A1"],
                "distance": 0,
                "fuel_budget": 10,
                "trajectory": [],
            }
        }
    }

    response = client.post(
        "/api/insert_missed_optimize",
        json={"solution": solution, "env": env, "drone_configs": drone_configs},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["distance_matrix"] is cached_matrix
