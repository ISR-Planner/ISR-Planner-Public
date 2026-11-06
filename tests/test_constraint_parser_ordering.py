"""Tests for hard ordering-constraint extraction and compilation.

Covers server/memory/constraint_parser.py's regex_fallback_parse (single-
target first/last) and server/memory/constraints.py's ConstraintProgram/
ConstraintCompiler wiring of ordering_constraints into solver_patches.
"""

from server.memory.constraint_parser import ConstraintParser, regex_fallback_parse
from server.memory.constraints import ConstraintCompiler, ConstraintProgram

TARGET_IDS = ["T1", "T2", "T3", "T4"]
DRONE_IDS = ["1", "2"]


# ---- regex_fallback_parse: ordering_constraints_raw extraction ----


def test_regex_extracts_single_target_first():
    _, _, ordering = regex_fallback_parse("Visit T5 first", TARGET_IDS, DRONE_IDS)
    assert ordering.get("first") == "T5"


def test_regex_extracts_single_target_last():
    _, _, ordering = regex_fallback_parse(
        "Make T3 the last target", TARGET_IDS, DRONE_IDS
    )
    assert ordering.get("last") == "T3"


def test_regex_multi_target_start_with_stays_a_soft_hint():
    # Multiple targets are inherently ambiguous about order among themselves,
    # so this must NOT be promoted to a hard ordering_constraints["first"].
    _, hints, ordering = regex_fallback_parse(
        "Start with T5 and T8", TARGET_IDS, DRONE_IDS
    )
    assert "first" not in ordering
    assert hints.get("start_with", {}).get("targets") == ["T5", "T8"]


def test_regex_no_ordering_language_returns_empty():
    _, _, ordering = regex_fallback_parse("Move T5 to D1", TARGET_IDS, DRONE_IDS)
    assert ordering == {}


# ---- ConstraintParser.parse(): regex path end-to-end ----


def test_parser_regex_path_builds_valid_ordering_constraints():
    parser = ConstraintParser(use_llm=False)
    result = parser.parse(
        "Visit T1 first",
        environment={"targets": [{"id": t} for t in TARGET_IDS]},
        drone_configs={d: {} for d in DRONE_IDS},
    )
    assert not result.program.has_errors()
    assert result.program.ordering_constraints is not None
    assert result.program.ordering_constraints["first"] == "T1"


def test_parser_regex_path_rejects_unknown_target():
    # "T99 first" names a target that doesn't exist in this environment —
    # validate_and_normalize_constraints should reject it as a program error,
    # not silently accept a constraint on a nonexistent target.
    parser = ConstraintParser(use_llm=False)
    result = parser.parse(
        "Visit T99 first",
        environment={"targets": [{"id": t} for t in TARGET_IDS]},
        drone_configs={d: {} for d in DRONE_IDS},
    )
    assert result.program.has_errors()
    assert "Invalid ordering constraints" in result.program.errors[0]


def test_parser_regex_path_no_ordering_language_leaves_it_none():
    parser = ConstraintParser(use_llm=False)
    result = parser.parse(
        "Skip T3",
        environment={"targets": [{"id": t} for t in TARGET_IDS]},
        drone_configs={d: {} for d in DRONE_IDS},
    )
    assert result.program.ordering_constraints is None


# ---- ConstraintProgram / ConstraintCompiler: solver_patches wiring ----


def test_program_serializes_ordering_constraints_round_trip():
    program = ConstraintProgram(source="user")
    program.set_ordering_constraints(
        {"first": "T1", "last": None, "before": [], "adjacent": []}
    )
    data = program.to_dict()
    assert data["ordering_constraints"]["first"] == "T1"

    restored = ConstraintProgram.from_dict(data)
    assert restored.ordering_constraints["first"] == "T1"


def test_program_without_ordering_constraints_omits_key():
    program = ConstraintProgram(source="user")
    data = program.to_dict()
    assert "ordering_constraints" not in data


def _make_compiler() -> ConstraintCompiler:
    environment = {"targets": [{"id": t, "type": "A"} for t in TARGET_IDS]}
    drone_configs = {d: {"accessible_targets": ["ALL"]} for d in DRONE_IDS}
    return ConstraintCompiler(drone_configs=drone_configs, environment=environment)


def test_compiler_passes_ordering_constraints_into_solver_patches():
    program = ConstraintProgram(source="user")
    program.set_ordering_constraints(
        {"first": "T1", "last": "T4", "before": [], "adjacent": []}
    )
    result = _make_compiler().compile(program)
    assert result.success
    assert result.solver_patches["ordering_constraints"]["first"] == "T1"
    assert result.solver_patches["ordering_constraints"]["last"] == "T4"


def test_compiler_without_ordering_constraints_omits_solver_patch_key():
    program = ConstraintProgram(source="user")
    result = _make_compiler().compile(program)
    assert "ordering_constraints" not in result.solver_patches
