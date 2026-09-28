# Validation status — 2026-09-28

The instruction-experiment workflow is **experimental**, locally implemented,
and not a validated instruction optimizer. No real model calls were made for
its current implementation validation. The existing private-session studies
test recording/scoring behavior, not the causal usefulness of instructions.

## Observed engineering evidence

- Python 3.11 on macOS: **231 tests passed** across the suite. This includes
  frozen snapshots, repeat identities, corrupted/tampered inputs, interrupted
  slots, process-group timeouts, protected files, independent acceptance,
  missing evidence, paired aggregation and HTML escaping.
- `ruff check src/keeptrue tests examples/ablation` and `git diff --check` passed.
- A wheel was built and installed into a fresh target directory. Both the
  packaged adherence demo and the synthetic experiment process pipeline passed.
  The wheel contains no private source transcripts. Build dependencies came
  from PyPI; no package was published.
- Installed `codex-cli 0.158.0-alpha.2.1`: the real, zero-inference OS-sandbox
  preflight passed. It allowed a candidate-directory write, denied a write
  outside that directory, and denied local network binding. This is a verifier
  sandbox test, not validation of a live model run or every supported OS.
- The user-selected private Claude session reparsed to the same aggregate
  observations as its frozen snapshot: 145 tool requests, 235 final-message
  words, 316 input tokens and 291,923 output tokens under the adapter's
  documented accounting. No raw session content was published.

The HTML report is generated and structurally tested. Browser visual inspection
was not completed because the browser's policy rejected local `file:` URLs.
CI now includes the offline example, but remote CI has not run on these local
changes. Local testing is not a claim that all CI platforms passed.

## Evidence still needed before broader claims

1. **Live runner integration.** After selecting a run/time budget, execute a
   small, fully frozen pilot. Record the CLI/model, every planned slot, all
   failures and externally verified outcomes. Check artifact completeness and
   actual instruction exposure before interpreting differences.
2. **Task-pack validity.** Choose representative real maintenance tasks before
   looking at outcomes. Review acceptance scripts with an independent person;
   confirm each accepts a legitimate solution and rejects a plausible wrong
   solution or no-op. Keep non-removable policy requirements fixed.
3. **Measurement validity.** Review sampled outcomes and adherence labels
   independently. Missing evidence, harness failures and task failures must
   remain distinguishable. A model-written judgment is not independent human
   validation.
4. **Practical adoption.** Observe developers outside this project completing
   setup without author intervention. Record time to first valid report,
   setup failures, decisions made from it, and repeat use. These are proposed
   observations, not existing users or success metrics. Obtain permission before
   contacting anyone or publishing their work.
5. **Release decision.** Publish a limited experimental release only after the
   above integration checks and review. Include limitations and reproducible
   public task artifacts. Do not advertise safe deletion, equivalence, token
   savings or universal instruction rankings from an underpowered pilot.

The next scope is a reviewed real pilot and usability evidence, not a dashboard,
billing system, automatic minimizer or additional provider integrations. See
[the runnable guide](instruction-experiments.md) for the exact current contract.
