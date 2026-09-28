# 03 · Two agents on one repo: analysis scripts that quote the rules

**Finding:** the first cross-agent scan got three of Codex's seven decidable
cells wrong: two false alarms (`pip install`, `git push --force`) and one false
pass (`ruff`). The Codex session was auditing keeptrue itself. Its Python
analysis scripts, run as `python - <<'PY' … PY`, contained the rule regexes
and messages such as "No git push with --force or -f appears." The command
checks matched that heredoc text as if the shell had executed it.

**Fix:** command checks now ignore heredoc bodies fed to a non-shell program
(`python`, `cat`, `node`, …). That text is data for another program. Bodies fed
to a shell (`bash`, `sh`, `ssh host bash`, …) are still checked, and `<<<`
here-strings are not treated as heredocs.

## Sample

- One Codex session and one Claude Code session, both working in this
  repository on 2026-09-28, scored with `keeptrue scan --agent all`.
- Nine generic rules derived from the demo `AGENTS.md` with `init --from`.
  Neither agent was given these rules. This is a retrospective check, not a
  test of instruction following.
- Codex session: 52 shell commands with execution evidence (exit codes from
  `CommandExecution` records).

## Before and after (Codex column)

| Rule | Before | After | Commands matching the pattern: raw text → shell text |
|---|---|---|---|
| Use uv, never `pip install` | Fail | Pass | 2 → 0 |
| Format touched files with ruff | **Pass** | Fail | 2 → 0 |
| Never `git push --force` | Fail | Pass | 2 → 0 |

The Claude Code column did not change. Its `pip install` came from a real
virtualenv setup command, so that failure stands.

## What this is not

- **Not an agent comparison.** One session per agent, doing different tasks:
  Codex audited the code that Claude Code wrote. Rule applicability and task
  mix are uncontrolled, and the Claude Code session was still running (its
  final answer was unknown at scan time).
- **Not independent validation.** The cells were checked against the
  normalized commands by the coding assistant that made the fix, with no
  human review. Raw session logs stay private.

## Why the adapter reads execution records

Current Codex versions run nearly everything through "code mode": the agent
writes JavaScript that calls tools. From the tool-call entries alone, every
command in these sessions would be unconfirmed. The adapter uses the rollout's
`item_completed` records instead. `CommandExecution` carries the argv, exit
code and status, and `FileChange` carries per-file diffs. Before this, the
newer sessions produced almost no decidable evidence.

## Open limitations

A command check still matches `echo "git push --force"`, shell comments, and
quoted strings passed to other programs. It cannot see through shell functions,
aliases or variables. Heredoc detection is line-based and only knows the program
that receives the heredoc, not what that program does with it.

## Reproduce

The mechanism is covered by synthetic regression tests in `tests/test_checks.py`
(the heredoc cases) and `tests/test_codex.py`, which need no private data. On
your own machine, in a repository where both agents have worked:

```bash
keeptrue init --from AGENTS.md    # or your CLAUDE.md
keeptrue scan --agent all
```
