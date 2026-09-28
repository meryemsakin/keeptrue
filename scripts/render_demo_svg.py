"""Render `keeptrue demo` to docs/demo.svg (colour-accurate, renders on GitHub).

    python scripts/render_demo_svg.py

We use rich's SVG export instead of a screen-recorded GIF so the hero image is
self-contained (no vhs/asciinema needed), version-controlled, and crisp. A real
terminal GIF can be produced later from docs/demo.tape.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI

from keeptrue.cli import _demo_dir, _run_report

OUT = Path(__file__).resolve().parent.parent / "docs" / "demo.svg"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    console = Console(record=True, width=94)
    d = _demo_dir()
    note = (
        "Demo data: illustrative runs bundled with the tool — works offline, for "
        "free. `keeptrue check` scores your own runs."
    )
    _run_report(str(d / "keeptrue.yaml"), str(d / "runs"), note=note, console=console)
    console.save_svg(str(OUT), title="keeptrue demo", theme=MONOKAI)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
