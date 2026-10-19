from datetime import date

import typer

app = typer.Typer(help="Sentinel: autonomous root-cause analysis.")


@app.command()
def simulate(scenarios: str = "scenarios/", out: str | None = None, days: int | None = None, sessions: int | None = None,
             seed: int | None = None, plot: bool = True):
    """Generate synthetic data + ground_truth.json (+ revenue.png)."""
    from sentinel.simulator.generator import simulate as sim

    typer.echo(sim(scenarios, out, days, sessions, seed, plot))


@app.command()
def run(date_str: str, data: str = "data/sim", out: str | None = None, model: str | None = None,
        baseline: str = "weekday"):
    """Analyze one day: DQ -> detect -> investigate -> critic -> report."""
    from pathlib import Path

    from sentinel.orchestrator import Orchestrator

    try:
        day = date.fromisoformat(date_str)
    except ValueError:
        typer.echo(f"Invalid date: {date_str}. Use YYYY-MM-DD format.")
        raise typer.Exit(1)

    data_dir = Path(data)
    if not data_dir.exists():
        typer.echo(f"Data directory not found: {data_dir}")
        typer.echo("Run 'sentinel simulate --out data/sim' first.")
        raise typer.Exit(1)

    output_dir = Path(out) if out else None
    try:
        orch = Orchestrator(data_dir, output_dir, model)
        report = orch.run(day, baseline)

        typer.echo(f"\n{'=' * 60}")
        typer.echo(f"Sentinel Report — {report.date}")
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
def eval(scenarios: str = "scenarios/", systems: str = "sentinel", runs: int = 3):
    """Benchmark systems against scenarios (M6)."""
    raise typer.Exit(_todo("eval (M6)"))


def _todo(name: str) -> int:
    typer.echo(f"{name}: not implemented yet")
    return 1


if __name__ == "__main__":
    app()
