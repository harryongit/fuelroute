import json
from pathlib import Path
import pytest
from django.core.cache import cache
from routing.services.dataclasses import StationData, CandidateStation

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear Django cache between tests for test isolation."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def sample_osrm_response():
    """Load sample OSRM fixture representing Dallas, TX to Los Angeles, CA."""
    fixture_path = FIXTURES_DIR / "osrm_dallas_la.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def make_candidate():
    """Factory fixture to construct CandidateStation instances easily."""
    def _make(
        st_id: int,
        mile_marker: float,
        price: float,
        detour_miles: float = 0.5,
        city: str = "Test City",
        state: str = "TX",
    ) -> CandidateStation:
        st = StationData(
            id=st_id,
            opis_id=1000 + st_id,
            name=f"Test Truckstop #{st_id}",
            address="100 Highway Rd",
            city=city,
            state=state,
            lat=32.0,
            lng=-97.0,
            price_per_gallon=price,
        )
        return CandidateStation(
            station=st,
            mile_marker=mile_marker,
            distance_off_route_miles=detour_miles,
        )
    return _make

