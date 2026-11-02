"""Delivery: optional Slack webhook for escalated reports (M7). Set AXIOM_SLACK_WEBHOOK to enable."""
import os

import httpx

from axiom.models import Report


def slack_text(report: Report) -> str:
    cause = f"\nCause: {report.primary_cause.statement}" if report.primary_cause else ""
    return (f"{':rotating_light:' if report.escalate else ':white_check_mark:'} Axiom {report.date}: "
            f"{report.headline} (confidence {report.confidence:.0%}){cause}\n"
            f"Action: {report.recommended_action}")


def notify_slack(report: Report, webhook: str | None = None) -> bool:
    """Post to Slack when the report says escalate (or always for data issues). Returns True if sent."""
    url = webhook or os.environ.get("AXIOM_SLACK_WEBHOOK")
    if not url or not (report.escalate or report.status == "data_issue"):
        return False
    return httpx.post(url, json={"text": slack_text(report)}, timeout=15).is_success
