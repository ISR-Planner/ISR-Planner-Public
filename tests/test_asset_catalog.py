"""Platform + sensor catalog (catalog/asset_catalog.json, server/asset_catalog.py)."""

import copy

import pytest

from server.asset_catalog import CatalogError, drone_fields, load_catalog, validate

CATALOG = {
    "platforms": [
        {
            "id": "P1",
            "cruise_altitude_km": 7.5,
            "climb_angle_deg": 8,
            "descent_angle_deg": 6,
            "cruise_speed_kmh": 300,
            "endurance_h": 20,
            "runway": "M",
            "sensors": ["EO", "RADAR", "SIG"],
        }
    ],
    "sensors": [
        {"id": "EO", "types": {"all": {"detection_km": 6, "loiter_min": 5}}},
        {
            "id": "RADAR",
            "types": {
                "a": {"detection_km": 20, "loiter_min": 2},
                "b": {"detection_km": 6, "loiter_min": 3},
            },
        },
        {"id": "SIG", "types": {"e": {"detection_km": 40, "loiter_min": 10}}},
    ],
}


def test_the_shipped_catalog_is_valid():
    cat = load_catalog()
    assert {p["id"] for p in cat["platforms"]} >= {"MQ-9A", "RQ-4B"}


def test_platform_fills_flight_fields_in_planner_units():
    f = drone_fields(CATALOG, "P1", ["EO"])
    assert f["cruise_altitude_km"] == 7.5
    assert f["climb_angle_deg"] == 8 and f["descent_angle_deg"] == 6
    assert f["speed"] == pytest.approx(5.0)  # 300 km/h = 5 km/min
    assert f["fuel_budget"] == pytest.approx(6000)  # 300 km/h x 20 h
    assert f["runway"] == "M"
    assert f["platform_id"] == "P1" and f["sensor_ids"] == ["EO"]
    assert f["ceiling_km"] is None  # P1 lists none
    assert drone_fields(load_catalog(), "MQ-1B", [])["ceiling_km"] == 7.6


def test_best_sensor_per_target_type():
    f = drone_fields(CATALOG, "P1", ["EO", "RADAR"])
    assert (
        f["detection_radius"]["a"] == 20 and f["loiter_times"]["a"] == 2
    )  # radar wins on A
    assert (
        f["detection_radius"]["c"] == 6 and f["loiter_times"]["c"] == 5
    )  # only EO sees C
    # B: both see it at 6 km -> the shorter loiter
    assert f["detection_radius"]["b"] == 6 and f["loiter_times"]["b"] == 3


def test_adding_a_sensor_never_makes_the_drone_worse():
    eo = drone_fields(CATALOG, "P1", ["EO"])
    more = drone_fields(CATALOG, "P1", ["EO", "RADAR", "SIG"])
    for t in "abcde":
        assert more["detection_radius"][t] >= eo["detection_radius"][t]


def test_types_no_sensor_sees_are_not_visited():
    f = drone_fields(CATALOG, "P1", ["SIG"])
    assert f["target_access"] == {
        "a": False,
        "b": False,
        "c": False,
        "d": False,
        "e": True,
    }
    assert f["detection_radius"]["e"] == 40 and f["loiter_enabled"] is True
    none = drone_fields(CATALOG, "P1", [])
    assert not any(none["target_access"].values()) and none["loiter_enabled"] is False


def test_a_platform_cannot_take_a_sensor_it_does_not_carry():
    cat = copy.deepcopy(CATALOG)
    cat["platforms"][0]["sensors"] = ["EO"]
    with pytest.raises(CatalogError, match="does not carry"):
        drone_fields(cat, "P1", ["RADAR"])


@pytest.mark.parametrize(
    "break_it, message",
    [
        (
            lambda c: c["sensors"].append(
                {"id": "EO", "types": {"all": {"detection_km": 1, "loiter_min": 1}}}
            ),
            "duplicate sensor",
        ),
        (lambda c: c["platforms"][0]["sensors"].append("NOPE"), "unknown sensor"),
        (
            lambda c: c["sensors"][0]["types"].update(
                {"z": {"detection_km": 1, "loiter_min": 1}}
            ),
            "unknown target category",
        ),
        (lambda c: c["platforms"][0].update({"endurance_h": 0}), "endurance_h"),
        (lambda c: c["platforms"][0].update({"runway": "X"}), "runway"),
    ],
)
def test_catalog_rules(break_it, message):
    cat = copy.deepcopy(CATALOG)
    break_it(cat)
    with pytest.raises(CatalogError, match=message):
        validate(cat)


