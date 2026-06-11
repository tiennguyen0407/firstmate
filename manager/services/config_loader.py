from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional
import yaml


_CONFIG_PATH = Path(__file__).parent.parent / "config" / "services.yaml"


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_service(service: str) -> Optional[dict]:
    return load_config()["services"].get(service)


def get_owner_sre(service: str) -> Optional[str]:
    svc = get_service(service)
    return svc["owner_sre"] if svc else None


def get_sres_for_service(service: str, exclude: list[str] = []) -> list[dict]:
    """Trả về list SRE có thể handle service, theo thứ tự ưu tiên."""
    cfg = load_config()
    owner = get_owner_sre(service)
    runners = cfg.get("runners", {})

    ordered = []
    # Owner đầu tiên
    if owner and owner not in exclude and owner in runners:
        ordered.append({"id": owner, **runners[owner]})

    # Các SRE khác có service trong services_owned
    for rid, info in runners.items():
        if rid == owner or rid in exclude or info.get("is_lead"):
            continue
        if service in info.get("services_owned", []):
            ordered.append({"id": rid, **info})

    return ordered


def get_lead() -> Optional[dict]:
    runners = load_config().get("runners", {})
    for rid, info in runners.items():
        if info.get("is_lead"):
            return {"id": rid, **info}
    return None


def get_sre_timeout() -> int:
    return load_config().get("sre_timeout_seconds", 300)


def get_lead_timeout() -> int:
    return load_config().get("lead_timeout_seconds", 600)
