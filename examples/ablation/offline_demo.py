"""Exercise planning, processes, verification, resume and reporting without AI.

The synthetic agent intentionally uses the selected instruction as a switch.
The resulting differences are pipeline fixtures, never evidence about a model.
"""

import argparse
import sys
from pathlib import Path

from create_example import create
from keeptrue.ablation.execution import run_plan
from keeptrue.ablation.specification import prepare_plan
from keeptrue.cli import main


class SyntheticRunner:
    def __init__(self, root):
        self.script = root / "synthetic_agent.py"
        self.script.write_text("""import json,pathlib,sys
sys.stdin.read()
root = pathlib.Path(sys.argv[1])
if 'Check negative inputs' in (root/'AGENTS.md').read_text():
    (root/'calculator.py').write_text('def add(a, b):\\n    return a + b\\n')
print(json.dumps({'type': 'turn.completed'}))
""")

    def preflight(self, root):
        return {
            "synthetic": True,
            "purpose": "offline pipeline test; no model evidence",
        }

    def command(self, workspace, instructions):
        return [sys.executable, "-I", "-B", str(self.script), str(workspace)]

    def verifier_command(self, workspace, verifier):
        return [sys.executable, "-I", "-B", str(verifier), str(workspace)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    spec = create(
        root, "SYNTHETIC — pipeline fixture, not model evidence", "synthetic-runner-v1"
    )
    plan = root / "plan"
    prepare_plan(spec, plan)
    run_plan(plan, 4, runner=SyntheticRunner(root))
    print("SYNTHETIC PIPELINE DEMO — NO MODEL CALLS OR REAL PERFORMANCE CLAIMS")
    raise SystemExit(main(["ablate", "report", "--input", str(plan)]))
