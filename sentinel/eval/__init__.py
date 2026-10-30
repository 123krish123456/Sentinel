"""Eval harness (M6, PRD §10): run systems over scenarios x seeds and score against ground truth.

Matching rules (DECISIONS D27/D28): a predicted filter set matches a true filter set when it contains
every (dimension, value) pair of the truth (extra dimensions are allowed, fewer or empty are not).
For two-cause scenarios acc@k counts a hit on EITHER true cause, and `both@3` requires both.
"""
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd

from sentinel.baselines import SYSTEMS, run_system


def matches(pred: dict, true: dict) -> bool:
    return bool(pred) and all(pred.get(k) == v for k, v in true.items())


def overlaps(pred: dict, true: dict) -> bool:
    return any(pred.get(k) == v for k, v in true.items())


def _true_contribution(data_dir: Path, gt: dict) -> float | None:
    """Share of the observed revenue move that the injected effect accounts for (0-1)."""
    from sentinel.detection import detect

    rev = [a for a in detect(data_dir, date.fromisoformat(gt["date"]), metrics=["revenue"])]
    if not rev:
        return None
    observed = rev[0].value - rev[0].baseline
    return min(max(gt["effect_revenue_delta"] / observed, 0.0), 1.0) if observed else None


def score(gt: dict, out: dict, data_dir: Path) -> dict:
    """Per-run metrics for one system output against one ground-truth row."""
    top = [c["filters"] for c in out["causes"]]
    truths, row = gt["expected_filters"], {}
    row["alert"] = out["status"] == "anomaly"
    row["expected_alert"] = bool(gt["expected_detect"])
    row["is_none"] = gt["category"] == "none"
    row["is_data_issue"] = gt["category"] == "data_issue"
    row["flagged_data_issue"] = out["status"] == "data_issue" or out.get("dq_status") in ("WARN", "BLOCK")
    if gt["category"] == "business" and truths:
        hit = lambda k: any(matches(p, t) for p in top[:k] for t in truths)
        row["acc1"], row["acc3"] = hit(1), hit(3)
        row["overlap3"] = any(overlaps(p, t) for p in top[:3] for t in truths)
        row["both3"] = all(any(matches(p, t) for p in top[:3]) for t in truths) if len(truths) > 1 else None
        row["n_true_causes"] = len(truths)
        if out["status"] == "anomaly":
            true_c = _true_contribution(data_dir, gt)
            got = sum(c["contribution_pct"] or 0 for c in out["causes"]
                      if any(matches(c["filters"], t) for t in truths))
            row["contribution_err"] = None if true_c is None else abs(got - 100 * true_c)
    return row


def ensure_data(seed: int, root: Path = Path("data/eval"), default: Path = Path("data/sim")) -> Path:
    """Data dir for a seed: data/sim for the default seed, else generate data/eval/seed_N (~15 s)."""
    from sentinel.config import load_settings
    from sentinel.simulator.generator import simulate

    if seed == load_settings()["seed"] and (default / "ground_truth.json").exists():
        return default
    out = root / f"seed_{seed}"
    if not (out / "ground_truth.json").exists():
        simulate("scenarios/", str(out), None, None, seed, False)
    return out


def evaluate(systems: list[str], seeds: list[int], scenario_ids: list[str] | None, out_dir: Path,
             client_factory=None) -> pd.DataFrame:
    """Run every (seed, scenario, system); append rows to out_dir/results.csv as they finish."""
    out_dir.mkdir(parents=True, exist_ok=True)
    custom = client_factory
    rows = []
    for seed in seeds:
        data_dir = ensure_data(seed)
        truth = json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))
        for gt in truth:
            if scenario_ids and gt["id"] not in scenario_ids:
                continue
            for system in systems:
                t0, err = time.time(), None
                try:
                    out = run_system(system, data_dir, date.fromisoformat(gt["date"]),
                                     custom or _default_factory(system),
                                     out_dir / "runs" / f"{system}_{gt['id']}_s{seed}")
                except Exception as e:  # noqa: BLE001 - one failed run (API limits etc.) must not end the eval
                    out, err = {"status": "error", "dq_status": None, "causes": []}, f"{type(e).__name__}: {e}"
                row = {"seed": seed, "scenario": gt["id"], "date": gt["date"], "category": gt["category"],
                       "system": system, "status": out["status"], "dq_status": out.get("dq_status"),
                       "error": err, "latency_s": round(time.time() - t0, 1),
                       **{k: out.get(k) for k in ("claims", "claims_supported", "numbers",
                                                  "numbers_unsupported", "dropped", "llm_calls",
                                                  "input_tokens", "output_tokens", "cost_usd")},
                       "causes": json.dumps(out["causes"], default=str)[:1500]}
                if err is None:
                    row.update(score(gt, out, data_dir))
                rows.append(row)
                pd.DataFrame(rows).to_csv(out_dir / "results.csv", index=False)
    return pd.DataFrame(rows)


