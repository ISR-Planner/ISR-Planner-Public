from server.agents.coordinator_v4 import CoordinatorV4


def test_airport_only_mission_is_valid():
    guardrails, errors = CoordinatorV4().validate_environment(
        {"airports": [{"id": "A1"}, {"id": "A2"}], "targets": []},
        {"1": {"start_airport": "A1", "end_airport": "A2"}},
        {"labels": ["A1", "A2"]},
    )
    assert guardrails["env_valid"]
    assert guardrails["matrix_valid"]
    assert errors == []


def test_airport_only_mission_still_requires_airports():
    guardrails, errors = CoordinatorV4().validate_environment(
        {"airports": [], "targets": []}, {}, None
    )
    assert not guardrails["env_valid"]
    assert "No airports in environment" in errors
