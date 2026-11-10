"""
API Integration tests for /api/route/, /api/route/map/<token>/, and /api/health/.
Uses mocked routing client with saved OSRM fixture.
Asserts:
- Exactly 1 external call on first request
- Exactly 0 external calls on cached second request
- Strict response schema compliance
- Map endpoint 200 HTML page
- 400 on invalid or non-US coordinates
"""

from unittest.mock import patch
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from routing.models import Station
from routing.services.station_index import StationSpatialIndex


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def seed_test_stations(db):
    """Seed test stations directly along the route in sample_osrm_response."""
    stations_data = [
        {"opis_id": 101, "name": "Cisco Travel Plaza", "city": "Cisco", "state": "TX", "lat": 32.518, "lng": -98.359, "price": 2.850},
        {"opis_id": 102, "name": "Big Spring Fuel Stop", "city": "Big Spring", "state": "TX", "lat": 32.173, "lng": -101.671, "price": 2.890},
        {"opis_id": 103, "name": "El Paso Truck Center", "city": "El Paso", "state": "TX", "lat": 31.666, "lng": -106.245, "price": 2.920},
        {"opis_id": 104, "name": "Tucson Petro Center", "city": "Tucson", "state": "AZ", "lat": 32.131, "lng": -110.884, "price": 3.050},
        {"opis_id": 105, "name": "Indio Oasis Fuel", "city": "Indio", "state": "CA", "lat": 33.680, "lng": -115.975, "price": 3.450},
    ]
    objs = [
        Station(
            opis_id=d["opis_id"],
            name=d["name"],
            address="Interstate 10/20",
            city=d["city"],
            state=d["state"],
            lat=d["lat"],
            lng=d["lng"],
            price=d["price"],
            geocode_quality="exact",
        )
        for d in stations_data
    ]
    Station.objects.bulk_create(objs)
    StationSpatialIndex.reload_index()
    return objs


@pytest.mark.django_db
def test_route_api_post_success_and_caching(api_client, seed_test_stations, sample_osrm_response):
    """
    Asserts:
    1. First request makes exactly 1 external routing call
    2. Response matches required JSON schema
    3. Second identical request makes 0 external calls (cached)
    """
    with patch("routing.services.routing_client.requests.Session.get") as mock_get:
        # Mock OSRM API response
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "code": "Ok",
            "routes": [{
                "distance": sample_osrm_response["distance_meters"],
                "duration": sample_osrm_response["duration_seconds"],
                "geometry": {
                    "type": "LineString",
                    "coordinates": sample_osrm_response["coordinates"],
                },
            }]
        }

        payload = {"start": "Dallas, TX", "finish": "Los Angeles, CA"}

        # Request 1: Fresh calculation
        resp1 = api_client.post("/api/route/", payload, format="json")
        assert resp1.status_code == status.HTTP_200_OK
        data1 = resp1.json()

        # Assert exactly 1 external API call
        assert mock_get.call_count == 1
        assert data1["external_api_calls"] == 1

        # Assert Schema
        assert "start" in data1 and data1["start"]["resolved"] == "Dallas, TX"
        assert "finish" in data1 and data1["finish"]["resolved"] == "Los Angeles, CA"
        assert data1["distance_miles"] > 1000.0
        assert data1["duration_minutes"] > 0
        assert data1["route"]["type"] == "LineString"
        assert len(data1["route"]["coordinates"]) <= 2000  # Downsampled for payload efficiency
        assert len(data1["fuel_stops"]) >= 1
        assert data1["total_gallons"] > 0
        assert data1["total_fuel_cost"] > 0
        assert "map_url" in data1 and "/api/route/map/" in data1["map_url"]

        # Check fuel stop structure
        first_stop = data1["fuel_stops"][0]
        assert first_stop["order"] == 1
        assert "station" in first_stop
        assert "mile_marker" in first_stop
        assert "distance_off_route_miles" in first_stop
        assert "gallons_purchased" in first_stop
        assert "cost" in first_stop

        # Request 2: Cached route calculation
        resp2 = api_client.post("/api/route/", payload, format="json")
        assert resp2.status_code == status.HTTP_200_OK
        data2 = resp2.json()

        # External call count must NOT increase
        assert mock_get.call_count == 1
        assert data2["external_api_calls"] == 0

        # Verify Map Endpoint
        map_url = data1["map_url"]
        token = map_url.rstrip("/").split("/")[-1]
        map_resp = api_client.get(f"/api/route/map/{token}/")
        assert map_resp.status_code == status.HTTP_200_OK
        assert "text/html" in map_resp["Content-Type"]
        assert "FuelRoute - Optimal Driving Route" in map_resp.content.decode()


@pytest.mark.django_db
def test_route_api_get_query_params(api_client, seed_test_stations, sample_osrm_response):
    """GET /api/route/?start=...&finish=... is supported."""
    with patch("routing.services.routing_client.requests.Session.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "code": "Ok",
            "routes": [{
                "distance": sample_osrm_response["distance_meters"],
                "duration": sample_osrm_response["duration_seconds"],
                "geometry": {
                    "type": "LineString",
                    "coordinates": sample_osrm_response["coordinates"],
                },
            }]
        }

        resp = api_client.get("/api/route/?start=Dallas,%20TX&finish=Los%20Angeles,%20CA")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["start"]["resolved"] == "Dallas, TX"


@pytest.mark.django_db
def test_invalid_location_outside_us_returns_400(api_client):
    """Coordinates outside the continental USA return clear 400 ValidationError."""
    payload = {"start": "51.5074,-0.1278", "finish": "Dallas, TX"}  # London, UK coordinates
    resp = api_client.post("/api/route/", payload, format="json")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "start" in resp.json()


@pytest.mark.django_db
def test_unresolvable_city_returns_400(api_client):
    """Unresolvable city name returns HTTP 400 with helpful error message."""
    payload = {"start": "Atlantis, ZZ", "finish": "Dallas, TX"}
    resp = api_client.post("/api/route/", payload, format="json")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "start" in resp.json()


@pytest.mark.django_db
def test_health_check_endpoint(api_client):
    """GET /api/health/ returns 200 OK and JSON status."""
    resp = api_client.get("/api/health/")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["status"] == "healthy"
    assert "stations_indexed" in data

