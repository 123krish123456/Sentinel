"""Streamlit UI (M7): run list, report view, evidence behind every claim, eval dashboard.

Run with `axiom ui` (or `streamlit run axiom/ui/app.py`).
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Axiom", layout="wide")
st.title("Axiom")
RUNS, EVALS = Path("runs"), Path("eval/results")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run_dirs() -> list[Path]:
    ds = [d for d in RUNS.glob("*") if (d / "report.json").exists()] if RUNS.exists() else []
    return sorted(ds, key=lambda d: (d / "report.json").stat().st_mtime, reverse=True)


def claim_view(h: dict, evidence: dict):
    """One hypothesis: its statement, numbers, and every cited evidence record (query + result)."""
    st.markdown(f"**{h['statement']}**")
    st.caption(f"segment: {h['segment_filters'] or 'global'} | explains {h['contribution_pct']:.1f}% "
               f"| chain: {' → '.join(h['chain'])}")
    for eid in h["evidence_ids"]:
        ev = evidence.get(eid)
        if ev is None:
            continue
        with st.expander(f"{eid} · {ev['tool']}"):
            st.write("params", ev["params"])
            if ev.get("query"):
                st.code(ev["query"], language="sql")
            st.json(ev["result"], expanded=False)


tab_runs, tab_eval = st.tabs(["Runs", "Eval"])

with tab_runs:
    runs = run_dirs()
    if not runs:
        st.info("No runs yet. Try `axiom run 2026-04-01`.")
    else:
        rows = []
        for d in runs:
            r = load(d / "report.json")
            rows.append({"run": d.name, "date": r["date"], "status": r["status"], "headline": r["headline"],
                         "confidence": r["confidence"], "escalate": r["escalate"]})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        pick = st.selectbox("Open run", [d.name for d in runs])
        d = RUNS / pick
        report = load(d / "report.json")
        evidence = {e["evidence_id"]: e for e in (load(d / "evidence.json") or [])}
        left, right = st.columns([3, 2])
        with left:
            st.markdown((d / "report.md").read_text(encoding="utf-8"))
        with right:
            st.subheader("Evidence behind each claim")
            if report.get("primary_cause"):
                st.markdown("##### Primary cause")
                claim_view(report["primary_cause"], evidence)
            for h in report.get("secondary_causes", []):
                st.markdown("##### Secondary cause")
                claim_view(h, evidence)
            for rj in report.get("rejected", []):
                st.markdown("##### Rejected")
                claim_view(rj["hypothesis"], evidence)
                st.caption("; ".join(rj["reasons"]))
            for name in ("dq", "detection", "critic", "meta"):
                if (blob := load(d / f"{name}.json")) is not None:
                    with st.expander(name):
                        st.json(blob, expanded=False)

with tab_eval:
    evs = sorted((d for d in EVALS.glob("*") if (d / "results.csv").exists()), reverse=True) if EVALS.exists() else []
    if not evs:
        st.info("No eval yet. Try `axiom eval --scenarios all --systems stats_only`.")
    else:
        pick = st.selectbox("Eval run", [d.name for d in evs])
        from axiom.eval import summarize

        df = pd.read_csv(EVALS / pick / "results.csv")
        st.subheader("Summary by system")
        summ = summarize(df)
        st.dataframe(summ.round(3), width="stretch")
        st.bar_chart(summ[["acc@1", "acc@3", "numeric_hallucination"]])
        st.subheader("All runs")
        st.dataframe(df.drop(columns=["causes"]), width="stretch", hide_index=True)
