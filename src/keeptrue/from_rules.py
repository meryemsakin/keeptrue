"""Propose deterministic checks from an AGENTS.md / CLAUDE.md.

`keeptrue init --from CLAUDE.md` reads your rules file and maps each rule to a
check *where it recognizes the intent*. No model is called — it's a library of
patterns, so it stays fast, offline, and deterministic. Rules it can't map are
written out with no check (and reported), so you see exactly what still needs a
signal instead of getting a false sense of coverage.
"""

from __future__ import annotations

import re


def _c(kind: str, **params) -> dict:
    return {"kind": kind, **params}


# Deliberately small templates, not keyword-based intent inference. A clause
# with conditions, exceptions or an unsupported action stays an unscored rule.
_NEGATIVE = r"(?:never|do not|don't)"
_FINISH = r"(?: before (?:finishing|you finish))?"
_CONDITIONAL = re.compile(
    r"\b(?:if|when|whenever|unless|until|except|only|otherwise|provided)\b"
)
_TESTS = r"\b(pytest|npm test|yarn test|go test|cargo test|make test)\b"


def _simple_check(t: str):
    if _CONDITIONAL.search(t):
        return None, None

    no_pip = rf"{_NEGATIVE} (?:run |use )?pip3? install(?: packages)?"
    use_uv = (
        rf"(?:always )?use uv (?:instead of pip|rather than pip|"
        rf"for (?:packages|package management)[,;] {_NEGATIVE} (?:use )?pip(?: install)?)"
    )
    if re.fullmatch(no_pip, t) or re.fullmatch(use_uv, t):
        return _c("forbidden_command", pattern=r"\bpip3? install\b"), "use-uv"

    run = re.fullmatch(
        rf"(?:always )?run (?:the )?(pytest|(?:python(?:3)? -m )pytest|npm test|yarn test|"
        rf"go test|cargo test|make test|test suite|tests|unit tests)(?: \(pytest\))?{_FINISH}",
        t,
    )
    if run:
        name = run.group(1)
        # A named test runner must not be satisfied by a different one.
        pattern = (
            _TESTS
            if name in ("test suite", "tests", "unit tests")
            else (
                r"\bpytest\b" if "pytest" in name else r"\b" + re.escape(name) + r"\b"
            )
        )
        if "(pytest)" in t:
            pattern = r"\bpytest\b"
        return _c("required_command", pattern=pattern), "run-tests"

    if re.fullmatch(
        rf"{_NEGATIVE} (?:edit|modify|change) (?:any )?(?:files (?:under|in) |anything (?:under|in) |the )?"
        r"migrations?/?",
        t,
    ):
        return _c(
            "forbidden_path", pattern=r"(^|/)migrations?/", tools=["edit"]
        ), "protect-migrations"

    debug = re.fullmatch(
        rf"(?:no|{_NEGATIVE} (?:add|use)) (?:any )?(print\(\)|console\.log\(\)|debug prints)"
        r"(?: calls| statements| debug statements)?",
        t,
    )
    if debug:
        signal = debug.group(1)
        name = (
            "(print|console\\.log)"
            if signal == "debug prints"
            else re.escape(signal[:-2])
        )
        return _c(
            "forbidden_in_diff", pattern=r"^\+.*(?<![.\w])" + name + r"\("
        ), "no-debug-prints"

    if re.fullmatch(
        rf"{_NEGATIVE} (?:run )?git push (?:--force|-f)", t
    ) or re.fullmatch(rf"{_NEGATIVE} force[- ]push(?: with git)?", t):
        return _c(
            "forbidden_command", pattern=r"git push\b.*(--force|-f\b)"
        ), "no-force-push"

    formatting = re.fullmatch(
        rf"(?:always )?run (?:the )?(ruff|black|prettier|eslint|gofmt|formatter|linter){_FINISH}",
        t,
    )
    if formatting:
        name = formatting.group(1)
        pattern = (
            r"\b(ruff|black|prettier|eslint|gofmt)\b"
            if name in ("formatter", "linter")
            else (r"\b" + name + r"\b")
        )
        return _c("required_command", pattern=pattern), "format"

    if re.fullmatch(rf"{_NEGATIVE} (?:edit|modify|change) (?:the )?\.env(?: file)?", t):
        return _c(
            "forbidden_path", pattern=r"(^|/)\.env$", tools=["edit"]
        ), "no-secrets"

    length = re.fullmatch(
        r"(?:keep|write|make) (?:(?:the|your) )?(?:final (?:message|summary|response|answer)|summary) "
        r"(under|below|less than|at most|no more than) (\d+) words",
        t,
    )
    if length:
        limit = int(length.group(2))
        if length.group(1) in ("under", "below", "less than"):
            limit -= 1  # max_final_length is inclusive
        if limit >= 0:
            return _c("max_final_length", unit="words", limit=limit), "concise-summary"
    return None, None


def _slug(text: str, hint: str | None) -> str:
    if hint:
        return hint
    words = re.sub(r"[^a-z0-9\s-]", "", text.lower()).split()[:4]
    return "-".join(words) or "rule"


def extract_rules(text: str) -> list[str]:
    """Pull rule-like lines: bullet items and short non-bullet imperatives."""
    rules: list[str] = []
    context = ""
    fence = None
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith(("```", "~~~")):
            marker = s[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence:
            continue
        if not s:
            continue
        if s.endswith(":") or s.startswith("#"):
            # Keep a scoped heading with its children; dropping the parent
            # would turn a conditional instruction into an unconditional one.
            context = (
                s.lstrip("# ").rstrip(":") if _CONDITIONAL.search(s.lower()) else ""
            )
            continue
        bullet = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", s)
        if bullet:
            s = bullet.group(2).strip()
        elif not s or s.startswith(("#", ">", "|", "```")):
            continue
        elif len(s.split()) > 20 or s.endswith(":"):
            continue
        s = re.sub(r"\s+", " ", s).strip(" .")
        s = re.sub(r"\*\*|`", "", s)  # drop markdown emphasis/code ticks
        if context:
            s = context + ": " + s
        if 3 <= len(s) <= 200 and s not in rules:
            rules.append(s)
    return rules


def propose_check(text: str):
    """Suggest a check only for a recognized unconditional rule template.

    This intentionally abstains on unsupported phrasing rather than inferring
    meaning from words such as ``pytest``, ``migration`` or ``secret`` alone.
    All proposals still require review; command/diff regexes are heuristics.
    """
    t = re.sub(r"\s+", " ", text.lower().replace("’", "'")).strip(" .")
    return _simple_check(t)


def propose_config(text: str, source: str = "your rules file") -> tuple[dict, int, int]:
    """Return (config dict, matched count, total rules)."""
    out: list[dict] = []
    matched = 0
    seen: set[str] = set()
    for rule in extract_rules(text):
        check, hint = propose_check(rule)
        rid = _slug(rule, hint)
        base, n = rid, 2
        while rid in seen:
            rid, n = f"{base}-{n}", n + 1
        seen.add(rid)
        entry: dict = {"id": rid, "text": rule}
        if check:
            entry["check"] = check
            matched += 1
        out.append(entry)
    return {"scenario": f"rules from {source}", "rules": out}, matched, len(out)
