"""
DRF Serializers for route request validation and structured API responses.
"""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Input serializer for route optimization requests."""
    start = serializers.CharField(
        required=True,
        help_text="Start location as 'City, ST' (e.g. 'Dallas, TX') or coordinates 'lat,lng'.",
    )
    finish = serializers.CharField(
        required=True,
        help_text="Finish location as 'City, ST' (e.g. 'Los Angeles, CA') or coordinates 'lat,lng'.",
    )
    starting_fuel_gallons = serializers.FloatField(
        required=False,
        default=50.0,
        min_value=0.0,
        max_value=50.0,
        help_text="Initial fuel in vehicle tank in gallons (default: 50.0 = full tank).",
    )
    max_detour_miles = serializers.FloatField(
        required=False,
        default=5.0,
        min_value=0.5,
        max_value=50.0,
        help_text="Maximum allowed detour search distance off the highway in miles (default: 5.0).",
    )
    detour_penalty = serializers.BooleanField(
        required=False,
        default=True,
        help_text="Enable detour penalty to prevent choosing distant stations for negligible price differences.",
    )


class LocationPointSerializer(serializers.Serializer):
    input = serializers.CharField()
    resolved = serializers.CharField()
    lat = serializers.FloatField()
    lng = serializers.FloatField()


class StationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField(allow_blank=True)
    city = serializers.CharField()
    state = serializers.CharField()
    lat = serializers.FloatField()
    lng = serializers.FloatField()
    price_per_gallon = serializers.FloatField()


class FuelStopSerializer(serializers.Serializer):
    order = serializers.IntegerField()
    station = StationSerializer()
    mile_marker = serializers.FloatField()
    distance_off_route_miles = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    cost = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    start = LocationPointSerializer()
    finish = LocationPointSerializer()
    distance_miles = serializers.FloatField()
    duration_minutes = serializers.FloatField()
    route = serializers.DictField()
    fuel_stops = FuelStopSerializer(many=True)
    total_gallons = serializers.FloatField()
    total_fuel_cost = serializers.FloatField()
    assumptions = serializers.DictField()
    map_url = serializers.CharField()
    external_api_calls = serializers.IntegerField()

