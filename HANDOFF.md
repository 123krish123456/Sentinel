
## 10. Live testing via an OpenAI-compatible provider (2026-10-30)

- `sentinel/llm/openai_compat.py` adapts `LLMClient` to any OpenAI-style endpoint. Select it with `provider: {base_url, api_key_env}` in `config/settings.yaml` (currently Dahl, key in `DAHL_API_KEY`; remove `provider` and set a `claude-*` model to use Anthropic). Retries 429/5xx/52x and honours `Retry-After`.
- S11 (2026-04-01) live: DeepSeek-V4-Flash found `category=electronics` (matches ground truth) with verified numbers. MiniMax-M2.7 found it too but was dropped by the verifier for citing numbers not in evidence (working as designed).
- Free tier is concurrency-limited (429 `model_concurrency`, 502/524): full DeepSeek runs including the Critic are unreliable. A full pipeline did complete with MiniMax (`runs/live_s11c`, no hypotheses survived the verifier).
- Fixes: Critic now forces `submit_verdicts`; a missing verdict is flagged `UNREVIEWED` with confidence capped at 0.3 instead of silently accepted; same-segment hypotheses are merged; report.md written as UTF-8 (Windows cp1252 crash).
- Still unverified: Critic's real LLM verdict on a live model, revise loop (never implemented), cost reporting (no pricing set).

## 11. M6-M8 build (2026-10-30)

- **Orchestrator** rewritten: real revise loop (D31), `use_critic` switch, one `_finish` path that always writes report, evidence and meta, robust verdict lookup. `investigate(..., hint=)` takes the Critic's hint.
- **M6:** `sentinel/baselines/` (`run_system` for sentinel / no_critic / baseline_a / stats_only; `pandas_agent.py` is Baseline A, sandboxed per D29), `sentinel/eval/` (scoring per D27/D28/D30, `evaluate`, `summarize`, `write_report`), `sentinel eval`. Each system has its own LLM cache (D32).
- **M7:** `sentinel/ui/app.py` (Streamlit; click a claim's evidence to see params, query and result), `sentinel daily`, `sentinel/delivery.py` (Slack, only when escalating). `sentinel ui` launches the app. Streamlit needed `starlette>=0.46` here.
- **M8:** `sentinel/ingestion/olist.py` + `sentinel adapt-olist` (D33), README results + limitations.
- **Results:** stats_only on 28 scenarios x 3 seeds: detection P/R 0.85/0.83, FP 0.08, acc@1 0.50, overlap@3 0.75, data-issue recall 1.00 (`eval/results/stats_only_3seeds/`).
- **Not done:** LLM-system benchmark (the free Dahl tier is too rate-limited, and a launch of the live eval was blocked by the permission classifier, so run it yourself), live Critic confirmation, real Olist run, pricing.
- 24 tests pass; ruff clean.
