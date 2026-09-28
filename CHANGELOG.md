# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Codex CLI adapter. `scan --agent codex` scores this repo's Codex sessions
  (matched by the recorded working directory); `--agent all` combines Claude Code
  and Codex. The log format is detected per file, so `--logs` and `audit prepare`
  accept either. Newer rollouts use `CommandExecution`/`FileChange` records as
  evidence; older ones use `exec_command` output and `apply_patch` envelopes.
- `scan --strict` for fail-fast imports. Default exploratory scans report each
  unreadable or malformed session and continue with the remaining files.
- Local `audit prepare` and `audit report`: frozen input snapshots, blank reference
  labels, explicit reviewer provenance, confusion counts, abstentions and per-rule results.
- A retrospective real-session case study with a reproducible pre-change comparison.

### Fixed
- Command checks ignore heredoc bodies fed to non-shell programs (for example
  `python - <<'PY'`). They matched analysis scripts that quoted a rule's pattern
  (see experiments/03).
- Diff evidence quotes the matching line instead of the change's first line.
- Claude Code tool requests are joined with their results; unconfirmed execution
  and errored edits no longer provide confident evidence of completed actions.
- Streaming message usage is deduplicated, synthetic notices do not replace the
  assistant response, and mixed-model sessions retain an explicit mixed label.
- Missing task outcomes show `n/a` rather than 0% success; partial outcome labels
  use a consistent subset for both success rates and per-success costs.
- Unknown evidence and invalid check parameters are excluded from pass-rate
  denominators, with sample and unknown counts displayed.

### Changed
- The animated demo now uses the terminal's observed-drop language and includes
  decidable-run and unknown counts, with an always-visible illustrative-data label.
- Comparisons are described as observed adherence drops, not causal model regressions.
- Malformed session JSONL is rejected as a whole file instead of silently omitting
  events. Audits fail; default scans explicitly report skipped files.

## [0.1.0] - 2026-09-28

Initial release.

### Added
- `keeptrue demo` — score bundled, illustrative runs with no API key and no
  cost, showing a model upgrade silently dropping its `pip`→`uv` and `pytest`
  rules.
- `keeptrue check --config … --runs …` — score your own recorded runs.
- `keeptrue init` — write a starter `keeptrue.yaml`.
- Deterministic checks: `forbidden_command`, `required_command`,
  `forbidden_path`, `required_path`, `forbidden_in_diff`, `required_in_diff`,
  `max_final_length`, `no_repeat_loops`.
- Karne report: per-rule adherence across runs, regression callout vs the
  baseline model, and a cost/reliability table (tokens- and $-per-success).
- Trajectory format documented in `docs/trajectory-format.md`.

[0.1.0]: https://github.com/meryemsakin/keeptrue/releases/tag/v0.1.0
