from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def _gateway_folder() -> str:
    folder = os.getenv("GATEWAY_FOLDER", "")
    if not folder:
        raise RuntimeError("GATEWAY_FOLDER chưa được cấu hình trong .env")
    return folder


def _gateway_login() -> str:
    login = os.getenv("GATEWAY_LOGIN", "")
    if not login:
        raise RuntimeError("GATEWAY_LOGIN chưa được cấu hình trong .env")
    return login


def find_log_path(domain: str, env: str, ip: str) -> str:
    """
    Tìm đường dẫn log file từ nginx vhost config.
    Trả về log path trên remote server (vd: /zserver/nginx/logs/dev.zalopay.vn_access.log).
    """
    gateway_folder = _gateway_folder()
    conf_path = Path(gateway_folder) / env / ip / "nginx" / "conf" / "vhost" / f"{domain}.conf"

    if not conf_path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy config file: {conf_path}\n"
            f"Kiểm tra: GATEWAY_FOLDER={gateway_folder}, env={env}, ip={ip}, domain={domain}"
        )

    content = conf_path.read_text()
    # Tìm dòng: access_log /path/to/file.log ...;
    match = re.search(r'access_log\s+(\S+)\s', content)
    if not match:
        raise ValueError(f"Không tìm thấy access_log trong {conf_path}")

    return match.group(1)


def fetch_gateway_log(domain: str, env: str, ip: str, lines: int = 200) -> str:
    """
    SSH vào gateway server và lấy log. Trả về nội dung log thô.
    """
    login = _gateway_login()
    log_path = find_log_path(domain, env, ip)

    cmd = [
        "/usr/local/bin/tsh", "ssh",
        f"--login={login}",
        f"ipv4={ip}",
        f"bash -c 'tail -n {lines} {log_path}'"
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        output = result.stdout or result.stderr or "[no output]"
        return output
    except subprocess.TimeoutExpired:
        return "[timeout after 30s — tsh ssh không phản hồi]"
    except FileNotFoundError:
        return "[error: không tìm thấy /usr/local/bin/tsh — kiểm tra Teleport client đã cài chưa]"
    except Exception as e:
        return f"[error: {e}]"
