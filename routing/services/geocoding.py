"""
Offline-first geocoding service for US cities and coordinates.
Resolves 'City, ST' queries offline via bundled city centroids without external API calls,
and validates coordinate boundaries for the continental United States.
"""

import csv
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Dict, Tuple, Optional, Any, List

import requests
from django.conf import settings
from django.core.cache import cache
from rapidfuzz import process, fuzz
from rest_framework.exceptions import ValidationError

from routing.services.dataclasses import LocationPoint

logger = logging.getLogger(__name__)

# Continental USA Bounding Box (+ minor buffer)
US_BOUNDS = {
    "lat_min": 24.396308,
    "lat_max": 49.384358,
    "lng_min": -125.000000,
    "lng_max": -66.934570,
}

# Standard 2-letter state abbreviations & common full names
US_STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}


def normalize_city_name(name: str) -> str:
    """Normalize city string for reliable dictionary matching."""
    if not name:
        return ""
    s = name.lower().strip()
    # Normalize common abbreviations
    s = re.sub(r"\bst\b\.?", "saint", s)
    s = re.sub(r"\bft\b\.?", "fort", s)
    s = re.sub(r"\bmt\b\.?", "mount", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class CityCentroidIndex:
    """In-memory singleton index of US city centroids."""
    _instance: Optional["CityCentroidIndex"] = None
    _lookup: Dict[Tuple[str, str], Tuple[float, float]] = {}
    _state_cities: Dict[str, Dict[str, Tuple[float, float]]] = {}
    _is_loaded: bool = False

    @classmethod
    def get_instance(cls) -> "CityCentroidIndex":
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.load()
        return cls._instance

    def load(self) -> None:
        """Load bundled city centroids dataset into memory."""
        if self._is_loaded:
            return

        csv_path = settings.DATA_DIR / "us_cities.csv"
        if not csv_path.exists():
            logger.warning("us_cities.csv not found at %s. Centroid index will be empty.", csv_path)
            self._is_loaded = True
            return

        lookup: Dict[Tuple[str, str], Tuple[float, float]] = {}
        state_cities: Dict[str, Dict[str, Tuple[float, float]]] = {}

        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                city = row.get("city", "").strip()
                state = row.get("state", "").strip().upper()
                try:
                    lat = float(row.get("lat", 0))
                    lng = float(row.get("lng", 0))
                except (ValueError, TypeError):
                    continue

                norm_city = normalize_city_name(city)
                if state and norm_city:
                    lookup[(state, norm_city)] = (lat, lng)
                    if state not in state_cities:
                        state_cities[state] = {}
                    state_cities[state][norm_city] = (lat, lng)

        self._lookup = lookup
        self._state_cities = state_cities
        self._is_loaded = True
        logger.info("Loaded %d city centroids across %d states.", len(lookup), len(state_cities))

    def resolve(self, city: str, state: str) -> Optional[Tuple[float, float, str]]:
        """
        Resolve city and state to (lat, lng, match_type).
        Tries exact match first, then fuzzy within state.
        """
        state = state.strip().upper()
        norm_city = normalize_city_name(city)

        # 1. Exact match
        if (state, norm_city) in self._lookup:
            lat, lng = self._lookup[(state, norm_city)]
            return lat, lng, "exact"

        # 2. Fuzzy match within the same state
        if state in self._state_cities:
            choices = list(self._state_cities[state].keys())
            match = process.extractOne(norm_city, choices, scorer=fuzz.WRatio, score_cutoff=75)
            if match:
                matched_city, score, _ = match
                lat, lng = self._state_cities[state][matched_city]
                logger.info("Fuzzy matched '%s, %s' to '%s' (score=%.1f)", city, state, matched_city, score)
                return lat, lng, "fuzzy"

        return None


def is_in_us_bounds(lat: float, lng: float) -> bool:
    """Validate that coordinates fall inside the continental United States."""
    return (
        US_BOUNDS["lat_min"] <= lat <= US_BOUNDS["lat_max"]
        and US_BOUNDS["lng_min"] <= lng <= US_BOUNDS["lng_max"]
    )


def parse_lat_lng_string(val: str) -> Optional[Tuple[float, float]]:
    """Try parsing coordinate string like '32.7767,-96.7970'."""
    parts = val.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lng = float(parts[1].strip())
            return lat, lng
        except ValueError:
            pass
    return None


def parse_city_state(text: str) -> Optional[Tuple[str, str]]:
    """Parse 'City, ST' or 'City, StateName'."""
    parts = text.split(",")
    if len(parts) != 2:
        return None

    city = parts[0].strip()
    state_input = parts[1].strip().upper()

    # If full state name was given, map to 2-letter code
    if state_input in US_STATE_NAMES:
        state_code = US_STATE_NAMES[state_input]
    elif len(state_input) == 2:
        state_code = state_input
    else:
        return None

    return city, state_code


def geocode_nominatim_fallback(query: str) -> Optional[Tuple[float, float, str]]:
    """Rate-limited, cached fallback geocoder using Nominatim / OpenStreetMap."""
    cache_key = f"nominatim_geocode_{query.lower().strip()}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "FuelRouteSeniorAssessment/1.0 (assessment@example.com)"}
    params = {"q": query, "format": "json", "countrycodes": "us", "limit": 1}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                lat = float(data[0]["lat"])
                lng = float(data[0]["lon"])
                res = (lat, lng, "nominatim")
                cache.set(cache_key, res, timeout=86400 * 7)
                return res
    except Exception as e:
        logger.warning("Nominatim geocoding failed for '%s': %s", query, e)

    return None


def resolve_location(raw_input: Any, field_name: str = "location", external_call_counter: Optional[List[int]] = None) -> LocationPoint:
    """
    Resolve start or finish location from raw string or dictionary input.
    Validates continental US bounding box. Raises ValidationError with clear messaging.
    """
    if raw_input is None or (isinstance(raw_input, str) and not raw_input.strip()):
        raise ValidationError({field_name: "Location must be provided as 'lat,lng' or 'City, ST'."})

    # Case 1: Dict with lat/lng
    if isinstance(raw_input, dict):
        if "lat" in raw_input and "lng" in raw_input:
            try:
                lat = float(raw_input["lat"])
                lng = float(raw_input["lng"])
                input_str = f"{lat},{lng}"
                if not is_in_us_bounds(lat, lng):
                    raise ValidationError({
                        field_name: f"Coordinates ({lat}, {lng}) are outside the continental USA bounding box."
                    })
                return LocationPoint(input=input_str, resolved=input_str, lat=lat, lng=lng)
            except (ValueError, TypeError):
                raise ValidationError({field_name: "Invalid lat/lng numeric values in dictionary."})

    val_str = str(raw_input).strip()

    # Case 2: 'lat,lng' string
    coords = parse_lat_lng_string(val_str)
    if coords is not None:
        lat, lng = coords
        if not is_in_us_bounds(lat, lng):
            raise ValidationError({
                field_name: f"Coordinates ({lat}, {lng}) are outside the continental USA bounding box."
            })
        return LocationPoint(input=val_str, resolved=f"{lat:.4f},{lng:.4f}", lat=lat, lng=lng)

    # Case 3: 'City, ST'
    city_state = parse_city_state(val_str)
    if city_state is not None:
        city, state = city_state
        centroid_idx = CityCentroidIndex.get_instance()
        match_result = centroid_idx.resolve(city, state)

        if match_result:
            lat, lng, match_type = match_result
            if not is_in_us_bounds(lat, lng):
                raise ValidationError({
                    field_name: f"Resolved location '{city}, {state}' ({lat}, {lng}) is outside continental USA bounds."
                })
            resolved_text = f"{city.title()}, {state}"
            return LocationPoint(input=val_str, resolved=resolved_text, lat=lat, lng=lng)

        # Fallback to Nominatim if enabled
        if getattr(settings, "NOMINATIM_FALLBACK_ENABLED", False):
            if external_call_counter is not None:
                external_call_counter[0] += 1
            nom_res = geocode_nominatim_fallback(val_str)
            if nom_res:
                lat, lng, _ = nom_res
                if not is_in_us_bounds(lat, lng):
                    raise ValidationError({
                        field_name: f"Nominatim coordinates ({lat}, {lng}) are outside continental USA bounds."
                    })
                return LocationPoint(input=val_str, resolved=f"{city.title()}, {state}", lat=lat, lng=lng)

    raise ValidationError({
        field_name: (
            f"Could not resolve '{val_str}' to a valid continental USA location. "
            "Please provide 'City, ST' (e.g. 'Dallas, TX') or coordinates 'lat,lng'."
        )
    })

