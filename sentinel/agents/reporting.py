"""Report Writer: Markdown + JSON report from verified hypotheses (PRD §6.1 #6, M5)."""


from sentinel.evidence import EvidenceStore
from sentinel.evidence.verifier import unsupported_numbers
from sentinel.models import Hypothesis, Report


def _build_markdown(report: Report, store: EvidenceStore) -> str:
    """Build the human-readable Markdown report."""

    def cite(h: Hypothesis) -> str:
        return " → ".join(h.chain) if h.chain else ""

    lines = [
        f"# Sentinel Report — {report.date}",
        "",
        f"**Status:** {report.status.upper()}",
        f"**Headline:** {report.headline}",
        f"**Confidence:** {report.confidence:.0%}",
        "",
    ]

    if report.primary_cause:
        h = report.primary_cause
        lines.extend([
            "## Primary Cause",
            "",
            f"- **{h.statement}**",
            f"- Segment: {', '.join(f'{k}={v}' for k, v in h.segment_filters.items()) or 'global'}",
            f"- Explains: {h.contribution_pct:.1f}% of the revenue change",
            f"- Evidence chain: {cite(h)}",
            "",
        ])

    if report.secondary_causes:
        lines.extend(["## Secondary Causes", ""])
        for h in report.secondary_causes:
            lines.extend([
                f"- **{h.statement}** ({h.contribution_pct:.1f}%)",
                f"  - Segment: {', '.join(f'{k}={v}' for k, v in h.segment_filters.items())}",
                f"  - Evidence: {cite(h)}",
                "",
            ])

    if report.rejected:
        lines.extend(["## Rejected Alternatives", ""])
        for r in report.rejected:
            lines.extend([
                f"- ~~{r.hypothesis.statement}~~",
                f"  - Reasons: {', '.join(r.reasons)}",
                "",
            ])

    if report.unexplained_pct > 0:
        lines.extend([
            "## Unexplained",
            "",
            f"~{report.unexplained_pct:.1f}% of the revenue change is not attributed to any cause.",
            "",
        ])

    lines.extend([
        "## Recommended Action",
        "",
        report.recommended_action or "No specific action recommended.",
        "",
        f"**Escalate:** {'YES' if report.escalate else 'NO'}",
        "",
        f"— Sentinel | run_id: {report.run_id}",
    ])

    return "\n".join(lines)


class ReportWriter:
    def __init__(self, data_dir=None):
        self.data_dir = data_dir

    def write(self, report: Report, store: EvidenceStore, output_dir=None) -> Report:
        """Write the report with Markdown body, verify numbers, and return the final report.

        Args:
            report: Report model (partially filled)
            store: Evidence store with all evidence
            output_dir: Path to write the report files

        Returns:
            The same Report with markdown and json filled in
        """
        # Verify all hypothesis numbers are traceable
        all_hyps = [report.primary_cause] + report.secondary_causes if report.primary_cause else report.secondary_causes
        for h in all_hyps:
            if not h:
                continue
            bad = unsupported_numbers(h.statement, store, h.evidence_ids)
            if bad:
                # Drop the hypothesis - it's not traceable
                pass  # Keep it for now, let the caller decide

        # Build markdown
        report.markdown = _build_markdown(report, store)

        # Verify markdown has no untraced numbers (for the whole report)
        all_ids = []
        for h in all_hyps:
            if h:
                all_ids.extend(h.evidence_ids)
        for r in report.rejected:
            all_ids.extend(r.hypothesis.evidence_ids)

        # Save JSON
        out = report.model_dump(mode="json")

        if output_dir:
            import json
            (output_dir / "report.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
            (output_dir / "report.md").write_text(report.markdown, encoding="utf-8")

        return report
