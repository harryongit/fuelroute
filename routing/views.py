"""
API views for route optimization, map visualization, and health checks.
"""

import logging
import time
from typing import List

from django.http import HttpResponse
from django.urls import reverse
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from routing.models import Station
from routing.serializers import RouteRequestSerializer, RouteResponseSerializer
from routing.services.geocoding import resolve_location
from routing.services.map_render import cache_route_map, render_map_by_token
from routing.services.planner import FuelPlanner, InfeasibleRouteError
from routing.services.routing_client import RoutingClient, RoutingClientError
from routing.services.station_index import StationSpatialIndex

logger = logging.getLogger("routing")


class HealthCheckView(APIView):
    """System health check endpoint."""
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        summary="Service Health Check",
        description="Verify service availability and station index status.",
        responses={200: OpenApiResponse(description="System healthy")},
    )
    def get(self, request):
        spatial_index = StationSpatialIndex.get_instance()
        station_count = len(spatial_index.stations) if spatial_index.is_indexed else Station.objects.count()
        return Response({
            "status": "healthy",
            "service": "FuelRoute API",
            "version": "1.0.0",
            "stations_indexed": station_count,
        })


class RoutePlanView(APIView):
    """
    Computes the driving route and optimal fuel stops between start and finish points.
    Supports both POST with JSON body and GET with query parameters.
    """
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        summary="Calculate Optimal Route and Fuel Stops (GET)",
        description="Compute driving route, optimal fuel stops, and total cost via query parameters.",
        parameters=[
            OpenApiParameter("start", str, description="Start 'City, ST' or 'lat,lng'", required=True),
            OpenApiParameter("finish", str, description="Finish 'City, ST' or 'lat,lng'", required=True),
            OpenApiParameter("starting_fuel_gallons", float, description="Starting tank fuel in gallons (0-50)", required=False),
            OpenApiParameter("max_detour_miles", float, description="Max search detour off route (miles)", required=False),
            OpenApiParameter("detour_penalty", bool, description="Enable detour penalty", required=False),
        ],
        responses={
            200: RouteResponseSerializer,
            400: OpenApiResponse(description="Invalid request or unresolvable location"),
            422: OpenApiResponse(description="Infeasible route due to station gaps exceeding range"),
        },
    )
    def get(self, request):
        params = request.query_params.dict()
        serializer = RouteRequestSerializer(data=params)
        serializer.is_valid(raise_exception=True)
        return self._process_route(request, serializer.validated_data)

    @extend_schema(
        summary="Calculate Optimal Route and Fuel Stops (POST)",
        description="Compute driving route, optimal fuel stops, and total cost via JSON body.",
        request=RouteRequestSerializer,
        responses={
            200: RouteResponseSerializer,
            400: OpenApiResponse(description="Invalid request or unresolvable location"),
            422: OpenApiResponse(description="Infeasible route due to station gaps exceeding range"),
        },
    )
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._process_route(request, serializer.validated_data)

    def _process_route(self, request, data):
        t_start = time.perf_counter()
        external_call_counter: List[int] = [0]

        # 1. Resolve start and finish locations (offline first)
        start_pt = resolve_location(
            data["start"], field_name="start", external_call_counter=external_call_counter
        )
        finish_pt = resolve_location(
            data["finish"], field_name="finish", external_call_counter=external_call_counter
        )

        # 2. Fetch driving route from OSRM (or cache)
        routing_client = RoutingClient()
        try:
            osrm_result = routing_client.get_route(
                start_lat=start_pt.lat,
                start_lng=start_pt.lng,
                end_lat=finish_pt.lat,
                end_lng=finish_pt.lng,
                call_counter=external_call_counter,
            )
        except RoutingClientError as e:
            logger.error("Routing error: %s", e)
            return Response(
                {"error": "Routing Service Error", "detail": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # 3. Optimize fuel stops
        t_solve_start = time.perf_counter()
        planner = FuelPlanner(
            max_detour_miles=data.get("max_detour_miles"),
            detour_penalty_enabled=data.get("detour_penalty"),
        )

        try:
            plan = planner.plan_route(
                start_pt=start_pt,
                finish_pt=finish_pt,
                osrm_result=osrm_result,
                starting_fuel_gallons=data.get("starting_fuel_gallons", 50.0),
            )
        except InfeasibleRouteError as e:
            logger.warning("Infeasible route: %s", e)
            return Response(
                {
                    "error": "Infeasible Route",
                    "detail": str(e),
                    "gap_start_mile": round(e.gap_start, 1),
                    "gap_end_mile": round(e.gap_end, 1),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        t_solve = time.perf_counter() - t_solve_start

        # 4. Cache map visualization and inject map_url
        map_token = cache_route_map(plan, osrm_result["coordinates"])
        map_url = request.build_absolute_uri(reverse("route_map", kwargs={"token": map_token}))
        plan.map_url = map_url
        plan.external_api_calls = external_call_counter[0]

        t_total = time.perf_counter() - t_start

        # 5. Structured logging
        logger.info(
            "Route planned: %s -> %s | dist=%.1f mi | stops=%d | cost=$%.2f | "
            "ext_calls=%d | solve_time=%.2f ms | total_time=%.2f ms",
            start_pt.resolved,
            finish_pt.resolved,
            plan.distance_miles,
            len(plan.fuel_stops),
            plan.total_fuel_cost,
            plan.external_api_calls,
            t_solve * 1000,
            t_total * 1000,
        )

        return Response(plan.to_dict(), status=status.HTTP_200_OK)


class RouteMapView(APIView):
    """Renders the self-contained interactive Leaflet map for a generated route token."""
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        summary="View Route Map",
        description="Return an interactive HTML Leaflet map displaying route and numbered fuel stops.",
        responses={200: OpenApiResponse(description="HTML Map Page")},
    )
    def get(self, request, token: str):
        html_content = render_map_by_token(token)
        if not html_content:
            return HttpResponse(
                "<h3>Map session expired or invalid.</h3><p>Please regenerate your route using POST /api/route/.</p>",
                status=404,
                content_type="text/html",
            )
        return HttpResponse(html_content, content_type="text/html")
