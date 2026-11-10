"""
HTTP client for external routing APIs (OSRM).
Features:
- Thread-safe session with timeout and bounded retries
- Hard-cap on external routing calls (max 1 call per route calculation)
- Deterministic response caching via Django cache
"""

import logging
from typing import Tuple, Dict, Any, List

import requests
from django.conf import settings
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

logger = logging.getLogger(__name__)

HARD_CAP_EXTERNAL_CALLS = 3


class RoutingClientError(Exception):
    """Raised when routing service fails or returns an invalid response."""
    pass


class RoutingClient:
    """Client for querying driving routes from OSRM with caching and retries."""

    def __init__(self, base_url: str = None, timeout: float = None, max_retries: int = None):
        self.base_url = (base_url or getattr(settings, "OSRM_BASE_URL", "https://router.project-osrm.org")).rstrip("/")
        self.timeout = timeout or getattr(settings, "ROUTING_TIMEOUT_SECONDS", 10.0)
        self.max_retries = max_retries if max_retries is not None else getattr(settings, "ROUTING_MAX_RETRIES", 2)
        self.cache_ttl = getattr(settings, "CACHE_TTL", 86400)

        self._session = requests.Session()
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def _build_cache_key(self, lat1: float, lng1: float, lat2: float, lng2: float) -> str:
        """Deterministic cache key based on 4-decimal rounded coordinates (~11m resolution)."""
        return f"route_osrm_{lat1:.4f}_{lng1:.4f}_{lat2:.4f}_{lng2:.4f}"

    def get_route(
        self,
        start_lat: float,
        start_lng: float,
        end_lat: float,
        end_lng: float,
        call_counter: List[int] = None,
    ) -> Dict[str, Any]:
        """
        Fetch driving route between two points.
        Returns GeoJSON geometry, distance (meters), and duration (seconds).
        Checks cache first; enforces hard cap on external network calls.
        """
        cache_key = self._build_cache_key(start_lat, start_lng, end_lat, end_lng)
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            logger.info("Route cache hit for key %s (0 external calls)", cache_key)
            result = dict(cached_data)
            result["from_cache"] = True
            return result

        # Enforce hard cap before making external request
        if call_counter is not None:
            if call_counter[0] >= HARD_CAP_EXTERNAL_CALLS:
                raise RoutingClientError(
                    f"External API hard-cap exceeded: cannot exceed {HARD_CAP_EXTERNAL_CALLS} external calls."
                )
            call_counter[0] += 1

        url = (
            f"{self.base_url}/route/v1/driving/"
            f"{start_lng:.6f},{start_lat:.6f};{end_lng:.6f},{end_lat:.6f}"
        )
        params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }

        logger.info("Calling external routing API: %s (call #%d)", url, call_counter[0] if call_counter else 1)

        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error("External routing API request failed: %s", e)
            raise RoutingClientError(f"External routing request failed: {e}") from e
        except ValueError as e:
            logger.error("External routing API returned invalid JSON: %s", e)
            raise RoutingClientError("External routing API returned invalid JSON response.") from e

        if data.get("code") != "Ok" or not data.get("routes"):
            msg = data.get("message", "No driving route found between specified points.")
            logger.warning("Routing service returned non-Ok code: %s (%s)", data.get("code"), msg)
            raise RoutingClientError(f"Route calculation error: {msg}")

        route = data["routes"][0]
        geometry = route.get("geometry", {})
        coordinates = geometry.get("coordinates", [])

        if not coordinates:
            raise RoutingClientError("Routing service returned empty coordinates geometry.")

        result = {
            "distance_meters": float(route["distance"]),
            "duration_seconds": float(route["duration"]),
            "coordinates": coordinates,  # List of [lng, lat]
            "from_cache": False,
        }

        # Store in Django cache
        cache.set(cache_key, result, timeout=self.cache_ttl)
        logger.info(
            "Cached route with %d points, %.1f miles",
            len(coordinates),
            result["distance_meters"] * 0.000621371,
        )

        return result

