from django.db import models


class Station(models.Model):
    """
    Commercial truck stop station with retail diesel pricing and geographic location.
    """
    opis_id = models.IntegerField(unique=True, db_index=True, help_text="Unique OPIS Truckstop ID")
    name = models.CharField(max_length=255, help_text="Truckstop Brand / Name")
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=3, help_text="Retail diesel price per gallon ($)")
    lat = models.FloatField(help_text="Latitude in decimal degrees")
    lng = models.FloatField(help_text="Longitude in decimal degrees")
    geocode_quality = models.CharField(
        max_length=20,
        default="exact",
        choices=[
            ("exact", "Exact City Match"),
            ("fuzzy", "Fuzzy City Match"),
            ("fallback", "Census/State Fallback"),
        ],
        help_text="Offline geocoding resolution quality",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state"], name="idx_station_state"),
            models.Index(fields=["lat", "lng"], name="idx_station_lat_lng"),
        ]
        ordering = ["opis_id"]

    def __str__(self):
        return f"{self.name} (#{self.opis_id}) - {self.city}, {self.state} (${self.price}/gal)"
