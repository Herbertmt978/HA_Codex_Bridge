"""Bounded command descriptions for the administrator activity view.

This deliberately hides the whole command when recognised credential material
is present, rather than attempting to reconstruct arbitrary shell syntax.
It is a conservative display filter, not a general-purpose secret detector.
"""

import re


_SENSITIVE_COMMAND = re.compile(
    r"secret|token|password|passwd|credential|authorization|bearer|cookie|"
    r"api[_-]?key|private[_ -]?key|client[_-]?secret|"
    r"\b(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]+|"
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"https?://[^\s]+[@?]|"
    r"(?:--?password|--?passwd|--?user|-u|-H|--header|--data|-d)\b|"
    r"\b[A-Z_][A-Z0-9_]*\s*=|\$env:|\bset(?:x)?\s+|"
    r"[A-Za-z0-9+/=_-]{80,}",
    re.IGNORECASE,
)


def command_preview(value: object) -> str | None:
    """Return display text only; never output, environment or working directory."""
    if not isinstance(value, str) or not value.strip() or len(value) > 16384:
        return None
    if _SENSITIVE_COMMAND.search(value):
        return None
    # Refuse control/bidi characters rather than allowing deceptive previews.
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127
           or "\u202a" <= char <= "\u202e" or "\u2066" <= char <= "\u2069"
           for char in value):
        return None
    text = value.strip()
    return text if len(text) <= 2000 else text[:1999] + "…"
