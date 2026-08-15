import re
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = re.compile(r"password|secret|token|cookie|authorization|storage.state|description", re.I)
HOME_PATH = re.compile(r"(?:[A-Za-z]:\\|/)(?:Users|home|workspace|tmp)/[^\s,'\"]+")


def redact(value: Any, key: str = "") -> Any:
    """Produce diagnostics safe for persistence and UI display."""
    if SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key) for v in value]
    if isinstance(value, Path):
        return f"[LOCAL_PATH]/{value.name}"
    if isinstance(value, str):
        value = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", value)
        return HOME_PATH.sub("[LOCAL_PATH]", value)
    return value
