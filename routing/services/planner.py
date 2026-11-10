"""
Fuel route optimization planner.
Computes optimal refuel stops along a highway route using a Greedy Lookahead algorithm
provably equivalent to the global minimum-cost linear program.
"""

import logging
import math
from typing import List, Dict, Any, Tuple, Optional

import numpy as np
from django.conf import settings
from scipy.optimize import linprog

from routing.services.dataclasses import (
    LocationPoint,
    StationData,
    CandidateStation,
    FuelStop,
    RoutePlan,
)
from routing.services.station_index import StationSpatialIndex

logger = logging.getLogger(__name__)

EARTH_RADIUS_MILES = 3958.7613


class InfeasibleRouteError(Exception):
    """Raised when vehicle range is exceeded by a gap between available fuel stations."""
    def __init__(self, message: str, gap_start: float = 0.0, gap_end: float = 0.0):
        super().__init__(message)
        self.gap_start = gap_start
        self.gap_end = gap_end


def compute_cumulative_distances(coordinates: np.ndarray, total_osrm_miles: float) -> np.ndarray:
    """
    Compute cumulative mileage along route points using vectorized haversine.
    Scales intermediate distances to align exactly with OSRM total distance.
    coordinates shape: (N, 2) where col 0 is latitude, col 1 is longitude.
    """
    if len(coordinates) < 2:
        return np.array([0.0])

    lats = np.radians(coordinates[:, 0])
    lngs = np.radians(coordinates[:, 1])

    dlat = np.diff(lats)
    dlng = np.diff(lngs)

    # Vectorized haversine between consecutive points
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lats[:-1]) * np.cos(lats[1:]) * np.sin(dlng / 2.0) ** 2
    )
    c = 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))
    seg_miles = EARTH_RADIUS_MILES * c

    cum_dist = np.insert(np.cumsum(seg_miles), 0, 0.0)

    # Scale to match OSRM's authoritative road distance
    if cum_dist[-1] > 0 and total_osrm_miles > 0:
        cum_dist = cum_dist * (total_osrm_miles / cum_dist[-1])

    return cum_dist


def downsample_coordinates(coords: List[List[float]], max_points: int = 2000) -> List[List[float]]:
    """
    Downsample route coordinates array to <= max_points for response payload efficiency,
    always preserving start and finish coordinates.
    """
    n = len(coords)
    if n <= max_points:
        return coords

    step = math.ceil(n / max_points)
    sampled = coords[::step]
    if sampled[-1] != coords[-1]:
        sampled.append(coords[-1])
    return sampled


