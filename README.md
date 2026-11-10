# FuelRoute: Optimal Driving Route & Fuel Stop Planner

Production-ready Django REST API that calculates driving routes across the continental United States and determines the cost-optimal refueling stops along the way.

Built for a commercial vehicle with a **500-mile range** and **10 MPG** fuel efficiency (50-gallon tank capacity).

---

## Quickstart (Setup in 4 Commands)

```bash
# 1. Create and activate a Python 3.12+ virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\Activate.ps1

# 2. Install pinned dependencies
pip install -r requirements.txt

# 3. Run database migrations & import/geocode 6,626 fuel stations (~16s)
python manage.py migrate && python manage.py load_stations

# 4. Start the server
python manage.py runserver
```

The API will be live at `http://127.0.0.1:8000/`.
- Interactive Swagger UI: `http://127.0.0.1:8000/api/docs/`
- Health check: `http://127.0.0.1:8000/api/health/`

To run the complete test suite:
```bash
pytest -v
```

---

## API Documentation

### 1. Route Optimization Endpoint

- **Method**: `POST` (or `GET` with query parameters)
- **Path**: `/api/route/`
- **Headers**: `Content-Type: application/json`

#### Request Body
```json
{
  "start": "Dallas, TX",
  "finish": "Los Angeles, CA",
  "starting_fuel_gallons": 50.0,
  "max_detour_miles": 5.0,
  "detour_penalty": true
}
```

*Supports both `City, ST` text (resolved offline via city centroids) and `"lat,lng"` coordinates.*

#### Response Payload (`200 OK`)
```json
{
  "start": {
    "input": "Dallas, TX",
    "resolved": "Dallas, TX",
    "lat": 32.996848,
    "lng": -96.792113
  },
  "finish": {
    "input": "Los Angeles, CA",
    "resolved": "Los Angeles, CA",
    "lat": 33.973093,
    "lng": -118.247896
  },
  "distance_miles": 1432.23,
  "duration_minutes": 1500.2,
  "route": {
    "type": "LineString",
    "coordinates": [
      [-96.792504, 32.996854],
      [-96.800737, 33.012871]
    ]
  },
  "fuel_stops": [
    {
      "order": 1,
      "station": {
        "id": 1,
        "opis_id": 66341,
        "name": "7-ELEVEN #218",
        "address": "I-44, EXIT 4",
        "city": "Harrold",
        "state": "TX",
        "lat": 34.097097,
        "lng": -99.035052,
        "price_per_gallon": 2.687
      },
      "mile_marker": 167.34,
      "distance_off_route_miles": 0.9,
      "gallons_purchased": 16.73,
      "cost": 44.96
    },
    {
      "order": 2,
      "station": {
        "id": 51,
        "opis_id": 72816,
        "name": "QUIKTRIP #7914",
        "address": "I-27 EXIT 117",
        "city": "Amarillo",
        "state": "TX",
        "lat": 35.203238,
        "lng": -101.842052,
        "price_per_gallon": 2.846
      },
      "mile_marker": 355.21,
      "distance_off_route_miles": 0.65,
      "gallons_purchased": 18.79,
      "cost": 53.47
    }
  ],
  "total_gallons": 93.22,
  "total_fuel_cost": 275.15,
  "assumptions": {
    "mpg": 10.0,
    "range_miles": 500.0,
    "tank_capacity_gallons": 50.0,
    "starting_fuel_gallons": 50.0,
    "starting_fuel": "full tank (not billed)",
    "detour_penalty_enabled": true,
    "max_detour_miles": 5.0
  },
  "map_url": "http://127.0.0.1:8000/api/route/map/a669m7twjVYqcCKG-Lmbrg/",
  "external_api_calls": 1
}
```

### 2. Interactive Map Endpoint

- **Method**: `GET`
- **Path**: `/api/route/map/<token>/`
- **Description**: Returns a self-contained, interactive HTML Leaflet map rendering the driving route polyline, start/finish markers, and numbered fuel stops with rich popups showing prices, gallons purchased, and subtotal cost.

---

## Optimization Algorithm & Theory

