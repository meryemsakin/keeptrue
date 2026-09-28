"""Bounded local execution of a frozen ablation plan (POSIX only).

The runner uses an explicit instruction surface, not native AGENTS discovery.
No API call occurs during planning, reporting, or runner preflight. Verifiers
run through the CLI's OS sandbox too: importing generated code is execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import platform
from pathlib import Path, PurePosixPath

from .specification import load_plan


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _process(
    argv: list[str],
    cwd: Path,
    output: Path,
    errors: Path,
    timeout: float,
    prompt: str | None = None,
) -> tuple[str, int | None, float]:
    """Stream logs to disk and terminate the whole process group on every exit."""
    started = time.monotonic()
    with output.open("wb") as out, errors.open("wb") as err:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE if prompt else subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        status = "completed"
        try:
            proc.communicate(
                prompt.encode("utf-8") if prompt else None, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            status = "timeout"
        except KeyboardInterrupt:
            status = "interrupted"
        finally:
            # Also stop orphaned children after an otherwise successful parent.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    return status, proc.returncode, round(time.monotonic() - started, 4)


def _extract(archive: Path, workspace: Path) -> None:
    # Validate again at the extraction boundary, independent of planning.
    with tarfile.open(archive) as tf:
        for member in tf:
            name = PurePosixPath(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError(f"unsafe archive entry: {member.name}")
            target = workspace / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as src, target.open("wb") as dst:
                    import shutil

                    shutil.copyfileobj(src, dst)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def _tree(root: Path) -> dict[str, str]:
    """Content+executable bit; don't follow generated symlinks or special files."""
    result = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            path = Path(current) / name
            if path.is_symlink():
                result[path.relative_to(root).as_posix()] = "symlink:" + os.readlink(
                    path
                )
                dirs.remove(name)
        for name in files:
            path = Path(current) / name
            key = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[key] = "symlink:" + os.readlink(path)
            elif path.is_file():
                result[key] = _hash(path) + (
                    ":executable" if path.stat().st_mode & 0o111 else ""
                )
            else:
                result[key] = "special-file"
    return result


def _protected_state(root: Path, paths: list[str]) -> dict[str, str]:
    tree = _tree(root)
    return {
        p: tree.get(p, "directory" if (root / p).is_dir() else "absent") for p in paths
    }


def _archive_result(workspace: Path, destination: Path) -> None:
    # Only ordinary files; symlinks/special files remain visible in final-tree.json.
    with tarfile.open(destination, "w") as tf:
        for current, dirs, files in os.walk(workspace, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(current) / d).is_symlink())
            for name in sorted(files):
                p = Path(current) / name
                if not p.is_symlink() and p.is_file():
                    tf.add(
                        p, arcname=p.relative_to(workspace).as_posix(), recursive=False
                    )


def instructions_for(plan: dict, slot: dict) -> str:
    blocks = [plan.get("preamble", "")]
    blocks += [u["text"] for u in plan["units"] if u["id"] != slot["removed_unit"]]
    return "\n\n".join(b for b in blocks if b.strip()) + "\n"


