from __future__ import annotations

import os
from functools import lru_cache
import yaml

_RULES_FILE = os.path.join(os.path.dirname(__file__), "commands.yaml")


@lru_cache(maxsize=1)
def load_rules() -> dict:
    with open(_RULES_FILE) as f:
        return yaml.safe_load(f)


def allow_patterns_for_settings() -> list[str]:
    """Chuyển allow list → Claude Code permissions.allow format."""
    rules = load_rules()
    out = []
    for cmd in rules.get("allow", []):
        cmd = cmd.strip()
        out.append(f"Bash({cmd})")
        out.append(f"Bash({cmd} *)")
    out.append("Read(*)")
    return out


def deny_patterns_for_settings() -> list[str]:
    rules = load_rules()
    out = []
    for cmd in rules.get("always_deny", []):
        cmd = cmd.strip()
        out.append(f"Bash({cmd})")
        out.append(f"Bash({cmd} *)")
    return out


def allow_patterns_plain() -> list[str]:
    """Plain list dùng cho hook script check."""
    rules = load_rules()
    return [cmd.strip() for cmd in rules.get("allow", [])]
