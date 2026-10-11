# Longview

**An analyst agent with memory that also rewrites its own playbook.**

Longview reads weekly sales data, forms hypotheses about why revenue moved, and keeps them in MongoDB as *beliefs* with confidence and a full history. When new data arrives, each belief is reinforced, contradicted or superseded. A self-improvement loop scores every run with hard metrics and lets the agent revise the rules it follows.

**The problem:** agents that summarize recurring reports forget everything between runs. Each week they start over: they rediscover the same red herrings, can't tell a one-off dip from a trend, and can't say *"we thought it was pricing, but the data ruled that out in week 3."*

---

## How it works

### Weekly loop (`agent.py`)

```
            weekly_sales (MongoDB)
                    │
                    ▼
 ┌──────────────────────────────────────┐
 │ 1. FLAG   region revenue fell > X%   │  X = playbook.parameters.drop_threshold
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐    no active beliefs?
 │ 2. HYPOTHESIZE  (LLM)                │◄── propose 2–3 causes, each
 │    each names one driver metric      │    naming a metric to test
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ 3. TEST against MongoDB aggregations │  metric series + revenue +
 │    (LLM judges claim vs. numbers)    │  interventions + belief history
 └──────────────────┬───────────────────┘
                    ▼
 ┌──────────────────────────────────────┐
 │ 4. UPDATE belief                     │  REINFORCE   → confidence ↑
 │    every change appended to history  │  CONTRADICT  → confidence ↓
 └──────────────────┬───────────────────┘  SUPERSEDE   → replaced, linked
                    ▼                       NO_CHANGE
              beliefs (MongoDB) ──► next week starts from here
```

### Self-improvement loop (`improve.py`)

```
 playbook vN ──► RUN weeks 2–6 ──► SCORE (0–100, hard metrics, no LLM)
      ▲                                   │
      │                                   ▼
      │                 REFLECT (LLM) using:
      │                   • score breakdown
      │                   • belief histories
      │                   • the run's Raindrop events (Query API)
      │                                   │
      │                                   ▼
      │                 new playbook vN+1 (author: agent, parent: vN)
      │                   rules ± · guardrails on/off · parameters (clamped)
      │                   rules naming a region or metric+threshold → dropped
      │                                   │
      │                                   ▼
      └── accept only if score is STRICTLY higher, else keep parent and log why
```

**Score (`score.py`, computed from stored beliefs + data):**

| Points | Criterion |
|---:|---|
| 35 | True cause found (active West belief on `distribution_points`, confidence ≥ 0.7) |
| 25 | Timeliness: full marks if confidence ≥ 0.7 is reached the week *after* the cause first appears in the data |
| 20 | −10 per wrong belief still active at the end |
| 10 | −5 per belief that tests a symptom (`units`, `revenue`) |
| 10 | −5 per duplicate belief (same region and metric as another active one) |

---

## MongoDB collections (database `longview`)

| Collection | Stores |
|---|---|
| `weekly_sales` | Synthetic sales: week, region, product, revenue, units, avg_price, distribution_points, promo_active |
| `beliefs` | Claim, metric, status (`active`/`contradicted`/`superseded`), confidence, evidence list, **full change history**, `superseded_by`/`supersedes` links |
| `interventions` | Real-world actions per region and week (e.g. the West promo) |
| `playbook` | Versioned playbooks: rules, guardrail flags, parameters, author (`human`/`agent`), parent version, change log, accepted flag |
| `runs` | One document per run: playbook version, final beliefs with histories, score + breakdown, `found_true_cause`, session id, log file |

---

## Results

**The planted story:** West raises prices about 8% in weeks 2–3, so revenue falls and pricing looks guilty. Stores start disappearing from week 3. Price returns to baseline in week 4 and a promo runs, but revenue keeps falling. The real cause is distribution loss.

### v0 (blank playbook) → agent-written versions

| Improve loop | v0 score | Agent versions | Best agent score |
|---|---:|---|---:|
| 1. Rules + guardrails | 80.0 | v3 → v4 → v5 | 100 |
| 2. + tunable parameters | 70.0 | v6 → v7 → v8 | 100 |
| 3. + strict acceptance | 70.0 | v9 | 100 *(later excluded: rule named a metric + threshold)* |
| 4. + Raindrop evidence, rule filters | 65.0 | v10 | 91.7 |
| *Human-written v2, for reference* | | | *91.7* |