class CodexRunner:
    """CLI protocol pinned by exact --version and binary digest before execution."""

    def __init__(self, runner: dict):
        self.executable = runner.get("executable", "codex")
        self.expected_version = runner["version"]
        self.model = runner["model"]

    @staticmethod
    def _config() -> list[str]:
        values = [
            'sandbox_mode="workspace-write"',
            'approval_policy="never"',
            "sandbox_workspace_write.network_access=false",
            "sandbox_workspace_write.writable_roots=[]",
            "sandbox_workspace_write.exclude_slash_tmp=true",
            "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            'shell_environment_policy.inherit="core"',
            "shell_environment_policy.ignore_default_excludes=false",
            "features.apps=false",
            'web_search="disabled"',
        ]
        return [part for value in values for part in ("-c", value)]

    def command(self, workspace: Path, instructions: str) -> list[str]:
        return [
            self.executable,
            "--no-daemon",
            "-a",
            "never",
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--model",
            self.model,
            "-C",
            str(workspace),
            *self._config(),
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            "developer_instructions=" + json.dumps(instructions, ensure_ascii=False),
            "-",
        ]

    def verifier_command(self, workspace: Path, verifier: Path) -> list[str]:
        return [
            self.executable,
            "sandbox",
            "--permission-profile",
            "keeptrue_verifier",
            "-C",
            str(workspace),
            "-c",
            'permissions={keeptrue_verifier={filesystem={":root"="read",":workspace_roots"="write"},network={enabled=false}}}',
            "--",
            sys.executable,
            "-I",
            "-B",
            str(verifier),
            str(workspace),
        ]

    def preflight(self, root: Path) -> dict:
        import shutil

        executable = shutil.which(self.executable)
        if not executable:
            raise ValueError(f"runner executable not found: {self.executable}")
        self.executable = str(Path(executable).resolve())
        version = subprocess.run(
            [self.executable, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        if version != self.expected_version:
            raise ValueError(
                f"CLI version mismatch: expected {self.expected_version!r}; got {version!r}"
            )
        help_text = subprocess.run(
            [self.executable, "exec", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json"):
            if flag not in help_text:
                raise ValueError(f"CLI lacks required isolation flag: {flag}")
        # Catch unavailable OS sandbox before paying for a model run.
        with tempfile.TemporaryDirectory(prefix="keeptrue-preflight-") as tmp:
            workspace = Path(tmp)
            probe = root / "sandbox-probe.py"
            probe.write_text(
                """import pathlib, socket, sys
workspace = pathlib.Path(sys.argv[1])
(workspace / "allowed-probe").write_text("ok")
outside = pathlib.Path(__file__).with_name("outside-write-probe")
try:
    outside.write_text("sandbox did not deny this write")
except PermissionError:
    pass
else:
    outside.unlink()
    raise SystemExit("sandbox allowed writing outside the candidate workspace")
try:
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
except PermissionError:
    pass
else:
    raise SystemExit("sandbox allowed network binding")
print("sandbox file/network boundary probes passed")
""",
                encoding="utf-8",
            )
            status, code, _ = _process(
                self.verifier_command(workspace, probe),
                workspace,
                root / "sandbox-probe.stdout",
                root / "sandbox-probe.stderr",
                15,
            )
            if status != "completed" or code != 0:
                raise ValueError(
                    "OS sandbox preflight failed; see sandbox-probe.stderr (no model call made)"
                )
        return {
            "cli_version": version,
            "binary_sha256": _hash(Path(self.executable)),
            "python": sys.version,
            "python_binary_sha256": _hash(Path(sys.executable)),
            "platform": platform.platform(),
            "implementation_sha256": {
                p.name: _hash(p) for p in Path(__file__).parent.glob("*.py")
            },
            "instruction_strategy": "explicit-developer-instructions-and-agents-md",
        }


def _usage(path: Path) -> tuple[int | None, int | None, bool]:
    """Codex exec JSONL differs from persisted rollouts. Missing stays unknown."""
    totals = []
    completed = False
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                return None, None, False
            if not isinstance(event, dict):
                return None, None, False
            if event.get("type") in ("turn.failed", "error"):
                return None, None, False
            if event.get("type") == "turn.completed":
                completed = True
                u = event.get("usage") or {}
                if not isinstance(u, dict):
                    return None, None, completed
                inp, out, cached = (
                    u.get("input_tokens"),
                    u.get("output_tokens"),
                    u.get("cached_input_tokens"),
                )
                if (
                    all(type(v) is int and v >= 0 for v in (inp, out, cached))
                    and cached <= inp
                ):
                    totals.append((inp - cached, out))
                else:
                    return None, None, completed
    return (
        (sum(x for x, _ in totals), sum(y for _, y in totals), completed)
        if totals
        else (None, None, completed)
    )


def read_records(root: Path, plan: dict) -> list[dict]:
    records = []
    expected = {s["id"] for s in plan["slots"]}
    run_root = root / "runs"
    if run_root.is_symlink():
        raise ValueError("runs directory cannot be a symlink")
    if run_root.exists():
        for path in run_root.iterdir():
            if path.name not in expected or path.is_symlink() or not path.is_dir():
                raise ValueError(f"unexpected run directory: {path.name}")
    for slot in plan["slots"]:
        directory = root / "runs" / slot["id"]
        path = directory / "result.json"
        if path.is_symlink():
            raise ValueError("result file cannot be a symlink")
        if not path.exists():
            # A claimed but unfinalized slot is not retried/billed silently.
            if directory.exists():
                records.append(
                    {
                        "slot_id": slot["id"],
                        "status": "interrupted",
                        "success": None,
                        "duration_s": None,
                        "input_tokens": None,
                        "output_tokens": None,
                    }
                )
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(
            record.get("artifacts"), dict
        ):
            raise ValueError(f"invalid result record: {slot['id']}")
        if (
            record.get("slot_id") != slot["id"]
            or record.get("plan_id") != plan["plan_id"]
        ):
            raise ValueError(f"run identity mismatch: {slot['id']}")
        for name, digest in record.get("artifacts", {}).items():
            if (
                Path(name).name != name
                or (directory / name).is_symlink()
                or _hash(directory / name) != digest
            ):
                raise ValueError(f"run artifact changed: {slot['id']}/{name}")
        records.append(record)
    return records


def run_plan(
    directory: str | Path, max_runs: int, *, runner=None, on_progress=None
) -> list[dict]:
    if os.name != "posix":
        raise ValueError("experimental execution requires POSIX process groups")
    if type(max_runs) is not int or max_runs < 1:
        raise ValueError("max_runs must be a positive integer")
    import fcntl

    root = Path(directory).expanduser().resolve()
    plan = load_plan(root)
    with (root / ".run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another process is executing this plan") from exc
        prior = read_records(root, plan)
        recorded = {r["slot_id"] for r in prior}
        slots = [s for s in plan["slots"] if s["id"] not in recorded][:max_runs]
        if not slots:
            return prior
        runner = runner or CodexRunner(plan["runner"])
        runtime = runner.preflight(root)
        runtime_file = root / "runtime.json"
        if runtime_file.exists() and json.loads(runtime_file.read_text()) != runtime:
            raise ValueError(
                "runtime changed; prepare a new plan rather than mixing environments"
            )
        _write_json(runtime_file, runtime)
        tasks = {t["id"]: t for t in plan["tasks"]}
        for slot in slots:
            load_plan(root)  # Frozen inputs must still agree before every run.
            task = tasks[slot["task_id"]]
            run_dir = root / "runs" / slot["id"]
            run_dir.mkdir(parents=True, mode=0o700)
            _write_json(
                run_dir / "started.json",
                {"slot_id": slot["id"], "plan_id": plan["plan_id"]},
            )
            if on_progress:
                on_progress(slot)
            record = {
                "slot_id": slot["id"],
                "plan_id": plan["plan_id"],
                "success": None,
                "input_tokens": None,
                "output_tokens": None,
                "duration_s": None,
                "status": "verification_error",
                "agent_exit_code": None,
                "verify_exit_code": None,
            }
            try:
                with tempfile.TemporaryDirectory(prefix="keeptrue-run-") as tmp:
                    workspace = Path(tmp)
                    _extract(root / "source.tar", workspace)
                    instructions = instructions_for(plan, slot)
                    (workspace / plan["instructions_file"]).write_text(
                        instructions, encoding="utf-8"
                    )
                    (run_dir / "instructions.md").write_text(
                        instructions, encoding="utf-8"
                    )
                    before = _tree(workspace)
                    protected_before = _protected_state(
                        workspace, task.get("protected_paths", [])
                    )
                    status, code, seconds = _process(
                        runner.command(workspace, instructions),
                        workspace,
                        run_dir / "agent.jsonl",
                        run_dir / "agent.stderr",
                        plan["timeout_s"],
                        task["prompt"],
                    )
                    record.update(
                        status=status, agent_exit_code=code, duration_s=seconds
                    )
                    inp, out, completed = _usage(run_dir / "agent.jsonl")
                    record.update(input_tokens=inp, output_tokens=out)
                    after = _tree(workspace)
                    changes = sorted(
                        p
                        for p in before.keys() | after.keys()
                        if before.get(p) != after.get(p)
                    )
                    record["changed_files"] = changes
                    protected_after = _protected_state(
                        workspace, task.get("protected_paths", [])
                    )
                    record["protected_path_violations"] = [
                        p
                        for p in protected_before
                        if protected_before[p] != protected_after[p]
                    ]
                    _write_json(run_dir / "final-tree.json", after)
                    _archive_result(workspace, run_dir / "final.tar")
                    load_plan(root)  # Never execute an altered verifier.
                    if any(
                        value == "special-file" or value.startswith("symlink:")
                        for value in after.values()
                    ):
                        record.update(
                            status="verification_error",
                            error="candidate contains unsupported links or special files",
                        )
                    elif status == "completed" and (code != 0 or not completed):
                        record["status"] = "agent_error"
                    elif status == "completed":
                        verify_status, verify_code, _ = _process(
                            runner.verifier_command(workspace, root / task["verifier"]),
                            workspace,
                            run_dir / "verify.stdout",
                            run_dir / "verify.stderr",
                            min(plan["timeout_s"], 60),
                        )
                        record["verify_exit_code"] = verify_code
                        load_plan(root)
                        if verify_status == "interrupted":
                            # Ctrl-C during verification must stop before another paid run.
                            record["status"] = "interrupted"
                        elif verify_status != "completed" or verify_code not in (0, 1):
                            record["status"] = "verification_error"
                        else:
                            record["success"] = (
                                verify_code == 0
                                and not record["protected_path_violations"]
                            )
                    # The archived tree is the evaluated artifact. Verification is
                    # allowed to create caches, but cannot rewrite existing files
                    # or add a replacement for a protected absent file.
                    if record["success"] is not None:
                        verified_tree = _tree(workspace)
                        if any(verified_tree.get(p) != v for p, v in after.items()) or (
                            _protected_state(workspace, list(protected_after))
                            != protected_after
                        ):
                            record.update(
                                status="verification_error",
                                success=None,
                                error="verifier modified the candidate tree",
                            )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                record.update(status="verification_error", success=None, error=str(exc))
            record["artifacts"] = {
                p.name: _hash(p) for p in sorted(run_dir.iterdir()) if p.is_file()
            }
            _write_json(run_dir / "result.json", record)
            if record["status"] == "interrupted":
                break
        return read_records(root, plan)
