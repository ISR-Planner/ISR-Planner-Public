"""Tests for server/solver/constraints.py's validate_and_normalize_constraints.

This is the shared entry-point validator both reasoning_agent.py's
commit_ordered_plan and v4's route_optimizer_node use for fresh,
user/LLM-supplied ordering constraints (first/last/before/adjacent).
"""

import pytest

from server.solver.constraints import validate_and_normalize_constraints

TARGET_IDS = {"T1", "T2", "T3", "T4"}


def test_empty_constraints_returns_all_none():
    result = validate_and_normalize_constraints(None, TARGET_IDS)
    assert result == {"first": None, "last": None, "before": [], "adjacent": []}


def test_simple_first_constraint():
    result = validate_and_normalize_constraints({"first": "T1"}, TARGET_IDS)
    assert result["first"] == "T1"


def test_before_and_adjacent_pairs():
    result = validate_and_normalize_constraints(
        {"before": [["T1", "T2"]], "adjacent": [["T3", "T4"]]}, TARGET_IDS
    )
    assert result["before"] == [("T1", "T2")]
    assert result["adjacent"] == [("T3", "T4")]


def test_unknown_target_raises():
    with pytest.raises(ValueError, match="unknown target"):
        validate_and_normalize_constraints({"first": "T99"}, TARGET_IDS)


def test_first_equals_last_raises():
    with pytest.raises(ValueError, match="cannot be BOTH first and last"):
        validate_and_normalize_constraints({"first": "T1", "last": "T1"}, TARGET_IDS)


def test_cycle_in_before_raises():
    with pytest.raises(ValueError, match="cycle"):
        validate_and_normalize_constraints(
            {"before": [["T1", "T2"], ["T2", "T1"]]}, TARGET_IDS
        )


def test_adjacent_contradicts_first_raises():
    # adjacent (T1,T2): T2 immediately follows T1, but T2 is also required first.
    with pytest.raises(ValueError, match="required first"):
        validate_and_normalize_constraints(
            {"first": "T2", "adjacent": [["T1", "T2"]]}, TARGET_IDS
        )


def test_adjacent_contradicts_last_raises():
    # adjacent (T1,T2): T2 immediately follows T1, but T1 is also required last.
    # This is the branch that was bugged in the original reasoning_agent.py
    # source (guarded by `first is not None` instead of `last is not None`) —
    # verify it actually fires when ONLY `last` is set, no `first`.
    with pytest.raises(ValueError, match="required last"):
        validate_and_normalize_constraints(
            {"last": "T1", "adjacent": [["T1", "T2"]]}, TARGET_IDS
        )


def test_malformed_pair_raises():
    with pytest.raises(ValueError, match=r"\[id, id\] pairs"):
        validate_and_normalize_constraints({"before": [["T1"]]}, TARGET_IDS)


def test_self_pair_raises():
    with pytest.raises(ValueError, match="same target"):
        validate_and_normalize_constraints({"before": [["T1", "T1"]]}, TARGET_IDS)


def test_non_dict_raises():
    with pytest.raises(ValueError, match="must be a dict"):
        validate_and_normalize_constraints("T1", TARGET_IDS)
