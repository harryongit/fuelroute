"""
In-memory spatial index of fuel stations using SciPy cKDTree.
Converts geographic coordinates to 3D unit-sphere Cartesian coordinates
to eliminate latitude distortion and enable microsecond radius queries.
"""

import logging
import threading
from typing import List, Tuple, Optional, Dict

import numpy as np
from django.db import connection
from scipy.spatial import cKDTree

from routing.models import Station
from routing.services.dataclasses import StationData, CandidateStation

logger = logging.getLogger(__name__)

EARTH_RADIUS_MILES = 3958.7613


def lat_lng_to_unit_sphere(lats: np.ndarray, lngs: np.ndarray) -> np.ndarray:
    """
    Convert arrays of latitude and longitude (in degrees) to 3D unit-sphere coordinates:
    x = cos(lat) * cos(lng)
    y = cos(lat) * sin(lng)
    z = sin(lat)
    """
    phi = np.radians(lats)
    lam = np.radians(lngs)
    x = np.cos(phi) * np.cos(lam)
    y = np.cos(phi) * np.sin(lam)
    z = np.sin(phi)
    return np.column_stack([x, y, z])


def miles_to_chord_distance(miles: float) -> float:
    """
    Exact Euclidean chord distance on unit sphere corresponding to great-circle miles:
    chord = 2 * sin(miles / (2 * R))
    """
    theta = miles / EARTH_RADIUS_MILES
    return float(2.0 * np.sin(theta / 2.0))


def chord_distance_to_miles(chord: float) -> float:
    """Convert Euclidean chord distance on unit sphere to great-circle miles."""
    # chord = 2 * sin(theta/2) => theta = 2 * arcsin(chord/2)
    clamped = np.clip(chord / 2.0, -1.0, 1.0)
    theta = 2.0 * np.arcsin(clamped)
    return float(theta * EARTH_RADIUS_MILES)


class StationSpatialIndex:
    """Thread-safe singleton holding the spatial KD-tree and in-memory station array."""
    _instance: Optional["StationSpatialIndex"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.tree: Optional[cKDTree] = None
        self.stations: List[StationData] = []
        self.station_coords_3d: Optional[np.ndarray] = None
        self.station_lats: Optional[np.ndarray] = None
        self.station_lngs: Optional[np.ndarray] = None
        self.station_prices: Optional[np.ndarray] = None
        self.station_ids: Optional[np.ndarray] = None
        self.is_indexed = False

    @classmethod
    def get_instance(cls) -> "StationSpatialIndex":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance.build_index()
        return cls._instance

    @classmethod
    def reload_index(cls) -> "StationSpatialIndex":
        """Invalidate and rebuild the in-memory spatial index."""
        with cls._lock:
            instance = cls()
            instance.build_index()
            cls._instance = instance
            return cls._instance

    def build_index(self) -> None:
        """Load stations from the database and construct the 3D cKDTree."""
        logger.info("Building StationSpatialIndex from database...")
        # Check if table exists
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            if "routing_station" not in tables:
                logger.warning("Table 'routing_station' does not exist yet. Spatial index empty.")
                return

        qs = Station.objects.all().values(
            "id", "opis_id", "name", "address", "city", "state", "lat", "lng", "price"
        )
        station_list = list(qs)
        if not station_list:
            logger.info("No stations in database. Spatial index initialized with 0 stations.")
            self.stations = []
            self.is_indexed = True
            return

        stations: List[StationData] = []
        lats = []
        lngs = []
        prices = []
        ids = []

        for row in station_list:
            st = StationData(
                id=row["id"],
                opis_id=row["opis_id"],
                name=row["name"],
                address=row["address"] or "",
                city=row["city"],
                state=row["state"],
                lat=float(row["lat"]),
                lng=float(row["lng"]),
                price_per_gallon=float(row["price"]),
            )
            stations.append(st)
            lats.append(st.lat)
            lngs.append(st.lng)
            prices.append(st.price_per_gallon)
            ids.append(st.id)

        self.stations = stations
        self.station_lats = np.array(lats, dtype=np.float64)
        self.station_lngs = np.array(lngs, dtype=np.float64)
        self.station_prices = np.array(prices, dtype=np.float64)
        self.station_ids = np.array(ids, dtype=np.int64)

        self.station_coords_3d = lat_lng_to_unit_sphere(self.station_lats, self.station_lngs)
        self.tree = cKDTree(self.station_coords_3d)
        self.is_indexed = True

        logger.info("StationSpatialIndex built successfully with %d stations.", len(stations))

    def find_candidates_along_route(
        self,
        route_coords: np.ndarray,      # shape (N, 2): [lat, lng]
        cum_distances: np.ndarray,     # shape (N,): cumulative miles
        max_detour_miles: float = 5.0,
        sample_step_miles: float = 2.5,
    ) -> List[CandidateStation]:
        """
        Find candidate stations near the route within max_detour_miles.
        Uses sampled route points to query the station KD-tree in bulk,
        then projects matched stations to the nearest route point.
        """
        if not self.is_indexed or self.tree is None or len(self.stations) == 0:
            return []

        total_route_miles = cum_distances[-1]
        route_coords_3d = lat_lng_to_unit_sphere(route_coords[:, 0], route_coords[:, 1])

        # Sample query points along the route every ~sample_step_miles
        sample_indices = [0]
        last_m = 0.0
        for i, m in enumerate(cum_distances):
            if m - last_m >= sample_step_miles:
                sample_indices.append(i)
                last_m = m
        if sample_indices[-1] != len(cum_distances) - 1:
            sample_indices.append(len(cum_distances) - 1)

        sample_indices = np.array(sample_indices, dtype=np.int64)
        query_points_3d = route_coords_3d[sample_indices]

        # Query KD-tree with chord radius
        chord_r = miles_to_chord_distance(max_detour_miles * 1.5)
        neighbor_lists = self.tree.query_ball_point(query_points_3d, r=chord_r)

        candidate_station_indices = set()
        for neighbors in neighbor_lists:
            candidate_station_indices.update(neighbors)

        if not candidate_station_indices:
            return []

        cand_idx_arr = np.array(sorted(list(candidate_station_indices)), dtype=np.int64)
        cand_pts_3d = self.station_coords_3d[cand_idx_arr]

        # Build KD-tree on all route points for projection
        route_tree = cKDTree(route_coords_3d)
        chords, nearest_route_pt_idxs = route_tree.query(cand_pts_3d)

        # Compute accurate off-route distances
        detour_miles = np.array([chord_distance_to_miles(c) for c in chords])
        mile_markers = cum_distances[nearest_route_pt_idxs]

        # Filter stations within max_detour_miles and strictly between 0 and total_route_miles
        candidates_by_station_id: Dict[int, CandidateStation] = {}

        for i, st_idx in enumerate(cand_idx_arr):
            dist_off = float(detour_miles[i])
            if dist_off > max_detour_miles:
                continue

            mm = float(mile_markers[i])
            if mm < 0.0 or mm > total_route_miles:
                continue

            st = self.stations[st_idx]
            cand = CandidateStation(
                station=st,
                mile_marker=mm,
                distance_off_route_miles=dist_off,
            )

            # Deduplicate by station ID: keep projection with smaller distance_off_route
            if st.id not in candidates_by_station_id or dist_off < candidates_by_station_id[st.id].distance_off_route_miles:
                candidates_by_station_id[st.id] = cand

        results = list(candidates_by_station_id.values())
        results.sort(key=lambda c: c.mile_marker)
        return results

