from datetime import date

import typer

app = typer.Typer(help="Axiom: autonomous root-cause analysis.")


@app.command()
def simulate(scenarios: str = "scenarios/", out: str | None = None, days: int | None = None, sessions: int | None = None,
             seed: int | None = None, plot: bool = True):
    """Generate synthetic data + ground_truth.json (+ revenue.png)."""
    from axiom.simulator.generator import simulate as sim

    typer.echo(sim(scenarios, out, days, sessions, seed, plot))


@app.command()
def run(date_str: str, data: str = "data/sim", out: str | None = None, model: str | None = None,
        baseline: str = "weekday"):
    """Analyze one day: DQ -> detect -> investigate -> critic -> report."""
    from pathlib import Path

    from axiom.orchestrator import Orchestrator

    try:
        day = date.fromisoformat(date_str)
    except ValueError:
        typer.echo(f"Invalid date: {date_str}. Use YYYY-MM-DD format.")
        raise typer.Exit(1)

    data_dir = Path(data)
    if not data_dir.exists():
        typer.echo(f"Data directory not found: {data_dir}")
        typer.echo("Run 'axiom simulate --out data/sim' first.")
        raise typer.Exit(1)

    output_dir = Path(out) if out else None
    try:
        orch = Orchestrator(data_dir, output_dir, model)
        report = orch.run(day, baseline)

        typer.echo(f"\n{'=' * 60}")
        typer.echo(f"Axiom Report — {report.date}")
        typer.echo(f"{'=' * 60}")
        typer.echo(f"Status: {report.status.upper()}")
        typer.echo(f"Headline: {report.headline}")
        typer.echo(f"Confidence: {report.confidence:.0%}")
        if report.primary_cause:
            typer.echo(f"Primary cause: {report.primary_cause.statement}")
            typer.echo(f"  Explains: {report.primary_cause.contribution_pct:.1f}% of change")
        typer.echo(f"Escalate: {'YES' if report.escalate else 'NO'}")
        typer.echo(f"\nFull report: {orch.output_dir / 'report.md'}")
        typer.echo(f"Run ID: {orch.run_id}")

        # Show cost if available
        if orch.client.cost_usd is not None:
            typer.echo(f"LLM cost: ${orch.client.cost_usd:.4f}")
        typer.echo(f"LLM calls: {orch.client.calls} (cache hits: {orch.client.cache_hits})")

    except Exception as e:  # noqa: BLE001 - CLI needs to report analysis errors nicely
        typer.echo(f"Error running analysis: {e}")
        raise typer.Exit(1)


@app.command()
def eval(scenarios: str = "all", systems: str = "axiom,no_critic,baseline_a,stats_only",
         seeds: str = "42", out: str | None = None):
    """Benchmark systems on scenarios x seeds -> results.csv + report.md (PRD §10.3).

    scenarios: "all" or comma-separated ids (S01,S11); seeds: comma-separated ints (42,43,44).
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from axiom.eval import evaluate, summarize, write_report

    out_dir = Path(out) if out else Path("eval/results") / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    ids = None if scenarios == "all" else scenarios.split(",")
    df = evaluate(systems.split(","), [int(x) for x in seeds.split(",")], ids, out_dir)
    typer.echo(summarize(df).round(2).T.to_string())
    typer.echo(f"Wrote {out_dir / 'results.csv'} and {write_report(df, out_dir)}")


@app.command()
def daily(data: str = "data/sim", day: str | None = None, out_root: str = "runs"):
    """Scheduled entry point: analyze `day` (default: yesterday), store the run, ping Slack if configured.

    Cron: `0 7 * * * cd /path/to/Axiom && axiom daily --data data/real`
    Windows Task Scheduler: run `axiom daily` daily with the project folder as working directory.
    """
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    from axiom.delivery import notify_slack
    from axiom.orchestrator import Orchestrator

    d = date.fromisoformat(day) if day else datetime.now(UTC).date() - timedelta(days=1)
    orch = Orchestrator(Path(data), Path(out_root) / f"daily_{d}")
    report = orch.run(d)
    typer.echo(f"{d}: {report.status} - {report.headline}")
    if notify_slack(report):
        typer.echo("Slack notified.")


@app.command()
def adapt_olist(src: str, out: str = "data/olist"):
    """Convert the Olist Brazilian e-commerce CSVs in SRC to Axiom's per-day parquet layout."""
    from pathlib import Path

    from axiom.ingestion.olist import convert

    typer.echo(f"Wrote {convert(Path(src), Path(out))} days to {out}")


@app.command()
def ui():
    """Open the Streamlit UI (run list, reports, evidence behind each claim, eval dashboard)."""
    import subprocess
    import sys
    from pathlib import Path

    app_path = Path(__file__).parent / "ui" / "app.py"
    raise typer.Exit(subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path)],
                                    check=False).returncode)


if __name__ == "__main__":
    app()
