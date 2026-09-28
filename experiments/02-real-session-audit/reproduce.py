"""Compare the frozen private pilot with the reviewed pre-audit revision.

Run from a checkout of keeptrue. Source transcripts are read, never executed.
Only aggregate counts and authored rule IDs appear in the output.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from keeptrue.audit import compare
from keeptrue.engine import cost_stats
from keeptrue.loader import load_runs

BASELINE = "76195b5aeabc88b4182b508475bde9ecb5bcf162"
FILES = ["__init__.py", "models.py", "checks.py", "engine.py", "config.py",
         "adapters/__init__.py", "adapters/claude_code.py"]
BASELINE_SCRIPT = """
import json, sys
from keeptrue.adapters.claude_code import load_sessions
from keeptrue.config import load_config
from keeptrue.engine import cost_stats
from keeptrue.checks import run_check
root = sys.argv[1]
_, rules, _ = load_config(root + '/config.yaml')
trajs = [t for t in load_sessions(root + '/sources') if t.model != 'unknown']
if len(trajs) != 1:
    raise ValueError('this pilot comparison expects exactly one real session')
t = trajs[0]
print(json.dumps({
    'input_tokens': t.usage.input_tokens,
    'output_tokens': t.usage.output_tokens,
    'success_rate': cost_stats([t])[t.model]['success_rate'],
    'final_words': len(t.final_message.split()),
    'predictions': {r.id: ('pass' if run_check(r.check, t).passed else 'fail')
                    for r in rules if r.check},
}))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", default=".keeptrue/audits/real-session-pilot")
    args = parser.parse_args()
    root = Path(args.audit).resolve()
    report = compare(str(root))  # validates immutable inputs and references first
    if report["source_kind"] != "claude_code" or report["runs"] != 1:
        parser.error("this comparison requires one Claude Code session")
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="keeptrue-baseline-") as temporary:
        for name in FILES:
            destination = Path(temporary) / "keeptrue" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(subprocess.check_output(
                ["git", "show", f"{BASELINE}:src/keeptrue/{name}"], cwd=repo,
            ))
        baseline = json.loads(subprocess.check_output(
            [sys.executable, "-c", BASELINE_SCRIPT, str(root)], cwd=temporary,
            env={**os.environ, "PYTHONPATH": temporary}, text=True,
        ))
    trajectories = load_runs(str(root / "runs"))
    t = trajectories[0]
    success_rate = cost_stats(trajectories)[t.model]["success_rate"]
    raw_assistant_records = 0
    message_ids = set()
    for source in sorted((root / "sources").glob("*.jsonl")):
        for index, line in enumerate(source.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            event = json.loads(line)
            message = event.get("message") or {}
            if event.get("type") == "assistant" and isinstance(message, dict):
                if message.get("model") == "<synthetic>":
                    continue
                raw_assistant_records += 1
                message_ids.add((source.name, message.get("id") or index))
    result = {
        "baseline_revision": BASELINE,
        "snapshot_sha256": report["snapshot_sha256"],
        "reviewer_kind": report["reviewer"].get("kind"),
        "source": {
            "sessions": 1, "tool_requests": len(t.steps),
            "bash_requests": sum(s.tool == "bash" for s in t.steps),
            "bash_with_execution_evidence": sum(s.tool == "bash" and s.exit_code is not None for s in t.steps),
            "bash_without_execution_evidence": sum(s.tool == "bash" and s.exit_code is None for s in t.steps),
            "assistant_records_excluding_synthetic": raw_assistant_records,
            "unique_assistant_message_ids": len(message_ids),
            "mixed_models": t.model.startswith("mixed: "),
        },
        "before": baseline,
        "after": {
            "input_tokens": t.usage.input_tokens, "output_tokens": t.usage.output_tokens,
            "success_rate": None if math.isnan(success_rate) else success_rate,
            "final_words": len(t.final_message.split()),
            "predictions": {row["rule_id"]: row["predicted"] for row in report["cases"]},
        },
        "reference": {row["rule_id"]: row["reference"] for row in report["cases"]},
        "after_metrics": report["metrics"],
        "limits": "One retrospectively selected session; agent-reviewed labels; raw transcripts private. "
                  "Usage excludes cache reads/writes and is not a provider billing total.",
    }
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
