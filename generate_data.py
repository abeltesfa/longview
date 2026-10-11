"""Generate synthetic weekly sales with a planted West-region story and load it into MongoDB.

Story: from week 2 onward, West loses ~15% of its distribution points each week,
so revenue falls. Prices stay flat, and a week-3 promo gives only a small lift.
"""
import random

from db import get_db

SEED = 42
WEEKS = range(1, 7)
REGIONS = ["North", "South", "East", "West"]

# product -> (base distribution points, units per store per week, avg price)
PRODUCTS = {
    "Sparkling Water": (400, 30, 1.50),
    "Protein Bar": (300, 25, 2.75),
    "Cold Brew": (250, 18, 4.25),
}

WEST_DIST_DROP = 0.15   # share of stores lost each week from week 2
WEST_PROMO_WEEK = 3
PROMO_UNIT_LIFT = 1.08  # promo boosts units per store by ~8%, not enough to offset the drop


def generate(rng):
    rows = []
    for region in REGIONS:
        for product, (base_dist, base_units_per_store, price) in PRODUCTS.items():
            for week in WEEKS:
                if region == "West":
                    weeks_of_decline = max(0, week - 1)
                    dist = round(base_dist * (1 - WEST_DIST_DROP) ** weeks_of_decline)
                    promo = week == WEST_PROMO_WEEK
                    units_per_store = base_units_per_store * rng.uniform(0.98, 1.02)
                else:
                    # Small noise (about ±3% on revenue) so only West crosses the 5% drop threshold.
                    dist = round(base_dist * rng.uniform(0.99, 1.01))
                    promo = False
                    units_per_store = base_units_per_store * rng.uniform(0.985, 1.015)

                if promo:
                    units_per_store *= PROMO_UNIT_LIFT

                units = round(dist * units_per_store)
                avg_price = round(price * rng.uniform(0.99, 1.01), 2)
                rows.append({
                    "week": week,
                    "region": region,
                    "product": product,
                    "revenue": round(units * avg_price, 2),
                    "units": units,
                    "avg_price": avg_price,
                    "distribution_points": dist,
                    "promo_active": promo,
                })
    return rows


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


def main():
    rows = generate(random.Random(SEED))
    coll = get_db()["weekly_sales"]
    coll.delete_many({})
    coll.insert_many(rows)
    print(f"Inserted {len(rows)} documents into longview.weekly_sales")
    print_summary(coll)


if __name__ == "__main__":
    main()
