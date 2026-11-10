"""
Unit tests for FuelPlanner optimization algorithm.
Validates:
- Short trips (0 stops)
- 500-mile exact boundary condition
- Cheaper station lookahead logic
- Multi-stop long-distance planning
- Infeasible gap detection
- Partial starting fuel support
- Detour penalty effect
- 200 random scenario equivalence between Greedy Lookahead and Linear Programming
"""

import random
import pytest

from routing.services.dataclasses import CandidateStation, StationData
from routing.services.planner import (
    FuelPlanner,
    InfeasibleRouteError,
    solve_verification_lp,
)


def test_short_trip_zero_stops(make_candidate):
    """Trip under 500 miles with a full starting tank requires 0 fuel stops."""
    planner = FuelPlanner()
    candidates = [
        make_candidate(1, 100.0, 3.20),
        make_candidate(2, 250.0, 2.90),
    ]
    stops = planner.solve_greedy_lookahead(
        candidates=candidates,
        total_distance_miles=350.0,
        starting_fuel_gallons=50.0,
    )
    assert len(stops) == 0


def test_exactly_500_mile_boundary(make_candidate):
    """Trip of exactly 500 miles completes on the initial 50-gallon tank."""
    planner = FuelPlanner()
    candidates = [
        make_candidate(1, 200.0, 3.10),
        make_candidate(2, 450.0, 3.00),
    ]
    stops = planner.solve_greedy_lookahead(
        candidates=candidates,
        total_distance_miles=500.0,
        starting_fuel_gallons=50.0,
    )
    assert len(stops) == 0


def test_cheaper_station_ahead_lookahead(make_candidate):
    """
    At station A (mile 300, price $3.50), if station B (mile 600, price $2.80)
    is reachable within 500 miles, driver should only buy enough at A to reach B.
    """
    planner = FuelPlanner(detour_penalty_enabled=False)
    candidates = [
        make_candidate(1, 300.0, 3.50),  # Arrives with 20 gal (50 - 30)
        make_candidate(2, 600.0, 2.80),  # Cheaper! Needs 30 gal from mile 300
    ]
    stops = planner.solve_greedy_lookahead(
        candidates=candidates,
        total_distance_miles=800.0,
        starting_fuel_gallons=50.0,
    )

    # Should stop at station 1 and buy exactly 10 gallons to reach station 2 (20 in tank + 10 bought = 30)
    stop1 = next(s for s in stops if s.station.id == 1)
    assert abs(stop1.gallons_purchased - 10.0) < 1e-2

    # At station 2 (mile 600), destination is 200 miles away (within range). Buy 20 gallons.
    stop2 = next(s for s in stops if s.station.id == 2)
    assert abs(stop2.gallons_purchased - 20.0) < 1e-2

    # Total purchased = 30 gallons. Total burned = 800 / 10 = 80 gal. (50 initial + 30 bought = 80).
    total_bought = sum(s.gallons_purchased for s in stops)
    assert abs(total_bought - 30.0) < 1e-2


def test_multi_stop_long_trip(make_candidate):
    """Multi-stop 1,500-mile highway corridor test."""
    planner = FuelPlanner(detour_penalty_enabled=False)
    candidates = [
        make_candidate(1, 350.0, 3.20),
        make_candidate(2, 700.0, 2.95),
        make_candidate(3, 1100.0, 3.10),
        make_candidate(4, 1350.0, 2.85),
    ]
    stops = planner.solve_greedy_lookahead(
        candidates=candidates,
        total_distance_miles=1500.0,
        starting_fuel_gallons=50.0,
    )

    assert len(stops) >= 2
    total_bought = sum(s.gallons_purchased for s in stops)
    expected_consumption = 1500.0 / 10.0 - 50.0  # 100 gallons
    assert abs(total_bought - expected_consumption) < 1e-2


def test_infeasible_gap_raises_422_error(make_candidate):
    """A gap > 500 miles between stations raises InfeasibleRouteError."""
    planner = FuelPlanner()
    candidates = [
        make_candidate(1, 200.0, 3.10),
        # Gap between mile 200 and mile 750 is 550 miles (> 500 miles range)
        make_candidate(2, 750.0, 3.00),
    ]
    with pytest.raises(InfeasibleRouteError) as exc_info:
        planner.solve_greedy_lookahead(
            candidates=candidates,
            total_distance_miles=1000.0,
            starting_fuel_gallons=50.0,
        )
    assert "No feasible fueling plan found" in str(exc_info.value) or "vehicle range" in str(exc_info.value)


