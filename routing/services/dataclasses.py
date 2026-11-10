from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class LocationPoint:
    """Geographic point representing a start or finish location."""
    input: str
    resolved: str
    lat: float
    lng: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input": self.input,
            "resolved": self.resolved,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
        }


@dataclass
class StationData:
    """In-memory station representation for performance and decoupled computations."""
    id: int
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    lat: float
    lng: float
    price_per_gallon: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "opis_id": self.opis_id,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "price_per_gallon": round(self.price_per_gallon, 3),
        }


@dataclass
class CandidateStation:
    """Candidate station projected onto the route."""
    station: StationData
    mile_marker: float
    distance_off_route_miles: float


@dataclass
class FuelStop:
    """Optimal fuel stop decision along the route."""
    order: int
    station: StationData
    mile_marker: float
    distance_off_route_miles: float
    gallons_purchased: float
    cost: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order": self.order,
            "station": self.station.to_dict(),
            "mile_marker": round(self.mile_marker, 2),
            "distance_off_route_miles": round(self.distance_off_route_miles, 2),
            "gallons_purchased": round(self.gallons_purchased, 2),
            "cost": round(self.cost, 2),
        }


@dataclass
class RoutePlan:
    """Complete route optimization response payload."""
    start: LocationPoint
    finish: LocationPoint
    distance_miles: float
    duration_minutes: float
    route: Dict[str, Any]
    fuel_stops: List[FuelStop]
    total_gallons: float
    total_fuel_cost: float
    assumptions: Dict[str, Any]
    map_url: str
    external_api_calls: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start.to_dict(),
            "finish": self.finish.to_dict(),
            "distance_miles": round(self.distance_miles, 2),
            "duration_minutes": round(self.duration_minutes, 1),
            "route": self.route,
            "fuel_stops": [stop.to_dict() for stop in self.fuel_stops],
            "total_gallons": round(self.total_gallons, 2),
            "total_fuel_cost": round(self.total_fuel_cost, 2),
            "assumptions": self.assumptions,
            "map_url": self.map_url,
            "external_api_calls": self.external_api_calls,
        }

