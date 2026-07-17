from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class RedactionResult:
    text: str
    redacted: bool
    matches: list[str]


class PIIRedactor:
    """Lightweight regex redaction for transcripts and logs."""

    _patterns: list[tuple[str, re.Pattern[str]]] = [
        ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
        ("phone", re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")),
        ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
        ("card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ]

    def redact(self, text: str) -> RedactionResult:
        matches: list[str] = []
        redacted = text
        for label, pattern in self._patterns:
            found = pattern.findall(redacted)
            if found:
                matches.extend(f"{label}:{item}" for item in found)
                redacted = pattern.sub(f"[REDACTED_{label.upper()}]", redacted)
        return RedactionResult(text=redacted, redacted=bool(matches), matches=matches)
