from datetime import date
from pathlib import Path

import pytest

from sentinel.eval import matches, score
from sentinel.models import Report
from sentinel.orchestrator import Orchestrator

DATA = Path("data/sim")
needs_sim = pytest.mark.skipif(not DATA.exists(), reason="run `sentinel simulate --out data/sim` first")


class Fake:
    """Scripted LLM: investigation submits one hypothesis; the critic asks for one revision, then accepts."""

    def __init__(self):
        self.calls = self.cache_hits = self.input_tokens = self.output_tokens = 0
        self.cost_usd = None
        self.critic_calls, self.first_user_msgs = 0, []

    def create(self, system, messages, tools, tool_choice=None, max_tokens=4096):
        self.calls += 1
        names = [t["name"] for t in tools]
        if "submit_verdicts" in names:
            self.critic_calls += 1
            v = ({"verdict": "revise", "reasons": ["too vague"], "revision_hint": "name the category"}
                 if self.critic_calls == 1 else {"verdict": "accept", "reasons": ["ok"]})
            return {"content": [{"type": "tool_use", "id": "c", "name": "submit_verdicts",
                                 "input": {"verdicts": [{"hypothesis_id": "H1", **v}]}}]}
        self.first_user_msgs.append(messages[0]["content"])
        h = {"statement": "Electronics stock-out", "segment_filters": {"category": "electronics"},
             "chain": ["E001"], "evidence_ids": ["E001"]}
        return {"content": [{"type": "tool_use", "id": "i", "name": "submit_hypotheses",
                             "input": {"hypotheses": [h]}}]}


@needs_sim
def test_revise_loop_reinvestigates_with_hint(tmp_path):
    fake = Fake()
    rep = Orchestrator(DATA, tmp_path / "r", client=fake).run(date(2026, 4, 1))
    assert fake.critic_calls == 2 and len(fake.first_user_msgs) == 2
    assert "name the category" in fake.first_user_msgs[1] and "name the category" not in fake.first_user_msgs[0]
    assert rep.primary_cause.segment_filters == {"category": "electronics"}
    for f in ("report.md", "report.json", "evidence.json", "meta.json", "critic.json"):
        assert (tmp_path / "r" / f).exists()


@needs_sim
def test_no_critic_and_early_exits_need_no_llm(tmp_path):
    rep = Orchestrator(DATA, tmp_path / "a", client=Fake(), use_critic=False).run(date(2026, 4, 1))
    assert rep.primary_cause and not (tmp_path / "a" / "critic.json").exists()
    class Boom:  # any LLM call on a clean or blocked day is a bug
        def create(self, *a, **k):
            raise AssertionError("LLM called")
    assert Orchestrator(DATA, tmp_path / "n", client=Boom()).run(date(2026, 6, 10)).status == "normal"  # S25
    blocked = Orchestrator(DATA, tmp_path / "b", client=Boom()).run(date(2026, 5, 6))  # S17 data issue
    assert blocked.status == "data_issue" and (tmp_path / "b" / "evidence.json").exists()


def test_match_rules():
    assert matches({"os": "android", "payment_method": "upi", "country": "IN"}, {"os": "android", "payment_method": "upi"})
    assert not matches({"os": "android"}, {"os": "android", "payment_method": "upi"})
    assert not matches({}, {})


def test_score_two_cause_and_no_anomaly():
    gt = {"category": "business", "expected_detect": True, "expected_filters": [{"a": "1"}, {"b": "2"}],
          "date": "2026-04-01", "effect_revenue_delta": 0}
    out = {"status": "anomaly", "dq_status": "PASS", "causes": [{"filters": {"a": "1"}, "contribution_pct": 60},
                                                                  {"filters": {"b": "2"}, "contribution_pct": 30}]}
    r = score(gt, {**out, "status": "normal"}, DATA)  # status normal -> no contribution lookup
    assert r["acc1"] and r["acc3"] and r["both3"] and not r["alert"] and r["expected_alert"]
    none = score({"category": "none", "expected_detect": False, "expected_filters": []},
                 {"status": "normal", "causes": []}, DATA)
    assert none["is_none"] and not none["alert"]


def test_slack_only_when_escalating(monkeypatch):
    from sentinel import delivery
    sent = []
    monkeypatch.setattr(delivery.httpx, "post", lambda url, **k: sent.append(url) or type("R", (), {"is_success": True})())
    quiet = Report(run_id="r", date=date(2026, 1, 1), status="normal", headline="ok")
    assert not delivery.notify_slack(quiet, "http://hook") and not sent
    loud = quiet.model_copy(update={"escalate": True})
    assert delivery.notify_slack(loud, "http://hook") and sent == ["http://hook"]


@needs_sim
def test_baseline_a_runs_sandbox_and_is_verified():
    from sentinel.baselines import run_system

    class Agent:
        n = calls = input_tokens = output_tokens = 0
        cost_usd = None

        def create(self, system, messages, tools, tool_choice=None, max_tokens=4096):
            self.n += 1
            if self.n == 1:
                return {"content": [{"type": "tool_use", "id": "a", "name": "run_python",
                                     "input": {"code": "print(len(load('orders', '2026-04-01')))"}}]}
            n = next(int(float(x)) for x in messages[-1]["content"][0]["content"].split('"output": "')[1].split("\\")[0:1])
            return {"content": [{"type": "tool_use", "id": "b", "name": "submit_answer", "input": {
                "status": "anomaly", "causes": [{"filters": {"category": "electronics"},
                                                 "statement": f"{n} orders on the day", "contribution_pct": 90}]}}]}

    out = run_system("baseline_a", DATA, date(2026, 4, 1), Agent, Path("unused"))
    assert out["status"] == "anomaly" and out["causes"][0]["filters"] == {"category": "electronics"}
    assert out["claims"] == 1 and out["claims_supported"] == 1 and out["numbers_unsupported"] == 0
