# Instruction experiments (experimental)

`keeptrue ablate` measures observed task outcomes with all selected instruction
units and with each unit removed individually. It is an experimental local
workflow for a reviewed task pack, with one Codex runner. It does not discover
which rules are safe to delete, and it does not minimize a rules file.

## Try the pipeline without an account or model call

From a source checkout with keeptrue installed:

```bash
python examples/ablation/offline_demo.py --output .keeptrue/ablation-demo
```

Open `.keeptrue/ablation-demo/plan/report.html`. The runner is a **synthetic
Python fixture**, deliberately programmed to behave differently across
variants. Its results validate plumbing, not instruction effectiveness.
Missing token usage remains unknown. The example confirms its verifier rejects
the seeded bug before creating the plan.

## Prepare a real experiment

Use a dedicated task repository at a committed baseline. Keep the experiment
specification and verifiers outside that repository. The current runner accepts
ordinary tracked files, a root `AGENTS.md`, and standalone Python verifiers.
It rejects symlinks, submodules, nested instruction files, `.codex` and `.agents`
directories to keep the first implementation's treatment explicit.

```yaml
schema_version: 1
repo:
  path: /absolute/path/to/task-repository
  ref: <baseline-commit>
runner:
  kind: codex
  model: <exact-accessible-model-id>
  version: <exact-output-of-codex---version>
instructions:
  file: AGENTS.md
  preamble: |
    Preserve public APIs. Do not install packages or modify policy.txt.
  units:
    - id: check-boundaries
      text: Check boundary inputs before finishing a numeric bug fix.
tasks:
  - id: fix-add
    prompt: Fix calculator.add(a, b) so it returns the sum of two numbers.
    verifier: verify_add.py
    protected_paths: [policy.txt]
repetitions: 3
timeout_s: 300
seed: 7
```

Paths resolve relative to the spec file. `repo.ref` is resolved to a full commit
and its tracked bytes are frozen; dirty/untracked working files are excluded.
Units are human-selected semantic blocks. Keep a condition and its child rules
together. Put hard safety requirements in `preamble` and independent verification,
not in removable units. `protected_paths` contains exact files, not globs or
directories; unchanged content and executable bits are required in every variant.

```bash
keeptrue ablate plan --spec experiment.yaml --output .keeptrue/experiments/pilot
keeptrue ablate report --input .keeptrue/experiments/pilot
# Review plan.json, the source snapshot, task acceptance and total run count.
# This next command consumes model quota; it launches at most two new slots:
keeptrue ablate run --input .keeptrue/experiments/pilot --max-runs 2
keeptrue ablate report --input .keeptrue/experiments/pilot
```

With T tasks, U removable units and R repetitions, the plan contains
T × (U + 1) × R independent slots. Each task/repetition block randomizes the
variant order deterministically from the plan inputs and seed. The seed chooses
execution order, **not the model's sampling seed**. A completed baseline is
paired with each removal within its task/repetition; it is not rerun per unit.

There is no dependency-install phase or unrestricted command hook. Prepare
required runtime dependencies in a dedicated environment before planning. This
first runner is suited to small local, network-independent tasks. The example
pack can be created with `examples/ablation/create_example.py --help`.

## What is controlled

- Same committed source bytes, task prompt, verifier, model argument and CLI
  version; one instruction unit changes. Each run receives a fresh temporary
  source directory and starts a new agent session.
- The rendered treatment is injected as `developer_instructions` and written
  as the root `AGENTS.md`. Implicit document discovery is disabled with
  `project_doc_max_bytes=0`. **This is an explicit instruction-surface experiment,
  not a test of native AGENTS.md discovery.**
- The runner requires recent Codex flags: `--ignore-user-config`, `--ignore-rules`,
  `--ephemeral`, `--json`, and uses `--no-daemon`. It requests workspace-write,
  no shell network access, no approval escalation, disabled app connectors and
  web search, and no extra writable directories. It never requests unrestricted
  execution. The verifier runs through the CLI's OS sandbox as well.
- A zero-cost sandbox preflight must pass before any model call. Unsupported
  flags, a CLI version mismatch, unavailable sandbox, or changed runtime aborts.
  There is no automatic fallback to unsandboxed execution.

