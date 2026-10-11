"""Recursive self-improvement: the agent rewrites its own playbook from its failures.

Starts from the blank v0 playbook. Each iteration: run weeks 2-6, score the run, reflect,
save a new agent-written playbook version, and run again. A candidate is accepted only if
its score does not drop; otherwise it is rejected and the reason is logged.

Usage: python improve.py [--iterations N]
"""
import argparse

from rich.console import Console
from rich.table import Table

import llm
from playbook import GUARDRAILS, ensure_seed_versions, params, get_active_playbook, get_playbook, set_active_version
from reflect import reflect
from run import execute_run

WEEKS = list(range(2, 7))
HUMAN_REFERENCE_VERSION = 2
console = Console()


def run_version(version):
    set_active_version(version)
    calls_before = llm.stats["calls"]
    with console.status(f"Running weeks {WEEKS[0]}–{WEEKS[-1]} with playbook v{version}…"):
        doc = execute_run(WEEKS, echo=False)
    p = doc["score_breakdown"]["points"]
    console.print(f"  v{version} scored [bold]{doc['score']}[/]  "
                  f"(found {doc['found_true_cause']}, {p})  "
                  f"[dim]{llm.stats['calls'] - calls_before} LLM calls · {doc['log_file']}[/]")
    return doc


def _playbook_cell(pb):
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(pb["rules"], 1)) or "(no rules)"
    guards = "\n".join(f"{'✔' if pb['guardrails'].get(g) else '✘'} {g}" for g in GUARDRAILS)
    knobs = "\n".join(f"{n} = {v}" for n, v in params(pb).items())
    return f"{rules}\n\n[bold]Guardrails[/]\n{guards}\n\n[bold]Parameters[/]\n{knobs}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()

    llm.usage_log = lambda msg: None
    ensure_seed_versions()
    original_active = get_active_playbook()["version"]

    console.rule("[bold]Baseline: playbook v0 (no rules, guardrails off)")
    current = get_playbook(0)
    current_run = run_version(0)
    rows = [(0, "human", "baseline: no rules, all guardrails off", current_run["score"],
             current_run["found_true_cause"], "baseline")]
    rejected = None
    reflect_calls = 0

    for i in range(1, args.iterations + 1):
        if current_run["score"] >= 100:
            console.print("[green]Perfect score; stopping early.[/]")
            break
        console.rule(f"[bold]Iteration {i}: reflect on v{current['version']}")
        before = llm.stats["calls"]
        with console.status("Reflecting…"):
            version, proposal = reflect(current_run, current, rejected)
        reflect_calls += llm.stats["calls"] - before
        console.print(f"[bold]Diagnosis:[/] {proposal['diagnosis']}")
        for c in proposal["changes"] or ["(no changes proposed)"]:
            console.print(f"  [cyan]•[/] {c}")
        console.print(f"Saved as playbook v{version} (author: agent, parent v{current['version']})")

        candidate_run = run_version(version)
        changes = "\n".join(proposal["changes"]) or "(none)"
        if candidate_run["score"] >= current_run["score"]:
            outcome = f"accepted ({current_run['score']} → {candidate_run['score']})"
            console.print(f"[green]✔ Accepted v{version}[/]: score {current_run['score']} → {candidate_run['score']}")
            current, current_run, rejected = get_playbook(version), candidate_run, None
        else:
            reason = f"score dropped {current_run['score']} → {candidate_run['score']}"
            outcome = f"rejected: {reason}"
            console.print(f"[red]✘ Rejected v{version}[/]: {reason}; keeping v{current['version']}")
            rejected = {"version": version, "changes": proposal["changes"], "score": candidate_run["score"],
                        "score_breakdown": candidate_run["score_breakdown"]["points"], "why_rejected": reason}
        rows.append((version, "agent", changes, candidate_run["score"], candidate_run["found_true_cause"], outcome))

    set_active_version(original_active)

    console.rule("[bold]Results")
    table = Table(show_lines=True)
    for col, kw in [("Version", {"justify": "right"}), ("Author", {}), ("Changes", {"ratio": 6}),
                    ("Score", {"justify": "right"}), ("found_true_cause", {}), ("Outcome", {"ratio": 2})]:
        table.add_column(col, **kw)
    for v, author, changes, score, found, outcome in rows:
        table.add_row(f"v{v}", author, changes, f"{score}", "[green]yes" if found else "[red]no",
                      f"[green]{outcome}" if outcome.startswith("accepted") else
                      f"[red]{outcome}" if outcome.startswith("rejected") else outcome)
    console.print(table)

    final = current if current["author"] == "agent" else None
    if final is None:
        console.print("[yellow]No agent-written version was accepted; v0 remains the best agent-side playbook.[/]")
    else:
        human = get_playbook(HUMAN_REFERENCE_VERSION)
        compare = Table(show_lines=True, title="Final playbooks")
        compare.add_column(f"v{final['version']} (agent, score {current_run['score']})", ratio=1)
        compare.add_column(f"v{human['version']} (human)", ratio=1)
        compare.add_row(_playbook_cell(final), _playbook_cell(human))
        console.print(compare)

    total = llm.stats
    console.print(f"\n[bold]LLM usage:[/] {total['calls']} calls "
                  f"({total['calls'] - reflect_calls} in runs, {reflect_calls} reflections) · "
                  f"tokens in {total['input_tokens']:,} / out {total['output_tokens']:,}")
    console.print(f"[dim]Active playbook restored to v{original_active}. "
                  f"To adopt the agent's version: playbook.set_active_version(<n>).[/]")


if __name__ == "__main__":
    main()
