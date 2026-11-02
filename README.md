# Axiom

Autonomous root-cause analysis for e-commerce business metrics.

Dashboards tell you *that* revenue dropped. Axiom works out *why*. For each day of event data it
checks data quality, detects which KPIs moved significantly, decomposes the metric tree, drills
through dimensions (platform, country, payment method, app version, ...) and produces
evidence-backed hypotheses about the cause.

> "Something changed. Figure out what happened, why it happened, and tell me whether I need to act."

**Status:** M0–M5 complete. M4 investigation agent verified live on S11 (OpenAI-compatible provider, see HANDOFF §10). `axiom run` is wired to the full pipeline. See [Team](#team) for who owns what, [HANDOFF.md](HANDOFF.md) for detailed status and [PRD.md](PRD.md) for the full spec.

## How it works

```
Ingestion → Data Quality (PASS/WARN/BLOCK) → Detection → Investigation (LLM + tools)
   → Critic (≤2 revise loops) → Reporting
```

The design rule is that the deterministic core computes the numbers and the LLM only reasons and
writes:

1. The LLM never computes numbers. Python/DuckDB does.
2. Every claim cites an `evidence_id`. A claim with no citation, or with a number that can't be
   traced to its cited evidence, is dropped ([axiom/evidence/verifier.py](axiom/evidence/verifier.py)).
3. If data quality is `BLOCK`, there is no analysis, only a data-quality alert.
4. The Critic can veto a hypothesis; a `revise` verdict sends the hypothesis back to investigation (max 2 loops).
5. Runs are reproducible: seeded data and cached LLM responses (temperature 0).
6. Contributions add up to at most 100%, and the unexplained remainder is shown.

Axiom ships with its own benchmark: a simulator that generates synthetic sessions, events and
orders with **28 injected scenarios** (payment failures, traffic drops, app-version bugs, stock-outs,
Simpson's-paradox mix shifts, two-cause days, tracking bugs, schema changes, late data, and
no-anomaly controls) plus a `ground_truth.json` to score against.

## Quick start

Requires Python 3.11+.

```bash
pip install -e .[dev]

# Generate 180 days of synthetic data (~15 s, ~90 MB) into data/sim/
axiom simulate --out data/sim

# Lint and test (the data-dependent tests skip if data/sim is missing)
ruff check .
pytest
```

`simulate` options: `--days`, `--sessions` (per day, default 10k), `--seed`, `--no-plot`. It
writes one parquet file per table per day, `ground_truth.json` and a `revenue.png` with the
scenario days marked.

### Commands

```bash
axiom run 2026-04-01                       # one day: DQ -> detect -> investigate -> critic -> report
axiom eval --systems stats_only --seeds 42,43,44          # offline benchmark, no LLM
axiom eval --scenarios S07,S11 --systems axiom,no_critic,baseline_a   # needs an LLM provider
axiom ui                                   # Streamlit: runs, reports, evidence behind each claim, eval dashboard
axiom daily --data data/sim                # scheduled run for yesterday; Slack ping if AXIOM_SLACK_WEBHOOK is set
axiom adapt-olist path/to/olist_csvs       # public dataset -> same parquet layout (data/olist)
```

Schedule `axiom daily` with cron (`0 7 * * *`) or Windows Task Scheduler.

### LLM provider

`config/settings.yaml` picks the model. With no `provider:` key it uses the Anthropic API
(`ANTHROPIC_API_KEY`). With `provider: {base_url, api_key_env}` it talks to any OpenAI-compatible
endpoint through [axiom/llm/openai_compat.py](axiom/llm/openai_compat.py); the key is read from the
named environment variable and never stored in the repo.

### Using the pieces from Python

```python
from datetime import date
from pathlib import Path

from axiom.quality import check_day
from axiom.detection import detect
from axiom.analysis.tools import Tools

data, day = Path("data/sim"), date(2026, 4, 1)   # S11: electronics stock-out
print(check_day(data, day).status)                # PASS / WARN / BLOCK

tools = Tools(data)
anomalies = detect(data, day, tools.store)        # KPIs that moved, with evidence ids
print(tools.find_causes(str(day)))                # deterministic tree walk + drill-down (Baseline C)
```

To try the LLM investigation agent, set `ANTHROPIC_API_KEY` and call
`axiom.agents.investigation.investigate(tools, LLMClient(), day, anomalies)`. The model is set in
[config/settings.yaml](config/settings.yaml).

## Project layout

| Path | What it is |
|---|---|
| `axiom/simulator/` | Synthetic data generator and scenario injector |
| `axiom/ingestion/` | Per-day parquet files → DuckDB views |
| `axiom/quality/` | Data-quality checks: schema diff, volume, nulls, duplicates, category drift, tracking invariants |
| `axiom/detection/` | Robust-z change detection against weekday and trailing-7-day baselines |
| `axiom/analysis/` | Metric cube, metric-tree decomposition (LMDI), Simpson-safe drill-down, LLM-facing tools |
| `axiom/evidence/` | Evidence store and numeric-hallucination verifier |
| `axiom/llm/` | Anthropic client with a deterministic response cache and token tally |
| `axiom/agents/` | Investigation agent (tool-use loop) and its prompt |
| `config/` | Settings, registered schema, metric tree |
| `scenarios/` | Scenario definitions (YAML) |
| `tests/` | Pytest suite |

`axiom/api`, `baselines`, `eval` and `ui` are placeholders for later milestones.

## Results

Benchmark of the deterministic baseline (**Baseline C / `stats_only`**: DQ gate + detection + tree walk + drill-down,
no LLM) on all 28 scenarios x 3 seeds (84 runs, `eval/results/stats_only_3seeds/`). Match rule: the predicted filter
set must contain every true (dimension, value) pair ([D27](DECISIONS.md)).

| Metric | stats_only |
|---|---|
| Detection precision / recall | 0.85 / 0.83 |
| False-positive rate (no-anomaly days) | 0.08 |
| Root-cause accuracy@1 / @3 (strict) | 0.50 / 0.50 |
| Lenient overlap@3 (any true pair found) | 0.75 |
| Both causes found (two-cause scenarios) | 0.00 |
| Contribution error | 37.9 pp |
| Data-issue recall / false flags | 1.00 / 0.00 |
| Latency (median) | 0.9 s |

**Axiom, No-Critic and Baseline A rows are still empty.** The harness and all three systems are built and
unit-tested with a scripted LLM, but the only LLM available during development was a rate-limited free tier,
which could not complete enough runs to report. Run
`axiom eval --systems axiom,no_critic,baseline_a,stats_only --seeds 42,43,44` with a provider that has
capacity and paste the table here. Until then there is no evidence that Axiom beats Baseline A.

## Limitations

- **No benchmark of the LLM systems yet** (above). The only live evidence is S11 on 2026-04-01: DeepSeek-V4-Flash
  named `category=electronics`, matching the ground truth, with all cited numbers verified. MiniMax-M2.7 found the
  same cause but its hypothesis was dropped by the numeric verifier.
- **The Critic's real LLM verdict was never confirmed live**; when the model returns no verdict the hypothesis is
  kept but flagged `UNREVIEWED` with confidence capped at 0.3.
- **Synthetic data.** All numbers above are on the simulator, whose scenarios the detector was tuned against.
  Scenarios with a true effect under ~6% (S02, S07, S08, S12) sit below the revenue noise floor ([D20](DECISIONS.md)).
- **Stats-only misses two-dimension causes** (S01, S05) and second causes (S23); the strict metric reflects that.
- **Contribution error is approximate** ([D30](DECISIONS.md)); per-segment ground truth is not stored.
- **Olist adapter** has no sessions or funnel data, so traffic and conversion metrics are degenerate. It was tested on
  a tiny fixture, not on the real download, and DQ thresholds are tuned for the simulator.
- **Baseline A sandbox** has no network or memory limit on Windows ([D29](DECISIONS.md)).
- `config/` is read relative to the source tree, so Axiom only works from a checkout or editable install.

## Team

### Done so far

- **M0** Skeleton: repo layout, Pydantic models, CLI, config, CI
- **M1** Simulator: data generator, scenario injector, 28 scenarios, ground truth
- **M2** Ingestion and data-quality checks
- **M3** Detection, metric-tree decomposition, drill-down, analysis tools, evidence store
- **M4** LLM client and investigation agent with the numeric verifier (tested against a scripted fake LLM)
- **M5** Critic agent (6 checks), report writer (Markdown + JSON), orchestrator with revise loop, `axiom run` wired
- **M6** Baseline A (sandboxed pandas agent), No-Critic ablation, stats-only baseline, `axiom eval` harness
- **M7** Streamlit UI, `axiom daily` scheduler entry point, Slack webhook
- **M8 (partial)** Olist adapter, results and limitations write-up

### Remaining work

| Item | Scope |
|---|---|
| LLM benchmark | Run `axiom eval` for the three LLM systems and fill the results table; confirm the Critic live |
| Real data | Run the Olist adapter on the real download and review DQ thresholds |
| Cost | Set `pricing:` in `config/settings.yaml`; check the <= $0.50/run target |

Design decisions and their reasoning are logged in [DECISIONS.md](DECISIONS.md).