class FuelPlanner:
    """
    Solves the optimal fuel stops problem for a highway trip.
    """

    def __init__(
        self,
        mpg: float = None,
        tank_capacity_gallons: float = None,
        max_detour_miles: float = None,
        detour_penalty_enabled: bool = None,
    ):
        self.mpg = mpg or getattr(settings, "DEFAULT_MPG", 10.0)
        self.tank_capacity = tank_capacity_gallons or getattr(settings, "TANK_CAPACITY_GALLONS", 50.0)
        self.max_detour_miles = max_detour_miles or getattr(settings, "MAX_DETOUR_MILES", 5.0)
        self.detour_penalty_enabled = (
            detour_penalty_enabled
            if detour_penalty_enabled is not None
            else getattr(settings, "DETOUR_PENALTY_ENABLED", True)
        )
        self.max_range_miles = self.tank_capacity * self.mpg

    def _effective_station_price(self, cand: CandidateStation) -> float:
        """
        Compute effective price of a candidate station factoring in detour penalty.
        Detour penalty = (2 * distance_off_route / mpg) * price.
        Averaged over a standard top-up volume (35 gallons) so a 5-mile detour
        is not selected for a 1-cent saving.
        """
        base_price = cand.station.price_per_gallon
        if not self.detour_penalty_enabled or cand.distance_off_route_miles <= 0.05:
            return base_price

        detour_gallons = (2.0 * cand.distance_off_route_miles) / self.mpg
        detour_cost = detour_gallons * base_price
        effective_price = base_price + (detour_cost / 35.0)
        return effective_price

    def _simulate_greedy_from_station(
        self,
        valid_candidates: List[CandidateStation],
        start_station_idx: int,
        start_fuel: float,
        total_distance_miles: float,
    ) -> Optional[List[FuelStop]]:
        """
        Run greedy lookahead simulation starting from a specific initial station
        with a given initial remaining fuel level.
        """
        curr_pos = valid_candidates[start_station_idx].mile_marker
        curr_fuel = start_fuel
        curr_idx = start_station_idx
        raw_stops: List[FuelStop] = []

        while curr_pos < total_distance_miles:
            dist_to_dest = total_distance_miles - curr_pos
            if dist_to_dest <= curr_fuel * self.mpg + 1e-6:
                break

            reach_dist = self.max_range_miles
            reachable_stations = [
                (idx, c)
                for idx, c in enumerate(valid_candidates)
                if idx > curr_idx and (c.mile_marker - curr_pos) <= reach_dist + 1e-6
            ]

            if not reachable_stations:
                if dist_to_dest <= self.max_range_miles + 1e-6:
                    needed_fuel = dist_to_dest / self.mpg
                    buy_fuel = max(0.0, needed_fuel - curr_fuel)
                    if buy_fuel > 1e-4:
                        st = valid_candidates[curr_idx]
                        raw_stops.append(
                            FuelStop(
                                order=0,
                                station=st.station,
                                mile_marker=st.mile_marker,
                                distance_off_route_miles=st.distance_off_route_miles,
                                gallons_purchased=buy_fuel,
                                cost=buy_fuel * st.station.price_per_gallon,
                            )
                        )
                    break
                return None  # Infeasible path

            curr_price = self._effective_station_price(valid_candidates[curr_idx])

            # Look for FIRST reachable station cheaper than curr_price
            cheaper_station = None
            for idx, c in reachable_stations:
                if self._effective_station_price(c) < curr_price - 1e-6:
                    cheaper_station = (idx, c)
                    break

            if cheaper_station:
                next_idx, next_st = cheaper_station
                dist = next_st.mile_marker - curr_pos
                needed_fuel = dist / self.mpg

                if needed_fuel > curr_fuel + 1e-6:
                    buy_fuel = needed_fuel - curr_fuel
                    if buy_fuel > 1e-4:
                        st = valid_candidates[curr_idx]
                        raw_stops.append(
                            FuelStop(
                                order=0,
                                station=st.station,
                                mile_marker=st.mile_marker,
                                distance_off_route_miles=st.distance_off_route_miles,
                                gallons_purchased=buy_fuel,
                                cost=buy_fuel * st.station.price_per_gallon,
                            )
                        )
                    curr_fuel = needed_fuel

                curr_fuel -= dist / self.mpg
                curr_pos = next_st.mile_marker
                curr_idx = next_idx
            else:
                if dist_to_dest <= self.max_range_miles + 1e-6:
                    needed_fuel = dist_to_dest / self.mpg
                    buy_fuel = max(0.0, needed_fuel - curr_fuel)
                    if buy_fuel > 1e-4:
                        st = valid_candidates[curr_idx]
                        raw_stops.append(
                            FuelStop(
                                order=0,
                                station=st.station,
                                mile_marker=st.mile_marker,
                                distance_off_route_miles=st.distance_off_route_miles,
                                gallons_purchased=buy_fuel,
                                cost=buy_fuel * st.station.price_per_gallon,
                            )
                        )
                    break
                else:
                    buy_fuel = self.tank_capacity - curr_fuel
                    if buy_fuel > 1e-4:
                        st = valid_candidates[curr_idx]
                        raw_stops.append(
                            FuelStop(
                                order=0,
                                station=st.station,
                                mile_marker=st.mile_marker,
                                distance_off_route_miles=st.distance_off_route_miles,
                                gallons_purchased=buy_fuel,
                                cost=buy_fuel * st.station.price_per_gallon,
                            )
                        )
                    curr_fuel = self.tank_capacity

                    best_next_idx, best_next_st = min(
                        reachable_stations,
                        key=lambda x: (self._effective_station_price(x[1]), -x[1].mile_marker),
                    )
                    dist = best_next_st.mile_marker - curr_pos
                    curr_fuel -= dist / self.mpg
                    curr_pos = best_next_st.mile_marker
                    curr_idx = best_next_idx

        # Renumber stops
        result: List[FuelStop] = []
        for i, stop in enumerate(raw_stops, start=1):
            stop.order = i
            result.append(stop)
        return result

    def solve_greedy_lookahead(
        self,
        candidates: List[CandidateStation],
        total_distance_miles: float,
        starting_fuel_gallons: float,
    ) -> List[FuelStop]:
        """
        Solve optimal refuel plan using the Greedy Lookahead algorithm.
        Evaluates Pareto-optimal initial stops within the starting fuel horizon,
        then computes lookahead decisions downstream.
        """
        initial_range = starting_fuel_gallons * self.mpg
        if total_distance_miles <= initial_range + 1e-6:
            return []

        valid = [c for c in candidates if 0.0 <= c.mile_marker <= total_distance_miles]
        valid.sort(key=lambda c: c.mile_marker)

        # Candidates reachable with starting fuel
        first_reachable = [c for c in valid if c.mile_marker <= initial_range + 1e-6]
        if not first_reachable:
            gap_end = min(total_distance_miles, initial_range)
            raise InfeasibleRouteError(
                f"Starting fuel ({starting_fuel_gallons:.1f} gal) is insufficient to reach "
                f"any fueling station within {initial_range:.1f} miles of start.",
                gap_start=0.0,
                gap_end=gap_end,
            )

        # Filter to Pareto-optimal candidate starting stations
        pareto_start_candidates: List[CandidateStation] = []
        min_p = float("inf")
        for c in reversed(first_reachable):
            p = self._effective_station_price(c)
            if p < min_p - 1e-4:
                pareto_start_candidates.append(c)
                min_p = p
        pareto_start_candidates.reverse()

        best_stops: Optional[List[FuelStop]] = None
        best_cost = float("inf")

        for cand in pareto_start_candidates:
            st_idx = valid.index(cand)
            rem_starting_fuel = starting_fuel_gallons - (cand.mile_marker / self.mpg)
            plan = self._simulate_greedy_from_station(
                valid_candidates=valid,
                start_station_idx=st_idx,
                start_fuel=rem_starting_fuel,
                total_distance_miles=total_distance_miles,
            )
            if plan is not None:
                cost = sum(s.cost for s in plan)
                if cost < best_cost:
                    best_cost = cost
                    best_stops = plan

        if best_stops is None:
            raise InfeasibleRouteError(
                "No feasible fueling plan found: vehicle range (500 miles) is exceeded by a gap between stations.",
                gap_start=0.0,
                gap_end=total_distance_miles,
            )

        return best_stops

    def plan_route(
        self,
        start_pt: LocationPoint,
        finish_pt: LocationPoint,
        osrm_result: Dict[str, Any],
        starting_fuel_gallons: float = 50.0,
    ) -> RoutePlan:
        """
        Execute full route optimization pipeline:
        1. Extract route coordinates and compute cumulative distances.
        2. Query candidate stations using progressive spatial radius (5 -> 15 -> 30 miles).
        3. Solve optimal refuel plan via Greedy Lookahead.
        4. Package structured RoutePlan.
        """
        raw_coords = osrm_result["coordinates"]
        coords_lat_lng = np.array([[pt[1], pt[0]] for pt in raw_coords], dtype=np.float64)

        total_distance_miles = osrm_result["distance_meters"] * 0.000621371192
        duration_minutes = osrm_result["duration_seconds"] / 60.0

        cum_distances = compute_cumulative_distances(coords_lat_lng, total_distance_miles)
        spatial_index = StationSpatialIndex.get_instance()

        radii_to_try = [self.max_detour_miles, 15.0, 30.0]
        radii_to_try = sorted(list(dict.fromkeys(radii_to_try)))

        last_error = None
        fuel_stops: List[FuelStop] = []

        for r in radii_to_try:
            candidates = spatial_index.find_candidates_along_route(
                route_coords=coords_lat_lng,
                cum_distances=cum_distances,
                max_detour_miles=r,
            )
            try:
                fuel_stops = self.solve_greedy_lookahead(
                    candidates=candidates,
                    total_distance_miles=total_distance_miles,
                    starting_fuel_gallons=starting_fuel_gallons,
                )
                last_error = None
                break
            except InfeasibleRouteError as e:
                last_error = e
                logger.warning(
                    "Route plan infeasible with radius %.1f mi: %s. Widening search...", r, e
                )

        if last_error is not None:
            raise last_error

        total_gallons = sum(s.gallons_purchased for s in fuel_stops)
        total_fuel_cost = sum(s.cost for s in fuel_stops)

        downsampled_coords = downsample_coordinates(raw_coords, max_points=2000)
        route_geojson = {
            "type": "LineString",
            "coordinates": downsampled_coords,
        }

        assumptions = {
            "mpg": self.mpg,
            "range_miles": self.max_range_miles,
            "tank_capacity_gallons": self.tank_capacity,
            "starting_fuel_gallons": starting_fuel_gallons,
            "starting_fuel": (
                "full tank (not billed)"
                if math.isclose(starting_fuel_gallons, self.tank_capacity)
                else f"{starting_fuel_gallons:.1f} gallons"
            ),
            "detour_penalty_enabled": self.detour_penalty_enabled,
            "max_detour_miles": self.max_detour_miles,
        }

        plan = RoutePlan(
            start=start_pt,
            finish=finish_pt,
            distance_miles=total_distance_miles,
            duration_minutes=duration_minutes,
            route=route_geojson,
            fuel_stops=fuel_stops,
            total_gallons=total_gallons,
            total_fuel_cost=total_fuel_cost,
            assumptions=assumptions,
            map_url="",
            external_api_calls=1 if not osrm_result.get("from_cache", False) else 0,
        )

        return plan


