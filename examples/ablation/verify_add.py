"""Trusted, frozen task acceptance. Exit 0=pass, 1=fail, other=verifier error.

Do not put verifier files into the editable source repository. This checks
ordinary correctness; it is not an adversarial code-execution security grader.
"""

import importlib.util
import sys
from pathlib import Path


def main():
    workspace = Path(sys.argv[1])
    try:
        spec = importlib.util.spec_from_file_location(
            "candidate", workspace / "calculator.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = [(2, 3, 5), (-2, -3, -5), (-2, 3, 1), (0, 0, 0), (1.5, 2.5, 4.0)]
        for a, b, expected in cases:
            if module.add(a, b) != expected:
                print(f"FAIL: add({a}, {b}) != {expected}")
                return 1
    except (
        ImportError,
        AttributeError,
        SyntaxError,
        TypeError,
        NameError,
        OSError,
    ) as exc:
        print(f"Candidate failure: {exc}")
        return 1
    print("PASS: all fixed acceptance cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
