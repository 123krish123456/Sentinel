
## 10. Live testing via an OpenAI-compatible provider (2026-10-30)

- `sentinel/llm/openai_compat.py` adapts `LLMClient` to any OpenAI-style endpoint. Select it with `provider: {base_url, api_key_env}` in `config/settings.yaml` (currently Dahl, key in `DAHL_API_KEY`; remove `provider` and set a `claude-*` model to use Anthropic). Retries 429/5xx/52x and honours `Retry-After`.
- S11 (2026-04-01) live: DeepSeek-V4-Flash found `category=electronics` (matches ground truth) with verified numbers. MiniMax-M2.7 found it too but was dropped by the verifier for citing numbers not in evidence (working as designed).
- Free tier is concurrency-limited (429 `model_concurrency`, 502/524): full DeepSeek runs including the Critic are unreliable. A full pipeline did complete with MiniMax (`runs/live_s11c`, no hypotheses survived the verifier).
- Fixes: Critic now forces `submit_verdicts`; a missing verdict is flagged `UNREVIEWED` with confidence capped at 0.3 instead of silently accepted; same-segment hypotheses are merged; report.md written as UTF-8 (Windows cp1252 crash).
- Still unverified: Critic's real LLM verdict on a live model, revise loop (never implemented), cost reporting (no pricing set).
