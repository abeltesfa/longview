"""Aggregation queries over weekly_sales."""
from db import get_db

METRICS = ["revenue", "units", "avg_price", "distribution_points", "promo_active"]

# How each metric rolls up across products for one region-week.
_ROLLUP = {
    "revenue": "$sum",
    "units": "$sum",
    "distribution_points": "$sum",
    "avg_price": "$avg",
    "promo_active": "$max",  # true if any product had a promo
}


def _sales():
    return get_db()["weekly_sales"]


def region_week_summary(week):
    """Revenue per region for `week`, with % change vs the previous week (None in week 1)."""
    rows = _sales().aggregate([
        {"$match": {"week": {"$in": [week - 1, week]}}},
        {"$group": {"_id": {"region": "$region", "week": "$week"}, "revenue": {"$sum": "$revenue"}}},
    ])
    totals = {(r["_id"]["region"], r["_id"]["week"]): r["revenue"] for r in rows}
    summary = []
    for region in sorted({region for region, _ in totals}):
        current = totals.get((region, week))
        if current is None:
            continue
        prev = totals.get((region, week - 1))
        pct = round((current - prev) / prev * 100, 1) if prev else None
        summary.append({"region": region, "week": week, "revenue": round(current, 2),
                        "prev_revenue": prev and round(prev, 2), "pct_change": pct})
    return summary


def get_metric_series(region, metric, up_to_week):
    """Weekly values of `metric` for `region`, weeks 1..up_to_week, as [{week, value}]."""
    if metric not in _ROLLUP:
        raise ValueError(f"Unknown metric {metric!r}; choose from {METRICS}")
    rows = _sales().aggregate([
        {"$match": {"region": region, "week": {"$lte": up_to_week}}},
        {"$group": {"_id": "$week", "value": {_ROLLUP[metric]: f"${metric}"}}},
        {"$sort": {"_id": 1}},
    ])
    series = []
    for r in rows:
        value = r["value"]
        value = bool(value) if metric == "promo_active" else round(value, 2)
        series.append({"week": r["_id"], "value": value})
    return series


def promo_regions(week):
    """Regions with any promo_active product in `week`."""
    return sorted(_sales().distinct("region", {"week": week, "promo_active": True}))
