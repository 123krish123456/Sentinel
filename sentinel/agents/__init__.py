"""Sentinel agents."""
from sentinel.agents.critic import CriticResult, review
from sentinel.agents.investigation import InvestigationResult, investigate
from sentinel.agents.reporting import ReportWriter

__all__ = ["CriticResult", "InvestigationResult", "ReportWriter", "investigate", "review"]
