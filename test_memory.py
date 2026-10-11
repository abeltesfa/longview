"""Play out a scripted belief story against MongoDB and print each change."""
from memory import (
    contradict,
    create_belief,
    get_belief,
    get_interventions,
    record_intervention,
    reinforce,
    reset_memory,
    supersede,
)


def show(label, belief_id):
    b = get_belief(belief_id)
    print(f"  {label}: \"{b['claim']}\" → status={b['status']}, "
          f"confidence={b['confidence']:.2f}, reinforced={b['times_reinforced']}x")


def print_history(belief_id):
    b = get_belief(belief_id)
    print(f"\n\"{b['claim']}\"  (final: {b['status']}, {b['confidence']:.2f})")
    if b.get("supersedes"):
        print(f"  supersedes: {get_belief(b['supersedes'])['claim']}")
    if b.get("superseded_by"):
        print(f"  superseded by: {get_belief(b['superseded_by'])['claim']}")
    for h in b["history"]:
        old_conf = "—" if h["old_confidence"] is None else f"{h['old_confidence']:.2f}"
        print(f"  wk {h['week']}  {h['action']:<12} "
              f"{h['old_status'] or '—':<12} → {h['new_status']:<12} "
              f"conf {old_conf} → {h['new_confidence']:.2f}   ({h['reason']})")


def main():
    reset_memory()
    print("Memory reset.\n")

    print("Week 1: form an initial hypothesis")
    pricing = create_belief("West", "revenue", "West decline caused by pricing", 0.6, week=1,
                            reason="West revenue dropped; pricing is the first suspect")
    show("created", pricing)

    print("\nWeek 2: decline continues")
    reinforce(pricing, week=2, evidence_summary="West revenue fell again")
    show("reinforced", pricing)

    print("\nWeek 3: action taken")
    record_intervention("West", week=3, description="West promo launched")
    for i in get_interventions("West"):
        print(f"  intervention: wk {i['week']} {i['region']} — {i['description']}")

    print("\nWeek 4: new evidence breaks the pricing story")
    contradict(pricing, week=4, evidence_summary="avg_price flat across all weeks")
    show("contradicted", pricing)
    distribution = supersede(pricing, "West decline caused by distribution loss", 0.8, week=4,
                             reason="distribution_points down ~15%/week while price flat; promo didn't help")
    show("superseded", pricing)
    show("new belief", distribution)

    print("\n=== Full history ===")
    print_history(pricing)
    print_history(distribution)


if __name__ == "__main__":
    main()
