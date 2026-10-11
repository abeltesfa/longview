"""Reset memory and run the agent week by week.

Each run's printed output is saved to runs/run_<timestamp>.txt and a summary document
is written to the `runs` collection.

Usage: python run.py          # weeks 2-6
       python run.py 2 3      # only the given weeks
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent import process_week
from db import get_db
from memory import ACTIVE, get_beliefs, reset_memory
from playbook import get_active_playbook

RUNS_DIR = Path(__file__).parent / "runs"
TRUE_CAUSE_REGION = "West"
TRUE_CAUSE_METRIC = "distribution_points"
TRUE_CAUSE_MIN_CONFIDENCE = 0.7


class Tee:
    """Write to several streams at once (terminal + log file)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


def summarize(started_at, playbook_version, weeks):
    beliefs = get_beliefs()
    final_beliefs = {}
    for b in beliefs:
        final_beliefs.setdefault(b["region"], []).append({
            "claim": b["claim"],
            "metric": b["metric"],
            "status": b["status"],
            "confidence": b["confidence"],
        })
    found = any(
        b["region"] == TRUE_CAUSE_REGION and b["status"] == ACTIVE
        and b["metric"] == TRUE_CAUSE_METRIC and b["confidence"] >= TRUE_CAUSE_MIN_CONFIDENCE
        for b in beliefs
    )
    return {
        "timestamp": started_at,
        "playbook_version": playbook_version,
        "weeks_processed": weeks,
        "final_beliefs": final_beliefs,
        "found_true_cause": found,
    }


def main():
    weeks = [int(w) for w in sys.argv[1:]] or list(range(2, 7))
    started_at = datetime.now(timezone.utc)
    RUNS_DIR.mkdir(exist_ok=True)
    log_path = RUNS_DIR / f"run_{started_at:%Y%m%d_%H%M%S}.txt"

    terminal = sys.stdout
    with open(log_path, "w", encoding="utf-8") as log:
        sys.stdout = Tee(terminal, log)
        try:
            playbook = get_active_playbook()
            print(f"Run started {started_at:%Y-%m-%d %H:%M:%S} UTC, playbook v{playbook['version']}")
            reset_memory()
            print("Memory reset.")
            for week in weeks:
                process_week(week)

            summary = summarize(started_at, playbook["version"], weeks)
            get_db()["runs"].insert_one(dict(summary))

            print(f"\n{'=' * 70}\nFINAL BELIEFS\n{'=' * 70}")
            for region, items in summary["final_beliefs"].items():
                for b in items:
                    print(f"  {region:<6} {b['status']:<12} {b['confidence']:.2f}  [{b['metric']}] {b['claim']}")
            print(f"\nfound_true_cause: {summary['found_true_cause']}")
            print(f"Saved log to runs/{log_path.name} and summary to runs collection.")
        finally:
            sys.stdout = terminal


if __name__ == "__main__":
    main()
