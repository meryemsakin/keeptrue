"""Freeze selected evidence, collect independent labels, and measure check errors.

An audit is local by default. Reference labels are never filled from predictions.
Snapshots keep original transcripts so a reviewer can inspect what the adapter lost.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .adapters.claude_code import load_sessions
from .checks import REGISTRY, run_check
from .config import load_config
from .loader import load_runs
from .models import CheckOutcome

VERDICTS = {"pass", "fail", "not_applicable", "unknown"}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _validate_rules(rules) -> None:
    if not rules:
        raise ValueError("the config has no rules to review")
    ids = [r.id for r in rules]
    if any(not isinstance(rid, str) or not rid for rid in ids) or len(ids) != len(set(ids)):
        raise ValueError("rule IDs must be nonempty and unique")
    for rule in rules:
        c = rule.check
        if c is None:
            continue
        if c.kind not in REGISTRY:
            raise ValueError(f"{rule.id}: unknown check kind {c.kind!r}")
        if c.kind.endswith(("command", "path", "diff")):
            try:
                re.compile(c.params["pattern"])
            except (KeyError, TypeError, re.error) as exc:
                raise ValueError(f"{rule.id}: missing or invalid regex pattern") from exc
        if c.kind == "max_final_length":
            limit = c.params.get("limit")
            if type(limit) is not int or limit < 0 or c.params.get("unit", "words") not in ("words", "chars"):
                raise ValueError(f"{rule.id}: expected nonnegative integer limit and words/chars unit")
        if c.kind == "no_repeat_loops":
            threshold = c.params.get("threshold", 3)
            if type(threshold) is not int or threshold < 1:
                raise ValueError(f"{rule.id}: threshold must be a positive integer")
        if "tools" in c.params and (
            not isinstance(c.params["tools"], list)
            or not all(isinstance(x, str) for x in c.params["tools"])
        ):
            raise ValueError(f"{rule.id}: tools must be a list of tool names")


def _source_files(paths: list[str], suffix: str) -> list[Path]:
    files = []
    for value in paths:
        path = Path(value).expanduser()
        selected = sorted(path.glob(f"*{suffix}")) if path.is_dir() else [path]
        if not selected or any(not p.is_file() or p.suffix != suffix for p in selected):
            raise ValueError(f"no {suffix} input files found at {path}")
        files.extend(selected)
    resolved = [p.resolve() for p in files]
    if len(resolved) != len(set(resolved)):
        raise ValueError("the same source file was selected more than once")
    return files


def prepare(config: str, output: str, *, logs: list[str] | None = None,
            runs: str | None = None, selection_note: str = "") -> dict:
    """Create a new audit directory atomically; never overwrite an existing audit."""
    if bool(logs) == bool(runs):
        raise ValueError("select either explicit --logs paths or a --runs directory")
    dest = Path(output).expanduser()
    if dest.exists():
        raise ValueError(f"{dest} already exists; choose a new audit directory")
    sources = _source_files(logs or [runs], ".jsonl" if logs else ".json")
    config_bytes = Path(config).expanduser().read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".audit-", dir=dest.parent) as temporary:
        root = Path(temporary) / "snapshot"
        root.mkdir()
        (root / ".gitignore").write_text("*\n", encoding="utf-8")
        (root / "sources").mkdir()
        (root / "runs").mkdir()
        (root / "config.yaml").write_bytes(config_bytes)
        scenario, rules, _ = load_config(str(root / "config.yaml"))
        _validate_rules(rules)
        hashes = {"config.yaml": _hash(config_bytes)}
        source_hashes: set[str] = set()
        for i, source in enumerate(sources, 1):
            relative = f"sources/{i:03d}{source.suffix}"
            data = source.read_bytes()
            digest = _hash(data)
            if digest in source_hashes:
                raise ValueError("duplicate source contents; select each source once")
            source_hashes.add(digest)
            (root / relative).write_bytes(data)
            hashes[relative] = digest
        if logs:
            loaded = load_sessions(str(root / "sources"), strict=True)
            trajectories = [t for t in loaded if t.model != "unknown"]
            excluded = len(sources) - len(trajectories)
        else:
            trajectories = load_runs(str(root / "sources"))
            excluded = 0
        if not trajectories:
            raise ValueError("no usable runs in the selected sources")
        identities = [(t.task_id, t.model, t.run) for t in trajectories]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate run identities (task_id, model, run); select each run once")
        normalized = _json([asdict(t) for t in trajectories]).encode("utf-8")
        (root / "runs" / "runs.json").write_bytes(normalized)
        hashes["runs/runs.json"] = _hash(normalized)
        snapshot = _hash(_json(hashes).encode("utf-8"))
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "keeptrue_version": __version__,
            "source_kind": "claude_code" if logs else "trajectory_json",
            "unit": "session" if logs else "run",
            "selection_note": selection_note,
            "selected_files": len(sources),
            "excluded_files": excluded,
            "runs": len(trajectories),
            "rules": len(rules),
            "sha256": hashes,
            "snapshot_sha256": snapshot,
        }
        (root / "manifest.json").write_text(_json(manifest), encoding="utf-8")
        labels = {
            "schema_version": 1,
            "snapshot_sha256": snapshot,
            "reviewer": {"name": "", "kind": ""},
            "labels": [
                {"case_id": f"case-{i:03d}", "task_id": t.task_id, "model": t.model,
                 "run": t.run, "rule_id": rule.id, "verdict": None, "reason": ""}
                for i, t in enumerate(trajectories, 1) for rule in rules
            ],
        }
        (root / "labels.json").write_text(_json(labels), encoding="utf-8")
        guide = [
            "# Local evidence review", "",
            "This directory contains private source transcripts. It is ignored by Git.",
            "Review source tool results as well as normalized runs. Do not publish raw logs.", "",
            "## Label before running the report", "",
            "In labels.json, set reviewer.name and reviewer.kind (human or agent).",
            "For each session/rule pair, set verdict and a reason citing a source event or tool ID:",
            "- pass: applicable, sufficient evidence, rule obeyed.",
            "- fail: applicable, sufficient evidence of a violation.",
            "- not_applicable: the rule did not govern this task/session.",
            "- unknown: incomplete evidence or ambiguous scope/meaning.",
            "Leave verdict null until reviewed. Predictions are deliberately absent here.", "",
            "A session may contain many tasks and models; do not treat its tool calls as independent runs.",
            "If these rules were not provided during the original run, this is a retrospective audit,",
            "not evidence of disobeying an instruction. Record that distinction in your reasons.", "",
            f"Scenario: {scenario}", "", "## Rules", "",
        ]
        for rule in rules:
            guide.extend([f"- {rule.id}: {rule.text}"])
        guide.extend(["", "## Evidence", "",
                      "Original events: sources/ (filenames and hashes in manifest.json).",
                      "Normalized commands, edits, tool result status and tool IDs: runs/runs.json.", ""])
        for i, t in enumerate(trajectories, 1):
            guide.append(f"- case-{i:03d}: {t.model}; {len(t.steps)} tool steps; task {t.task_id}")
        (root / "review.md").write_text("\n".join(guide) + "\n", encoding="utf-8")
        root.rename(dest)
    return manifest


def _load_verified(root: Path):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported audit schema")
    hashes = manifest.get("sha256", {})
    if not {"config.yaml", "runs/runs.json"}.issubset(hashes):
        raise ValueError("audit manifest is missing required snapshot hashes")
    for relative, expected in hashes.items():
        path = (root / relative).resolve()
        if root.resolve() not in path.parents:
            raise ValueError("snapshot path is outside the audit directory")
        if _hash(path.read_bytes()) != expected:
            raise ValueError(f"snapshot changed: {relative}; prepare a new audit")
    if _hash(_json(hashes).encode("utf-8")) != manifest.get("snapshot_sha256"):
        raise ValueError("snapshot manifest fingerprint does not match")
    _, rules, _ = load_config(str(root / "config.yaml"))
    _validate_rules(rules)
    trajectories = load_runs(str(root / "runs"))
    if {p.name for p in (root / "runs").glob("*.json")} != {"runs.json"}:
        raise ValueError("unexpected run files; prepare a new audit")
    labels = json.loads((root / "labels.json").read_text(encoding="utf-8"))
    if not isinstance(labels, dict) or not isinstance(labels.get("labels"), list):
        raise ValueError("reference file must be an object with a labels list")
    if labels.get("schema_version") != 1 or labels.get("snapshot_sha256") != manifest["snapshot_sha256"]:
        raise ValueError("labels belong to a different snapshot or schema")
    return manifest, rules, trajectories, labels


def _metrics(rows: list[dict]) -> dict:
    counts = Counter()
    for row in rows:
        reference, predicted = row["reference"], row["predicted"]
        counts["total"] += 1
        if reference is None:
            counts["pending"] += 1
        elif reference in ("not_applicable", "unknown"):
            counts[reference] += 1
            if reference == "not_applicable" and predicted == "fail":
                counts["alarms_on_not_applicable"] += 1
        else:
            counts["applicable"] += 1
            if predicted == "unknown":
                counts["abstained"] += 1
                if reference == "fail":
                    counts["abstained_violations"] += 1
            else:
                counts[{("fail", "fail"): "tp", ("pass", "fail"): "fp",
                        ("fail", "pass"): "fn", ("pass", "pass"): "tn"}
                       [(reference, predicted)]] += 1
    out = {k: counts[k] for k in (
        "total", "pending", "not_applicable", "unknown", "alarms_on_not_applicable",
        "applicable", "abstained", "abstained_violations", "tp", "fp", "fn", "tn",
    )}
    def ratio(n, d):
        return n / d if d else None
    decided = counts["applicable"] - counts["abstained"]
    out.update(
        precision=ratio(counts["tp"], counts["tp"] + counts["fp"]),
        recall=ratio(counts["tp"], counts["tp"] + counts["fn"] + counts["abstained_violations"]),
        decision_coverage=ratio(decided, counts["applicable"]),
        agreement_when_decided=ratio(counts["tp"] + counts["tn"], decided),
    )
    return out


def compare(directory: str) -> dict:
    root = Path(directory).expanduser()
    manifest, rules, trajectories, labels = _load_verified(root)
    expected = {
        (t.task_id, t.model, t.run, rule.id): (f"case-{i:03d}", t, rule)
        for i, t in enumerate(trajectories, 1) for rule in rules
    }
    seen = set()
    rows = []
    for label in labels.get("labels", []):
        if not isinstance(label, dict):
            raise ValueError("each reference label must be an object")
        key = tuple(label.get(k) for k in ("task_id", "model", "run", "rule_id"))
        if key not in expected or key in seen:
            raise ValueError("duplicate or unrecognized label identity")
        seen.add(key)
        case_id, t, rule = expected[key]
        if label.get("case_id") != case_id:
            raise ValueError("label case_id does not match its run")
        verdict = label.get("verdict")
        if verdict is not None and (not isinstance(verdict, str) or verdict not in VERDICTS):
            raise ValueError(f"{case_id}/{rule.id}: invalid reference verdict")
        if verdict is not None and not str(label.get("reason") or "").strip():
            raise ValueError(f"{case_id}/{rule.id}: reviewed labels need an evidence reason")
        outcome = run_check(rule.check, t) if rule.check else CheckOutcome(None, "no automated check")
        rows.append({"case_id": case_id, "rule_id": rule.id, "reference": verdict,
                     "predicted": outcome.verdict, "reason": label.get("reason", ""),
                     "evidence": outcome.evidence})
    if seen != set(expected):
        raise ValueError("label file is missing cases; keep unreviewed cases with verdict null")
    reviewer = labels.get("reviewer") or {}
    if not isinstance(reviewer, dict):
        raise ValueError("reviewer must be an object")
    if any(row["reference"] is not None for row in rows):
        if reviewer.get("kind") not in ("human", "agent") or not str(reviewer.get("name", "")).strip():
            raise ValueError("set reviewer.name and reviewer.kind (human or agent) before reporting labels")
    metrics = _metrics(rows)
    engine_files = ["checks.py", "models.py", "config.py", "loader.py", "audit.py"]
    engine_hash = _hash(b"".join((Path(__file__).parent / f).read_bytes() for f in engine_files))
    return {
        "schema_version": 1, "keeptrue_version": __version__, "engine_sha256": engine_hash,
        "snapshot_sha256": manifest["snapshot_sha256"], "labels_sha256": _hash((root / "labels.json").read_bytes()),
        "source_kind": manifest["source_kind"], "unit": manifest["unit"], "runs": len(trajectories),
        "reviewer": reviewer, "status": "complete" if not metrics["pending"] else "incomplete",
        "metrics": metrics,
        "per_rule": {rule.id: _metrics([r for r in rows if r["rule_id"] == rule.id]) for rule in rules},
        "cases": rows,
    }


def render_markdown(result: dict) -> str:
    """Aggregate summary only; detailed reasons and raw evidence stay in local JSON."""
    m = result["metrics"]
    def pct(value):
        return "n/a" if value is None else f"{100 * value:.1f}%"
    lines = [
        "# keeptrue reference audit", "",
        f"Status: **{result['status']}**. Reviewer kind: **{result['reviewer'].get('kind') or 'not set'}**.",
        f"Sample: {result['runs']} {result['unit']}(s), {m['total']} rule/run pairs.",
        f"Source: {result['source_kind']}. This is not a controlled model comparison.", "",
        "| Measure | Value |", "|---|---:|",
        f"| Pending reference labels | {m['pending']} |",
        f"| Applicable, known reference labels | {m['applicable']} |",
        f"| Not applicable | {m['not_applicable']} |",
        f"| Unknown reference | {m['unknown']} |",
        f"| Correct violation alerts (TP) | {m['tp']} |",
        f"| False alerts (FP) | {m['fp']} |",
        f"| Missed violations predicted pass (FN) | {m['fn']} |",
        f"| Correct passes (TN) | {m['tn']} |",
        f"| Tool abstentions | {m['abstained']} |",
        f"| Violations among abstentions | {m['abstained_violations']} |",
        f"| Alerts on inapplicable rules | {m['alarms_on_not_applicable']} |",
        f"| Violation precision | {pct(m['precision'])} |",
        f"| Violation recall, including abstentions as undetected | {pct(m['recall'])} |",
        f"| Decision coverage on applicable labels | {pct(m['decision_coverage'])} |",
        f"| Agreement when the tool decides | {pct(m['agreement_when_decided'])} |", "",
        "## Interpretation", "",
        "An empty denominator is n/a, never 100%. Pending, unknown and inapplicable reference labels",
        "are excluded from precision/recall; alerts on inapplicable rules are counted separately.",
        "An unknown tool prediction cannot earn a correct decision and lowers recall when the reference is fail.",
        "Rule/run pairs from one session are correlated, not independent trials.",
        "Agent-reviewed labels are provisional and are not independent human validation.",
        "Rule exposure must be established separately: retrospective criteria do not prove instruction violations.",
        "Completion means labels are filled, not that the sample is representative or the tool is accurate.",
        "If labels remain pending, every metric is provisional. Inspect per-rule counts and disagreements",
        "in results.json before drawing conclusions. Regex checks do not establish shell execution semantics.", "",
        "## Reproducibility", "",
        f"- keeptrue version: {result['keeptrue_version']}",
        f"- Frozen input SHA-256: `{result['snapshot_sha256']}`",
        f"- Reference labels SHA-256: `{result['labels_sha256']}`",
        f"- Evaluator source SHA-256: `{result['engine_sha256']}`", "",
        "Raw transcripts, paths, commands, reviewer reasons and model identifiers are omitted from this summary.",
        "Review any material selected for publication separately; this command does not publish it.", "",
    ]
    return "\n".join(lines)