def solve_verification_lp(
    stations: List[Dict[str, Any]],
    total_distance: float,
    starting_fuel: float = 50.0,
    capacity: float = 50.0,
    mpg: float = 10.0,
) -> Optional[float]:
    """
    Independent Linear Programming solver using HiGHS algorithm.
    Used in test suites to assert that Greedy Lookahead
    produces the exact global optimum cost.
    """
    valid = [s for s in stations if 0.0 <= s["mile"] <= total_distance]
    valid.sort(key=lambda s: s["mile"])
    n = len(valid)

    if total_distance / mpg <= starting_fuel:
        return 0.0

    if n == 0:
        return None

    positions = [0.0] + [s["mile"] for s in valid] + [total_distance]
    c = [s["price"] for s in valid]

    a_ub = []
    b_ub = []

    # 1. Fuel arriving at each node >= 0
    for k in range(1, n + 2):
        row = [-1.0 if j < (k - 1) else 0.0 for j in range(n)]
        a_ub.append(row)
        b_ub.append(starting_fuel - (positions[k] - positions[0]) / mpg)

    # 2. Fuel leaving each station <= capacity
    for k in range(1, n + 1):
        row = [1.0 if j < k else 0.0 for j in range(n)]
        a_ub.append(row)
        b_ub.append(capacity - starting_fuel + (positions[k] - positions[0]) / mpg)

    bounds = [(0, capacity) for _ in range(n)]

    res = linprog(c, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if res.success:
        return float(res.fun)
    return None
