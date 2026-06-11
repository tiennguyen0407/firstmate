from __future__ import annotations

import subprocess
from langchain_core.tools import tool


def _kubectl(*args: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            ["kubectl", *args],
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout or result.stderr
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
    except FileNotFoundError:
        return "[error: kubectl not found in PATH]"


@tool
def list_pods(namespace: str, filter: str = "all") -> str:
    """List pods trong namespace. filter='crashlooping' để tìm pod lỗi."""
    out = _kubectl("get", "pods", "-n", namespace, "-o", "wide")
    if filter == "crashlooping":
        lines = [l for l in out.splitlines()
                 if "CrashLoop" in l or "Error" in l or "OOMKilled" in l
                 or l.startswith("NAME")]
        return "\n".join(lines) if lines else "Không có pod nào crashlooping."
    return out


@tool
def get_pod_logs(pod_name: str, namespace: str, lines: int = 200) -> str:
    """Lấy logs của pod. Tự động thử --previous nếu pod đã crash."""
    out = _kubectl("logs", pod_name, "-n", namespace, f"--tail={lines}")
    if not out.strip():
        out = _kubectl("logs", pod_name, "-n", namespace,
                       f"--tail={lines}", "--previous")
    return out or "[no logs found]"


@tool
def get_k8s_events(namespace: str, last_minutes: int = 30) -> str:
    """Lấy K8s events trong namespace."""
    return _kubectl("get", "events", "-n", namespace,
                    "--sort-by=.lastTimestamp")


@tool
def describe_pod(pod_name: str, namespace: str) -> str:
    """Describe pod: resource usage, conditions, events."""
    return _kubectl("describe", "pod", pod_name, "-n", namespace)


@tool
def get_configmap(name: str, namespace: str) -> str:
    """Lấy nội dung configmap."""
    return _kubectl("get", "configmap", name, "-n", namespace, "-o", "yaml")


@tool
def get_deployment_status(deployment: str, namespace: str) -> str:
    """Check deployment status và rollout history."""
    status = _kubectl("rollout", "status", f"deployment/{deployment}",
                      "-n", namespace)
    history = _kubectl("rollout", "history", f"deployment/{deployment}",
                       "-n", namespace)
    return f"STATUS:\n{status}\n\nHISTORY:\n{history}"


@tool
def describe_node(node_name: str) -> str:
    """Describe node: disk/memory pressure, allocatable resources."""
    return _kubectl("describe", "node", node_name)


K8S_READ_TOOLS = [
    list_pods,
    get_pod_logs,
    get_k8s_events,
    describe_pod,
    get_configmap,
    get_deployment_status,
    describe_node,
]
