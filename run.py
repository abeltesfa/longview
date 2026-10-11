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
from llm import track_run_scored
from db import get_db
from memory import get_beliefs, reset_memory
from playbook import get_active_playbook
from score import score_run

RUNS_DIR = Path(__file__).parent / "runs"


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


def build_run_summary(started_at, playbook, weeks, log_name):
    beliefs = get_beliefs()
    final_beliefs = {}
    for b in beliefs:
        final_beliefs.setdefault(b["region"], []).append({
            "claim": b["claim"],
            "metric": b["metric"],
            "status": b["status"],
            "confidence": b["confidence"],
            "history": [{k: h[k] for k in ("week", "action", "new_status", "new_confidence", "reason")}
                        for h in b["history"]],
        })
    breakdown = score_run(weeks[-1])
    return {
        "timestamp": started_at,
        "playbook_version": playbook["version"],
        "playbook_author": playbook.get("author"),
        "weeks_processed": weeks,
        "final_beliefs": final_beliefs,
        "found_true_cause": breakdown["found_true_cause"],
        "score": breakdown["score"],
        "score_breakdown": breakdown,
        "log_file": f"runs/{log_name}",
    }


def execute_run(weeks, echo=True):
    """Reset memory, run the active playbook over `weeks`, save log + summary; return the summary."""
    started_at = datetime.now(timezone.utc)
    RUNS_DIR.mkdir(exist_ok=True)
    log_path = RUNS_DIR / f"run_{started_at:%Y%m%d_%H%M%S}.txt"

    terminal = sys.stdout
    with open(log_path, "w", encoding="utf-8") as log:
        sys.stdout = Tee(terminal, log) if echo else log
        try:
            playbook = get_active_playbook()
            print(f"Run started {started_at:%Y-%m-%d %H:%M:%S} UTC, "
                  f"playbook v{playbook['version']} ({playbook.get('author')})")
            reset_memory()
            print("Memory reset.")
            for week in weeks:
                process_week(week)

            summary = build_run_summary(started_at, playbook, weeks, log_path.name)
            get_db()["runs"].insert_one(dict(summary))
            track_run_scored(playbook["version"], summary["score_breakdown"], source="run",
                             extra={"log_file": summary["log_file"]})

            print(f"\n{'=' * 70}\nFINAL BELIEFS\n{'=' * 70}")
            for region, items in summary["final_beliefs"].items():
                for b in items:
                    print(f"  {region:<6} {b['status']:<12} {b['confidence']:.2f}  [{b['metric']}] {b['claim']}")
            print(f"\nfound_true_cause: {summary['found_true_cause']}")
            print(f"score: {summary['score']}  {summary['score_breakdown']['points']}")
            print(f"Saved log to runs/{log_path.name} and summary to runs collection.")
        finally:
            sys.stdout = terminal
    return summary


def main():
    weeks = [int(w) for w in sys.argv[1:]] or list(range(2, 7))
    execute_run(weeks)


if __name__ == "__main__":
    main()