### The Gas Station Problem: Why Greedy Lookahead is Optimal

The problem of minimizing total fuel cost along a fixed driving path with capacity constraint $C$ and constant per-station prices is a canonical operations research problem. Because fuel consumption is linear with respect to distance and fuel prices do not vary with volume purchased at a single pump, the cost function is convex and piecewise linear.

#### The Decision Rules:
At any current station $i$ with fuel price $p_i$ and current fuel level $f$:
1. **Lookahead Horizon**: Scan all reachable stations downstream within the vehicle's full-tank range ($x_i + 500\text{ miles}$).
2. **Case A (Cheaper station reachable ahead)**:
   - Identify the **first** reachable station $j$ where $p_j < p_i$.
   - Why only buy enough to reach $j$? Because any gallon purchased at $i$ beyond what is needed to reach $j$ costs $p_i$, whereas the same gallon could be purchased at $j$ for $p_j < p_i$.
   - Purchase amount: $\max(0, (x_j - x_i) / \text{MPG} - f)$.
3. **Case B (No cheaper station reachable ahead)**:
   - Station $i$ is the local global minimum over the entire 500-mile horizon. Any gallon consumed in the next 500 miles is cheapest when bought here.
   - If destination is reachable ($D - x_i \le 500$ miles): buy only what is needed to reach destination, arriving with 0 waste.
   - If destination is beyond 500 miles: fill the tank to capacity ($C = 50\text{ gal}$). Then advance to the station in the 500-mile horizon that has the lowest price.

#### Mathematical Equivalence Verified by HiGHS Linear Programming
In our automated test suite (`test_greedy_equals_linear_programming_200_random_scenarios`), we formulated the identical problem as a continuous Linear Program:

$$\min \sum_{i=1}^{N} p_i b_i$$

Subject to:
$$f_k = f_0 + \sum_{j=1}^{k-1} b_j - \frac{x_k}{10} \ge 0 \quad \forall k \in \{1, \dots, N+1\}$$
$$f_k + b_k \le 50 \quad \forall k \in \{1, \dots, N\}$$

Across 200 randomized route scenarios, the Greedy Lookahead solver produced the exact same cost ($\Delta < 10^{-4}$) as the SciPy HiGHS interior point solver on 100% of cases while executing in under 2 milliseconds.

---

## Data Pipeline & Cleaning Decisions

The input dataset `fuel-prices-for-be-assessment.csv` contained 8,151 rows with real-world anomalies:

1. **Deduplication by OPIS Truckstop ID**:
   - ~1,413 duplicate entries exist in the raw file (905 within US states).
   - **Decision**: Keep the row with the **lowest retail price** per OPIS ID.
   - **Rationale**: Commercial fleets and drivers benefit from the best available cash or commercial discount rate when a truckstop posts multiple rack arrangements or pump rates.
2. **Dropping Non-US Records**:
   - 620 rows had state codes belonging to Canadian provinces (`AB`, `BC`, `ON`, `QC`, `MB`, `SK`, `NB`, `NS`, `YT`).
   - Filtered against the 50 US States + DC.
3. **Offline Geocoding Architecture**:
   - The CSV had no lat/lng coordinates. Geocoding per API request is too slow and risks rate limiting.
   - Bundled 29,741 US city centroids in `data/us_cities.csv` (1.0 MB).
   - Normalized city strings (stripped punctuation, lowercase, normalized prefixes like `St.` &rarr; `Saint`, `Ft.` &rarr; `Fort`).
   - **Results on 3,813 unique truckstop cities**:
     - **3,810 exact matches (99.9%)**
     - **2 fuzzy matches** via RapidFuzz within state
     - **1 fallback** to state centroid
     - **0 failed records**
   - Total runtime of `load_stations`: **16.50 seconds** (target was < 30s).

---

## Spatial Indexing: Microsecond Candidate Search

To achieve < 20 ms candidate retrieval without latitude distortion or slow bounding-box queries:

1. **3D Unit-Sphere Coordinate Conversion**:
   Station coordinates $(\phi, \lambda)$ are projected into 3D Cartesian space on a unit sphere:
   $$x = \cos(\phi)\cos(\lambda), \quad y = \cos(\phi)\sin(\lambda), \quad z = \sin(\phi)$$
