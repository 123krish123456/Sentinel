"""Axiom agents."""
from axiom.agents.critic import CriticResult, review
from axiom.agents.investigation import InvestigationResult, investigate
from axiom.agents.reporting import ReportWriter

__all__ = ["CriticResult", "InvestigationResult", "ReportWriter", "investigate", "review"]
