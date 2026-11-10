from django.urls import path
from routing.views import RoutePlanView, RouteMapView

urlpatterns = [
    path("route/", RoutePlanView.as_view(), name="route_plan"),
    path("route/map/<str:token>/", RouteMapView.as_view(), name="route_map"),
]

