"""
Management command to import, clean, deduplicate, and geocode truckstop fuel stations.
"""

import logging
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, Tuple, Optional, Set

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from rapidfuzz import process, fuzz

from routing.models import Station
from routing.services.geocoding import normalize_city_name
from routing.services.station_index import StationSpatialIndex

logger = logging.getLogger(__name__)

# Continental 50 US States + DC
US_STATES: Set[str] = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


class Command(BaseCommand):
    help = "Import, clean, deduplicate, and geocode fuel stations from CSV."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default=str(settings.DATA_DIR / "fuel_prices.csv"),
            help="Path to raw fuel prices CSV file.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing stations before loading.",
        )

    def handle(self, *args, **options):
        t0 = time.perf_counter()
        csv_path = Path(options["csv"])
        if not csv_path.exists():
            self.stderr.write(self.style.ERROR(f"CSV file not found at: {csv_path}"))
            return

        self.stdout.write(self.style.NOTICE(f"Loading fuel station data from: {csv_path}"))

        # 1. Read CSV using pandas
        raw_df = pd.read_csv(csv_path)
        raw_row_count = len(raw_df)

        # Standardize column headers
        raw_df.columns = [c.strip() for c in raw_df.columns]

        # Clean string columns
        for col in ["Truckstop Name", "Address", "City", "State"]:
            if col in raw_df.columns:
                raw_df[col] = raw_df[col].astype(str).str.strip()

        # Parse numeric columns
        raw_df["Retail Price"] = pd.to_numeric(raw_df["Retail Price"], errors="coerce")
        raw_df["OPIS Truckstop ID"] = pd.to_numeric(raw_df["OPIS Truckstop ID"], errors="coerce")

        # 2. Clean: Drop invalid rows
        valid_df = raw_df.dropna(subset=["Retail Price", "City", "State", "OPIS Truckstop ID"]).copy()
        valid_df["OPIS Truckstop ID"] = valid_df["OPIS Truckstop ID"].astype(int)
        dropped_missing_count = raw_row_count - len(valid_df)

        # 3. Drop non-US rows (Canadian provinces like AB, BC, ON, QC, etc.)
        valid_df["State"] = valid_df["State"].str.upper()
        us_df = valid_df[valid_df["State"].isin(US_STATES)].copy()
        non_us_count = len(valid_df) - len(us_df)

        # 4. Deduplicate by OPIS Truckstop ID -> keep LOWEST retail price per ID
        # Decision justification: Commercial drivers and fleet routing algorithms seek the best
        # posted cash/retail diesel price when a truckstop lists multiple pumps or rack agreements.
        us_df_sorted = us_df.sort_values(by="Retail Price", ascending=True)
        deduped_df = us_df_sorted.drop_duplicates(subset=["OPIS Truckstop ID"], keep="first").copy()
        dupes_removed_count = len(us_df) - len(deduped_df)

        self.stdout.write(f"Raw rows: {raw_row_count} | US rows: {len(us_df)} | Deduplicated unique stations: {len(deduped_df)}")

        # 5. Build Offline City Centroid Lookup
        self.stdout.write("Loading city centroid datasets for offline geocoding...")
        city_lookup, state_cities, state_centroids = self._load_city_centroids()

        # 6. Geocode unique (city, state) pairs
        unique_places = deduped_df[["City", "State"]].drop_duplicates()
        geocode_cache: Dict[Tuple[str, str], Tuple[float, float, str]] = {}

        exact_count = 0
        fuzzy_count = 0
        fallback_count = 0
        failed_count = 0

        for _, row in unique_places.iterrows():
            city_str = row["City"]
            state_str = row["State"]
            norm_city = normalize_city_name(city_str)
            key = (state_str, norm_city)

            # A. Exact match
            if key in city_lookup:
                lat, lng = city_lookup[key]
                geocode_cache[(city_str, state_str)] = (lat, lng, "exact")
                exact_count += 1
                continue

            # B. Fuzzy match within state (score >= 75)
            matched = False
            if state_str in state_cities:
                choices = list(state_cities[state_str].keys())
                match = process.extractOne(norm_city, choices, scorer=fuzz.WRatio, score_cutoff=75)
                if match:
                    matched_city, score, _ = match
                    lat, lng = state_cities[state_str][matched_city]
                    geocode_cache[(city_str, state_str)] = (lat, lng, "fuzzy")
                    fuzzy_count += 1
                    matched = True

            if matched:
                continue

            # C. Fallback: State centroid
            if state_str in state_centroids:
                lat, lng = state_centroids[state_str]
                geocode_cache[(city_str, state_str)] = (lat, lng, "fallback")
                fallback_count += 1
            else:
                failed_count += 1

        # 7. Prepare Station model records
        station_objects = []
        for _, row in deduped_df.iterrows():
            c_val = row["City"]
            s_val = row["State"]
            geo = geocode_cache.get((c_val, s_val))
            if not geo:
                continue

            lat, lng, quality = geo
            rack_val = row.get("Rack ID")
            try:
                rack_id = int(rack_val) if pd.notna(rack_val) else None
            except (ValueError, TypeError):
                rack_id = None

            station_objects.append(
                Station(
                    opis_id=int(row["OPIS Truckstop ID"]),
                    name=str(row["Truckstop Name"])[:255],
                    address=str(row["Address"])[:255] if pd.notna(row["Address"]) else "",
                    city=c_val[:100],
                    state=s_val[:2],
                    rack_id=rack_id,
                    price=Decimal(f"{float(row['Retail Price']):.3f}"),
                    lat=round(lat, 6),
                    lng=round(lng, 6),
                    geocode_quality=quality,
                )
            )

        # 8. Save to DB in bulk transaction
        with transaction.atomic():
            if options.get("clear"):
                Station.objects.all().delete()

            Station.objects.bulk_create(
                station_objects,
                batch_size=2000,
                update_conflicts=True,
                update_fields=["name", "address", "city", "state", "rack_id", "price", "lat", "lng", "geocode_quality"],
                unique_fields=["opis_id"],
            )

        elapsed = time.perf_counter() - t0

        # Reload spatial index
        StationSpatialIndex.reload_index()

        # 9. Print structured summary report
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("           LOAD STATIONS SUMMARY REPORT"))
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(f" Raw rows in CSV:               {raw_row_count:>7,}")
        self.stdout.write(f" Missing price/city dropped:    {dropped_missing_count:>7,}")
        self.stdout.write(f" Non-US rows removed:           {non_us_count:>7,}")
        self.stdout.write(f" Duplicates removed (OPIS ID):  {dupes_removed_count:>7,}")
        self.stdout.write(f" Final US stations:             {len(station_objects):>7,}")
        self.stdout.write("-" * 65)
        self.stdout.write(f" Unique (City, ST) locations:   {len(unique_places):>7,}")
        self.stdout.write(f"   - Exact offline match:       {exact_count:>7,}")
        self.stdout.write(f"   - Fuzzy match within state:  {fuzzy_count:>7,}")
        self.stdout.write(f"   - Fallback (state centroid): {fallback_count:>7,}")
        self.stdout.write(f"   - Failed geocodes:           {failed_count:>7,}")
        self.stdout.write("-" * 65)
        self.stdout.write(f" Total Stations Saved to DB:    {len(station_objects):>7,}")
        self.stdout.write(f" Execution Time:                {elapsed:.2f} seconds")
        self.stdout.write(self.style.SUCCESS("=" * 65))

    def _load_city_centroids(self):
        """Load bundled city centroids dataset into lookup dictionaries and state centroids."""
        cities_csv = settings.DATA_DIR / "us_cities.csv"
        lookup: Dict[Tuple[str, str], Tuple[float, float]] = {}
        state_cities: Dict[str, Dict[str, Tuple[float, float]]] = {}
        state_lat_sum: Dict[str, float] = {}
        state_lng_sum: Dict[str, float] = {}
        state_counts: Dict[str, int] = {}

        if cities_csv.exists():
            df = pd.read_csv(cities_csv)
            for _, r in df.iterrows():
                st = str(r["state"]).strip().upper()
                c = str(r["city"]).strip()
                lat = float(r["lat"])
                lng = float(r["lng"])
                nc = normalize_city_name(c)

                if st and nc:
                    lookup[(st, nc)] = (lat, lng)
                    if st not in state_cities:
                        state_cities[st] = {}
                    state_cities[st][nc] = (lat, lng)

                    state_lat_sum[st] = state_lat_sum.get(st, 0.0) + lat
                    state_lng_sum[st] = state_lng_sum.get(st, 0.0) + lng
                    state_counts[st] = state_counts.get(st, 0) + 1

        state_centroids: Dict[str, Tuple[float, float]] = {}
        for st, count in state_counts.items():
            if count > 0:
                state_centroids[st] = (state_lat_sum[st] / count, state_lng_sum[st] / count)

        return lookup, state_cities, state_centroids

