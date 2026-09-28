"""Frozen ablation plans isolate treatment inputs without executing an agent."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from keeptrue.ablation.specification import load_plan, prepare_plan


def git(repo, *arguments):
    return (
        subprocess.run(
            ["git", "-C", str(repo), *arguments], check=True, capture_output=True
        )
        .stdout.decode()
        .strip()
    )


@pytest.fixture
def inputs(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "app.py").write_text("def answer():\n    return 42\n")
    (repo / "AGENTS.md").write_text(
        "Original instructions are replaced by frozen treatments.\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Fixture snapshot")
    verifier = tmp_path / "verify.py"
    verifier.write_text("raise SystemExit(0)\n")
    spec = {
        "schema_version": 1,
        "repo": {"path": "repository", "ref": "HEAD"},
        "runner": {
            "kind": "codex",
            "model": "example-pinned-model",
            "version": "1.2.3",
        },
        "instructions": {
            "file": "AGENTS.md",
            "preamble": "Keep hard constraints fixed.",
            "units": [
                {"id": "tests", "text": "Run the relevant tests."},
                {"id": "style", "text": "Use clear function names."},
            ],
        },
        "tasks": [
            {
                "id": "answer",
                "prompt": "Implement the answer.",
                "verifier": "verify.py",
                "protected_paths": ["locked.txt"],
            }
        ],
        "repetitions": 2,
        "timeout_s": 120,
        "seed": 17,
    }
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return repo, path, spec, tmp_path / "plan"


def write_spec(inputs):
    inputs[1].write_text(yaml.safe_dump(inputs[2], sort_keys=False))


def test_freezes_commit_verifiers_and_independent_replicates(inputs):
    repo, source, spec, output = inputs
    tracked = (repo / "app.py").read_bytes()
    (repo / "app.py").write_text("dirty worktree must not be included\n")
    (repo / "untracked.txt").write_text("untracked source must not be included\n")
    before_status = git(repo, "status", "--porcelain")
    plan = prepare_plan(source, output)
    assert plan == load_plan(output)
    assert plan["commit"] == git(repo, "rev-parse", "HEAD")
    assert (output / "specification.yaml").read_bytes() == source.read_bytes()
    assert (output / "verifiers/answer.py").read_text() == "raise SystemExit(0)\n"
    with tarfile.open(output / plan["snapshot"]) as archive:
        assert archive.extractfile("app.py").read() == tracked
        assert "untracked.txt" not in archive.getnames()
    assert git(repo, "status", "--porcelain") == before_status
    assert (
        plan["runner"]["instruction_strategy"]
        == "explicit-developer-instructions-and-agents-md"
    )
    assert len(plan["slots"]) == 6
    assert len({slot["id"] for slot in plan["slots"]}) == 6
    for repeat in (0, 1):
        assert {
            slot["variant"] for slot in plan["slots"] if slot["repeat"] == repeat
        } == {"full", "without:tests", "without:style"}
    assert plan["tasks"][0]["protected_paths"] == ["locked.txt"]
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / ".gitignore").read_text() == "*\n"
    assert (output / "plan.json").stat().st_mode & 0o077 == 0


def test_plan_identity_and_order_are_reproducible_and_seeded(inputs):
    _, source, spec, output = inputs
    first = prepare_plan(source, output)
    second = prepare_plan(source, output.with_name("second"))
    assert first["plan_id"] == second["plan_id"]
    assert first["slots"] == second["slots"]
    spec["seed"] += 1
    write_spec(inputs)
    third = prepare_plan(source, output.with_name("third"))
    assert first["plan_id"] != third["plan_id"]
    assert not {s["id"] for s in first["slots"]} & {s["id"] for s in third["slots"]}


@pytest.mark.parametrize("field", ["model", "version", "executable"])
def test_runner_settings_are_in_identity(inputs, field):
    _, source, spec, output = inputs
    first = prepare_plan(source, output)
    spec["runner"][field] = "different-pinned-value"
    write_spec(inputs)
    second = prepare_plan(source, output.with_name("second"))
    assert first["plan_id"] != second["plan_id"]
    assert first["slots"][0]["id"] != second["slots"][0]["id"]


def test_plan_preparation_does_not_execute_verifier(inputs):
    _, source, _, output = inputs
    marker = output.with_name("must-not-exist")
    (source.parent / "verify.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    prepare_plan(source, output)
    assert not marker.exists()


def test_existing_output_is_never_overwritten(inputs):
    _, source, _, output = inputs
    output.mkdir()
    (output / "valuable.txt").write_text("Keep me")
    with pytest.raises(ValueError, match="already exists"):
        prepare_plan(source, output)
    assert (output / "valuable.txt").read_text() == "Keep me"


@pytest.mark.parametrize(
    "location,field,value,match",
    [
        ("top", "unexpected", True, "unknown keys"),
        ("top", "schema_version", True, "schema_version"),
        ("top", "repetitions", 0, "repetitions"),
        ("top", "repetitions", True, "repetitions"),
        ("top", "repetitions", 101, "repetitions"),
        ("top", "timeout_s", 3601, "timeout_s"),
        ("top", "seed", 1.5, "seed"),
        ("top", "tasks", [], "tasks"),
        ("runner", "kind", "claude", "runner.kind"),
        ("runner", "model", "", "runner.model"),
        ("runner", "version", "latest", "specific CLI version"),
        ("runner", "extra", "", "unknown keys"),
        ("repo", "ref", "HEAD\n", "single line"),
        ("repo", "path", "repository/../repository", "traversal"),
        ("instructions", "file", "CLAUDE.md", "AGENTS.md"),
        ("instructions", "file", "../AGENTS.md", "AGENTS.md"),
        ("instructions", "units", [], "nonempty list"),
        ("task", "id", "../../bad", "slug"),
        ("task", "id", "bad:id", "slug"),
        ("task", "verifier", "../verify.py", "traversal"),
        ("task", "protected_paths", ["../app.py"], "relative path"),
        ("task", "protected_paths", ["/app.py"], "relative path"),
        ("task", "protected_paths", ["C:\\app.py"], "relative path"),
        ("task", "protected_paths", ["AGENTS.md"], "treatment file"),
        ("task", "protected_paths", [".git/config"], "Git metadata"),
        ("task", "protected_paths", ["app.py", "app.py"], "duplicate protected"),
    ],
)
def test_invalid_design_rejected_before_creation(inputs, location, field, value, match):
    _, source, spec, output = inputs
    target = (
        spec
        if location == "top"
        else spec["tasks"][0]
        if location == "task"
        else spec[location]
    )
    target[field] = value
    write_spec(inputs)
    with pytest.raises(ValueError, match=match):
        prepare_plan(source, output)
    assert not output.exists()


@pytest.mark.parametrize("kind", ["tasks", "units"])
def test_duplicate_semantic_ids_rejected(inputs, kind):
    _, source, spec, output = inputs
    collection = spec["tasks"] if kind == "tasks" else spec["instructions"]["units"]
    collection.append(collection[0].copy())
    write_spec(inputs)
    with pytest.raises(ValueError, match="duplicate"):
        prepare_plan(source, output)


def test_duplicate_yaml_keys_rejected(inputs):
    _, source, _, output = inputs
    source.write_text(source.read_text() + "seed: 99\n")
    with pytest.raises(ValueError, match="duplicate specification key"):
        prepare_plan(source, output)


@pytest.mark.parametrize(
    "name",
    [
        "nested/AGENTS.md",
        "CLAUDE.md",
        "AGENTS.override.md",
        ".codex/config.toml",
        "nested/.agents/skills/custom.md",
    ],
)
def test_uncontrolled_instruction_surfaces_rejected(inputs, name):
    repo, source, _, output = inputs
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("uncontrolled instructions")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Add uncontrolled surface")
    with pytest.raises(ValueError, match="uncontrolled"):
        prepare_plan(source, output)
    assert not output.exists()


def test_tracked_symlinks_rejected(inputs):
    repo, source, _, output = inputs
    (repo / "link").symlink_to("app.py")
    git(repo, "add", "link")
    git(repo, "commit", "-qm", "Add symbolic link")
    with pytest.raises(ValueError, match="unsupported link"):
        prepare_plan(source, output)


def test_gitlinks_rejected_without_initializing_submodule(inputs):
    repo, source, _, output = inputs
    git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{git(repo, 'rev-parse', 'HEAD')},vendor",
    )
    git(repo, "commit", "-qm", "Add gitlink")
    with pytest.raises(ValueError, match="submodules"):
        prepare_plan(source, output)


@pytest.mark.parametrize(
    "attribute", ["app.py export-ignore\n", "app.py export-subst\n"]
)
def test_export_attributes_cannot_silently_change_snapshot(inputs, attribute):
    repo, source, _, output = inputs
    (repo / ".gitattributes").write_text(attribute)
    (repo / "app.py").write_text("# $Format:%H$\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Export attribute")
    with pytest.raises(ValueError, match="archive (omits|differs)"):
        prepare_plan(source, output)


@pytest.mark.parametrize(
    "filename", ["source.tar", "specification.yaml", "verifiers/answer.py"]
)
def test_modified_frozen_input_rejected(inputs, filename):
    _, source, _, output = inputs
    prepare_plan(source, output)
    path = output / filename
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="frozen input changed"):
        load_plan(output)


def test_modified_manifest_rejected(inputs):
    _, source, _, output = inputs
    plan = prepare_plan(source, output)
    plan["timeout_s"] = 45
    (output / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="fingerprint changed"):
        load_plan(output)


@pytest.mark.parametrize("directory", [False, True])
def test_symlinked_frozen_input_rejected_even_with_identical_bytes(inputs, directory):
    _, source, _, output = inputs
    prepare_plan(source, output)
    original = output / ("verifiers" if directory else "verifiers/answer.py")
    moved = output.with_name("moved-input")
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=directory)
    with pytest.raises(ValueError, match="symlink"):
        load_plan(output)


def rewrite_plan(output, plan):
    canonical = json.dumps(
        {k: v for k, v in plan.items() if k not in {"plan_id", "created_at"}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    plan["plan_id"] = hashlib.sha256(canonical).hexdigest()
    (output / "plan.json").write_text(json.dumps(plan))


def test_recomputed_fingerprint_cannot_hide_invalid_slot_design(inputs):
    _, source, _, output = inputs
    plan = prepare_plan(source, output)
    plan["slots"].pop()
    rewrite_plan(output, plan)
    with pytest.raises(ValueError, match="slots do not match"):
        load_plan(output)


def test_recomputed_fingerprint_cannot_escape_frozen_paths(inputs):
    _, source, _, output = inputs
    plan = prepare_plan(source, output)
    plan["tasks"][0]["verifier"] = "../verify.py"
    rewrite_plan(output, plan)
    with pytest.raises(ValueError, match="invalid frozen verifier path"):
        load_plan(output)


@pytest.mark.parametrize(
    "member_type,name",
    [
        (tarfile.SYMTYPE, "link"),
        (tarfile.LNKTYPE, "link"),
        (tarfile.REGTYPE, "../escape"),
    ],
)
def test_even_rehashed_archives_must_be_safe(inputs, member_type, name):
    _, source, _, output = inputs
    plan = prepare_plan(source, output)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.type = member_type
        member.linkname = "outside"
        archive.addfile(member)
    data = stream.getvalue()
    (output / "source.tar").write_bytes(data)
    plan["hashes"]["source.tar"] = hashlib.sha256(data).hexdigest()
    rewrite_plan(output, plan)
    with pytest.raises(ValueError, match="unsupported link|relative path"):
        load_plan(output)


def test_failure_cleans_only_own_partial_output(inputs, monkeypatch):
    _, source, _, output = inputs
    original = Path.write_bytes

    def fail_snapshot(path, data):
        if path == output / "source.tar":
            raise OSError("simulated full disk")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_snapshot)
    with pytest.raises(OSError, match="full disk"):
        prepare_plan(source, output)
    assert not output.exists()
    assert source.exists()
