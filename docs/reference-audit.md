# Validate a check against real evidence

An audit measures how well keeptrue agrees with reviewed reference labels.
It does not run an agent, call a model, or decide its own reference answers.

## 1. Choose a scope before inspecting results

Start with a single repository and a few explicit rules. Record why you selected
the sample. A prospectively selected set of tasks gives stronger evidence than
cherry-picked failure cases. One session may contain many tasks, but the Claude
Code adapter treats the whole session as one unit. Do not count its tool calls
as independent runs or compare mixed-model sessions as if they used one model.

For an instruction-following claim, establish which rules were actually supplied
to the agent and when. Applying a rule after the run is a retrospective detector
audit. It cannot establish that the agent disobeyed an instruction.

## 2. Freeze selected inputs

```bash
keeptrue audit prepare \
  --config keeptrue.yaml \
  --logs /path/to/selected-session.jsonl \
  --output .keeptrue/audits/pilot \
  --selection-note "One repo; sessions selected before scoring"
```

`--logs` accepts one or more explicit JSONL paths or directories (nonrecursive).
It never searches all projects implicitly. Use `--runs ./runs` instead to audit
JSON trajectories from another harness. The source kind is recorded; user-supplied
JSON is not automatically described as real Claude Code data.

The new directory contains:

| File | Purpose |
|---|---|
| `sources/` | Byte-for-byte copies of the selected input files |
| `config.yaml` | Frozen rule definitions |
| `runs/runs.json` | Normalized evidence, including tool IDs and result status |
| `manifest.json` | Source kind, selection note, counts and input SHA-256 hashes |
| `review.md` | Labeling instructions and case list |
| `labels.json` | Blank reference labels, with no suggested predictions |

Existing audit directories are never overwritten. Duplicate source contents or
run identities, invalid checks, malformed JSONL, and empty samples are rejected.
Each source must be a complete export; copy an actively written log after it has
finished rather than grading a truncated last line.

The directory's `.gitignore` excludes all its contents. It contains private
transcripts, not automatically anonymized data. No data is uploaded. An arbitrary
external folder may not live in a Git repository; its `.gitignore` is not access
control or encryption.

## 3. Record reference labels before inspecting predictions

Open `review.md`, inspect the original source events and their tool results, then
edit `labels.json`. Normalized evidence alone can hide adapter mistakes.
Give each reviewed label a reason with source line numbers or tool IDs.

```json
{
  "reviewer": {"name": "Reviewer name", "kind": "human"}
}
```

Use `kind: "agent"` for an assistant review. Such labels are provisional and
must not be presented as independent human validation. Do not replace the rest
of the generated label file with the example above.

| Verdict | Meaning |
|---|---|
| `pass` | Rule applicable, sufficient evidence, requirement met |
| `fail` | Rule applicable, sufficient evidence of a violation |
| `not_applicable` | The rule did not govern this task or session |
| `unknown` | Evidence incomplete or meaning/scope ambiguous |
| `null` | Review not yet done |

Keep case IDs, task IDs, model labels, run numbers and rule IDs unchanged. Every
rule/run pair must remain present, including those not reviewed. Labels are tied
to the frozen input fingerprint. Changing the sample or the rules requires a new
audit, which also avoids quietly relabeling a different experiment.

## 4. Compare and inspect disagreements

```bash
keeptrue audit report --input .keeptrue/audits/pilot
```

This checks input hashes and label identities, then runs the current evaluator
against the frozen normalized evidence. It writes `results.json` with per-case
and per-rule details, plus an aggregate `report.md`. Re-running refreshes these
two derived outputs; it never edits the snapshot or reference labels.

Exit status is 0 for a completed reference review, 2 when labels remain pending,
and 1 for invalid input. A completed review does not imply that the tool passed
an accuracy threshold. Missing and malformed labels cannot silently count as
correct answers.

Treat a violation as the positive class:

- TP: reference fail, prediction fail.
- FP: reference pass, prediction fail.
- FN: reference fail, prediction pass.
- TN: reference pass, prediction pass.
- Abstention: reference pass/fail, prediction unknown.

Precision is `TP / (TP + FP)`. Recall is
`TP / (TP + FN + abstentions_on_reference_fail)` so abstaining on a violation
cannot inflate recall. Agreement is reported only on decided, applicable pairs,
next to decision coverage. An empty denominator is `n/a`.

Pending, unknown and inapplicable reference labels are excluded from those
metrics. Alerts on inapplicable rules are counted separately; they still matter
to users. Always report those counts, sample size and coverage with percentages.

The report fingerprints the inputs, labels and evaluator source. These detect
accidental changes; they are not signed attestations. Rerunning with a new
evaluator uses the frozen imported evidence. To evaluate an importer change,
prepare a new audit from the same raw sources and review the new normalization.

## 5. Share a defensible case study

State who labeled the evidence, how it was selected, which rules were known to
apply, where the tool disagreed, and what decision changed. Preserve before/after
results when fixing a detector. Do not adjust the reference labels just to agree
with the implementation.

`report.md` omits raw commands, paths, model identifiers, reviewer names and
free-text reasons. `results.json`, `labels.json` and the source directories do
not. Review selected examples before copying them to public documentation. If
the underlying data stays private, describe the procedure as reproducible while
being explicit that outsiders cannot independently replay that exact sample.

See [the first real-session pilot](../experiments/02-real-session-audit/) for an
example with disclosed errors and limitations, rather than a benchmark claim.