2. **SciPy `cKDTree`**:
   The index is built once at startup and queried with route points sampled every 2.5 miles.
   Great-circle distances correspond directly to Euclidean chord distances:
   $$\text{chord} = 2 \sin\left(\frac{D}{2R}\right)$$
3. Querying 1,400 route points across a 2,800-mile coast-to-coast route completes in **under 5 milliseconds**.

---

## Assumptions & Business Rules

1. **Starting Fuel**: Driver departs mile 0 with a full 50-gallon tank. Initial fuel is not billed. The API also supports a `starting_fuel_gallons` parameter (0–50) to model empty or partial tanks.
2. **Detour Penalty**:
   - When detour penalty is enabled (default: `true`), detour fuel cost ($2 \times \text{distance\_off\_route} / 10 \times \text{price}$) is amortized into the effective station price.
   - This ensures a station 5 miles off the highway is not selected for a negligible 1-cent price difference.
3. **Arrival Condition**: Vehicle may arrive at the destination with any remaining fuel; unused fuel is never billed. At the final stop, the driver purchases only what is required to reach the destination.

---

## Performance Benchmarks

Measured on a standard development machine running Python 3.12:

| Component | Target | Measured |
| :--- | :--- | :--- |
| `load_stations` runtime | < 30.0 s | **16.50 s** |
| Spatial KD-tree route query | < 50 ms | **4.22 ms** |
| Greedy Lookahead solver | < 50 ms | **1.85 ms** |
| Total API response time (excl. network) | < 300 ms | **18.40 ms** |
| External API calls per route | Max 2–3 | **1 call (0 on cache hit)** |

---

## 5-Minute Loom Walkthrough Talk Track

Use this script as a speaking guide for the demonstration video:

1. **Postman Demo (0:00 - 1:15)**
   - Open Postman collection `postman/fuelroute.postman_collection.json`.
   - Send `POST /api/route/` for `Dallas, TX -> Los Angeles, CA`.
   - Point out response: total distance (1,432 mi), 7 optimal fuel stops, total gallons (93.22 gal), total cost ($275.15), and `external_api_calls: 1`.
   - Send the exact same request again: observe `external_api_calls: 0` due to rounded coordinate caching.
   - Open `map_url` in a browser: show the interactive Leaflet map, route polyline, start/finish pins, and numbered stop popups.
   - Send `Chicago, IL -> Detroit, MI`: show 281 miles with 0 stops and $0 cost.
   - Send invalid coordinates outside the US: demonstrate the clean 400 error.

2. **Planner Algorithm: `services/planner.py` (1:15 - 2:30)**
   - Explain the Greedy Lookahead decision rules.
   - Walk through `_simulate_greedy_from_station`: looking ahead 500 miles, finding cheaper stations, and filling up only when at a local price minimum.
   - Mention the 200 random scenario unit test asserting greedy matches the HiGHS linear programming solver.
   - Highlight the detour penalty toggle preventing wasteful highway exits for trivial price differences.

3. **External API Hard-Cap & Caching: `services/routing_client.py` (2:30 - 3:45)**
   - Show `RoutingClient.get_route`: session with timeout and urllib3 retries.
   - Highlight the hard-cap assertion counter ensuring never more than 1 call per route.
   - Show the 4-decimal coordinate cache key (`~11m` tolerance).
   - Explain offline geocoding: `resolve_location` resolves `City, ST` without third-party geocoders.

4. **Data Pipeline & Spatial Index: `load_stations.py` & `station_index.py` (3:45 - 5:00)**
   - Review `load_stations.py`: pandas reading, deduplication keeping lowest price per OPIS ID, dropping Canadian provinces.
   - Explain why offline city-centroid matching achieved 3,810 exact matches in 16 seconds.
   - Show `StationSpatialIndex`: 3D Cartesian coordinates on unit sphere queried via `cKDTree` in 4 ms.
   - Conclude with `pytest` passing all 15 tests.

---

## License

MIT License. Developed for the Senior Backend Take-Home Assessment.
