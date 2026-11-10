from django.apps import AppConfig


class RoutingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "routing"

    def ready(self):
        # Warm up offline city centroid lookup
        try:
            from routing.services.geocoding import CityCentroidIndex
            CityCentroidIndex.get_instance()
        except Exception:
            pass