def _default_factory(system: str):
    """Per-system response cache so one system's cache hits never flatter another's latency/tokens."""
    def make():
        from sentinel.llm import LLMClient
        return LLMClient(cache_dir=Path("runs/llm_cache") / system)
    return make


def _rate(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.astype(float).mean()) if len(s) else float("nan")


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """One row per system: the PRD §10.2 metrics, aggregated across scenarios and seeds."""
    rows = []
    for system, d in df.groupby("system"):
        ok = d[d["error"].isna()]
        tp = (ok.alert & ok.expected_alert).sum()
        rows.append({
            "system": system, "runs": len(d), "errors": int(d["error"].notna().sum()),
            "det_precision": tp / ok.alert.sum() if ok.alert.sum() else float("nan"),
            "det_recall": tp / ok.expected_alert.sum() if ok.expected_alert.sum() else float("nan"),
            "false_positive_rate": _rate(ok[ok.is_none].alert),
            "acc@1": _rate(ok.acc1), "acc@3": _rate(ok.acc3), "overlap@3": _rate(ok.overlap3),
            "both@3(two-cause)": _rate(ok.both3),
            "contribution_mae_pp": _rate(ok.contribution_err),
            "evidence_support": ok.claims_supported.sum() / ok.claims.sum() if ok.claims.sum() else float("nan"),
            "numeric_hallucination": ok.numbers_unsupported.sum() / ok.numbers.sum() if ok.numbers.sum() else float("nan"),
            "data_issue_recall": _rate(ok[ok.is_data_issue].flagged_data_issue),
            "data_issue_false_flag": _rate(ok[~ok.is_data_issue].flagged_data_issue),
            "latency_s_median": float(ok.latency_s.median()) if len(ok) else float("nan"),
            "tokens_per_run": float((ok.input_tokens.fillna(0) + ok.output_tokens.fillna(0)).mean()) if len(ok) else float("nan"),
        })
    return pd.DataFrame(rows).set_index("system")


def write_report(df: pd.DataFrame, out_dir: Path) -> Path:
    s = summarize(df)
    fmt = s.copy()
    for c in fmt.columns:
        if c not in ("runs", "errors"):
            fmt[c] = fmt[c].map(lambda v: "n/a" if pd.isna(v) else f"{v:.2f}")
    per = (df[df["error"].isna() & df.category.eq("business")]
           .pivot_table(index="scenario", columns="system", values="acc1", aggfunc="mean"))
    md = ["# Sentinel eval report", "",
          (f"{len(df)} runs ({df.scenario.nunique()} scenarios, {df.seed.nunique()} seed(s), systems: "
           f"{', '.join(sorted(df.system.unique()))})."), "", "## Summary", "",
          fmt.to_markdown() if _has_tabulate() else "```\n" + fmt.to_string() + "\n```", "",
          "## acc@1 per business scenario (mean over seeds)", "",
          per.round(2).to_markdown() if _has_tabulate() else "```\n" + per.round(2).to_string() + "\n```", ""]
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        ax = s[["acc@1", "acc@3", "numeric_hallucination"]].plot.bar(figsize=(7, 4), rot=0)
        ax.set_title("Accuracy and hallucination by system")
        plt.tight_layout()
        plt.savefig(out_dir / "summary.png", dpi=110)
        plt.close()
        md += ["![summary](summary.png)", ""]
    except ImportError:  # matplotlib is optional for the chart
        pass
    path = out_dir / "report.md"
    path.write_text("\n".join(md), encoding="utf-8")
    return path


def _has_tabulate() -> bool:
    try:
        import tabulate  # noqa: F401
        return True
    except ImportError:
        return False


__all__ = ["SYSTEMS", "evaluate", "matches", "score", "summarize", "write_report"]
