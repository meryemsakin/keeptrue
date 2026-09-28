"""Small text helpers."""

import re


def normalize_space(text: str) -> str:
    """Collapse runs of whitespace into single spaces and trim the ends."""
    return re.sub(r"\s+", " ", text).strip()


def title_case(text: str) -> str:
    """Capitalize each word, keeping the rest of the word as it is."""
    return " ".join(w[:1].upper() + w[1:] for w in normalize_space(text).split(" "))


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    """Cut text to at most `limit` characters, including the suffix."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix
