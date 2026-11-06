"""One rule for which target categories a drone may visit
(server/solver/target_access.py), used by every solver path."""

from server.solver.target_access import allowed_types, type_allowed


def test_no_access_set_means_every_category_including_new_ones():
    assert allowed_types({}) is None
    for t in "abcdefg":
        assert type_allowed({}, {"type": t})


def test_only_the_categories_set_true():
    cfg = {"target_access": {"a": True, "b": False, "g": True}}
    assert allowed_types(cfg) == {"a", "g"}
    assert type_allowed(cfg, {"type": "G"})  # stored upper or lower case
    assert not type_allowed(cfg, {"type": "b"})
    assert not type_allowed(cfg, {"type": "f"})  # missing from the dict


def test_solver_filter_no_longer_drops_type_e():
    # solve_mission allowed only A-D to a drone without target_access.
    from server.solver.target_allocator import TargetAllocator

    caps = TargetAllocator()._calculate_capabilities(
        [{"id": "T1", "type": "e"}, {"id": "T2", "type": "g"}], {"1": {}}
    )
    assert caps["1"] == {"T1", "T2"}
