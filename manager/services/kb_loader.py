from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import yaml

_KB_PATH = Path(__file__).parent.parent / "config" / "knowledge_base.yaml"


@lru_cache(maxsize=1)
def _load_raw() -> dict:
    if not _KB_PATH.exists():
        return {}
    with open(_KB_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def reload():
    """Xóa cache để load lại KB (gọi sau khi edit file)."""
    _load_raw.cache_clear()


def format_for_prompt() -> str:
    """Trả về KB dưới dạng plain text để inject vào system prompt của LLM."""
    kb = _load_raw()
    if not kb:
        return ""

    parts = ["## Kiến thức hệ thống (System Knowledge Base)\n"]

    if kb.get("servers"):
        parts.append("### Servers / Infrastructure")
        for s in kb["servers"]:
            line = f"- **{s['ip']}** ({s.get('name', '?')}): {s.get('role', '')}"
            if s.get("notes"):
                line += f" — {s['notes']}"
            parts.append(line)
        parts.append("")

    if kb.get("namespaces"):
        parts.append("### Kubernetes Namespaces")
        for ns in kb["namespaces"]:
            line = f"- `{ns['name']}` (env: {ns.get('env', '?')}, team: {ns.get('team', '?')})"
            if ns.get("services"):
                line += f" — services: {', '.join(ns['services'])}"
            parts.append(line)
        parts.append("")

    if kb.get("services"):
        parts.append("### Services")
        for svc in kb["services"]:
            line = f"- **{svc['name']}**: {svc.get('description', '')}"
            if svc.get("default_replicas"):
                line += f" (default: {svc['default_replicas']} replicas)"
            if svc.get("notes"):
                line += f" ⚠️ {svc['notes']}"
            parts.append(line)
        parts.append("")

    if kb.get("network"):
        parts.append("### Network / CIDR")
        for net in kb["network"]:
            line = f"- `{net['cidr']}` — {net.get('name', '')}"
            if net.get("notes"):
                line += f": {net['notes']}"
            parts.append(line)
        parts.append("")

    if kb.get("teams"):
        parts.append("### Teams")
        for team in kb["teams"]:
            line = f"- **{team['name']}**: lead={team.get('lead', '?')}"
            if team.get("slack"):
                line += f", slack={team['slack']}"
            parts.append(line)
        parts.append("")

    if kb.get("notes"):
        parts.append("### Ghi chú")
        parts.append(kb["notes"].strip())
        parts.append("")

    return "\n".join(parts)
