"""Orchestrator: state machine driving the full pipeline (PRD §6, M5).

DQ -> detect -> investigate -> critic (max `revise_loops` revise loops) -> report
"""
import json
import uuid
from datetime import date
from pathlib import Path

from sentinel.agents.critic import CriticResult, review
from sentinel.agents.investigation import investigate
from sentinel.agents.reporting import ReportWriter
from sentinel.analysis.tools import Tools
from sentinel.config import load_settings
from sentinel.detection import detect
from sentinel.evidence import EvidenceStore
from sentinel.models import DQReport, Hypothesis, RejectedHypothesis, Report
from sentinel.quality import check_day


class Orchestrator:
    """Drives the full Sentinel pipeline and persists everything under runs/<run_id>/."""

    def __init__(self, data_dir: Path, output_dir: Path | None = None, model: str | None = None,
                 client=None, use_critic: bool = True):
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir) if output_dir else Path("runs") / f"run_{uuid.uuid4().hex[:8]}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.model, self.use_critic = model, use_critic
        self.store = EvidenceStore()
        self.tools = Tools(self.data_dir, self.store)
        self._client = client  # created lazily so DQ-only / no-anomaly days need no API key
        self.run_id = self.output_dir.name
        self.day: date | None = None
        self.dq_report: DQReport | None = None
        self.anomalies: list = []
        self.investigation_result = None
        self.critic_result: CriticResult | None = None

    @property
    def client(self):
        if self._client is None:
            from sentinel.llm import LLMClient
            self._client = LLMClient(model=self.model)
        return self._client

    def run(self, day: date, baseline: str = "weekday") -> Report:
        """Run the full pipeline for one day. Returns the final report."""
        self.day = day
        cfg = load_settings()

        dq = self.dq_report = check_day(self.data_dir, day)
        self._save("dq", {"status": dq.status, "issues": [i.model_dump() for i in dq.issues]})
        if dq.status == "BLOCK":  # invariant 3: no analysis on bad data
            detail = "; ".join(i.detail for i in dq.issues[:3]) or "Data quality check failed"
            return self._finish(Report(
                run_id=self.run_id, date=day, status="data_issue", confidence=1.0, escalate=True,
                headline=f"Data quality issue blocked analysis: {detail[:100]}",
                recommended_action="Fix data pipeline issues before re-running analysis."))

        self.anomalies = detect(self.data_dir, day, self.store,
                                min_rel_change=cfg["detection"]["min_rel_change"])
        self._save("detection", {"anomalies": [a.model_dump(mode="json") for a in self.anomalies]})
        if not self.anomalies:
            return self._finish(Report(
                run_id=self.run_id, date=day, status="normal", confidence=1.0,
                headline="No significant metric changes detected.", recommended_action="No action needed."))

        dq_text = dq.status + "".join(f"; {i.check}: {i.detail}" for i in dq.issues)
        inv = self.investigation_result = investigate(self.tools, self.client, day, self.anomalies, baseline,
                                                      dq=dq_text)
        crit = None
        if self.use_critic and inv.hypotheses:
            crit = review(self.tools, self.client, inv.hypotheses, day, self.dq_report, baseline)
            for _ in range(cfg["budgets"]["revise_loops"]):
                if not crit.revisions_requested:
                    break
                hint = " | ".join(f"{r['hypothesis_id']}: {r['hint']}" for r in crit.revisions_requested)
                inv = self.investigation_result = investigate(
                    self.tools, self.client, day, self.anomalies, baseline, hint=hint, dq=dq_text)
                if not inv.hypotheses:
                    break
                crit = review(self.tools, self.client, inv.hypotheses, day, self.dq_report, baseline)
        self.critic_result = crit
        self._save("investigation", {
            "hypotheses": [h.model_dump(mode="json") for h in inv.hypotheses],
            "rejected_alternatives": inv.rejected_alternatives, "unexplained_pct": inv.unexplained_pct,
            "tool_calls": inv.tool_calls, "budget_exhausted": inv.budget_exhausted, "dropped": inv.dropped})
        if crit:
            self._save("critic", {
                "verdicts": [v.model_dump() for v in crit.verdicts],
                "revisions_requested": crit.revisions_requested, "tool_calls": crit.tool_calls,
                "budget_exhausted": crit.budget_exhausted})

        if not inv.hypotheses:
            top = max(self.anomalies, key=lambda a: abs(a.pct_change))
            return self._finish(Report(
                run_id=self.run_id, date=day, status="anomaly", confidence=0.3, escalate=True,
                headline=f"{top.metric} {top.pct_change:+.1%} vs baseline, root cause unclear",
                recommended_action="Manual investigation required."), baseline)
        return self._finish(self._build_report(day, inv, crit), baseline)

    def _save(self, name: str, data) -> None:
        (self.output_dir / f"{name}.json").write_text(json.dumps(data, indent=2, default=str),
                                                      encoding="utf-8")

    def _finish(self, report: Report, baseline: str = "weekday") -> Report:
        """Write report files, evidence store and run metadata (every exit path goes through here)."""
        if report.status != "anomaly":  # nothing to explain on a normal or blocked day
            report.unexplained_pct = 0.0
        report = ReportWriter(self.data_dir).write(report, self.store, self.output_dir)
        self.store.save(self.output_dir / "evidence.json")
        c = self._client
        self._save("meta", {"run_id": self.run_id, "date": str(self.day), "baseline": baseline,
                            "llm_calls": getattr(c, "calls", 0),
                            "llm_input_tokens": getattr(c, "input_tokens", 0),
                            "llm_output_tokens": getattr(c, "output_tokens", 0),
                            "llm_cost_usd": getattr(c, "cost_usd", None)})
        return report

    def _build_report(self, day: date, inv, crit: CriticResult | None) -> Report:
        verdicts = {v.hypothesis_id: v for v in (crit.verdicts if crit else [])}

        def verdict(h: Hypothesis) -> str:
            v = verdicts.get(h.id)
            if v is None:  # no critic (or it skipped this one): keep, but it carries no confidence
                return "accept"
            return "reject" if v.verdict == "revise" else v.verdict  # unresolved revise -> not trusted

        accepted = sorted((h for h in inv.hypotheses if verdict(h) == "accept"),
                          key=lambda h: h.contribution_pct, reverse=True)
        rejected = [RejectedHypothesis(
            hypothesis=h, reasons=(verdicts[h.id].reasons if h.id in verdicts else []) or ["Rejected by critic"])
            for h in inv.hypotheses if verdict(h) == "reject"]
        primary = accepted[0] if accepted else None

        top = max(self.anomalies, key=lambda a: abs(a.pct_change))
        headline = f"{top.metric} {top.pct_change:+.1%} vs baseline"
        if primary:
            headline += f" (z={top.z_score:.1f}, {top.severity})"
        else:
            headline += ", no accepted root cause"

        confs = [verdicts[h.id].confidence for h in accepted if h.id in verdicts]
        confidence = sum(confs) / len(confs) if confs else 0.3 if accepted else 0.0
        action = (self._suggest_action(primary.segment_filters) if primary
                  else "Investigate further with detailed analysis.")
        return Report(
            run_id=self.run_id, date=day, status="anomaly", headline=headline, primary_cause=primary,
            secondary_causes=accepted[1:], rejected=rejected, unexplained_pct=inv.unexplained_pct,
            confidence=confidence, recommended_action=action,
            escalate=bool(confidence >= 0.5 and top.severity in ("medium", "high")))

    @staticmethod
    def _suggest_action(segment_filters: dict) -> str:
        seg = ", ".join(f"{k}={v}" for k, v in segment_filters.items()) or "the affected segment"
        return f"Investigate {seg} for issues (e.g., payment failures, app bugs, inventory)."


def run(data_dir: Path, day: date, output_dir: Path | None = None, model: str | None = None) -> Report:
    """Convenience function to run the full pipeline."""
    return Orchestrator(data_dir, output_dir, model).run(day)
