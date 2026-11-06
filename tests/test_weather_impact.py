from server.solver.weather_impact import assess_impact


def cell(lng=-97.2, end="2026-10-12T04:00:00Z"):
    return {
        "rule": "No fly",
        "validFrom": "2026-10-12T00:00:00Z",
        "validTo": end,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lng, 31.8],
                    [lng + 0.4, 31.8],
                    [lng + 0.4, 32.2],
                    [lng, 32.2],
                    [lng, 31.8],
                ]
            ],
        },
    }


def assess(previous, current, targets=None, start=1791763200):
    # 2026-10-12 00:00 UTC
    from server.solver.weather_impact import epoch

    return assess_impact(
        {
            "airports": [
                {"id": "A1", "lat": 32, "lng": -98},
                {"id": "A2", "lat": 32, "lng": -96},
            ],
            "targets": [],
            "sams": [],
        },
        {"1": [[32, -98], [32, -96]]},
        targets or [],
        previous,
        current,
        epoch("2026-10-12T00:00:00Z"),
        epoch("2026-10-12T04:00:00Z"),
        "2026-10-12T00:00:00Z",
    )


def test_new_polygon_intersecting_remaining_route_triggers():
    result = assess([], [cell()])
    assert result["affected"] and result["canReplan"]


def test_remote_changes_do_not_interrupt():
    assert not assess([], [cell(-110)])["affected"]


def test_unchanged_polygon_does_not_interrupt():
    assert not assess([cell()], [cell()])["affected"]


def test_released_missed_target_is_reconsidered_with_fresh_weather():
    result = assess([cell()], [], [{"id": "T1", "lat": 32, "lng": -97}])
    assert result["affected"] and result["canReplan"]


def test_future_expiry_is_not_confirmed_clearance():
    from server.solver.weather_impact import epoch

    result = assess_impact(
        {
            "airports": [
                {"id": "A1", "lat": 32, "lng": -98},
                {"id": "A2", "lat": 32, "lng": -96},
            ],
            "targets": [],
            "sams": [],
        },
        {"1": [[32, -98], [32, -96]]},
        [{"id": "T1", "lat": 32, "lng": -97}],
        [cell(end="2026-10-12T01:00:00Z")],
        [],
        epoch("2026-10-12T02:00:00Z"),
        epoch("2026-10-12T04:00:00Z"),
        "2026-10-12T00:00:00Z",
    )
    assert result["affected"] and not result["canReplan"]


def test_unconfirmed_released_target_triggers_reviewable_replan():
    from server.solver.weather_impact import epoch

    env = {
        "airports": [
            {"id": "A1", "lat": 32, "lng": -98},
            {"id": "A2", "lat": 32, "lng": -96},
        ],
        "targets": [],
        "sams": [],
    }
    result = assess_impact(
        env,
        {"1": [[32, -98], [33, -97], [32, -96]]},
        [{"id": "T1", "lat": 32, "lng": -97}],
        [cell(end="2026-10-12T01:00:00Z")],
        [],
        epoch("2026-10-12T02:00:00Z"),
        epoch("2026-10-12T04:00:00Z"),
        "2026-10-12T00:00:00Z",
    )
    assert result["affected"]
    assert result["canReplan"]
    assert result["recoveredTargets"] == ["T1"]
    assert result["requiresWeatherReview"]
    assert result["notices"]


def test_legacy_request_without_semantic_legs_does_not_infer_detour_release():
    from server.solver.weather_impact import epoch

    result = assess_impact(
        {
            "airports": [
                {"id": "A1", "lat": 32, "lng": -98},
                {"id": "A2", "lat": 32, "lng": -96},
            ],
            "targets": [],
            "sams": [],
        },
        {"1": [[32, -98], [33, -97], [32, -96]]},
        [],
        [cell(end="2026-10-12T01:00:00Z")],
        [],
        epoch("2026-10-12T02:00:00Z"),
        epoch("2026-10-12T04:00:00Z"),
        "2026-10-12T00:00:00Z",
    )
    assert not result["affected"]


def test_same_footprint_with_new_validity_does_not_trigger_cut():
    newer = cell(end="2026-10-12T05:00:00Z")
    assert not assess([cell()], [newer])["affected"]


DETOUR = [[32, -98], [33, -97], [32, -96]]


def assess_detour(
    legs=None, previous=None, current=None, sams=None, targets=None, retrieved=None
):
    from server.solver.weather_impact import epoch

    legs = [DETOUR] if legs is None else legs
    route = [p for leg in legs for p in leg]
    return assess_impact(
        {
            "airports": [
                {"id": "A1", "lat": 32, "lng": -98},
                {"id": "A2", "lat": 32, "lng": -96},
            ],
            "targets": targets or [],
            "sams": sams or [],
        },
        {"1": route},
        [],  # Every target is already scheduled: no missed-target trigger.
        previous if previous is not None else [cell(end="2026-10-12T01:00:00Z")],
        current or [],
        epoch("2026-10-12T02:00:00Z"),
        epoch("2026-10-12T04:00:00Z"),
        retrieved or "2026-10-12T02:00:00Z",
        remaining_legs={"1": legs},
    )


def test_expired_unflown_detour_triggers_with_all_targets_scheduled():
    result = assess_detour(targets=[{"id": "T1", "lat": 32, "lng": -96}])
    assert result["affected"] and result["canReplan"]
    assert result["releasedDetours"] == ["1"]
    assert result["recoveredTargets"] == []
    assert not result["requiresWeatherReview"]
    assert "shorter remaining detour" in " ".join(result["reasons"])