Loops 1–2 are shown rescored under the current timeliness rule; see Limitations. `python history.py` prints the live lineage.

v0 usually *does* find the cause; it loses points for clutter. It tests symptoms (`units`), opens duplicate beliefs, and leaves wrong beliefs active. Each time, the first reflection fixes that by writing rules and turning on both guardrails.

### Agent playbook (v8, default for the demo) vs. human-written v2

| | **v8 (agent)**, score 100 | **v2 (human)**, score 91.7 |
|---|---|---|
| Rules | 1. Only causal drivers; revenue and units are outcomes. 2. Reuse an existing belief on the same region and metric instead of duplicating it. 3. Compare each driver's weekly change with revenue's; a driver that stops moving while revenue keeps falling can't explain the decline. 4. A single-week match is tentative until the driver keeps moving. 5. Re-test rejected drivers each week; reinforce decisively on a large move. 6. A replacement belief must test a different causal metric. | 1. Hypotheses name a driver metric, never revenue or units. 2. Judge a belief against its week-1 baseline; replace it only once its own metric no longer supports it. 3. No duplicate beliefs per region and metric. |
| Guardrails | both on | both on |
| Parameters | initial 0.65 · boost 0.30 · penalty 0.4 · flag at 3% | initial 0.5 · boost 0.15 · penalty 0.4 · flag at 5% |
| Behavior | Drops pricing in **week 3** and is confident in distribution by **week 4** | Holds pricing through week 3 and is confident in distribution in **week 5** |

### Same week-6 summary, memory off vs. on

