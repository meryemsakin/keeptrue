# keeptrue reference audit

Status: **complete**. Reviewer kind: **agent**.
Sample: 1 session(s), 9 rule/run pairs.
Source: claude_code. This is not a controlled model comparison.

| Measure | Value |
|---|---:|
| Pending reference labels | 0 |
| Applicable, known reference labels | 7 |
| Not applicable | 1 |
| Unknown reference | 1 |
| Correct violation alerts (TP) | 3 |
| False alerts (FP) | 0 |
| Missed violations predicted pass (FN) | 0 |
| Correct passes (TN) | 3 |
| Tool abstentions | 1 |
| Violations among abstentions | 1 |
| Alerts on inapplicable rules | 1 |
| Violation precision | 100.0% |
| Violation recall, including abstentions as undetected | 75.0% |
| Decision coverage on applicable labels | 85.7% |
| Agreement when the tool decides | 100.0% |

## Interpretation

An empty denominator is n/a, never 100%. Pending, unknown and inapplicable reference labels
are excluded from precision/recall; alerts on inapplicable rules are counted separately.
An unknown tool prediction cannot earn a correct decision and lowers recall when the reference is fail.
Rule/run pairs from one session are correlated, not independent trials.
Agent-reviewed labels are provisional and are not independent human validation.
Rule exposure must be established separately: retrospective criteria do not prove instruction violations.
Completion means labels are filled, not that the sample is representative or the tool is accurate.
If labels remain pending, every metric is provisional. Inspect per-rule counts and disagreements
in results.json before drawing conclusions. Regex checks do not establish shell execution semantics.

## Reproducibility

- keeptrue version: 0.1.0
- Frozen input SHA-256: `ef9503e54e840157fe97e78c3f1f1be95682f2986ed8c03cbd34f74da4f81544`
- Reference labels SHA-256: `024c31f7e84ad22a316035acdc7131226da2a5b7add1118592a1eeaa7620a185`
- Evaluator source SHA-256: `90ada2a06711b2b10474c94af032f1d5489f8a7194ec673984d5de3757f9d4cc`

Raw transcripts, paths, commands, reviewer reasons and model identifiers are omitted from this summary.
Review any material selected for publication separately; this command does not publish it.
