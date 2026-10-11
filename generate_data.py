"""Generate synthetic weekly sales with a planted West-region story and load it into MongoDB.

Story (built so the obvious first guess fits early, then breaks):
- Weeks 2-3: West raises avg_price ~8%; demand drops, so revenue falls. Pricing looks guilty.
- Week 3 onward: West loses ~15% of its distribution points each week.
- Week 4: price returns to baseline and a promo runs, yet revenue keeps falling.
  Pricing is refuted; distribution loss is the real driver through week 6.
Other regions are stable with about ±3% noise.

Scenario B (python generate_data.py --scenario B), a held-out test the playbooks never saw:
- South runs a long promo in weeks 1-2. It ends in week 3 and promo-driven demand unwinds
  through week 6 (true cause: promo_active).
- Week 4: South distribution dips ~5% for one week and recovers in week 5 (red herring).
- Price flat. All other regions, West included, are stable.

Scenario C (--scenario C), a second held-out test:
- East distribution dips ~12% in weeks 2-3 and recovers in week 4 (misleading first guess).
- East avg_price rises ~9% from week 3 and stays up; customers drift away over the following
  weeks, so revenue keeps falling after distribution recovers (true cause: avg_price).
- No promo. All other regions are stable.
"""
import argparse
import random

from db import DB_NAME, get_db
from scenarios import SCENARIOS, record_scenario

SEED = 42
WEEKS = range(1, 7)
REGIONS = ["North", "South", "East", "West"]

# product -> (base distribution points, units per store per week, avg price)
PRODUCTS = {
    "Sparkling Water": (400, 30, 1.50),
    "Protein Bar": (300, 25, 2.75),
    "Cold Brew": (250, 18, 4.25),
}

WEST_PRICE_HIKE = 1.08          # avg_price multiplier during the hike
WEST_PRICE_HIKE_WEEKS = {2, 3}
PRICE_HIKE_DEMAND = 0.86        # units per store during the hike (revenue ≈ 1.08 × 0.86 ≈ -7%)
WEST_DIST_DROP = 0.15           # share of stores lost each week
WEST_DIST_DROP_START = 3        # first week with fewer stores
WEST_PROMO_WEEK = 4
PROMO_UNIT_LIFT = 1.02          # small promo lift, not enough to offset the drop

# Scenario B (South). Demand multiplier per week: promo lift in weeks 1-2, unwinding after it ends.
SOUTH_PROMO_WEEKS = {1, 2}
SOUTH_DEMAND = {1: 1.30, 2: 1.30, 3: 1.17, 4: 1.08, 5: 0.96, 6: 0.90}
SOUTH_DIST_DIP_WEEK = 4
SOUTH_DIST_DIP = 0.95

# Scenario C (East). Distribution dip weeks 2-3; price rise from week 3 with growing demand loss.
EAST_DIST_DIP_WEEKS = {2, 3}
EAST_DIST_DIP = 0.88
EAST_PRICE_RISE = 1.09
EAST_PRICE_RISE_START = 3
EAST_DEMAND = {1: 1.0, 2: 1.0, 3: 0.85, 4: 0.68, 5: 0.61, 6: 0.55}  # customers drift away after the rise


def generate(rng, scenario="A"):
    rows = []
    for region in REGIONS:
        for product, (base_dist, base_units_per_store, price) in PRODUCTS.items():
            for week in WEEKS:
                if scenario == "B" and region == "South":
                    dist = round(base_dist * rng.uniform(0.99, 1.01)
                                 * (SOUTH_DIST_DIP if week == SOUTH_DIST_DIP_WEEK else 1))
                    promo = week in SOUTH_PROMO_WEEKS
                    units_per_store = base_units_per_store * rng.uniform(0.985, 1.015) * SOUTH_DEMAND[week]
                    rows.append(_row(week, region, product, dist, units_per_store, price, promo, rng))
                    continue
                if scenario == "C" and region == "East":
                    dist = round(base_dist * rng.uniform(0.99, 1.01)
                                 * (EAST_DIST_DIP if week in EAST_DIST_DIP_WEEKS else 1))
                    units_per_store = base_units_per_store * rng.uniform(0.985, 1.015) * EAST_DEMAND[week]
                    price_now = price * (EAST_PRICE_RISE if week >= EAST_PRICE_RISE_START else 1)
                    rows.append(_row(week, region, product, dist, units_per_store, price_now, False, rng))
                    continue
                if region == "West" and scenario == "A":
                    weeks_of_decline = max(0, week - WEST_DIST_DROP_START + 1)
                    dist = round(base_dist * (1 - WEST_DIST_DROP) ** weeks_of_decline)
                    promo = week == WEST_PROMO_WEEK
                    units_per_store = base_units_per_store * rng.uniform(0.98, 1.02)
                    if week in WEST_PRICE_HIKE_WEEKS:
                        units_per_store *= PRICE_HIKE_DEMAND
                else:
                    # Small noise (about ±3% on revenue) so only West crosses the 5% drop threshold.
                    dist = round(base_dist * rng.uniform(0.99, 1.01))
                    promo = False
                    units_per_store = base_units_per_store * rng.uniform(0.985, 1.015)

                if promo:
                    units_per_store *= PROMO_UNIT_LIFT

                hiked = scenario == "A" and region == "West" and week in WEST_PRICE_HIKE_WEEKS
                rows.append(_row(week, region, product, dist, units_per_store,
                                 price * WEST_PRICE_HIKE if hiked else price, promo, rng))
    return rows


def _row(week, region, product, dist, units_per_store, price, promo, rng):
    units = round(dist * units_per_store)
    avg_price = round(price * rng.uniform(0.99, 1.01), 2)
    return {
        "week": week,
        "region": region,
        "product": product,
        "revenue": round(units * avg_price, 2),
        "units": units,
        "avg_price": avg_price,
        "distribution_points": dist,
        "promo_active": promo,
    }


def print_summary(coll):
    pipeline = [
        {"$group": {"_id": {"region": "$region", "week": "$week"}, "revenue": {"$sum": "$revenue"}}},
    ]
    totals = {(d["_id"]["region"], d["_id"]["week"]): d["revenue"] for d in coll.aggregate(pipeline)}

    header = f"{'Region':<8}" + "".join(f"{'Wk ' + str(w):>11}" for w in WEEKS) + f"{'Wk1→6':>9}"
    print("\nRevenue by region and week")
    print(header)
    print("-" * len(header))
    for region in REGIONS:
        vals = [totals[(region, w)] for w in WEEKS]
        change = (vals[-1] - vals[0]) / vals[0] * 100
        print(f"{region:<8}" + "".join(f"{v:>11,.0f}" for v in vals) + f"{change:>8.0f}%")


def load(scenario="A"):
    rows = generate(random.Random(SEED), scenario)
    coll = get_db()["weekly_sales"]
    coll.delete_many({})
    coll.insert_many(rows)
    record_scenario(scenario)
    print(f"Inserted {len(rows)} documents into {DB_NAME}.weekly_sales (scenario {scenario})")
    print_summary(coll)


def main():
    parser = argparse.ArgumentParser(description="Load a synthetic sales scenario into MongoDB.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="A")
    load(parser.parse_args().scenario)


if __name__ == "__main__":
    main()
