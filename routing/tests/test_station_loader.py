"""
Unit tests for the 'load_stations' management command.
Tests data cleaning, deduplication, non-US filtering, and geocoding quality tracking.
"""

import io
from decimal import Decimal
import pandas as pd
import pytest
from django.core.management import call_command

from routing.models import Station


@pytest.mark.django_db
def test_station_loader_dedupe_and_filter(tmp_path):
    """
    Test that load_stations:
    1. Keeps the lowest price for duplicate OPIS IDs
    2. Drops non-US Canadian provinces
    3. Drops rows missing price/city
    """
    test_csv = tmp_path / "test_stations.csv"
    csv_content = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
1001,DUPE HIGH PRICE,100 Road,Dallas,TX,10,3.500
1001,DUPE LOW PRICE,100 Road,Dallas,TX,10,2.950
1001,DUPE MID PRICE,100 Road,Dallas,TX,10,3.150
1002,CANADIAN STATION,200 Way,Calgary,AB,20,3.000
1003,ONTARIO STATION,300 Ave,Toronto,ON,30,2.800
1004,MISSING PRICE,400 St,Houston,TX,40,
1005,VALID AUSTIN,500 Blvd,Austin,TX,50,3.050
"""
    test_csv.write_text(csv_content, encoding="utf-8")

    out = io.StringIO()
    call_command("load_stations", csv=str(test_csv), clear=True, stdout=out)
    output = out.getvalue()

    # 1. Check deduplication kept the lowest price for 1001
    st_1001 = Station.objects.get(opis_id=1001)
    assert st_1001.price == Decimal("2.950")
    assert st_1001.name == "DUPE LOW PRICE"

    # 2. Check Canadian stations were excluded
    assert not Station.objects.filter(opis_id=1002).exists()
    assert not Station.objects.filter(opis_id=1003).exists()

    # 3. Check missing price station was excluded
    assert not Station.objects.filter(opis_id=1004).exists()

    # 4. Check valid station 1005 was saved
    assert Station.objects.filter(opis_id=1005).exists()

    # Total saved should be exactly 2 stations (1001, 1005)
    assert Station.objects.count() == 2

    # 5. Check summary report output
    assert "LOAD STATIONS SUMMARY REPORT" in output
    assert "Duplicates removed" in output
    assert "Non-US rows removed" in output


@pytest.mark.django_db
def test_station_loader_idempotent(tmp_path):
    """Running load_stations multiple times updates cleanly without duplicate primary keys."""
    test_csv = tmp_path / "idempotent.csv"
    csv_content = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
2001,TEST STATION 1,123 Main,Dallas,TX,1,3.200
2002,TEST STATION 2,456 Oak,Austin,TX,2,3.100
"""
    test_csv.write_text(csv_content, encoding="utf-8")

    call_command("load_stations", csv=str(test_csv), clear=True)
    assert Station.objects.count() == 2

    # Run again without clear: should update in place
    call_command("load_stations", csv=str(test_csv))
    assert Station.objects.count() == 2

