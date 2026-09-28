"""Freeze reproducible, private leave-one-unit-out experiments without agents.

The plan is an integrity-checked input snapshot, not a signature or a claim that
instructions can safely be removed. Verification programs are trusted user code;
preparing a plan copies them without executing them.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml


PROTOCOL_VERSION = "keeptrue-ablation-v1"
INSTRUCTION_STRATEGY = "explicit-developer-instructions-and-agents-md"
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SURFACES = {"AGENTS.md", "AGENTS.override.md", "CLAUDE.md"}


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ValueError("specification mapping keys must be strings")
        if key in result:
            raise ValueError(f"duplicate specification key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapping(
    value: Any, name: str, required: set[str], optional: set[str] = frozenset()
) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    missing, extra = required - value.keys(), value.keys() - required - optional
    if missing:
        raise ValueError(f"{name} missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{name} unknown keys: {', '.join(sorted(extra))}")
    return value


def _text(value: Any, name: str, *, empty: bool = False, line: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or "\0" in value:
        raise ValueError(
            f"{name} must be {'a' if empty else 'a nonempty'} string without NUL"
        )
    if line and ("\n" in value or "\r" in value):
        raise ValueError(f"{name} must be a single line")
    return value


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def _slug(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > 100 or not _SLUG.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase slug (letters, digits, hyphens)")
    return value


def _relative(value: Any, name: str) -> str:
    value = _text(value, name, line=True)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).drive
        or "\\" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError(f"{name} must be a safe repository-relative path")
    return value


def _input_path(value: Any, base: Path, name: str) -> Path:
    value = _text(value, name, line=True)
    if ".." in Path(value).parts:
        raise ValueError(f"{name} cannot contain path traversal ('..')")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _runner(value: Any, *, frozen: bool = False) -> dict:
    required = {"kind", "model", "version"}
    optional = {"executable"}
    if frozen:
        required |= {"executable", "instruction_strategy"}
    value = _mapping(value, "runner", required, optional)
    if value["kind"] != "codex":
        raise ValueError("runner.kind must be codex")
    if frozen and value["instruction_strategy"] != INSTRUCTION_STRATEGY:
        raise ValueError("unsupported runner instruction strategy")
    version = _text(value["version"], "runner.version", line=True)
    if version.lower() in {"latest", "main", "master", "*"}:
        raise ValueError("runner.version must pin a specific CLI version")
    return {
        "kind": "codex",
        "model": _text(value["model"], "runner.model", line=True),
        "executable": _text(
            value.get("executable", "codex"), "runner.executable", line=True
        ),
        "version": version,
        "instruction_strategy": INSTRUCTION_STRATEGY,
    }


def _units(value: Any) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError("instructions.units must be a nonempty list")
    result, ids = [], set()
    for item in value:
        item = _mapping(item, "instruction unit", {"id", "text"})
        unit_id = _slug(item["id"], "instruction unit id")
        if unit_id in ids:
            raise ValueError(f"duplicate instruction unit id: {unit_id}")
        ids.add(unit_id)
        result.append(
            {"id": unit_id, "text": _text(item["text"], f"unit {unit_id} text")}
        )
    return result


def _protected(value: Any, target: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("task protected_paths must be a list")
    result = [_relative(item, "protected path") for item in value]
    if len(set(result)) != len(result):
        raise ValueError("duplicate protected path")
    if target in result:
        raise ValueError("instruction treatment file cannot be a protected path")
    if any(path == ".git" or path.startswith(".git/") for path in result):
        raise ValueError("protected paths cannot refer to Git metadata")
    return result


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (
            exc.stderr.decode("utf-8", "replace").strip()
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        raise ValueError(f"cannot freeze repository: {detail[:1000]}") from exc
    return result.stdout


def _validate_archive(data: bytes, target: str) -> set[str]:
    """Reject any member that cannot be extracted as an ordinary tracked file."""
    regular = set()
    seen = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                name = member.name.rstrip("/") if member.isdir() else member.name
                _relative(name, "snapshot member")
                if name in seen:
                    raise ValueError(f"duplicate snapshot member: {name}")
                seen.add(name)
                if not (member.isfile() or member.isdir()):
                    raise ValueError(
                        f"snapshot contains unsupported link or special file: {name}"
                    )
                if name == ".git" or name.startswith(".git/"):
                    raise ValueError("snapshot contains Git metadata")
                if any(
                    part in {".codex", ".agents"} for part in PurePosixPath(name).parts
                ):
                    raise ValueError(
                        f"uncontrolled agent configuration in snapshot: {name}"
                    )
                if PurePosixPath(name).name in _SURFACES and name != target:
                    raise ValueError(
                        f"uncontrolled instruction surface in snapshot: {name}"
                    )
                if name == target and not member.isfile():
                    raise ValueError("instruction target must be a regular file")
                if member.isfile():
                    regular.add(name)
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"invalid repository snapshot: {exc}") from exc
    return regular


def _verify_tracked_snapshot(data: bytes, tree: bytes, commit: str) -> None:
    """Fail closed when export attributes omit or substitute tracked contents."""
    expected = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, encoded_path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split(b" ")
        try:
            name = encoded_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("snapshot filenames must be valid UTF-8") from exc
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError(f"snapshot contains unsupported tracked file: {name}")
        expected[name] = object_id.decode("ascii")
    observed = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            if not member.isfile():
                continue
            observed.add(member.name)
            contents = archive.extractfile(member).read()
            payload = f"blob {len(contents)}\0".encode() + contents
            digest = (
                hashlib.sha1(payload).hexdigest()
                if len(commit) == 40
                else hashlib.sha256(payload).hexdigest()
            )
            if expected.get(member.name) != digest:
                raise ValueError(
                    f"archive differs from tracked commit: {member.name} (check export-subst attributes)"
                )
    if observed != expected.keys():
        raise ValueError("archive omits tracked files (check export-ignore attributes)")


def _slots(plan: dict) -> list[dict]:
    # Content sorting by seed-derived hash gives stable random order without
    # depending on a Python-version-specific PRNG implementation.
    identity = {
        key: value
        for key, value in plan.items()
        if key not in {"slots", "plan_id", "created_at"}
    }
    context = _sha(_canonical(identity))
    result = []
    for task in plan["tasks"]:
        for repeat in range(plan["repetitions"]):
            block = []
            for removed in [None, *(unit["id"] for unit in plan["units"])]:
                variant = "full" if removed is None else f"without:{removed}"
                slot_id = _sha(
                    _canonical(
                        {
                            "context": context,
                            "protocol_version": PROTOCOL_VERSION,
                            "task_id": task["id"],
                            "repeat": repeat,
                            "variant": variant,
                        }
                    )
                )
                block.append(
                    {
                        "id": slot_id,
                        "task_id": task["id"],
                        "repeat": repeat,
                        "variant": variant,
                        "removed_unit": removed,
                    }
                )
            result.extend(sorted(block, key=lambda item: item["id"]))
    return result


def _fingerprint(plan: dict) -> str:
    return _sha(
        _canonical(
            {
                key: value
                for key, value in plan.items()
                if key not in {"plan_id", "created_at"}
            }
        )
    )


def prepare_plan(spec_path: str | Path, output: str | Path) -> dict:
    """Validate inputs and freeze the exact committed snapshot; execute nothing."""
    source = Path(spec_path).resolve()
    destination = Path(output).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"plan output already exists: {destination}")
    spec_bytes = source.read_bytes()
    try:
        spec = yaml.load(spec_bytes, Loader=_UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid specification YAML: {exc}") from exc
    spec = _mapping(
        spec,
        "specification",
        {
            "schema_version",
            "repo",
            "runner",
            "instructions",
            "tasks",
            "repetitions",
            "timeout_s",
        },
        {"seed"},
    )
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    repository = _mapping(spec["repo"], "repo", {"path", "ref"})
    repo = _input_path(repository["path"], source.parent, "repo.path")
    ref = _text(repository["ref"], "repo.ref", line=True)
    runner = _runner(spec["runner"])
    instructions = _mapping(
        spec["instructions"], "instructions", {"file", "units"}, {"preamble"}
    )
    target = instructions["file"]
    if target != "AGENTS.md":
        raise ValueError("instructions.file must be AGENTS.md for the Codex runner")
    units = _units(instructions["units"])
    preamble = _text(
        instructions.get("preamble", ""), "instructions.preamble", empty=True
    )
    repetitions = _integer(spec["repetitions"], "repetitions", 1, 100)
    timeout = _integer(spec["timeout_s"], "timeout_s", 1, 3600)
    seed = _integer(spec.get("seed", 0), "seed", -(2**63), 2**63 - 1)
    if not isinstance(spec["tasks"], list) or not spec["tasks"]:
        raise ValueError("tasks must be a nonempty list")
    tasks, frozen, task_ids = [], {}, set()
    for task in spec["tasks"]:
        task = _mapping(task, "task", {"id", "prompt", "verifier"}, {"protected_paths"})
        task_id = _slug(task["id"], "task id")
        if task_id in task_ids:
            raise ValueError(f"duplicate task id: {task_id}")
        task_ids.add(task_id)
        verifier = _input_path(
            task["verifier"], source.parent, f"task {task_id} verifier"
        )
        if verifier.suffix != ".py" or not verifier.is_file():
            raise ValueError(
                f"task {task_id} verifier must be a standalone Python file"
            )
        data = verifier.read_bytes()
        try:
            compile(data, str(verifier), "exec")
        except (SyntaxError, ValueError) as exc:
            raise ValueError(
                f"task {task_id} verifier is not valid Python: {exc}"
            ) from exc
        path = f"verifiers/{task_id}.py"
        frozen[path] = data
        tasks.append(
            {
                "id": task_id,
                "prompt": _text(task["prompt"], f"task {task_id} prompt"),
                "verifier": path,
                "verifier_sha256": _sha(data),
                "protected_paths": _protected(task.get("protected_paths", []), target),
            }
        )
    commit = (
        _git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
        .decode()
        .strip()
    )
    tree = _git(repo, "ls-tree", "-r", "-z", commit)
    if any(entry.startswith(b"160000 ") for entry in tree.split(b"\0")):
        raise ValueError(
            "repository snapshot contains submodules; independent snapshots require ordinary files"
        )
    snapshot = _git(repo, "archive", "--format=tar", commit)
    regular = _validate_archive(snapshot, target)
    _verify_tracked_snapshot(snapshot, tree, commit)
    for task in tasks:
        for protected in task["protected_paths"]:
            if any(name.startswith(protected + "/") for name in regular):
                raise ValueError(
                    f"protected path must be an exact file, not a directory: {protected}"
                )
    frozen.update({"source.tar": snapshot, "specification.yaml": spec_bytes})
    plan = {
        "schema_version": 1,
        "protocol_version": PROTOCOL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "model": runner["model"],
        "runner": runner,
        "instructions_file": target,
        "preamble": preamble,
        "units": units,
        "tasks": tasks,
        "repetitions": repetitions,
        "timeout_s": timeout,
        "seed": seed,
        "snapshot": "source.tar",
        "hashes": {name: _sha(data) for name, data in frozen.items()},
    }
    plan["slots"] = _slots(plan)
    plan["plan_id"] = _fingerprint(plan)
    created = False
    try:
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        created = True
        (destination / ".gitignore").write_text("*\n")
        for name, data in frozen.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o600)
        (destination / "plan.json").write_text(
            json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
        )
        (destination / "plan.json").chmod(0o600)
    except BaseException:
        if created:
            shutil.rmtree(destination)
        raise
    return plan


def load_plan(root: Path) -> dict:
    """Read a plan only if its manifest and every frozen input remain intact."""
    root = Path(root).resolve()
    path = root / "plan.json"
    if path.is_symlink():
        raise ValueError("plan manifest cannot be a symlink")
    try:
        plan = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read ablation plan: {exc}") from exc
    plan = _mapping(
        plan,
        "plan",
        {
            "schema_version",
            "protocol_version",
            "created_at",
            "plan_id",
            "commit",
            "model",
            "runner",
            "instructions_file",
            "preamble",
            "units",
            "tasks",
            "repetitions",
            "timeout_s",
            "seed",
            "snapshot",
            "hashes",
            "slots",
        },
    )
    if (
        type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["protocol_version"] != PROTOCOL_VERSION
    ):
        raise ValueError("unsupported ablation plan version")
    if (
        not isinstance(plan["plan_id"], str)
        or not _SHA.fullmatch(plan["plan_id"])
        or plan["plan_id"] != _fingerprint(plan)
    ):
        raise ValueError("plan fingerprint changed")
    if not isinstance(plan["commit"], str) or not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", plan["commit"]
    ):
        raise ValueError("plan commit must be a full Git object ID")
    runner = _runner(plan["runner"], frozen=True)
    if (
        plan["model"] != runner["model"]
        or plan["instructions_file"] != "AGENTS.md"
        or plan["snapshot"] != "source.tar"
    ):
        raise ValueError("inconsistent plan runner or snapshot")
    _text(plan["created_at"], "created_at", line=True)
    _text(plan["preamble"], "preamble", empty=True)
    _units(plan["units"])
    _integer(plan["repetitions"], "repetitions", 1, 100)
    _integer(plan["timeout_s"], "timeout_s", 1, 3600)
    _integer(plan["seed"], "seed", -(2**63), 2**63 - 1)
    if not isinstance(plan["tasks"], list) or not plan["tasks"]:
        raise ValueError("plan tasks must be a nonempty list")
    expected = {"source.tar", "specification.yaml"}
    ids = set()
    for task in plan["tasks"]:
        _mapping(
            task,
            "plan task",
            {"id", "prompt", "verifier", "verifier_sha256", "protected_paths"},
        )
        task_id = _slug(task["id"], "task id")
        if task_id in ids:
            raise ValueError(f"duplicate task id: {task_id}")
        ids.add(task_id)
        _text(task["prompt"], "task prompt")
        _protected(task["protected_paths"], plan["instructions_file"])
        if task["verifier"] != f"verifiers/{task_id}.py":
            raise ValueError("invalid frozen verifier path")
        expected.add(task["verifier"])
    _mapping(plan["hashes"], "plan hashes", expected)
    for name, digest in plan["hashes"].items():
        _relative(name, "frozen input path")
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise ValueError(f"invalid frozen input hash: {name}")
        frozen_path = root / name
        if any(
            parent.is_symlink()
            for parent in [frozen_path, *frozen_path.parents]
            if parent != root and root in parent.parents
        ):
            raise ValueError(f"frozen input is a symlink: {name}")
        try:
            data = frozen_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read frozen input {name}: {exc}") from exc
        if _sha(data) != digest:
            raise ValueError(f"frozen input changed: {name}")
    for task in plan["tasks"]:
        if task["verifier_sha256"] != plan["hashes"][task["verifier"]]:
            raise ValueError("inconsistent verifier hash")
    _validate_archive((root / plan["snapshot"]).read_bytes(), plan["instructions_file"])
    if plan["slots"] != _slots(plan):
        raise ValueError("plan slots do not match the frozen experiment design")
    return plan
