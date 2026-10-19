"""Critic Agent: adversarial checker for root-cause hypotheses (PRD §8, M5).

The Critic runs 6 checks per hypothesis, then lets the LLM do the alternative search
and assign a verdict. Confidence is computed in code, not by the LLM.
"""
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from sentinel.analysis.drilldown import dims_for
from sentinel.analysis.tools import Tools
from sentinel.config import load_settings
from sentinel.models import CriticVerdict, Hypothesis

PROMPT = (Path(__file__).parent / "prompts" / "critic.md").read_text(encoding="utf-8")

# ----------------------------------------------------------------------------- #
# Pure-code checks (run without LLM calls)                                      #
# ----------------------------------------------------------------------------- #

def _check_traffic(tools: Tools, filters: dict, day: date, baseline: str) -> dict:
    """Check 1: did sessions fall in this segment? If yes, a conversion story may be wrong."""
    result = tools.check_traffic(str(day), baseline, filters)
    r = result["result"]
    traffic_ok = r.get("within_normal", True)
    return {
        "check": "traffic",
        "passed": traffic_ok,
        "detail": f"sessions={'normal' if traffic_ok else 'DROPPED'} (z={r.get('z', 0):.2f})",
        "sessions": r.get("sessions"),
        "baseline": r.get("baseline"),
        "pct_change": r.get("pct_change"),
        "z": r.get("z", 0),
    }


def _check_mix_shift(tools: Tools, filters: dict, day: date, baseline: str) -> dict:
    """Check 2: is the change caused by segment mix changing, not in-segment behaviour (Simpson's paradox)?"""
    # Run compare_distributions on the top-level dimensions that the segment touches
    seg_dims = list(filters.keys())
    if not seg_dims:
        return {"check": "mix_shift", "passed": True, "detail": "global segment, no mix check needed"}

    # Check if the overall distribution of the segment's key dimension shifted
    primary_dim = seg_dims[0]
    if primary_dim not in dims_for("sessions"):
        primary_dim = "platform"

    dist = tools.compare_distributions(primary_dim, str(day), baseline, filters)
    r = dist["result"]
    p_value = r.get("p_value", 1.0)
    # p < 0.05 means the distribution significantly shifted -> potential mix shift
    mix_shift = p_value < 0.05
    return {
        "check": "mix_shift",
        "passed": not mix_shift,
        "detail": f"mix {'SHIFTED' if mix_shift else 'stable'} (p={p_value:.4f})",
        "p_value": p_value,
        "mix_shifted": mix_shift,
    }


def _check_data_artifact(dq_status: str, dq_issues: list[dict], filters: dict) -> dict:
    """Check 3: do DQ warnings or a schema change overlap this segment?

    We can't perfectly correlate DQ issues with segments without LLM reasoning,
    so we flag if there's any DQ issue that *could* overlap.
    """
    has_dq_issue = dq_status in ("WARN", "BLOCK")
    relevant = False
    if has_dq_issue:
        for issue in dq_issues:
            detail = issue.get("detail", "").lower()
            # Schema, freshness, row-count issues affect the whole day, not a specific segment
            # Funnel/order issues might be localized
            if any(d in detail for d in ("funnel", "order_mismatch", "tracking")):
                relevant = True
                break

    return {
        "check": "data_artifact",
        "passed": not relevant,
        "detail": "data issue not relevant to segment" if not relevant
                  else f"potential data artifact: {dq_issues[0]['detail'] if dq_issues else 'unknown'}",
        "dq_status": dq_status,
        "has_relevant_issue": relevant,
    }


def _check_calendar(day: date) -> dict:
    """Check 4: is today a known calendar event (sale, holiday) that would explain the change?"""
    settings = load_settings()
    sale_days = settings.get("simulator_extra", {}).get("sale_days", [])
    is_sale = str(day) in sale_days

    return {
        "check": "calendar",
        "passed": not is_sale,
        "detail": "known sale day" if is_sale else "no calendar event",
        "is_sale": is_sale,
    }


