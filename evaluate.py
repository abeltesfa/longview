"""Held-out generalization test: frozen playbooks on scenario B, in a separate database.

Runs entirely in LONGVIEW_DB (default longview_holdout), so the demo database is never touched.
Copies the playbook collection from the main `longview` database, loads scenario B, then runs
each playbook version frozen (no reflection) several times and reports mean score.

Usage: python evaluate.py [--versions 0 2 10] [--repeats 2]
"""
import os

os.environ.setdefault("LONGVIEW_DB", "longview_holdout")
if os.environ["LONGVIEW_DB"] == "longview":
    raise SystemExit("evaluate.py must not run against the demo database 'longview'.")

import argparse  # noqa: E402  (env must be set before project modules read it)

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

import llm  # noqa: E402
from db import DB_NAME, get_db  # noqa: E402
from generate_data import load  # noqa: E402
from playbook import get_playbook, set_active_version  # noqa: E402
from run import execute_run  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

SOURCE_DB = "longview"
SCENARIO = "B"
WEEKS = list(range(2, 7))
console = Console()


def copy_playbooks():
    docs = list(get_db(SOURCE_DB)["playbook"].find())
    target = get_db()["playbook"]
    target.delete_many({})
    target.insert_many(docs)
    return len(docs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versions", type=int, nargs="+", default=[0, 2, 10])
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()

    llm.usage_log = lambda msg: None
    console.rule(f"[bold]Held-out test · database {DB_NAME} · scenario {SCENARIO}")
    console.print(f"[dim]{SCENARIOS[SCENARIO]['description']}[/]")
    console.print(f"Copied {copy_playbooks()} playbook versions from {SOURCE_DB}.")
    load(SCENARIO)

    rows = []
    for v in args.versions:
        pb = get_playbook(v)
        set_active_version(v)
        scores, found = [], 0
        for i in range(1, args.repeats + 1):
            before = llm.stats["calls"]
            with console.status(f"v{v} run {i}/{args.repeats}…"):
                doc = execute_run(WEEKS, echo=False)
            b = doc["score_breakdown"]
            scores.append(doc["score"])
            found += int(doc["found_true_cause"])
            console.print(f"  v{v} run {i}: score {doc['score']}  found {doc['found_true_cause']}  "
                          f"confident wk {b['true_cause_confident_week']}  {b['points']}  "
                          f"[dim]{llm.stats['calls'] - before} calls · {doc['log_file']}[/]")
        rows.append((v, pb.get("author", "?"), scores, found))

    table = Table(title=f"Scenario {SCENARIO} (held out) · {args.repeats} runs per version", title_justify="left")
    for col, kw in [("Version", {"justify": "right"}), ("Author", {}), ("Mean score", {"justify": "right"}),
                    ("Runs", {}), (f"found_true_cause (x/{args.repeats})", {"justify": "right"})]:
        table.add_column(col, **kw)
    for v, author, scores, found in rows:
        table.add_row(f"v{v}", author, f"{sum(scores) / len(scores):.1f}", ", ".join(f"{s}" for s in scores),
                      f"{found}/{args.repeats}")
    console.print(table)
    console.print(f"[bold]LLM usage:[/] {llm.stats['calls']} calls · tokens in {llm.stats['input_tokens']:,} "
                  f"/ out {llm.stats['output_tokens']:,}")


if __name__ == "__main__":
    main()
