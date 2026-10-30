# Sentinel: Implementation Status

## Overview

| Milestone | Status | Notes |
|-----------|--------|-------|
| M0 Skeleton | ✅ Done | Repo, models, CLI, config, CI |
| M1 Simulator | ✅ Done | 28 scenarios, ground truth |
| M2 Ingestion + DQ | ✅ Done | Schema diff, checks |
| M3 Detection + Analysis | ✅ Done | Baselines, metric tree, drill-down, tools |
| M4 Investigation Agent | ✅ Live-tested | Live on S11 via Dahl (DeepSeek found electronics); see HANDOFF §10 |
| M5 Critic + Reporting | ✅ Done | Orchestrator, CLI wired, 16 tests pass, ruff clean |
| M6 Baselines + Eval | ❌ TODO | Baseline A, No-Critic, eval harness |
| M7 UI + Scheduling | ❌ TODO | Streamlit, scheduler |
| M8 Real Data + Write-up | ❌ TODO | Public dataset adapter, README final |

---

## ✅ Completed

### M0 — Skeleton
- Repo layout per PRD §13
- Pydantic models in `sentinel/models/__init__.py`
- Typer CLI stubs
- Config files: `settings.yaml`, `schema.yaml`, `metric_tree.yaml`
- CI workflow (ruff + pytest)
- DECISIONS.md (D1–D25)

### M1 — Simulator
- `sentinel/simulator/generator.py` — 180 days of synthetic e-commerce data
- `sentinel/simulator/injector.py` — anomaly injection
- **28 scenarios** across 4 YAML files:
  - `business.yaml` (12 scenarios): payment failures, traffic drops, app bugs, stock-outs
  - `data_issues.yaml` (8 scenarios): tracking bugs, schema changes, late data
  - `hard.yaml` (2 scenarios): Simpson's paradox, two-cause days
  - `no_anomaly.yaml` (4 scenarios): normal days + sub-threshold wobble
- `ground_truth.json` with `true_revenue`, `counterfactual_revenue`, `effect_revenue_delta`
- `sentinel simulate` CLI command

### M2 — Ingestion + Data Quality
- `sentinel/ingestion/__init__.py` — per-day parquet → DuckDB views
- `sentinel/quality/__init__.py` — `check_day()` returning DQReport (PASS/WARN/BLOCK)
- Checks: schema diff, row counts, null rates, duplicates, category drift, tracking invariants
- Data-issue scenarios (S13–S20) return BLOCK/WARN correctly

### M3 — Detection + Analysis
- `sentinel/detection/__init__.py` — robust z-score detection against weekday/trailing baselines
- `sentinel/analysis/`:
  - `cube.py` — metric cube with all dimensions
  - `metric_tree.py` — LMDI decomposition
  - `drilldown.py` — Simpson-safe additive/ratio drill-down
  - `tools.py` — LLM-facing tools (get_metric, decompose_metric, drill_down, etc.)
- `sentinel/evidence/__init__.py` — evidence store
- Baseline C (deterministic) hits top-3 on 10/14 single-cause scenarios (71%)

### M4 — Investigation Agent
- `sentinel/llm/__init__.py` — Anthropic client with caching + token tally
- `sentinel/agents/investigation.py` — LLM tool-use loop, 25-call budget
- `sentinel/agents/prompts/investigation.md` — prompt
- `sentinel/evidence/verifier.py` — numeric hallucination checker
- **NOT tested against live API** — no `ANTHROPIC_API_KEY` in environment

### M5 — Critic + Reporting + Orchestrator
- `sentinel/agents/critic.py` — 6 checks (traffic, mix-shift, data-artifact, calendar, magnitude, LLM verdict)
- `sentinel/agents/prompts/critic.md` — Critic prompt
- `sentinel/agents/reporting.py` — ReportWriter (Markdown + JSON with evidence citations)
- `sentinel/orchestrator.py` — full pipeline: DQ → detect → investigate → critic → report
- `sentinel/cli.py` — `sentinel run` wired to orchestrator with progress output
- Evidence persisted to `runs/<run_id>/evidence.json`
- **NOT tested against live API** — requires `ANTHROPIC_API_KEY`

---

## ❌ Remaining

### M6 — Baselines + Eval Harness

| Task | Description |
|------|-------------|
| **Baseline A** | Single LLM agent + pandas tool (sandboxed). Same model as Sentinel. |
| **Baseline B (No-Critic)** | Sentinel without the Critic. |
| **Baseline C** | Already done — `Tools.find_causes`. |
| **Eval harness** | `sentinel eval` → `results.csv` + `report.md`. 3 seeds per scenario. |
| **Metrics** | Precision/recall, false-positive rate, root-cause accuracy@1/@3, contribution error, hallucination rate. |

**Acceptance:** Full metrics table for all systems.

**Open decisions** (HANDOFF §7 items 12–15):
- Baseline A sandboxing approach
- Partial match scoring for filter sets
- Two-cause scenario scoring

### M7 — UI + Scheduling

| Task | Description |
|------|-------------|
| **Streamlit app** | Run list, report view, click claim → query + result, eval dashboard |
| **Scheduler** | Daily run (cron or APScheduler) |
| **Alerts** | Optional Slack webhook |

### M8 — Real Data + Write-up

| Task | Description |
|------|-------------|
| **Public dataset adapter** | Olist or GA4 sample → same per-day parquet layout |
| **README final** | Architecture diagram, benchmark results, limitations |
| **Final eval** | Run full benchmark, report honestly |

---

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
sentinel simulate --out data/sim  # Generate data
ruff check .                      # Lint
pytest                            # Tests

# M5 smoke test (after getting API key)
$env:ANTHROPIC_API_KEY = "sk-..."
sentinel run --date 2026-04-01    # Should produce report
```

**Definition of Done** (PRD §17):
- `sentinel run --date YYYY-MM-DD` produces a report
- `sentinel eval` produces full metrics table
- Sentinel beats Baseline A on accuracy + hallucination rate
- Streamlit UI shows evidence behind any claim
- README has architecture + results + limitations