def test_partial_starting_fuel(make_candidate):
    """
    When starting fuel is partial (e.g. 15 gallons = 150 miles range),
    vehicle must stop within 150 miles to refuel.
    """
    planner = FuelPlanner(detour_penalty_enabled=False)
    candidates = [
        make_candidate(1, 100.0, 3.30),
        make_candidate(2, 400.0, 3.00),
    ]
    stops = planner.solve_greedy_lookahead(
        candidates=candidates,
        total_distance_miles=600.0,
        starting_fuel_gallons=15.0,  # 150 miles range
    )
    assert len(stops) >= 1
    # First stop must be at or before mile 150
    assert stops[0].mile_marker <= 150.0


def test_detour_penalty_suppresses_expensive_off_route_station(make_candidate):
    """
    Station A is on-highway (0.1 mi detour, price $3.00).
    Station B is 5.0 miles off-route with price $2.99 (1-cent cheaper).
    Detour penalty should prioritize Station A.
    """
    planner_penalty_on = FuelPlanner(detour_penalty_enabled=True)
    planner_penalty_off = FuelPlanner(detour_penalty_enabled=False)

    cand_on_route = make_candidate(1, 400.0, price=3.00, detour_miles=0.1)
    cand_off_route = make_candidate(2, 400.0, price=2.99, detour_miles=5.0)

    # With penalty enabled, on-route station has lower effective price
    eff_on = planner_penalty_on._effective_station_price(cand_on_route)
    eff_off = planner_penalty_on._effective_station_price(cand_off_route)
    assert eff_on < eff_off

    # With penalty disabled, off-route station has lower base price
    base_on = planner_penalty_off._effective_station_price(cand_on_route)
    base_off = planner_penalty_off._effective_station_price(cand_off_route)
    assert base_off < base_on


def test_greedy_equals_linear_programming_200_random_scenarios():
    """
    Asserts that Greedy Lookahead produces the exact same total cost as the
    authoritative HiGHS Linear Programming solver across 200 randomized scenarios.
    """
    random.seed(42)
    planner = FuelPlanner(detour_penalty_enabled=False)

    feasible_matches = 0
    infeasible_matches = 0

    for scenario_idx in range(200):
        total_dist = random.uniform(300.0, 2200.0)
        num_stations = random.randint(3, 18)
        sample_range = max(1, int(total_dist) - 40)
        k = min(num_stations, sample_range)
        miles = sorted(random.sample(range(20, int(total_dist) - 10), k))

        station_dicts = []
        candidates = []
        for j, m in enumerate(miles):
            p = round(random.uniform(2.50, 4.20), 2)
            station_dicts.append({"id": j, "mile": float(m), "price": p})
            st_data = StationData(
                id=j, opis_id=j, name=f"St #{j}", address="", city="", state="",
                lat=0.0, lng=0.0, price_per_gallon=p
            )
            candidates.append(CandidateStation(station=st_data, mile_marker=float(m), distance_off_route_miles=0.0))

        # Solve via LP
        lp_cost = solve_verification_lp(station_dicts, total_dist, starting_fuel=50.0)

        # Solve via Greedy
        try:
            greedy_stops = planner.solve_greedy_lookahead(candidates, total_dist, starting_fuel_gallons=50.0)
            greedy_cost = sum(s.cost for s in greedy_stops)
            greedy_feasible = True
        except InfeasibleRouteError:
            greedy_cost = None
            greedy_feasible = False

        if not greedy_feasible or lp_cost is None:
            assert greedy_cost is None
            assert lp_cost is None
            infeasible_matches += 1
        else:
            diff = abs(greedy_cost - lp_cost)
            assert diff < 1e-4, f"Mismatch on scenario #{scenario_idx}: greedy=${greedy_cost:.4f}, lp=${lp_cost:.4f}"
            feasible_matches += 1

    assert feasible_matches > 100
    assert feasible_matches + infeasible_matches == 200

