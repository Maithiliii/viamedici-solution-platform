"""Small text helpers shared by indexer, search and guardrails."""
from __future__ import annotations

import re

# Error codes / technical identifiers: ERR-IMP-2207, API-AUTH-410, WF-COND-301,
# HTTP status codes, snake_case attribute names (item_type_keyword), versions.
_CODE_PATTERNS = [
    re.compile(r"\b[A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]+)+\b"),          # ERR-IMP-2207, API-AUTH-410
    re.compile(r"\b(?:HTTP\s*)?([1-5]\d\d)\b(?=\s*(?:[A-Z][a-z]|Unauthorized|Forbidden|Not Found|Too Many))"),
    re.compile(r"\b[a-z]+(?:_[a-z0-9]+){1,}\b"),                     # item_type_keyword, read_only_allow_delete
    re.compile(r"\$\{attr:[^}]+\}|\{\{[^}]+\}\}"),                   # workflow expressions
]


def extract_codes(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for pattern in _CODE_PATTERNS:
        for m in pattern.finditer(text):
            token = m.group(1) if m.groups() else m.group(0)
            if token not in found:
                found.append(token)
    return found


def version_major(version: str | None) -> int | None:
    if not version:
        return None
    try:
        return int(str(version).split(".")[0])
    except ValueError:
        return None


def version_tuple(version: str | None) -> tuple[int, int] | None:
    if not version:
        return None
    parts = str(version).split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None


def join(*parts: str | None, sep: str = "\n") -> str:
    return sep.join(p.strip() for p in parts if p and p.strip())


def truncate(text: str | None, n: int) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[: n - 1] + "…"