def test_unconfirmed_future_detour_release_requires_reviewable_draft():
    result = assess_detour(retrieved="2026-10-12T00:00:00Z")
    assert result["affected"] and result["canReplan"]
    assert result["releasedDetours"] == ["1"]
    assert result["requiresWeatherReview"]
    assert "do not confirm route clearance" in " ".join(result["notices"])


def test_remote_and_already_completed_avoidance_do_not_interrupt():
    remote = assess_detour(previous=[cell(-110, end="2026-10-12T01:00:00Z")])
    completed = assess_detour(legs=[DETOUR[1:]])
    behind = assess_detour(legs=[[[32, -96.7], [33, -96.3], [32, -96]]])
    assert not remote["affected"]
    assert not completed["affected"]
    assert not behind["affected"]


def test_required_target_and_refuel_turns_are_not_weather_detours():
    # Identical geometry, but the north vertex is a required stop. Its two
    # semantic legs must never become one airport-to-airport shortcut.
    legs = [DETOUR[:2], DETOUR[1:]]
    target = assess_detour(legs=legs, targets=[{"id": "T1", "lat": 33, "lng": -97}])
    refuel = assess_detour(legs=legs)
    assert not target["affected"]
    assert not refuel["affected"]


def test_permanent_sam_still_blocks_released_weather_shortcut():
    result = assess_detour(
        sams=[{"id": "S1", "center": {"lat": 32, "lng": -97}, "range_km": 30}]
    )
    assert not result["affected"]
    assert result["releasedDetours"] == []


def test_renewed_or_replacement_weather_still_blocks_shortcut():
    renewed = assess_detour(current=[cell(end="2026-10-12T05:00:00Z")])
    replacement = assess_detour(current=[cell(-97.1)])
    assert not renewed["affected"]
    assert not replacement["affected"]


def test_partial_release_shortens_one_detour_while_another_remains():
    first = cell(-99.2, end="2026-10-12T01:00:00Z")
    second = cell()
    path = [[32, -100], [33, -99], [32, -98], [33, -97], [32, -96]]
    result = assess_detour(legs=[path], previous=[first, second], current=[second])
    assert result["affected"] and result["canReplan"]
    assert result["releasedDetours"] == ["1"]


def test_shrunken_weather_can_release_a_detour_before_expiry():
    import copy

    smaller = copy.deepcopy(cell())
    for point in smaller["geometry"]["coordinates"][0]:
        point[1] = 31.8 + (point[1] - 31.8) / 4
    result = assess_detour(previous=[cell()], current=[smaller])
    assert result["affected"] and result["releasedDetours"] == ["1"]


def test_insignificant_bend_does_not_create_checkpoint():
    result = assess_detour(legs=[[[32, -98], [32.00001, -97], [32, -96]]])
    assert not result["affected"]


def test_dense_geodesic_samples_reduce_to_actual_route_bends():
    from path_planning_core.latlng_utils import latlng_to_local_xy, local_xy_to_latlng
    from server.solver.weather_impact import _leg_bends

    corners = [latlng_to_local_xy(*p, 32, -97) for p in DETOUR]
    samples = []
    for a, b in zip(corners, corners[1:]):
        samples.extend(
            (a[0] + (b[0] - a[0]) * i / 500, a[1] + (b[1] - a[1]) * i / 500)
            for i in range(500)
        )
    samples.append(corners[-1])
    assert len(_leg_bends(samples)) == 3
    dense = [list(local_xy_to_latlng(*p, 32, -97)) for p in samples]
    result = assess_detour(legs=[dense], retrieved="2026-10-12T00:00:00Z")
    assert result["releasedDetours"] == ["1"]


def test_geodesic_samples_without_a_real_bend_do_not_trigger():
    from path_planning_core.latlng_utils import latlng_to_local_xy, local_xy_to_latlng

    a = latlng_to_local_xy(33, -98, 32, -97)
    b = latlng_to_local_xy(33, -96, 32, -97)
    path = [
        list(
            local_xy_to_latlng(
                a[0] + (b[0] - a[0]) * i / 100, a[1] + (b[1] - a[1]) * i / 100, 32, -97
            )
        )
        for i in range(101)
    ]
    assert not assess_detour(legs=[path])["affected"]


def test_impact_api_keeps_legacy_requests_and_forwards_semantic_legs():
    from server.api.weather_routes import WeatherImpactRequest, weather_impact
    from server.solver.weather_impact import epoch

    payload = {
        "env": {
            "airports": [
                {"id": "A1", "lat": 32, "lng": -98},
                {"id": "A2", "lat": 32, "lng": -96},
            ],
            "targets": [],
            "sams": [],
        },
        "remaining_routes": {"1": DETOUR},
        "missed_targets": [],
        "previous": [cell(end="2026-10-12T01:00:00Z")],
        "current": [],
        "start": epoch("2026-10-12T02:00:00Z"),
        "end": epoch("2026-10-12T04:00:00Z"),
        "retrieved_at": "2026-10-12T02:00:00Z",
    }
    assert not weather_impact(WeatherImpactRequest(**payload))["affected"]
    payload["remaining_legs"] = {"1": [DETOUR]}
    assert weather_impact(WeatherImpactRequest(**payload))["releasedDetours"] == ["1"]
