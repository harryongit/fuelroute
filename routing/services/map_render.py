"""
Map rendering service for interactive Leaflet route visualizations.
Caches route visualization data under a unique token for web browser inspection.
"""

import logging
import secrets
from typing import Dict, Any, Optional, List

from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string

from routing.services.dataclasses import RoutePlan

logger = logging.getLogger(__name__)


def format_duration(minutes: float) -> str:
    """Format minutes into human-readable 'Xh Ym' string."""
    total_min = int(round(minutes))
    hours = total_min // 60
    mins = total_min % 60
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def cache_route_map(plan: RoutePlan, full_route_coordinates: List[List[float]]) -> str:
    """
    Store map rendering context in Django cache under a random token.
    Returns the token string.
    """
    token = secrets.token_urlsafe(16)
    cache_key = f"map_token_{token}"

    # Use downsampled coordinates for Leaflet rendering performance
    coords_for_map = plan.route.get("coordinates", full_route_coordinates)

    map_payload = {
        "start": plan.start.to_dict(),
        "finish": plan.finish.to_dict(),
        "distance_miles": round(plan.distance_miles, 1),
        "duration_formatted": format_duration(plan.duration_minutes),
        "fuel_stops": [stop.to_dict() for stop in plan.fuel_stops],
        "total_gallons": f"{plan.total_gallons:.2f}",
        "total_fuel_cost": f"{plan.total_fuel_cost:.2f}",
        "route_coordinates": coords_for_map,
    }

    context = {
        "start_name": plan.start.resolved,
        "finish_name": plan.finish.resolved,
        "distance_miles": f"{plan.distance_miles:.1f}",
        "duration_formatted": format_duration(plan.duration_minutes),
        "total_gallons": f"{plan.total_gallons:.2f}",
        "total_fuel_cost": f"{plan.total_fuel_cost:.2f}",
        "fuel_stops": [stop.to_dict() for stop in plan.fuel_stops],
        "map_data_json": map_payload,
    }

    cache.set(cache_key, context, timeout=getattr(settings, "CACHE_TTL", 86400))
    return token


def render_map_by_token(token: str) -> Optional[str]:
    """Retrieve cached context by token and render self-contained HTML page."""
    cache_key = f"map_token_{token}"
    context = cache.get(cache_key)
    if not context:
        return None

    return render_to_string("routing/map.html", context)

