"""Baseline A: one LLM agent with a sandboxed pandas tool and no Sentinel machinery (PRD §10.1).

The model gets the raw per-day parquet tables and the prompt "analyze yesterday vs baseline and explain
any significant change". It writes its own pandas code and its own numbers. No DQ gate, no metric tree,
no Critic, no evidence verifier in the loop.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from sentinel.evidence import EvidenceStore

TIMEOUT_S, MAX_OUT, MAX_CALLS = 40, 6000, 12
_ENV = {k: v for k, v in os.environ.items()
        if k in {"SYSTEMROOT", "PATH", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME", "TEMP", "TMP"}}

SYSTEM = """You are a data analyst. Tables are parquet files, one per day: sessions, events, orders.
Use the run_python tool to analyze them with pandas. In the sandbox, `load(table, start, end)` returns a
DataFrame for ISO dates start..end inclusive (end optional), and `pd`, `np` are imported. Print what you
need; only printed output is returned. Columns: sessions(session_id, ts, platform, os, app_version,
country, traffic_source, device_type, new_vs_returning, category), events(event_id, session_id, ts,
event_type, error_code, payment_method, category), orders(order_id, session_id, ts, payment_method,
amount, items). Compare the target day to the previous 28 days (same weekday is a good baseline).
When done, call submit_answer."""

_PRE = '''
import sys, pandas as pd, numpy as np
from pathlib import Path
_D = Path(sys.argv[1])
def load(table, start, end=None):
    end = end or start
    days = pd.date_range(start, end).strftime("%Y-%m-%d")
    fs = [_D / table / f"{d}.parquet" for d in days if (_D / table / f"{d}.parquet").exists()]
    return pd.concat([pd.read_parquet(f).assign(day=f.stem) for f in fs], ignore_index=True) if fs else pd.DataFrame()
'''

TOOLS = [
    {"name": "run_python", "description": "Run pandas code; returns stdout (truncated).",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "submit_answer", "description": "Final answer. Call once.", "input_schema": {
        "type": "object", "properties": {
            "status": {"type": "string", "enum": ["normal", "anomaly", "data_issue"]},
            "causes": {"type": "array", "items": {"type": "object", "properties": {
                "filters": {"type": "object", "additionalProperties": {"type": "string"},
                            "description": "dimension -> value, e.g. {\"category\": \"electronics\"}"},
                "statement": {"type": "string"}, "contribution_pct": {"type": "number"}},
                "required": ["filters", "statement"]}}},
            "required": ["status", "causes"]}},
]


def _nums(text: str) -> list[float]:
    out = []
    for x in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text):
        try:
            out.append(float(x.replace(",", "")))
        except ValueError:
            pass
    return out


def run_sandboxed(code: str, data_dir: Path) -> str:
    """Subprocess with an allowlisted env (no API keys), temp cwd, hard timeout. ponytail: no network or
    memory limit on Windows; add a container if untrusted prompts ever reach this."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "s.py"
        script.write_text(_PRE + "\n" + code, encoding="utf-8")
        try:
            p = subprocess.run([sys.executable, str(script), str(Path(data_dir).resolve())], check=False,
                               capture_output=True, text=True, timeout=TIMEOUT_S, cwd=tmp, env=_ENV)
        except subprocess.TimeoutExpired:
            return f"TimeoutError: exceeded {TIMEOUT_S}s"
    text = p.stdout + (("\n" + p.stderr[-1500:]) if p.returncode else "")
    return text[:MAX_OUT] + ("...[truncated]" if len(text) > MAX_OUT else "")


def run(client, data_dir: Path, day: date, store: EvidenceStore) -> dict:
    """Returns {status, causes, claims, calls}; every tool output is stored as evidence."""
    messages = [{"role": "user", "content":
                 f"Analyze {day} vs baseline and explain any significant change in revenue."}]
    calls, ans, nudges = 0, None, 0
    for _ in range(MAX_CALLS + 6):
        spent = calls >= MAX_CALLS
        tools = [TOOLS[1]] if spent else TOOLS
        resp = client.create(SYSTEM, messages, tools,
                             {"type": "tool", "name": "submit_answer"} if spent else None)
        messages.append({"role": "assistant", "content": resp["content"]})
        uses = [b for b in resp["content"] if b["type"] == "tool_use"]
        if not uses:
            if nudges >= 2:
                break
            nudges += 1
            messages.append({"role": "user", "content": "Call submit_answer now."})
            continue
        results = []
        for u in uses:
            if u["name"] == "submit_answer":
                ans = u["input"]
                break
            calls += 1
            code = str(u["input"].get("code", ""))
            out = run_sandboxed(code, data_dir)
            ev = store.add("run_python", {"code": code[:2000]}, None,
                           {"output": out, "numbers": _nums(out)})
            results.append({"type": "tool_result", "tool_use_id": u["id"],
                            "content": json.dumps({"evidence_id": ev.evidence_id, "output": out})})
        if ans is not None:
            break
        messages.append({"role": "user", "content": results})
    ans = ans if isinstance(ans, dict) else {}
    ids = list(store.items)
    causes = [{"filters": {str(k): str(v) for k, v in c["filters"].items()}
               if isinstance(c.get("filters"), dict) else {},
               "contribution_pct": _num(c.get("contribution_pct")),
               "statement": str(c.get("statement", "")), "evidence_ids": ids}
              for c in (ans.get("causes") if isinstance(ans.get("causes"), list) else []) if isinstance(c, dict)]
    status = ans.get("status") if ans.get("status") in ("normal", "anomaly", "data_issue") else "normal"
    return {"status": status, "causes": causes, "calls": calls}


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