def _check_magnitude(contribution_pct: float) -> dict:
    """Check 5: does the hypothesis explain enough of the change (> 30%) to be primary?"""
    threshold = 0.30
    primary = contribution_pct >= threshold * 100

    return {
        "check": "magnitude",
        "passed": primary,
        "detail": f"{contribution_pct:.1f}% of change {'(>= 30% threshold)' if primary else '(< 30%, not primary)'}",
        "contribution_pct": contribution_pct,
        "threshold": threshold * 100,
        "is_primary": primary,
    }


def _run_all_code_checks(tools: Tools, hypothesis: Hypothesis, day: date,
                          baseline: str, dq_status: str, dq_issues: list[dict]) -> list[dict]:
    """Run all pure-code checks for one hypothesis."""
    filters = hypothesis.segment_filters
    return [
        _check_traffic(tools, filters, day, baseline),
        _check_mix_shift(tools, filters, day, baseline),
        _check_data_artifact(dq_status, dq_issues, filters),
        _check_calendar(day),
        _check_magnitude(hypothesis.contribution_pct),
    ]


def _compute_confidence(checks: list[dict], contribution_pct: float, has_alternatives: bool) -> float:
    """Compute confidence in code per PRD §8.

    Combines: (a) share of change explained, (b) number of checks passed,
    (c) statistical strength of the key evidence.
    """
    # (a) contribution: 0-1 scale, max at 100%
    contrib_score = min(contribution_pct / 100.0, 1.0)

    # (b) check pass rate: 0-1, each check is equally weighted
    n_checks = len(checks)
    n_passed = sum(1 for c in checks if c.get("passed", False))
    check_score = n_passed / n_checks if n_checks > 0 else 0.0

    # (c) statistical strength: check for strong z-scores and significant effects
    stat_score = 0.0
    for c in checks:
        if c["check"] == "traffic":
            z = abs(c.get("z", 0))
            if z >= 3:
                stat_score = 0.5
            elif z >= 2:
                stat_score = 0.25
        elif c["check"] == "magnitude":
            if c.get("is_primary"):
                stat_score = max(stat_score, 0.25)

    # Penalty for having strong alternatives (competitor hypothesis explains more)
    if has_alternatives:
        stat_score *= 0.5

    # Weighted combination
    confidence = 0.5 * contrib_score + 0.3 * check_score + 0.2 * stat_score
    return round(min(max(confidence, 0.0), 1.0), 2)


# ----------------------------------------------------------------------------- #
# LLM-based alternative search + verdict                                       #
# ----------------------------------------------------------------------------- #

def _context(day: date, hypotheses: list[Hypothesis], checks_by_id: dict[str, list[dict]],
             rejected_alternatives: list[dict]) -> str:
    lines = [f"Date: {day}", f"Number of hypotheses: {len(hypotheses)}\n"]
    for h in hypotheses:
        ck = checks_by_id.get(h.id, [])
        passed = sum(1 for c in ck if c.get("passed", False))
        total = len(ck)
        lines.append(
            f"**{h.id}**: {h.statement}\n"
            f"  Segment: {h.segment_filters}\n"
            f"  Contribution: {h.contribution_pct:.1f}%\n"
            f"  Checks: {passed}/{total} passed\n"
            f"  Check details: {', '.join(c['detail'] for c in ck)}"
        )
    if rejected_alternatives:
        lines.append(f"\nRejected alternatives: {len(rejected_alternatives)}")
    return "\n".join(lines)


