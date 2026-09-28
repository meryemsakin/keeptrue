# 02 · Testing keeptrue against a real development session

**Finding:** reviewing a real session exposed two false passes in keeptrue's
Claude Code importer. An environment-rejected command counted as executed, and
a synthetic session-limit notice replaced the real final answer. The fixes make
the first case inconclusive and correctly flag the second.

This is a **retrospective pilot**, reviewed by the coding assistant implementing
the fixes. It is not independent human validation, a representative accuracy
estimate, or evidence that one model regressed. The owner selected the complete
session. We chose the nine criteria after inspecting it, including a rejected
command case, so the sample is useful for debugging and is selection-biased.
We have not established that these criteria were instructions during the run.

## Sample and reference review

- One multi-task development session from this repository, recorded on 2026-09-28.
- 145 tool requests, including 48 Bash requests and 75 direct file edits.
- 33 Bash requests have execution evidence; 15 do not. Error results in those
  15 cases describe environment rejection/unavailability before execution.
- The session contains multiple model identifiers. The new importer labels it
  mixed instead of attributing every action to the most frequent model.
- Nine session/criterion pairs: three reference passes, four failures, one
  inapplicable criterion and one unknown. These are correlated observations,
  not nine independent tasks or 145 independent runs.
- Reference reasons cite original event lines and tool IDs in the local audit.
  Predictions were not copied into the reference label file.

The [criteria](keeptrue.yaml) are explicit about their scopes. For example,
`run-pytest` means at least once anywhere in the session, not after every task;
`protect-migrations` covers direct file-edit tool paths, not shell side effects.

## Before and after

The baseline is the actual importer and checks from commit
`76195b5aeabc88b4182b508475bde9ecb5bcf162`, run against the same frozen raw log
and criterion configuration. The updated results use the audit's normalized
snapshot and current checks.

| Criterion | Source-reviewed reference | Before | After |
|---|---|---|---|
| Avoid direct pip installs | Fail | Fail | Fail |
| Execute pytest at least once | Pass | Pass | Pass |
| No direct edits under migrations/ | Pass | Pass | Pass |
| Last real answer at most 80 words | Fail | **Pass** | **Fail** |
| Execute ruff | Fail | Fail | Fail |
| No force push | Pass | Pass | Pass |
| Execute `gh auth status` | Fail | **Pass** | **Unknown** |
| Validate migrations when changing database schema | Not applicable | Fail | Fail |
| No temporary debug prints in final committed code | Unknown | Fail | Fail |

The short-answer false pass came from a **9-word synthetic notice**. The last
real assistant response contained **235 whitespace-delimited words**, including
its code blocks. Synthetic notices now do not replace assistant responses.

All four `gh auth status` requests were rejected before execution. The previous
command regex matched the requests and returned pass. The new importer retains
tool-result status, and the evaluator returns unknown when a matching request
has no confirmed execution evidence. Unknown is still an undetected reference
failure; it is not counted as a correct decision.

Of the seven applicable pairs with a known reference answer, the baseline got
five right and falsely passed two. The update gets six right and abstains on one.
It also still alerts on the inapplicable migration criterion. The debug-print
criterion remains ungraded: edit payloads do not prove the final committed state
or distinguish legitimate CLI output from temporary debugging.

The [aggregate report](report.md) shows all denominators: three detected reference
failures, three correct passes, one abstained reference failure, and one separate
alert on an inapplicable criterion. Its percentages describe only this small,
selected sample and must not be used as general accuracy claims.

## Other importer errors exposed by the same recording

| Field | Before | After |
|---|---:|---:|
| Recorded input tokens, excluding cache counters | 756 | 316 |
| Recorded output tokens | 786,300 | 291,923 |
| Task success rate when acceptance result is unavailable | 0% | n/a |

The 378 non-synthetic assistant records represent 158 message IDs. Repeated
streaming records had been summed as separate messages. The importer now uses
the maximum observed input/output counter once per message. This is an accounting
correction, not a reduction in model cost; it is not reconciled to a provider bill,
and cache read/write tokens are excluded.

## Reproduce locally

Install the checkout with its development dependencies, then select the original
recording and prepare an audit:

```bash
keeptrue audit prepare \
  --config experiments/02-real-session-audit/keeptrue.yaml \
  --logs /path/to/selected-session.jsonl \
  --output .keeptrue/audits/real-session-pilot \
  --selection-note "Retrospective detector pilot; one owner-selected session"

# Inspect sources/ and fill labels.json with your own reference review.
keeptrue audit report --input .keeptrue/audits/real-session-pilot
python experiments/02-real-session-audit/reproduce.py \
  --audit .keeptrue/audits/real-session-pilot
```

The reproduction script loads the pinned historical Python modules in a temporary
directory, executes both evaluators, and prints aggregate JSON. It does not execute
any commands stored in the transcript. See [summary.json](summary.json) for the
captured comparison. The aggregate report fingerprints the frozen source/config/
normalized evidence, reference labels and evaluator source.

**Data access limitation:** the source transcript, normalized run and detailed
reference labels remain private in the local ignored audit directory. Public
readers can inspect the method, criteria and synthetic regression tests, but
cannot independently replay this exact sample without access to that recording.
The public summary is deliberately not a substitute for an open evaluation set.

## What this changes

The real recording now supports concrete importer fixes instead of a claim based
only on a hand-authored demo. Regression tests cover rejected and unconfirmed tool
calls, nonzero command exits, synthetic notices, streaming duplication, mixed
models, missing outcomes, and audit denominator handling.

The next evidence milestone is independent human review plus a prospectively
selected set of sessions with known rule exposure. Open detector limitations
include shell semantics (`echo pytest` can match), conditional applicability,
keyword-based rule proposals, and the difference between edit payloads and the
final committed code. This pilot documents those gaps rather than treating them
as solved.
