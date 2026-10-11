"""Instant lineage view of the playbook's evolution. Reads MongoDB only; no LLM calls.

Usage: python history.py
"""
from rich.console import Console
from rich.table import Table

from playbook import (GUARDRAILS, _playbook, best_agent_version, ensure_seed_versions, get_playbook,
                      params, version_scores)

HUMAN_REFERENCE_VERSION = 2
console = Console()


def diff(old, new):
    """Lists of (kind, text) describing how `new` differs from `old`."""
    out = []
    for r in new["rules"]:
        if r not in old["rules"]:
            out.append(("+ rule", r))
    for r in old["rules"]:
        if r not in new["rules"]:
            out.append(("- rule", r))
    for g in GUARDRAILS:
        before, after = old["guardrails"].get(g, False), new["guardrails"].get(g, False)
        if before != after:
            out.append(("guardrail", f"{g}: {'on' if before else 'off'} → {'on' if after else 'off'}"))
    p_old, p_new = params(old), params(new)
    for n in p_new:
        if p_old[n] != p_new[n]:
            out.append(("parameter", f"{n}: {p_old[n]} → {p_new[n]}"))
    return out


def _summary(pb, by_version):
    parent = by_version.get(pb.get("parent_version"))
    if parent is None:
        return pb.get("note", "")
    d = diff(parent, pb)
    if not d:
        return "(no changes)"
    counts = {}
    for kind, _ in d:
        counts[kind] = counts.get(kind, 0) + 1
    parts = [f"{k[0]}{counts[k]} rule{'s' if counts[k] > 1 else ''}" for k in ("+ rule", "- rule") if k in counts]
    parts += [text for kind, text in d if kind in ("guardrail", "parameter")]
    return "; ".join(parts)


def main():
    ensure_seed_versions()
    playbooks = list(_playbook().find().sort("version", 1))
    by_version = {p["version"]: p for p in playbooks}
    scores = version_scores()
    best = best_agent_version()

    table = Table(title="Playbook lineage", title_justify="left", show_lines=True)
    for col, kw in [("Version", {"justify": "right"}), ("Author", {}), ("Parent", {"justify": "right"}),
                    ("Score", {"justify": "right"}), ("found_true_cause", {}), ("Changes vs parent", {"ratio": 1})]:
        table.add_column(col, **kw)
    for pb in playbooks:
        run = scores.get(pb["version"])
        v = pb["version"]
        label = f"v{v}" + (" ★" if v == best else "") + (" (active)" if pb.get("active") else "")
        table.add_row(
            label,
            f"[cyan]{pb.get('author')}" if pb.get("author") == "agent" else pb.get("author", "?"),
            "–" if pb.get("parent_version") is None else f"v{pb['parent_version']}",
            "–" if run is None else f"{run['score']}",
            "–" if run is None else ("[green]yes" if run["found_true_cause"] else "[red]no"),
            _summary(pb, by_version),
            style="bold" if v == best else "",
        )
    console.print(table)
    console.print("[dim]Score = latest scored run of that version. ★ = best agent version.[/]\n")

    if best is None:
        console.print("[yellow]No scored agent-written versions yet. Run improve.py.[/]")
        return

    d = diff(get_playbook(0), get_playbook(best))
    t = Table(title=f"What the agent learned: v0 → v{best}", title_justify="left", show_lines=True)
    t.add_column("Change")
    t.add_column("Detail", ratio=1)
    style = {"+ rule": "green", "- rule": "red", "guardrail": "yellow", "parameter": "magenta"}
    for kind, text in d:
        t.add_row(f"[{style[kind]}]{kind}", text)
    console.print(t)

    human = scores.get(HUMAN_REFERENCE_VERSION)
    agent = scores[best]
    cmp = Table(title="Agent vs human playbook", title_justify="left")
    cmp.add_column("Score component")
    cmp.add_column(f"v{best} (agent)", justify="right")
    cmp.add_column(f"v{HUMAN_REFERENCE_VERSION} (human)", justify="right")
    for k, v in agent["score_breakdown"]["points"].items():
        cmp.add_row(k, f"{v}", "–" if human is None else f"{human['score_breakdown']['points'][k]}")
    cmp.add_row("[bold]total", f"[bold]{agent['score']}", "–" if human is None else f"[bold]{human['score']}")
    console.print(cmp)
    if human is None:
        console.print(f"[dim]v{HUMAN_REFERENCE_VERSION} has no scored run yet.[/]")


if __name__ == "__main__":
    main()
