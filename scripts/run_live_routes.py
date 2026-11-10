import requests
import json
import time

routes = [
    ("Dallas, TX", "Los Angeles, CA"),
    ("New York, NY", "Los Angeles, CA"),
    ("Miami, FL", "Seattle, WA"),
    ("Chicago, IL", "Detroit, MI"),
]

for start, finish in routes:
    print("=" * 70)
    print(f"ROUTE: {start} -> {finish}")
    t0 = time.perf_counter()
    resp = requests.post(
        "http://127.0.0.1:8000/api/route/",
        json={"start": start, "finish": finish},
        timeout=35,
    )
    elapsed = time.perf_counter() - t0
    print(f"Status: {resp.status_code} (took {elapsed:.2f}s)")
    
    if resp.status_code == 200:
        data = resp.json()
        print(f"Distance: {data['distance_miles']} miles | Duration: {data['duration_minutes']/60:.1f} hours")
        print(f"Stops: {len(data['fuel_stops'])} | Gallons: {data['total_gallons']} gal | Total Cost: ${data['total_fuel_cost']}")
        print(f"External Calls: {data['external_api_calls']}")
        print(f"Map URL: {data['map_url']}")
        for s in data["fuel_stops"]:
            print(f"   Stop #{s['order']}: {s['station']['name']} ({s['station']['city']}, {s['station']['state']}) | Mile: {s['mile_marker']} | Buy: {s['gallons_purchased']} gal @ ${s['station']['price_per_gallon']:.3f} | Cost: ${s['cost']:.2f}")
    else:
        print("Response:", resp.text)
