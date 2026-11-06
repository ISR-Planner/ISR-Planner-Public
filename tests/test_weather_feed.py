import asyncio
import copy

import pytest
import requests
from fastapi import HTTPException

from server.api import weather_routes as weather


def feed_with_geometry(geometry):
    return {
        "product": "sigmet",
        "url": "NOAA",
        "data": {"features": [{"geometry": geometry, "properties": {"hazard": "TS"}}]},
    }


def test_empty_noaa_response_is_valid(monkeypatch):
    class Response:
        status_code = 204
        url = "https://aviationweather.gov/api/data/sigmet?format=geojson"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(weather.requests, "get", lambda *a, **kw: Response())
    assert weather.fetch_feed("sigmet")["data"]["features"] == []


def test_normalization_preserves_geometry_validity_and_policy():
    geometry = {
        "type": "Polygon",
        "coordinates": [[[-100, 30], [-99, 30], [-99, 31], [-100, 30]]],
    }
    properties = {
        "hazard": "TS",
        "validTimeFrom": "2026-10-12T00:00:00Z",
        "validTimeTo": "2026-10-12T04:00:00Z",
    }
    result = weather.normalize(
        [
            {
                "product": "sigmet",
                "url": "NOAA",
                "data": {
                    "features": [{"geometry": geometry, "properties": properties}]
                },
            }
        ],
        "2026-10-12T00:01:00Z",
    )
    cell = result["cells"][0]
    assert cell["rule"] == "No fly"
    assert cell["geometry"] == geometry
    assert cell["validTo"] == properties["validTimeTo"]
    assert result["retrievedAt"] == "2026-10-12T00:01:00Z"


def test_unclosed_noaa_triangle_is_retained_and_closed_without_mutating_feed():
    # Real SIGMET D2 from the NOAA capture used by the original preview. NOAA
    # omits the repeated closing position; all three vertices are usable.
    ring = [[-107.117, 15.283], [-114.05, 17.1], [-105.9, 24.483]]
    feed = feed_with_geometry({"type": "Polygon", "coordinates": [ring]})
    original = copy.deepcopy(feed)
    result = weather.normalize([feed], "2026-10-12T00:01:00Z")
    assert len(result["cells"]) == 1
    cell = result["cells"][0]
    assert cell["geometry"]["coordinates"] == [ring + [ring[0]]]
    assert cell["rule"] == "No fly"
    assert cell["box"] == [-114.05, 15.283, -105.9, 24.483]
    assert feed == original
    assert cell["geometry"]["coordinates"][0][-1] is not ring[0]
    assert weather._normalize_polygon_geometry(cell["geometry"]) == cell["geometry"]


@pytest.mark.parametrize("geometry_type", ["Polygon", "MultiPolygon"])
def test_every_polygon_and_hole_is_closed(geometry_type):
    outer = [[0, 0], [10, 0], [10, 10], [0, 10]]
    hole = [[1, 1], [2, 1], [2, 2]]
    closed_outer = outer + [outer[0]]
    closed_hole = hole + [hole[0]]
    second = [[20, 0], [30, 0], [30, 10], [20, 0]]
    coordinates = [outer, hole]
    expected = [closed_outer, closed_hole]
    if geometry_type == "MultiPolygon":
        coordinates = [coordinates, [second]]
        expected = [expected, [second]]
    geometry = {"type": geometry_type, "coordinates": coordinates}
    before = copy.deepcopy(geometry)
    normalized = weather._normalize_polygon_geometry(geometry)
    assert normalized == {"type": geometry_type, "coordinates": expected}
    assert geometry == before


@pytest.mark.parametrize(
    "coordinates",
    [
        None,
        [],
        [None],
        [[]],
        [[[0, 0], [1, 1]]],
        [[[0, 0], [1, 1], [0, 0]]],
        [[[0, 0], [1, 1], [0, 0], [1, 1]]],
        [[[0, 0], [1, 1], [None, 2]]],
        [[[0, 0], [1, 1], ["2", 2]]],
        [[[0, 0], [1, 1], [True, 2]]],
        [[[0, 0], [1, 1], [2, float("nan")]]],
        [[[0, 0], [1, 1], [float("inf"), 2]]],
        [[[0, 0], [1, 1], [541, 2]]],
        [[[0, 0], [1, 1], [2, 91]]],
    ],
)
def test_unusable_polygon_is_rejected_not_silently_dropped(coordinates):
    with pytest.raises(ValueError, match="polygon"):
        weather.normalize(
            [feed_with_geometry({"type": "Polygon", "coordinates": coordinates})],
            "2026-10-12T00:01:00Z",
        )


def test_nonpolygon_exclusion_count_is_unchanged():
    result = weather.normalize(
        [feed_with_geometry({"type": "LineString", "coordinates": [[0, 0], [1, 1]]})],
        "2026-10-12T00:01:00Z",
    )
    assert result["cells"] == []
    assert result["excludedLines"] == 1


def test_invalid_feed_geometry_does_not_install_a_partial_capture(monkeypatch):
    previous = {"retrievedAt": "old"}
    monkeypatch.setattr(weather, "_cached", previous)
    monkeypatch.setattr(weather, "_cached_at", -100000)
    malformed = feed_with_geometry({"type": "Polygon", "coordinates": [[[0, 0]]]})
    monkeypatch.setattr(weather, "fetch_feed", lambda *args: malformed)
    with pytest.raises(HTTPException) as error:
        asyncio.run(weather.current_weather())
    assert error.value.status_code == 502
    assert weather._cached is previous


def test_retrieval_failure_does_not_return_old_data(monkeypatch):
    monkeypatch.setattr(weather, "_cached", {"retrievedAt": "old"})
    monkeypatch.setattr(weather, "_cached_at", -100000)

    def fail(*args):
        raise requests.Timeout("NOAA timed out")

    monkeypatch.setattr(weather, "fetch_feed", fail)
    with pytest.raises(HTTPException) as error:
        asyncio.run(weather.current_weather())
    assert error.value.status_code == 502
