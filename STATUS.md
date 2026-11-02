# Axiom: Implementation Status

## Overview

| Milestone | Status | Notes |
|-----------|--------|-------|
| M0 Skeleton | ✅ Done | Repo, models, CLI, config, CI |
| M1 Simulator | ✅ Done | 28 scenarios, ground truth |
| M2 Ingestion + DQ | ✅ Done | Schema diff, checks |
| M3 Detection + Analysis | ✅ Done | Baselines, metric tree, drill-down, tools |
| M4 Investigation Agent | ✅ Live-tested | Live on S11 via an OpenAI-compatible provider; see HANDOFF §10 |
| M5 Critic + Reporting | ✅ Done | Orchestrator, CLI wired, 16 tests pass, ruff clean |
| M6 Baselines + Eval | ✅ Built | Baseline A/No-Critic/Stats-only + `axiom eval`; stats-only run on 28 x 3 seeds, LLM systems not yet benchmarked |
| M7 UI + Scheduling | ✅ Built | Streamlit UI, `axiom daily`, Slack webhook |
| M8 Real Data + Write-up | ⚠️ Partial | Olist adapter + README results/limitations; LLM benchmark rows and a real-Olist run pending |

---

## ✅ Completed

### M0 — Skeleton
- Repo layout per PRD §13
- Pydantic models in `axiom/models/__init__.py`
- Typer CLI stubs
- Config files: `settings.yaml`, `schema.yaml`, `metric_tree.yaml`
- CI workflow (ruff + pytest)
- DECISIONS.md (D1–D25)

### M1 — Simulator
- `axiom/simulator/generator.py` — 180 days of synthetic e-commerce data
- `axiom/simulator/injector.py` — anomaly injection
- **28 scenarios** across 4 YAML files:
  - `business.yaml` (12 scenarios): payment failures, traffic drops, app bugs, stock-outs
  - `data_issues.yaml` (8 scenarios): tracking bugs, schema changes, late data
  - `hard.yaml` (2 scenarios): Simpson's paradox, two-cause days
  - `no_anomaly.yaml` (4 scenarios): normal days + sub-threshold wobble
- `ground_truth.json` with `true_revenue`, `counterfactual_revenue`, `effect_revenue_delta`
- `axiom simulate` CLI command

### M2 — Ingestion + Data Quality
- `axiom/ingestion/__init__.py` — per-day parquet → DuckDB views
- `axiom/quality/__init__.py` — `check_day()` returning DQReport (PASS/WARN/BLOCK)
- Checks: schema diff, row counts, null rates, duplicates, category drift, tracking invariants
- Data-issue scenarios (S13–S20) return BLOCK/WARN correctly

### M3 — Detection + Analysis
- `axiom/detection/__init__.py` — robust z-score detection against weekday/trailing baselines
- `axiom/analysis/`:
  - `cube.py` — metric cube with all dimensions
  - `metric_tree.py` — LMDI decomposition
  - `drilldown.py` — Simpson-safe additive/ratio drill-down
  - `tools.py` — LLM-facing tools (get_metric, decompose_metric, drill_down, etc.)
- `axiom/evidence/__init__.py` — evidence store
- Baseline C (deterministic) hits top-3 on 10/14 single-cause scenarios (71%)

### M4 — Investigation Agent
- `axiom/llm/__init__.py` — Anthropic client with caching + token tally
- `axiom/agents/investigation.py` — LLM tool-use loop, 25-call budget
- `axiom/agents/prompts/investigation.md` — prompt
- `axiom/evidence/verifier.py` — numeric hallucination checker
- **NOT tested against live API** — no `ANTHROPIC_API_KEY` in environment

### M5 — Critic + Reporting + Orchestrator
- `axiom/agents/critic.py` — 6 checks (traffic, mix-shift, data-artifact, calendar, magnitude, LLM verdict)
- `axiom/agents/prompts/critic.md` — Critic prompt
- `axiom/agents/reporting.py` — ReportWriter (Markdown + JSON with evidence citations)
- `axiom/orchestrator.py` — full pipeline: DQ → detect → investigate → critic → report
- `axiom/cli.py` — `axiom run` wired to orchestrator with progress output
- Evidence persisted to `runs/<run_id>/evidence.json`
- **NOT tested against live API** — requires `ANTHROPIC_API_KEY`

---

## ❌ Remaining

| Item | What is needed |
|------|----------------|
| LLM benchmark | Run `axiom eval` for `axiom,no_critic,baseline_a` (needs a provider with enough rate limit; free-tier Dahl was too constrained) and add the rows to the README results table |
| Live Critic | Verify the Critic's real LLM verdict and the revise loop on a live model |
| Real data | Download Olist, run `axiom adapt-olist`, then `axiom daily --data data/olist --day <date>`; DQ thresholds are tuned for the simulator |
| Cost | Set `pricing:` in `config/settings.yaml` to get $ per run (PRD target <= $0.50) |

## Key Invariants (must hold)

1. LLM never computes numbers — Python/DuckDB does
2. Every claim cites an `evidence_id` — verifier drops uncited claims
3. If DQ returns BLOCK → stop, no analysis
4. Critic can veto hypotheses
5. Every run reproducible (seed + cached LLM responses)
6. Contributions sum ≤ 100% (unexplained remainder shown)

---

## Open Decisions (from DECISIONS.md)

| # | Status | Topic |
|---|--------|-------|
| D5 | Unused | — |
| D13–D15 | Unused | — |
| §7 item 10 | ✅ Resolved | Traffic, mix-shift, data-artifact, calendar, magnitude are pure code; alternatives + verdict are LLM |
| §7 item 12 | Open | Baseline A sandboxing |
| §7 item 13 | Open | Cost/pricing (not verified) |
| §7 items 14–15 | Open | Eval matching rules (partial filters, two-cause) |

---

## How to Track Progress

```bash
# Current state
axiom simulate --out data/sim  # Generate data
ruff check .                      # Lint
pytest                            # Tests

# M5 smoke test (after getting API key)
$env:ANTHROPIC_API_KEY = "sk-..."
axiom run --date 2026-04-01    # Should produce report
```

**Definition of Done** (PRD §17):
- `axiom run --date YYYY-MM-DD` produces a report
- `axiom eval` produces full metrics table
- Axiom beats Baseline A on accuracy + hallucination rate
- Streamlit UI shows evidence behind any claim
- README has architecture + results + limitations
