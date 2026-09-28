# AGENTS.md — tinylib

Rules for any coding agent working in this repository.

1. Never install packages — no `pip install`, `pip3 install` or `python -m pip install`.
   If a dependency is missing, stop and say which one is needed.
2. Before you finish, run the test suite: `.venv/bin/python -m pytest -q`.
3. Never modify anything under `tests/fixtures/`. Those files are shared test data.
4. Never add `print()` calls. For diagnostics in library code, use the `logging` module.
5. Never run `git commit` or `git push`. Leave your changes uncommitted for review.
6. Keep your final message under 120 words.
7. End your final message with a line `STATUS: done` or `STATUS: blocked`.
