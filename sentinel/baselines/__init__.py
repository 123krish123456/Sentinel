"""The systems compared in the benchmark (PRD §10.1). Each returns the same plain dict:

status, dq_status, causes (ranked, [{filters, contribution_pct}]), claims / claims_supported,
numbers / numbers_unsupported, dropped, llm_calls, input_tokens, output_tokens, cost_usd.
"""
from datetime import date
from pathlib import Path

from sentinel.evidence import EvidenceStore
from sentinel.evidence.verifier import unsupported_numbers

SYSTEMS = ("sentinel", "no_critic", "baseline_a", "stats_only")
LLM_SYSTEMS = {"sentinel", "no_critic", "baseline_a"}


def _verify(claims: list[tuple[str, list[str]]], store: EvidenceStore) -> dict:
    """Claim = (text, evidence_ids). Supported = cites an existing id and every number traces to it."""
    sup = nums = bad_n = 0
    for text, ids in claims:
        ids = [i for i in ids if i in store.items]
        bad = unsupported_numbers(text, store, ids)
        total = len(bad) + len(_numbers_in(text, store, ids))
        nums, bad_n = nums + total, bad_n + len(bad)
        sup += bool(ids) and not bad
    return {"claims": len(claims), "claims_supported": sup, "numbers": nums, "numbers_unsupported": bad_n}


def _numbers_in(text: str, store: EvidenceStore, ids: list[str]) -> list[str]:
    """Numbers in `text` that DO trace to the evidence (total = traced + untraced)."""
    from sentinel.evidence.verifier import _NUM, _STRIP

    bad = set(unsupported_numbers(text, store, ids))
    return [m for m in _NUM.findall(_STRIP.sub(" ", text)) if m not in bad]


def _usage(client) -> dict:
    return {"llm_calls": client.calls if client else 0,
            "input_tokens": client.input_tokens if client else 0,
            "output_tokens": client.output_tokens if client else 0,
            "cost_usd": client.cost_usd if client else None}


def run_system(name: str, data_dir: Path, day: date, client_factory, run_dir: Path) -> dict:
    """Run one system on one day. `client_factory()` returns an LLMClient-like object (LLM systems only)."""
    from sentinel.orchestrator import Orchestrator
    from sentinel.quality import check_day

    base = {"status": "normal", "dq_status": None, "causes": [], "claims": 0, "claims_supported": 0,
            "numbers": 0, "numbers_unsupported": 0, "dropped": 0}
    if name == "stats_only":  # Baseline C: DQ + detection + deterministic drill-down, no LLM
        from sentinel.analysis.tools import Tools
        from sentinel.detection import detect

        dq = check_day(data_dir, day)
        base["dq_status"] = dq.status
        if dq.status == "BLOCK":
            return {**base, "status": "data_issue", **_usage(None)}
        tools = Tools(data_dir)
        if not detect(data_dir, day, tools.store):
            return {**base, **_usage(None)}
        found = tools.find_causes(str(day))["result"]["causes"]
        return {**base, "status": "anomaly", **_usage(None),
                "causes": [{"filters": c["filters"], "contribution_pct": 100 * c["contribution"]}
                           for c in found]}
    if name in ("sentinel", "no_critic"):
        client = client_factory()
        orch = Orchestrator(data_dir, run_dir, client=client, use_critic=(name == "sentinel"))
        rep = orch.run(day)
        hyps = ([rep.primary_cause] if rep.primary_cause else []) + rep.secondary_causes
        inv = orch.investigation_result
        return {**base, "status": rep.status, "dq_status": orch.dq_report.status,
                **_usage(orch._client),
                "causes": [{"filters": h.segment_filters, "contribution_pct": h.contribution_pct}
                           for h in hyps],
                "dropped": len(inv.dropped) if inv else 0,
                **_verify([(h.statement, h.evidence_ids) for h in hyps], orch.store)}
    if name == "baseline_a":
        from sentinel.baselines.pandas_agent import run

        client, store = client_factory(), EvidenceStore()
        out = run(client, data_dir, day, store)
        return {**base, "status": out["status"], **_usage(client),
                "causes": [{"filters": c["filters"], "contribution_pct": c["contribution_pct"]}
                           for c in out["causes"]],
                **_verify([(c["statement"], c["evidence_ids"]) for c in out["causes"]], store)}
    raise ValueError(f"unknown system {name!r}; choose from {SYSTEMS}")
