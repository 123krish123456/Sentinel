"""Regression checks for the bug-sweep fixes."""
from datetime import date
from pathlib import Path

import pytest

from axiom.agents.critic import _check_calendar, _check_data_artifact, _check_mix_shift
from axiom.agents.investigation import _clean
from axiom.analysis.tools import Tools
from axiom.baselines.pandas_agent import run as baseline_a

DATA = Path("data/sim")
needs_sim = pytest.mark.skipif(not DATA.exists(), reason="run `axiom simulate --out data/sim` first")


def test_get_events_rejects_sql_in_filter_keys():
    with pytest.raises(ValueError, match="unknown session dimension"):
        Tools(DATA).get_events("view", "2026-04-01", {"1=1; drop table x; --": "a"})


@needs_sim
def test_short_history_does_not_crash():
    t = Tools(DATA)
    assert t.find_causes("2026-01-02")["result"]["causes"] is not None  # 1 day of history: no z
    assert t.check_traffic("2026-01-01")["result"]["within_normal"]


@needs_sim
def test_mix_shift_check_is_not_a_noop():
    class T:  # records the segment the check compares within
        def compare_distributions(self, col, day, baseline, segment):
            self.args = (col, segment)
            return {"result": {"p_value": 0.5}}
    t = T()
    _check_mix_shift(t, {"platform": "ios", "country": "IN"}, date(2026, 4, 1), "weekday")
    assert t.args == ("platform", {"country": "IN"})


def test_calendar_and_data_artifact_checks():
    assert _check_calendar(date(2026, 3, 14))["is_sale"]
    assert not _check_calendar(date(2026, 3, 15))["is_sale"]
    funnel = [{"check": "funnel_order", "detail": "400/1000 checkout sessions have no add_to_cart"}]
    assert not _check_data_artifact("WARN", funnel, {})["passed"]
    nulls = [{"check": "null_rate", "detail": "sessions.app_version: null rate 30% vs 0% baseline"}]
    assert not _check_data_artifact("WARN", nulls, {"app_version": "5.3.0"})["passed"]
    assert _check_data_artifact("WARN", nulls, {"country": "IN"})["passed"]


def test_malformed_model_output_is_coerced():
    out = _clean([{"statement": 5, "segment_filters": {"os": 1}, "evidence_ids": "E001"}, "junk"])
    assert out == [{"statement": "5", "segment_filters": {"os": "1"}, "evidence_ids": [], "chain": []}]
    assert _clean(None) == []

    class Fake:
        def create(self, *a, **k):
            return {"content": [{"type": "tool_use", "id": "1", "name": "submit_answer",
                                 "input": {"status": "weird", "causes": [{"filters": ["x"],
                                                                          "contribution_pct": "lots"}]}}]}
    from axiom.evidence import EvidenceStore
    out = baseline_a(Fake(), DATA, date(2026, 4, 1), EvidenceStore())
    assert out["status"] == "normal" and out["causes"][0]["filters"] == {}
    assert out["causes"][0]["contribution_pct"] is None