def test_drone_fields_endpoint():
    from fastapi.testclient import TestClient

    from server.main import app

    client = TestClient(app)
    ok = client.post(
        "/api/asset_catalog/drone_fields",
        json={"platform_id": "MQ-9A", "sensor_ids": ["EOIR"]},
    )
    assert ok.status_code == 200 and ok.json()["platform_id"] == "MQ-9A"
    bad = client.post(
        "/api/asset_catalog/drone_fields",
        json={"platform_id": "MQ-9A", "sensor_ids": ["NOPE"]},
    )
    assert bad.status_code == 400 and "does not carry" in bad.json()["detail"]
    assert client.get("/api/asset_catalog").status_code == 200


def test_fuel_is_the_published_range_when_there_is_one():
    cat = copy.deepcopy(CATALOG)
    assert drone_fields(cat, "P1", [])["fuel_budget"] == pytest.approx(6000)  # 300 x 20
    cat["platforms"][0]["range_km"] = 1851
    assert drone_fields(cat, "P1", [])["fuel_budget"] == pytest.approx(1851)
    cat["platforms"][0]["range_km"] = 0
    with pytest.raises(CatalogError, match="range_km"):
        validate(cat)


def test_agent_sets_a_platform_and_lists_the_catalog():
    import server.agents.reasoning_agent as ra

    ra.set_mission_context(
        {"airports": [], "targets": [], "sams": []},
        {"1": {"enabled": True, "fuel_budget": 150}},
    )
    out = ra.set_drone.invoke(
        {"drone_id": "1", "platform": "MQ-9A", "sensors": ["EOIR"]}
    )
    cfg = ra._mission_context["drone_configs"]["1"]
    assert "platform=MQ-9A" in out
    assert cfg["platform_id"] == "MQ-9A" and cfg["sensor_ids"] == ["EOIR"]
    assert (
        cfg["cruise_altitude_km"] == 7.6
        and cfg["preset"]["fuel_budget"] == cfg["fuel_budget"]
    )
    # Without sensors: every sensor it carries.
    ra.set_drone.invoke({"drone_id": "1", "platform": "RQ-4B"})
    assert ra._mission_context["drone_configs"]["1"]["sensor_ids"] == [
        "EOIR",
        "SAR",
        "SIGINT",
    ]
    assert "Error" in ra.set_drone.invoke(
        {"drone_id": "1", "platform": "RQ-4B", "sensors": ["NOPE"]}
    )
    listing = ra.list_platforms.invoke({})
    assert "MQ-9A" in listing and "SAR" in listing and "nominal" in listing
    assert "platform=RQ-4B" in ra.get_drones.invoke({})


def test_the_shipped_catalog_names_seven_target_categories():
    from server.asset_catalog import category_ids

    cat = load_catalog()
    names = {c["id"]: c["name"] for c in cat["target_categories"]}
    assert category_ids(cat) == ["a", "b", "c", "d", "e", "f", "g"]
    assert names["a"] == "Personnel" and names["d"] == "Emitters / radar sites"
    assert names["g"] == "Maritime"
    # EO/IR sees small targets close in and large fixed ones far off.
    eoir = drone_fields(cat, "MQ-9A", ["EOIR"])
    assert eoir["detection_radius"]["a"] < eoir["detection_radius"]["f"]
    # SIGINT only finds emitters and C2.
    sig = drone_fields(cat, "RQ-4B", ["SIGINT"])
    assert [t for t, on in sig["target_access"].items() if on] == ["d", "e"]


def test_a_category_the_mission_added_is_not_visited_without_sensor_figures():
    f = drone_fields(load_catalog(), "MQ-9A", ["EOIR"], ["a", "b", "h"])
    assert set(f["target_access"]) == {"a", "b", "h"}
    assert f["target_access"]["h"] is False and f["detection_radius"]["h"] == 0


def test_older_catalogs_without_categories_keep_a_to_e():
    from server.asset_catalog import category_ids

    assert category_ids(CATALOG) == ["a", "b", "c", "d", "e"]
    cat = copy.deepcopy(CATALOG)
    cat["target_categories"] = [{"id": "a"}, {"id": "a"}]
    with pytest.raises(CatalogError, match="duplicate target category"):
        validate(cat)
