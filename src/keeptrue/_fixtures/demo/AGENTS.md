# AGENTS.md

House rules for any coding agent working in this repo.

- Use `uv` for Python packages. Never call `pip install` directly.
- Always run the test suite (`pytest`) before you finish.
- Never edit anything under `migrations/` — those are generated.
- Keep your final summary under 80 words.
- Don't leave `print()` debug statements in committed code.
- If a command fails, don't just retry it — stop and rethink.
- Format touched files with `ruff` before finishing.
- Never `git push --force`.
