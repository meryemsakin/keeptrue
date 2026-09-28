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


# (predicate over lowercased rule text) -> (check | None for length-special, id hint)
_MATCHERS = [
    (lambda t: "uv" in t and "pip" in t,
     _c("forbidden_command", pattern=r"\bpip install\b"), "use-uv"),
    (lambda t: re.search(r"\b(pytest|test suite|run the tests?|unit tests?)\b", t),
     _c("required_command", pattern=r"\b(pytest|npm test|yarn test|go test|cargo test|make test)\b"),
     "run-tests"),
    (lambda t: "migration" in t,
     _c("forbidden_path", pattern=r"(^|/)migrations?/"), "protect-migrations"),
    (lambda t: re.search(r"\b(print|console\.log|debug (statement|log|print))", t),
     _c("forbidden_in_diff", pattern=r"^\+.*(?<![.\w])(print|console\.log)\("), "no-debug-prints"),
    (lambda t: "force" in t and "push" in t,
     _c("forbidden_command", pattern=r"git push\b.*(--force|-f\b)"), "no-force-push"),
    (lambda t: re.search(r"\b(ruff|black|prettier|eslint|gofmt|formatt?er?|linter?)\b", t),
     _c("required_command", pattern=r"\b(ruff|black|prettier|eslint|gofmt)\b"), "format"),
    (lambda t: re.search(r"(\.env\b|secret|credential|\bapi key\b|password)", t),
     _c("forbidden_path", pattern=r"(^|/)\.env"), "no-secrets"),
    (lambda t: re.search(r"\b(concise|brief|short|word limit|under \d+ words|summary)\b", t),
     None, "concise-summary"),  # None => length check, resolved below
]


def _length_check(text: str) -> dict:
    m = re.search(r"(\d+)\s*words?", text.lower())
    return _c("max_final_length", unit="words", limit=int(m.group(1)) if m else 100)


def _slug(text: str, hint: str | None) -> str:
    if hint:
        return hint
    words = re.sub(r"[^a-z0-9\s-]", "", text.lower()).split()[:4]
    return "-".join(words) or "rule"


def extract_rules(text: str) -> list[str]:
    """Pull rule-like lines: bullet items and short non-bullet imperatives."""
    rules: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        bullet = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", s)
        if bullet:
            s = bullet.group(2).strip()
        elif not s or s.startswith(("#", ">", "|", "```")):
            continue
        elif len(s.split()) > 20 or s.endswith(":"):
            continue
        s = re.sub(r"\s+", " ", s).strip(" .")
        s = re.sub(r"\*\*|`", "", s)  # drop markdown emphasis/code ticks
        if 3 <= len(s) <= 200 and s not in rules:
            rules.append(s)
    return rules


def propose_check(text: str):
    t = text.lower()
    for pred, check, hint in _MATCHERS:
        if pred(t):
            return (_length_check(text) if check is None else dict(check)), hint
    return None, None


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