These controls are checked against the installed CLI and documented in the
[Codex non-interactive guide](https://learn.chatgpt.com/docs/non-interactive-mode)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

The host OS, installed packages, managed policy, system skills and model-server
changes are not hermetically pinned. A version-pinned CLI is not a frozen model
service. The shell sandbox limits writes/network; it is not a guarantee that
private host files are unreadable. Use a dedicated evaluation machine/container
with only the required credentials for sensitive tasks. Never place secrets in
the source snapshot or task prompts. These are material limits on interpretation
and execution, not claims of container-level isolation.

## External acceptance, not agent self-report

The frozen verifier is executed outside the editable tree as:

```text
python -I -B /frozen/verifiers/task.py /candidate/workspace
```

Exit **0** means acceptance passed, **1** means task failure, and other exit
codes/timeouts mean verification error. A verifier must be a reviewed standalone
script. It should test behavior and relevant invariants, catch candidate failures
appropriately, and leave existing candidate files unchanged. Editing the verifier
after planning is detected. Test deletion or a CLI exit code of zero cannot replace
external acceptance. The example checks both a known failing baseline and corrected
behavior. For refactors, choose checks that distinguish a valid refactor from a
no-op; a preexisting green test suite alone is insufficient.

Do not infer task success from model text. An agent timeout/error or malformed
event stream produces an unknown outcome, not a success or a hidden exclusion.
Verification is capped at min(agent timeout, 60 seconds). Agent timeout is wall
time, not a dollar/token spending cap. `--max-runs` limits launches in one
invocation; monitor provider-side budgets separately.

The verifier is protected from ordinary workspace edits, not hidden from all
host reads. It is not an adversarial evaluator of intentionally malicious code.

## Records and restart behavior

The private output includes `plan.json`, frozen source/spec/verifiers, runtime
metadata, and one directory per planned slot. Each completed record links hashes
to raw stdout/stderr, instruction text, verification logs, final tree metadata and
an archive of ordinary final files. Source filenames and transcripts stay local.
Reports contain aggregates and IDs; review IDs and model labels before sharing.
Hashes detect accidental changes; they are not signatures or tamper-proof storage.

Re-running resumes **unstarted** slots only. Failed, timed-out or interrupted
slots remain visible and are never automatically retried or overwritten. A
claimed directory without a finalized record is counted as interrupted. This
avoids silently selecting successful retries or billing them twice. Prepare a
new plan to repeat a failed experiment, and disclose both plans. Process groups
are terminated on normal completion, timeout and keyboard interruption; processes
that deliberately detach from that group are outside this guarantee.

## Read the result conservatively

JSON, Markdown and offline HTML show all planned slots; pass/fail, unknown,
pending and execution errors are separate. Usage and time have their own coverage
counts. Input tokens exclude reported cached input; output follows the CLI's
counter. They are not an invoice or comparable prices across providers.

The paired effect is **full success − success without the unit**, in percentage
points. Repetitions are averaged within a task, then tasks are equally weighted.
Positive values favor the full instruction set. Reports include the number of
complete pairs and distinct tasks, so missing evidence remains visible.

An exploratory 95% task-cluster bootstrap interval is shown only with at least
10 distinct tasks, complete planned pair coverage and nonconstant observed task
effects. This gate avoids a misleading zero-width interval on tiny or uniform
samples; it does not itself prove adequate statistical power. Tasks must represent
the intended use population. Multiple comparisons are unadjusted; repeats on one
task are not independent tasks. A zero observed difference is **not equivalence**.

Each unit is assessed only alongside the other full-set units. Interactions mean
you cannot combine individually removed units into a validated minimal policy.
Task applicability is explicit in the task pack chosen before running; this
version does not infer it or retrospectively filter task failures. Use separate
preplanned packs for different scopes. Instruction adherence remains the separate
`scan`/`audit` measurement, not a composite score hidden inside task success.

## Before calling this a product result

Run the pipeline on a prospectively selected real task pack; inspect raw evidence
and have another person review acceptance and a sample of outcomes. Include every
planned slot, disclose missingness, pin settings, and publish only reviewed
aggregates or redistributable synthetic data. An offline fixture, an agent-reviewed
pilot, and independent real-world validation are different evidence levels.

The immediate adoption test is whether a developer can run this without author
assistance and make a documented, defensible workflow decision. User counts,
retention, cost savings and safe deletion rates are unmeasured until that happens.
