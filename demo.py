"""Live demo: watch the agent's beliefs evolve week by week.

Usage: python demo.py                 # best-scoring agent playbook, pause between weeks
       python demo.py --no-pause
       python demo.py --playbook 2      # run with a specific playbook version
"""
import argparse

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import llm
from score import score_run
from agent import found_true_cause, process_week
from memory import get_beliefs, reset_memory
from playbook import best_agent_version, get_active_playbook, params, set_active_version
from summary import REGION, summarize

WEEKS = range(2, 7)
console = Console()

ACTION_STYLE = {"REINFORCE": "green", "CONTRADICT": "red", "SUPERSEDE": "bold magenta", "NO_CHANGE": "dim"}
STATUS_STYLE = {"active": "green", "contradicted": "red", "superseded": "magenta"}


def _series(points):
    return "  ".join(f"wk{p['week']} {p['value']:,}" if not isinstance(p["value"], bool)
                     else f"wk{p['week']} {'yes' if p['value'] else 'no'}" for p in points)


def render_revenue(summary, threshold_pct):
    table = Table(title="Revenue", title_justify="left", box=None, padding=(0, 2))
    table.add_column("Region")
    table.add_column("Revenue", justify="right")
    table.add_column("WoW", justify="right")
    for s in summary:
        pct = s["pct_change"]
        flagged = pct is not None and pct < threshold_pct
        table.add_row(s["region"], f"{s['revenue']:,.0f}",
                      Text(f"{pct:+.1f}%" if pct is not None else "n/a", style="bold red" if flagged else ""),
                      style="bold" if flagged else "dim")
    console.print(table)


def render_events(events):
    for e in events:
        if e["type"] == "intervention":
            console.print(f"[yellow]⚑ Intervention recorded:[/] {e['description']}")
        elif e["type"] == "flag":
            console.print(f"\n[bold red]▼ {e['region']} flagged[/] (revenue {e['pct_change']:+.1f}% WoW)")
        elif e["type"] == "hypothesis":
            console.print(f"  [cyan]+ hypothesis[/] [dim]\\[{e['metric']}][/] {e['claim']}")
        elif e["type"] == "decision":
            style = ACTION_STYLE.get(e["action"], "")
            console.print(f"\n  [bold]{e['claim']}[/]")
            console.print(f"    [dim]{e['metric']}:[/] {_series(e['series'])}")
            line = f"    [{style}]{e['action']}[/]  {e['old_confidence']:.2f} → {e['new_confidence']:.2f}"
            if e.get("new_claim"):
                line += f"\n    [bold magenta]↳ new belief[/] [dim]\\[{e['new_metric']}][/] {e['new_claim']}"
            console.print(line)
            console.print(f"    [dim italic]{e['reason']}[/]")


def render_beliefs(week, previous):
    """Print the belief table; return a snapshot to diff against next week."""
    table = Table(title=f"Beliefs after week {week}", title_justify="left", show_lines=True)
    table.add_column("Claim", ratio=5)
    table.add_column("Metric", ratio=2)
    table.add_column("Status", ratio=2)
    table.add_column("Conf", justify="right", ratio=1)
    table.add_column("Change since last week", ratio=3)

    snapshot = {}
    for b in get_beliefs():
        key = str(b["_id"])
        snapshot[key] = (b["status"], b["confidence"])
        before = previous.get(key)
        closed = next((h["week"] for h in b["history"] if h["new_status"] != "active"), None)
        row_style = "" if closed is None else "dim"
        if before is None:
            change = Text("NEW", style="bold cyan")
            row_style = "on grey11"
        elif before[0] != b["status"]:
            change = Text(f"{before[0].upper()} → {b['status'].upper()}", style="bold white on dark_magenta")
            row_style = "on grey11"
        elif before[1] != b["confidence"]:
            delta = b["confidence"] - before[1]
            change = Text(f"{delta:+.2f}", style="green" if delta > 0 else "red")
        else:
            change = Text("–", style="dim")
        status = Text(b["status"], style=STATUS_STYLE.get(b["status"], ""))
        if closed is not None:
            status.append(f"\nclosed wk {closed}", style="dim")
        table.add_row(b["claim"], b["metric"], status,
                      f"{b['confidence']:.2f}", change, style=row_style)
    console.print(table)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-pause", action="store_true", help="run straight through without waiting for Enter")
    parser.add_argument("--playbook", type=int, default=None,
                        help="playbook version to use (default: best-scoring agent version)")
    args = parser.parse_args()

    previous_active = get_active_playbook()["version"]
    version = args.playbook if args.playbook is not None else best_agent_version()
    if version is not None:
        set_active_version(version)
    try:
        run_demo(args)
    finally:
        set_active_version(previous_active)


def run_demo(args):
    llm.usage_log = lambda msg: None  # keep the demo output clean; totals are shown at the end
    playbook = get_active_playbook()
    threshold_pct = -100 * params(playbook)["drop_threshold"]
    console.print(Panel.fit(
        f"[bold]Longview[/]: an analyst agent with memory\n"
        f"[dim]Playbook v{playbook['version']} ({playbook.get('author', '?')}) · "
        f"{len(playbook['rules'])} rules · model {llm.MODEL}[/]",
        border_style="blue"))
    reset_memory()
    console.print("[dim]Memory reset.[/]")

    snapshot = {}
    for week in WEEKS:
        console.rule(f"[bold]Week {week}")
        events = process_week(week, verbose=False)
        render_revenue(next(e["regions"] for e in events if e["type"] == "summary"), threshold_pct)
        render_events(events)
        console.print()
        snapshot = render_beliefs(week, snapshot)
        if not args.no_pause and week != WEEKS[-1]:
            console.input(f"[dim]Press Enter for week {week + 1}…[/]")

    last = WEEKS[-1]
    console.rule(f"[bold]Week {last} executive summary: {REGION}")
    with console.status("Writing summaries…"):
        without = summarize(last, use_memory=False)
        with_mem = summarize(last, use_memory=True)
    side_by_side = Table.grid(expand=True, padding=(0, 1))
    side_by_side.add_column(ratio=1)
    side_by_side.add_column(ratio=1)
    side_by_side.add_row(
        Panel(without, title="Without memory", subtitle="this week's numbers only", border_style="red"),
        Panel(with_mem, title="With memory", subtitle="trend + beliefs + interventions", border_style="green"),
    )
    console.print(side_by_side)

    found = found_true_cause()
    llm.track_run_scored(playbook["version"], score_run(WEEKS[-1]), source="demo")
    console.print(Panel.fit(
        f"[bold]found_true_cause: {'[green]yes' if found else '[red]no'}[/][/]\n"
        f"[dim]Active {REGION} belief on distribution_points with confidence ≥ 0.7[/]",
        border_style="green" if found else "red"))
    console.print(f"[dim]LLM calls: {llm.stats['calls']} · tokens in {llm.stats['input_tokens']:,} "
                  f"/ out {llm.stats['output_tokens']:,}[/]")


if __name__ == "__main__":
    main()
