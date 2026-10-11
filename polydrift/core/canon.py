"""Canonicalisation helpers (manuscript 3.2).

Every helper is a total function from raw values to canonical values, so
agreement on an axis is the kernel of a function and therefore an
equivalence relation. There are no tolerance comparisons anywhere.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Iterable, Sequence

_LWS = re.compile(r"[ \t]*(?:\r?\n)?[ \t]+")


def identity(value: Any) -> Any:
    return value


def casefold(value: str | None) -> str | None:
    return None if value is None else value.casefold()


def collapse_lws(value: str | None) -> str | None:
    """Collapse linear whitespace (including folded lines) to one space and strip."""
    if value is None:
        return None
    return _LWS.sub(" ", value).strip()


def sort_unordered(values: Iterable[Any] | None) -> tuple[Any, ...]:
    """Sort a collection the specification declares unordered."""
    if values is None:
        return ()
    return tuple(sorted(values, key=repr))


def bucket(value: Any, rule: Callable[[Any], str]) -> str | None:
    """Map a legitimately varying value to a named bucket.

    ``rule`` is the cell's bucketing function (for example a DNS TTL to one of
    original, decremented, zero, capped). It must be a function, not a
    tolerance, so the result stays transitive.
    """
    if value is None:
        return None
    return rule(value)


def digest_after(canon: Callable[[str], str], text: str | None) -> str | None:
    """SHA-256 of a body after its own canonicalisation. Never a raw-byte hash."""
    if text is None:
        return None
    return hashlib.sha256(canon(text).encode("utf-8", errors="replace")).hexdigest()


def normalise_line_endings(text: str) -> str:
    """LF line endings, trailing whitespace stripped, trailing blank lines dropped."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


class AlphaRenamer:
    """Consistent renaming of volatile identifiers within one record.

    Each distinct volatile value becomes ``#1``, ``#2``, ... in order of first
    appearance. Values are renamed, never deleted, so a response that fails to
    echo an identifier is still caught. One renamer is used per observation
    record and must see values in the record's step order.
    """

    def __init__(self) -> None:
        self._map: dict[Any, str] = {}

    def __call__(self, value: Any) -> Any:
        if value is None:
            return None
        if value not in self._map:
            self._map[value] = f"#{len(self._map) + 1}"
        return self._map[value]

    def rename_in(self, text: str | None, pattern: re.Pattern[str], group: int = 1) -> str | None:
        """Rename every match of ``pattern`` (its ``group``) inside ``text``."""
        if text is None:
            return None

        def _sub(match: re.Match[str]) -> str:
            start, end = match.span(group)
            whole_start = match.start(0)
            token = match.group(group)
            prefix = match.group(0)[: start - whole_start]
            suffix = match.group(0)[end - whole_start :]
            return f"{prefix}{self(token)}{suffix}"

        return pattern.sub(_sub, text)

    @property
    def mapping(self) -> dict[Any, str]:
        return dict(self._map)


def first_present(values: Sequence[Any]) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