> **Without memory** (this week's numbers only): *"…Without prior-week or target figures, it is unclear whether this is an increase or decline, so the cause of any performance change cannot be determined from the data provided."*

> **With memory** (trend + beliefs + interventions): *"West revenue fell for the sixth straight week, from $57,960 to $30,119 (about -48%)… The most likely cause is distribution point losses, which dropped from 950 to 497… average price was roughly flat… the week 4 promo gave no lasting lift. Note that the early price-based and promo-based explanations were retired… Next step: find out why West is losing distribution points…"*

---

## Generalization (held-out scenarios B and C)

Playbooks were learned and tuned on scenario A only. To check whether they carry over, `evaluate.py` runs them frozen (no reflection) on **scenarios B and C**, which they never saw, in a separate database (`longview_holdout`).

| Version | Author | Scenario B mean | Scenario C mean | found_true_cause (B + C) |
|---|---|---:|---:|---:|
| v0 | human (blank) | 65.0 | 80.0 | 4/4 |
| v2 | human | 100.0 | **91.7** | 4/4 |
| v10 | agent | 100.0 | 87.5 | 4/4 |

**On the held-out scenarios, the agent-written v10 did no better than the human v2, and slightly worse on C.**

### Scenario B: promo ends

**Scenario B:** South runs a long promo in weeks 1–2. It ends in week 3 and promo-driven demand unwinds through week 6, so revenue falls −9%, −12%, −8% and −7% week over week. **True cause: `promo_active`.** There's a one-week distribution dip (about −5%) in week 4 that recovers in week 5, as a red herring. Price is flat and other regions are stable. The true cause is read from the scenario config, not hard-coded.

| Version | Author | Mean score | Runs | found_true_cause |
|---|---|---:|---|---:|
| v0 | human (blank) | 65.0 | 65.0, 65.0 | 2/2 |
| v2 | human | 100.0 | 100.0, 100.0 | 2/2 |
| v10 | agent | 100.0 | 100.0, 100.0 | 2/2 |

*54 LLM calls in total. Output: `notes/holdout_scenario_b.txt`.*

**What this shows, and what it doesn't:**
- **The agent's rules carried over without hurting.** v10's playbook, written by the agent from scenario A runs, scored 100 on a different region, cause and red herring, the same as the human v2. One worry was that v10's rule "a lever that stayed flat cannot be the cause" would wrongly reject the promo after it ended, because promo_active stays flat (off) in weeks 4–6. It didn't.
- **v10 was faster, but the score can't show it.** v10 was confident in week 3, the week the promo ended; v2 got there in week 4. Both earn full timeliness points, so the faster reaction isn't rewarded.
- **v0 still finds the cause but clutters memory,** just as in scenario A: it tests symptoms, leaves wrong beliefs active and creates a duplicate. The gap between v0 and the guarded playbooks is the same kind of gain as in training.
- **Scenario B is easier than A.** There's no misleading first guess backed by a moving metric: the promo is the only driver that changes when the decline starts, and the distribution dip arrives after the promo belief already exists. This test checks that the playbooks don't overfit. It isn't a hard test of reasoning.
### Scenario C: a misleading first guess, then a price rise

**Scenario C:** East's store count dips about 12% in weeks 2–3 (947 → 837) and recovers in week 4 (951). That's the misleading first guess. avg_price rises about 9% from week 3 (2.84 → 3.08) and stays up, and customers drift away over the following weeks. **True cause: `avg_price`.** No promo runs. Revenue falls −13%, −5%, −11%, −10% and −11% week over week, so the decline continues after the stores come back.

| Version | Author | Mean score | Runs | found_true_cause | Confident in price |
|---|---|---:|---|---:|---|
| v0 | human (blank) | 80.0 | 85.0, 75.0 | 2/2 | week 4, week 4 |
| v2 | human | 91.7 | 91.7, 91.7 | 2/2 | week 5, week 5 |
| v10 | agent | **87.5** | 91.7, 83.3 | 2/2 | week 5, week 6 |

*64 LLM calls in total. Output: `notes/holdout_scenario_c.txt`.*

**What happened (from the run logs):**
- **v2 and v10 behaved the same at first.** In week 2 they correctly rejected price (flat then) and committed to distribution. In week 3 price jumped, but they kept distribution because the stores were still about 11% below week 1. Both switched to price only in **week 4**, once the stores had recovered. Their one-belief-per-metric discipline, plus "judge a belief on its own metric", makes them slow to drop a first guess that is still partly true.
- **v10 didn't apply its own rule.** Its rule "a lever that stayed flat cannot be the cause" should have fired in week 3: the stores were flat from week 2 (837 → 843) while revenue fell. It reinforced distribution instead. In one run it also made a NO_CHANGE in week 5, so it became confident a week later (83.3).
- **v0 was the fastest to find price.** With no duplicate guard it carried several parallel beliefs, and one of them switched to price in **week 3**. It still lost points for clutter: symptom beliefs, duplicates, and in one run a wrong belief left active.
- **The trade-off the guardrails make:** a cleaner memory, but a slower switch when the first guess is still partly true. The self-improvement loop can't see this, because it trained only on scenario A, where the guardrails cost nothing.

### Learning from scenario C (v10 → v11)

One improve iteration started from v10, ran on scenario C in `longview_holdout`. **The agent accepted a new version, v11** (91.7 → 100 on C).

What v11 changed, in the agent's words:
- **Added:** "Each week, check every causal lever for movement, not only the one already believed. When a different lever changes during an ongoing decline, create a hypothesis on it immediately instead of waiting for the current explanation to fail."
- **Added:** "A single lever can explain only the part of the decline that it coincides with. If the decline continues or deepens after that lever stabilizes or recovers, treat the lever as incomplete and look for another that moved later."
- **Edited** the causal-judgment rule to also check the *direction* of a lever's change.
- **Parameters:** reinforce_boost 0.2 → 0.15, drop_threshold 0.05 → 0.04.

Its diagnosis matches what I found in the logs by hand: it had reinforced distribution through week 3 even though price had moved.

| Check | Run | Score | Confident in true cause |
|---|---|---:|---|
| v10 on C (baseline) | 1 | 91.7 | week 5 |
| v11 on C | 1 | 100.0 | week 4 |
| v11 on A (regression check) | 1 | 100.0 | week 4 (superseded pricing in week 3) |

*25 LLM calls (17 for the improve iteration, 8 for the A check). Output: `notes/improve_holdout_c_from_v10.txt` and the run logs in `runs/`.*

**Read this carefully:**
- **C is no longer held out for v11.** It was trained on C, so 100 on C shows that the loop can fix a failure it observed. It doesn't show generalization. A fresh scenario would be needed for that.
- **No regression on A, from one run.** v11 scored 100 on A. That's not proof it's better than v10 there: v10 has scored both 91.7 and 100 on A across runs.
- **Only part of the Raindrop evidence arrived.** The reflector received 3 of the run's 8 events, probably because the rest hadn't been processed yet when it queried.
- **v11 exists only in `longview_holdout`.** It isn't in the demo database's playbook collection, and the demo still uses the main database's versions.

### Caveats
- **Small sample:** 2 runs per version per held-out scenario; v10's 4.2-point gap on C comes from a single run.
- **Hand-designed scenarios:** both were written by me. B turned out to be easy (no misleading guess backed by a moving metric). C was harder and separated the playbooks.
- **Configured cause week:** each scenario records the week its cause first appears (`cause_first_week`), and timeliness uses that. It's configured rather than detected because avg_price carries about ±1% noise every week.

Run it with `python evaluate.py --scenario B` or `--scenario C`. It writes only to `longview_holdout` and refuses to run against `longview`.

---

## Raindrop integration

**What's traced (`llm.py`):** each LLM call becomes one Raindrop event. It records the real prompt and reply, the model, token counts, stop reason, whether the JSON retry fired, and any error.

| Event | Source |
|---|---|
| `hypothesis_generation` | agent proposes causes |
| `belief_decision` | REINFORCE / CONTRADICT / SUPERSEDE / NO_CHANGE |
| `executive_summary` | week summary with or without memory |
| `playbook_reflection` | reflector proposes a new playbook |
| `run_scored` | end of every run and demo: score, each point component, `found_true_cause` |

- **User** is `playbook-v<N>`, so Raindrop's per-user views compare playbook versions.
- **Thread:** every call made by one process (a run, the demo, an improve loop) shares a `session-…` thread id.

**Read-back into reflection (`raindrop_query.py`):** before reflecting, the loop calls the Raindrop **Query API** (`GET https://query.raindrop.ai/v1/events`, plain HTTP, filtered by user, thread and run start time). It then adds a compact summary to the reflector's evidence: event counts, token totals, JSON retries, errors, signals, and raw decision outputs. Without `RAINDROP_QUERY_API_KEY` this step is skipped and the reflector says why.

---

## Honest limitations

- **Synthetic data, one planted scenario.** Six weeks, four regions, three products and one designed failure mode. The scorer knows the planted answer; real data has no ground truth.
- **Run-to-run variance.** LLM claims and decisions vary; blank-slate v0 scored between 65 and 80 across four runs. Each candidate version is accepted on a single run.
- **Human playbooks v1/v2** were written by me with AI coding assistance. They aren't independent domain-expert baselines.
- **The scoring metric was revised once.** Timeliness first required confidence in the *same* week the cause appeared. In scenario A that couldn't happen, because the agent reaches the cause by superseding the pricing belief, and a replacement belief is first tested the following week. I first wrote that same-week confidence was impossible in general. That was wrong: the held-out test showed a brand-new hypothesis can reach 0.7 the week it's created. Timeliness now targets the following week, and same-week confidence still earns full marks. Stored runs were rescored exactly from their saved inputs.
- **The reflector can infer the answer** from belief histories, where metric names are visible. Filters drop rules that name a region or pair a metric with a numeric threshold. These are pattern-based heuristics, not proof of generality.
- **Parameter drift.** Before strict acceptance was added, tied scores let parameters creep to their clamp limits (v8: initial 0.65, boost 0.30).
- **Older Raindrop SDK.** Python 3.9 limits `raindrop-ai` to 0.0.19, which has one event per call and no tracing spans.

---

## How to run it

**Requirements:** Python 3.9+, a MongoDB Atlas cluster, an Anthropic API key, and optionally Raindrop.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the values
```

`.env.example`:

```
MONGODB_URI=
ANTHROPIC_API_KEY=
RAINDROP_WRITE_KEY=
RAINDROP_QUERY_API_KEY=
```

`LLM_MODEL` is optional and defaults to `claude-sonnet-5-5`.

```bash
python generate_data.py              # load the synthetic dataset (fixed seed)
python demo.py                       # live demo, Enter between weeks (best eligible agent playbook)
python demo.py --no-pause --playbook 2   # straight through, with the human-written v2
python history.py                    # instant lineage view, no LLM calls
python improve.py --iterations 3     # self-improvement loop from v0 (~25–45 LLM calls)
python run.py                        # one scored run with the active playbook (log in runs/)
```

A demo makes about 10 LLM calls.

---

Built solo during the **MongoDB Harness Engineering Hackathon, SF Tech Week, Oct 11 2026**, with Claude Code as a coding assistant.
