from __future__ import annotations

import subprocess
import shlex


def run_kubectl(cmd: str, timeout: int = 30) -> str:
    """Chạy kubectl command. cmd là full command string bắt đầu bằng 'kubectl'."""
    if not cmd.strip().startswith("kubectl"):
        return "[error: only kubectl commands allowed]"
    try:
        parts = shlex.split(cmd)
        result = subprocess.run(
            parts, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout or result.stderr or "[no output]"
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
    except Exception as e:
        return f"[error: {e}]"
