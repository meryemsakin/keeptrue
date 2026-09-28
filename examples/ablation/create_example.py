"""Create a tiny task pack outside its committed source snapshot. No model calls.

python examples/ablation/create_example.py --output .keeptrue/ablation-example
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def create(output: Path, model: str, cli_version: str) -> Path:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (output / ".gitignore").write_text("*\n")
    repo = output / "repository"
    repo.mkdir()
    (repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "policy.txt").write_text("Keep this file unchanged.\n")
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=keeptrue example",
            "-c",
            "user.email=example@keeptrue.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Synthetic task baseline",
        ],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    verifier = output / "verify_add.py"
    shutil.copyfile(Path(__file__).with_name("verify_add.py"), verifier)
    # Establish the known negative baseline using only our own fixture code.
    baseline = subprocess.run(
        [sys.executable, "-I", "-B", str(verifier), str(repo)], capture_output=True
    )
    if baseline.returncode != 1:
        raise ValueError("example verifier did not reject the seeded bug")
    spec = {
        "schema_version": 1,
        "repo": {"path": "repository", "ref": "HEAD"},
        "runner": {"kind": "codex", "model": model, "version": cli_version},
        "instructions": {
            "file": "AGENTS.md",
            "preamble": "Work locally on the requested fix. Do not install packages or modify policy.txt.",
            "units": [
                {
                    "id": "check-negative-inputs",
                    "text": "Check negative inputs and zero before finishing a numeric bug fix.",
                }
            ],
        },
        "tasks": [
            {
                "id": "fix-add",
                "prompt": "Fix calculator.add(a, b) so it returns the sum of two numbers.",
                "verifier": "verify_add.py",
                "protected_paths": ["policy.txt"],
            }
        ],
        "repetitions": 2,
        "timeout_s": 300,
        "seed": 7,
    }
    spec_path = output / "experiment.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return spec_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--model", required=True, help="exact accessible model ID; no default guess"
    )
    parser.add_argument(
        "--cli-version", required=True, help="exact output of codex --version"
    )
    args = parser.parse_args()
    print(create(args.output, args.model, args.cli_version))