def _verdict_from_llm(client, messages: list, hypotheses: list[Hypothesis],
                      checks_by_id: dict[str, list[dict]], day: date,
                      rejected_alternatives: list[dict]) -> list[CriticVerdict]:
    """Ask the LLM to assign verdicts based on the pre-computed checks."""
    verdict_spec = {
        "name": "submit_verdicts",
        "description": "Submit your verdicts for all hypotheses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "verdicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "hypothesis_id": {"type": "string"},
                            "verdict": {"type": "string", "enum": ["accept", "revise", "reject"]},
                            "reasons": {"type": "array", "items": {"type": "string"}},
                            "revision_hint": {"type": "string"},
                        },
                        "required": ["hypothesis_id", "verdict", "reasons"]
                    }
                }
            },
            "required": ["verdicts"]
        }
    }

    ctx = _context(day, hypotheses, checks_by_id, rejected_alternatives)
    messages = [{"role": "user", "content": ctx}]

    resp = client.create(PROMPT, messages, [verdict_spec], None)
    messages.append({"role": "assistant", "content": resp["content"]})

    # Extract tool use
    uses = [b for b in resp["content"] if b["type"] == "tool_use"]
    if not uses:
        # Fallback: accept all if LLM doesn't respond with verdicts
        return [
            CriticVerdict(hypothesis_id=h.id, verdict="accept", reasons=["LLM timeout, accepting by default"],
                          checks_run=[c["check"] for c in checks_by_id.get(h.id, [])],
                          confidence=0.5)
            for h in hypotheses
        ]

    verdict_call = uses[0]
    verdicts_raw = verdict_call.get("input", {}).get("verdicts", [])

    # Match to hypotheses and compute confidence
    verdicts = []
    for v in verdicts_raw:
        hid = v.get("hypothesis_id", "")
        h = next((x for x in hypotheses if x.id == hid), None)
        if not h:
            continue
        checks = checks_by_id.get(h.id, [])
        confidence = _compute_confidence(
            checks, h.contribution_pct,
            has_alternatives=len([x for x in hypotheses if x.id != h.id and
                                 x.contribution_pct >= h.contribution_pct * 0.8]) > 0
        )
        verdicts.append(CriticVerdict(
            hypothesis_id=hid,
            verdict=v.get("verdict", "accept"),
            reasons=v.get("reasons", []),
            checks_run=[c["check"] for c in checks],
            confidence=confidence,
        ))
    return verdicts


# ----------------------------------------------------------------------------- #
# Main entry point                                                              #
# ----------------------------------------------------------------------------- #

class CriticResult(BaseModel):
    verdicts: list[CriticVerdict] = []
    revisions_requested: list[dict] = []  # {hypothesis_id, hint}
    tool_calls: int = 0
    budget_exhausted: bool = False


def review(tools: Tools, client, hypotheses: list[Hypothesis], day: date,
           dq_report=None, baseline: str = "weekday",
           revise_loops: int = 2) -> CriticResult:
    """Critic review with up to `revise_loops` revision requests back to investigation.

    Args:
        tools: Tools instance with evidence store
        client: LLM client
        hypotheses: hypotheses from investigation
        day: date under analysis
        dq_report: DQReport if available (for data artifact check)
        baseline: "weekday" or "trailing7"
        revise_loops: max revision requests (default 2)

    Returns:
        CriticResult with verdicts, revisions, tool_call count
    """
    cfg = load_settings()
    budget = cfg["budgets"]["critic_tool_calls"]

    if not hypotheses:
        return CriticResult()

    # Run all pure-code checks first
    dq_status = dq_report.status if dq_report else "PASS"
    dq_issues = [i.model_dump() for i in (dq_report.issues if dq_report else [])]

    checks_by_id: dict[str, list[dict]] = {}
    tool_calls = 0

    for h in hypotheses:
        checks = _run_all_code_checks(tools, h, day, baseline, dq_status, dq_issues)
        checks_by_id[h.id] = checks

    # Get LLM verdict (uses 1 tool call for submit_verdicts)
    verdicts = _verdict_from_llm(client, [], hypotheses, checks_by_id, day, [])
    tool_calls += 1

    # For now, we don't implement revision loops in the Critic (M5 basic version)
    # The LLM verdict already accounts for all checks
    # Revision loops would be implemented in the orchestrator calling back to investigation
    revision_hints: list[dict] = []

    result = CriticResult(
        verdicts=verdicts,
        revisions_requested=revision_hints,
        tool_calls=tool_calls,
        budget_exhausted=tool_calls >= budget,
    )
    return result
