"""Orchestrator: state machine driving the full pipeline (PRD §6, M5).

DQ -> detect -> investigate -> critic (max 2 revise loops) -> report
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

    def __init__(self, data_dir: Path, output_dir: Path | None = None, model: str | None = None):
        self.data_dir = Path(data_dir)
        self.output_dir = output_dir or Path("runs") / f"run_{uuid.uuid4().hex[:8]}"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.model = model
        self.store = EvidenceStore()
        self.tools = Tools(self.data_dir, self.store)

        # LLM client (lazy import to avoid errors when API key is missing)
        self._client = None

        # Pipeline state
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

        # ---- Stage 1: Data Quality ----
        dq = check_day(self.data_dir, day)
        self.dq_report = dq
        self._save_stage("dq", {"status": dq.status, "issues": [i.model_dump() for i in dq.issues]})

        if dq.status == "BLOCK":
            # Stop here - data is bad
            return self._make_data_issue_report(day)

        # ---- Stage 2: Detection ----
        anomalies = detect(self.data_dir, day, self.store,
                          min_rel_change=cfg["detection"]["min_rel_change"])
        self.anomalies = anomalies
        self._save_stage("detection", {
            "anomalies": [a.model_dump(mode="json") for a in anomalies]
        })

        if not anomalies:
            # Nothing significant happened
            return self._make_normal_report(day)

        # ---- Stage 3: Investigation ----
        inv_result = investigate(self.tools, self.client, day, anomalies, baseline)
        self.investigation_result = inv_result
        self._save_stage("investigation", {
            "hypotheses": [h.model_dump(mode="json") for h in inv_result.hypotheses],
            "rejected_alternatives": inv_result.rejected_alternatives,
            "unexplained_pct": inv_result.unexplained_pct,
            "tool_calls": inv_result.tool_calls,
            "budget_exhausted": inv_result.budget_exhausted,
            "dropped": inv_result.dropped,
        })

        if not inv_result.hypotheses:
            # No hypotheses found
            return self._make_anomaly_report(day, anomalies, inv_result)

        # ---- Stage 4: Critic ----
        crit_result = review(self.tools, self.client, inv_result.hypotheses, day,
                            self.dq_report, baseline)
        self.critic_result = crit_result
        self._save_stage("critic", {
            "verdicts": [v.model_dump() for v in crit_result.verdicts],
            "revisions_requested": crit_result.revisions_requested,
            "tool_calls": crit_result.tool_calls,
            "budget_exhausted": crit_result.budget_exhausted,
        })

        # ---- Stage 5: Report ----
        report = self._build_report(day, anomalies, inv_result, crit_result)

        # ---- Persist ----
        self._save_stage("evidence", None)  # saves the evidence store
        self._save_stage("report", report.model_dump(mode="json"))
        self._save_stage("meta", {
            "run_id": self.run_id,
            "date": str(day),
            "baseline": baseline,
            "llm_calls": self.client.calls,
            "llm_input_tokens": self.client.input_tokens,
            "llm_output_tokens": self.client.output_tokens,
            "llm_cost_usd": self.client.cost_usd,
        })

        return report

    def _save_stage(self, name: str, data) -> None:
        """Save a pipeline stage's output."""
        path = self.output_dir / f"{name}.json"
        if data is None:
            # Special case: save evidence store
            if name == "evidence":
                self.store.save(self.output_dir / "evidence.json")
            return
        path.write_text(json.dumps(data, indent=2, default=str))

    def _build_report(self, day: date, anomalies: list, inv_result, crit_result) -> Report:
        """Build the final Report from pipeline results."""
        hypotheses = inv_result.hypotheses
        verdicts = {v.hypothesis_id: v for v in (crit_result.verdicts if crit_result else [])}

        # Filter to accepted hypotheses
        accepted = [h for h in hypotheses if verdicts.get(h.id, type("", (), {"verdict": "accept"})()).verdict == "accept"]
        rejected = [
            RejectedHypothesis(hypothesis=h, reasons=verdicts.get(h.id, type("", (), {"reasons": []})()).reasons or ["Rejected by critic"])
            for h in hypotheses if verdicts.get(h.id, type("", (), {"verdict": "accept"})()).verdict == "reject"
        ]

        # Sort by contribution
        accepted.sort(key=lambda h: h.contribution_pct, reverse=True)

        primary = accepted[0] if len(accepted) >= 1 else None
        secondary = accepted[1:] if len(accepted) > 1 else []

        # Headline
        top_anomaly = max(anomalies, key=lambda a: abs(a.pct_change)) if anomalies else None
        if top_anomaly and primary:
            headline = (f"{top_anomaly.metric} {top_anomaly.pct_change:+.1%} vs baseline "
                       f"(z={top_anomaly.z_score:.1f}, {top_anomaly.severity})")
        elif top_anomaly:
            headline = f"{top_anomaly.metric} {top_anomaly.pct_change:+.1%} vs baseline"
        else:
            headline = "Anomaly detected, no clear root cause identified"

        # Confidence: average of accepted hypothesis confidences
        confidence = 0.0
        if accepted:
            confids = [verdicts.get(h.id, type("", (), {"confidence": 0.5})()).confidence or 0.5
                      for h in accepted]
            confidence = sum(confids) / len(confids)

        # Recommended action
        if primary:
            seg = primary.segment_filters
            action = self._suggest_action(primary, seg)
        else:
            action = "Investigate further with detailed analysis."
            confidence = confidence * 0.8  # Lower confidence when no primary cause

        # Escalate: yes if confidence >= 0.5 and there's a significant change
        escalate = confidence >= 0.5 and (top_anomaly and top_anomaly.severity in ("medium", "high"))

        report = Report(
            run_id=self.run_id,
            date=day,
            status="anomaly",
            headline=headline,
            primary_cause=primary,
            secondary_causes=secondary,
            rejected=rejected,
            unexplained_pct=inv_result.unexplained_pct,
            confidence=confidence,
            recommended_action=action,
            escalate=escalate,
            markdown="",  # Will be filled by ReportWriter
        )

        # Write markdown
        writer = ReportWriter(self.data_dir)
        report = writer.write(report, self.store, self.output_dir)

        return report

    def _suggest_action(self, hypothesis: Hypothesis, segment_filters: dict) -> str:
        """Generate a recommended action based on the hypothesis."""
        # Build a context-aware action recommendation
        parts = []
        for k, v in segment_filters.items():
            parts.append(f"{k}={v}")

        filter_str = ", ".join(parts) if parts else "the affected segment"
        action = f"Investigate {filter_str} for issues (e.g., payment failures, app bugs, inventory)."
        return action

    def _make_data_issue_report(self, day: date) -> Report:
        """Report when data quality blocked analysis."""
        issues = self.dq_report.issues if self.dq_report else []
        detail = "; ".join(i.detail for i in issues[:3]) if issues else "Data quality check failed"

        report = Report(
            run_id=self.run_id,
            date=day,
            status="data_issue",
            headline=f"Data quality issue blocked analysis: {detail[:100]}",
            confidence=1.0,
            recommended_action="Fix data pipeline issues before re-running analysis.",
            escalate=True,
        )

        writer = ReportWriter(self.data_dir)
        report = writer.write(report, self.store, self.output_dir)

        self._save_stage("evidence", None)
        self._save_stage("report", report.model_dump(mode="json"))

        return report

    def _make_normal_report(self, day: date) -> Report:
        """Report when no anomalies were detected."""
        report = Report(
            run_id=self.run_id,
            date=day,
            status="normal",
            headline="No significant metric changes detected.",
            confidence=1.0,
            recommended_action="No action needed.",
            escalate=False,
        )

        writer = ReportWriter(self.data_dir)
        report = writer.write(report, self.store, self.output_dir)

        self._save_stage("evidence", None)
        self._save_stage("report", report.model_dump(mode="json"))

        return report

    def _make_anomaly_report(self, day: date, anomalies: list, inv_result) -> Report:
        """Report when anomalies were detected but no hypotheses were formed."""
        top = max(anomalies, key=lambda a: abs(a.pct_change)) if anomalies else None

        report = Report(
            run_id=self.run_id,
            date=day,
            status="anomaly",
            headline=f"{top.metric} {top.pct_change:+.1%} vs baseline, root cause unclear",
            confidence=0.3,
            recommended_action="Manual investigation required.",
            escalate=True,
        )

        writer = ReportWriter(self.data_dir)
        report = writer.write(report, self.store, self.output_dir)

        return report


def run(data_dir: Path, day: date, output_dir: Path | None = None, model: str | None = None) -> Report:
    """Convenience function to run the full pipeline."""
    orch = Orchestrator(data_dir, output_dir, model)
    return orch.run(day)
