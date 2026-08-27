"""Conservative, local redaction before anything enters the durable vault.

This is deliberately a defense-in-depth filter, not a password-manager
boundary. Callers should still avoid handing secrets to the provider. The
function returns only replacement text and non-sensitive pattern names; it
never stores a digest of the detected value.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redacted: bool
    patterns: tuple[str, ...] = ()


_PATTERNS: tuple[tuple[str, re.Pattern[str], str | None], ...] = (
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.IGNORECASE | re.DOTALL,
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (
        "authorization_header",
        re.compile(
            r"(?im)(\b(?:authorization|proxy-authorization)\s*:\s*)(?:bearer|basic|token)\s+[^\r\n]+",
        ),
        r"\1[REDACTED]",
    ),
    (
        "cookie_header",
        re.compile(r"(?im)(\b(?:cookie|set-cookie)\s*:\s*)[^\r\n]+"),
        r"\1[REDACTED]",
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(\b(?:api[_-]?key|access[_-]?key|client[_-]?secret|refresh[_-]?token|"
            r"password|passwd|secret|token|auth[_-]?token|session[_-]?token)\b\s*[:=]\s*)"
            r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
        ),
        r"\1[REDACTED]",
    ),
    (
        "credential_query_parameter",
        re.compile(
            r"(?i)([?&](?:api[_-]?key|access[_-]?token|client[_-]?secret|password|secret|token|auth)=)"
            r"[^&#\s]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
        ),
        "[REDACTED JWT]",
    ),
    (
        "known_token_format",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{8,}|sk-proj-[A-Za-z0-9_-]{8,}|"
            r"gh[pousr]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,}|"
            r"xox[baprs]-[A-Za-z0-9-]{8,}|AIza[A-Za-z0-9_-]{20,})\b"
        ),
        "[REDACTED TOKEN]",
    ),
)


def redact_text(value: Any) -> RedactionResult:
    """Return a redacted string plus non-sensitive detection metadata."""

    text = "" if value is None else str(value)
    patterns: list[str] = []
    for name, pattern, replacement in _PATTERNS:
        replacement_text = replacement if replacement is not None else "[REDACTED]"
        text, count = pattern.subn(replacement_text, text)
        if count and name not in patterns:
            patterns.append(name)
    return RedactionResult(text=text, redacted=bool(patterns), patterns=tuple(patterns))


def redact_object(value: Any) -> tuple[Any, tuple[str, ...]]:
    """Recursively redact strings in JSON-like metadata values."""

    patterns: list[str] = []

    def merge(names: tuple[str, ...]) -> None:
        for name in names:
            if name not in patterns:
                patterns.append(name)

    if isinstance(value, str):
        result = redact_text(value)
        merge(result.patterns)
        return result.text, tuple(patterns)
    if isinstance(value, list):
        output = []
        for item in value:
            clean, names = redact_object(item)
            output.append(clean)
            merge(names)
        return output, tuple(patterns)
    if isinstance(value, tuple):
        clean, names = redact_object(list(value))
        return tuple(clean), names
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            clean_key, key_names = redact_object(str(key))
            clean_item, item_names = redact_object(item)
            output[clean_key] = clean_item
            merge(key_names)
            merge(item_names)
        return output, tuple(patterns)
    return value, ()
