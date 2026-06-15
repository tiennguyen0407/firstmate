from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from shared.models import Job
from runner.rules import allow_patterns_for_settings, deny_patterns_for_settings, allow_patterns_plain

logger = logging.getLogger("firstmate.runner.terminal")

_HOOKS_DIR = Path(__file__).parent / "hooks"
_HOOK_SCRIPT = _HOOKS_DIR / "notify_sre.sh"


async def open_claude_terminal(job: Job) -> None:
    """Mở terminal mới với Claude Code đã được cấu hình cho job này."""
    session_dir = _prepare_session(job)
    startup = _write_startup_script(session_dir, job)
    _launch_terminal(startup, job)
    logger.info(f"Terminal opened for job={job.id[:8]} service={job.service}")


# ── Chuẩn bị session directory ────────────────────────────────────

def _prepare_session(job: Job) -> Path:
    d = Path(f"/tmp/firstmate-{job.id}")
    (d / ".claude").mkdir(parents=True, exist_ok=True)

    _write_claude_md(d, job)
    _write_settings_json(d)
    _write_whitelist_txt(d)
    _write_session_config(d, job)
    _write_notify_script(d)

    return d


def _write_claude_md(d: Path, job: Job) -> None:
    lines = [
        f"# FirstMate Task — {job.service}",
        "",
        f"**Job ID**: `{job.id}`",
        f"**Service**: {job.service}",
        f"**Environment**: {job.env}",
        "",
        "## Yêu cầu",
        "",
        job.description,
    ]
    if job.commands:
        lines += ["", "## Commands gợi ý", ""]
        for cmd in job.commands:
            lines.append(f"- `{cmd.cmd}` — {cmd.description}")

    if job.prev_summary:
        lines += [
            "",
            "---",
            "",
            "## Ngữ cảnh từ lần điều tra trước",
            "",
            "> Đây là follow-up task. Kết quả điều tra trước đã có đầy đủ namespace, "
            "deployment name và trạng thái service. **Dùng trực tiếp thông tin này, "
            "KHÔNG cần scan lại `--all-namespaces` hay tìm kiếm service.**",
            "",
            job.prev_summary,
        ]

    lines += [
        "",
        "---",
        "",
        "## Quy tắc điều tra Kubernetes",
        "",
        "### Namespace scoping — QUAN TRỌNG",
        "",
        "- **Chỉ 1 lần duy nhất** được phép dùng `--all-namespaces` hoặc `-A` — là lần đầu tiên tìm namespace.",
        "- Ngay khi tìm thấy bất kỳ namespace nào chứa service → **dừng quét all, chuyển sang `-n <namespace>` ngay lập tức**.",
        "- Nếu không tìm thấy đúng môi trường (ví dụ task nói 'production' nhưng chỉ có 'qc') →",
        "  **dùng namespace tìm được** (qc/dev/staging) thay vì tiếp tục quét all-namespaces tìm production.",
        "- Nhiều namespace: kiểm tra **từng cái riêng biệt** bằng `-n`, không quét all lần nữa.",
        "- **Lý do**: cluster có hàng nghìn pod, mỗi lần quét all tốn 5-10 giây và load cluster.",
        "",
        "### Search term — tìm đúng service, chịu lỗi chính tả",
        "",
        "Tên service trong Telegram có thể viết sai chính tả, thiếu từ hoặc thừa từ.",
        "Dùng **một lệnh grep duy nhất** với tất cả pattern từ cụ thể đến rộng, phân tách bằng `|`:",
        "",
        "```",
        "# Ví dụ với 'loyalty reward store':",
        "kubectl get pods -A 2>/dev/null | grep -iE 'loyalty-reward-store|loyalty-reward|reward-store|loyalty|reward' | grep -v '^NAMESPACE'",
        "```",
        "",
        "Cách build pattern từ tên service (N từ):",
        "1. Full kebab-case: `w1-w2-w3`",
        "2. Tất cả cặp liền kề: `w1-w2`, `w2-w3`",
        "3. Từng từ quan trọng (bỏ: store, service, app, api, server): `w1`, `w2`",
        "",
        "Sau khi có kết quả: nếu nhiều service match, liệt kê tất cả và chọn cái gần nhất",
        "với mô tả gốc. Ghi rõ trong summary service nào được chọn và tại sao.",
        "",
        "## Hướng dẫn",
        "",
        "- Commands trong whitelist chạy tự động không cần confirm",
        "- Commands write: **KHÔNG hỏi xác nhận trong conversation** — chạy lệnh ngay, "
        "FirstMate tự gửi Telegram alert và Claude Code hiện dialog approve trong terminal",
        "- Không tự sửa settings.json hay bất kỳ file config nào trong session này",
        "",
        "## Khi hoàn thành",
        "",
        "**Bắt buộc**: Trước khi thoát, viết tóm tắt kết quả vào file `summary.md`.",
        "File này được gửi tự động về manager để SRE verify và thông báo cho requester.",
        "",
        "**Chọn format phù hợp theo loại task — chỉ giữ các field có dữ liệu thực, bỏ field không liên quan:**",
        "",
        "**Kiểm tra định kỳ / thông tin** (không có sự cố):",
        "```",
        "## Kết quả kiểm tra — <service>",
        "**Yêu cầu**: <mô tả lại yêu cầu gốc>",
        "**Kết quả**: <số liệu / trạng thái cụ thể tìm được>",
        "**Chi tiết**: <danh sách thông tin bổ sung nếu có>",
        "**Kết luận**: <đánh giá ngắn gọn — bình thường / cần chú ý>",
        "```",
        "",
        "**Sự cố / incident** (có lỗi hoặc vấn đề):",
        "```",
        "## Kết quả điều tra — <service>",
        "**Triệu chứng**: <mô tả vấn đề quan sát được>",
        "**Nguyên nhân**: <root cause xác định được>",
        "**Đã xử lý**: <hành động đã thực hiện>",
        "**Trạng thái hiện tại**: <service đang ở trạng thái nào sau xử lý>",
        "**Bước tiếp theo**: <nếu cần thêm action>",
        "```",
        "",
        "**Không tìm thấy / không đủ quyền**:",
        "```",
        "## Kết quả điều tra — <service>",
        "**Yêu cầu**: <mô tả lại yêu cầu gốc>",
        "**Kết quả**: Không tìm thấy / Không đủ quyền truy cập",
        "**Lý do**: <giải thích cụ thể — namespace không tồn tại, không có quyền, v.v.>",
        "**Đề xuất**: <SRE cần làm gì tiếp>",
        "```",
    ]
    (d / "CLAUDE.md").write_text("\n".join(lines))


def _write_settings_json(d: Path) -> None:
    settings = {
        "permissions": {
            # Write(summary.md) cần allow để Claude ghi kết quả mà không hỏi
            # Bash write ops (kubectl scale/apply/...) KHÔNG allow — hook gửi Telegram
            # rồi exit 0, Claude Code hiện permission dialog trong terminal cho SRE approve
            "allow": allow_patterns_for_settings() + ["Write(*)"],
            "deny": deny_patterns_for_settings(),
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"bash {_HOOK_SCRIPT}",
                        }
                    ],
                }
            ]
        },
    }
    (d / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2, ensure_ascii=False)
    )


def _write_whitelist_txt(d: Path) -> None:
    """Plain text whitelist cho hook script đọc nhanh."""
    (d / ".whitelist.txt").write_text("\n".join(allow_patterns_plain()))


def _write_session_config(d: Path, job: Job) -> None:
    """Config cho hook script (bot token, telegram id, v.v.)"""
    cfg = {
        "job_id": job.id,
        "service": job.service,
        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "sre_telegram_id": os.getenv("SRE_TELEGRAM_ID", ""),
        "manager_url": os.getenv("MANAGER_URL", ""),
        "runner_id": os.getenv("RUNNER_ID", ""),
    }
    (d / ".firstmate.json").write_text(json.dumps(cfg, indent=2))


def _write_notify_script(d: Path) -> None:
    """Viết _notify.py — gọi sau khi summary.md được tạo để báo manager."""
    script = '''import json, os, sys, urllib.request, urllib.error

session_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(session_dir, ".firstmate.json")
summary_path = os.path.join(session_dir, "summary.md")

try:
    with open(config_path) as f:
        cfg = json.load(f)
except Exception as e:
    print(f"[notify] ERROR: cannot read config: {e}", flush=True)
    sys.exit(1)

if not os.path.exists(summary_path):
    print("[notify] ERROR: summary.md not found", flush=True)
    sys.exit(1)

with open(summary_path) as f:
    summary = f.read().strip()

url = cfg["manager_url"] + "/webhook/job-complete"
payload = json.dumps({
    "job_id": cfg["job_id"],
    "runner_id": cfg["runner_id"],
    "summary": summary,
}).encode()

print(f"[notify] POSTing to {url}", flush=True)
try:
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=15)
    body = resp.read().decode()
    print(f"[notify] OK: {body}", flush=True)
except urllib.error.HTTPError as e:
    print(f"[notify] HTTP {e.code}: {e.read().decode()[:300]}", flush=True)
    sys.exit(1)
except Exception as e:
    print(f"[notify] ERROR: {e}", flush=True)
    sys.exit(1)
'''
    (d / "_notify.py").write_text(script)


# ── Startup script ─────────────────────────────────────────────────

def _write_startup_script(d: Path, job: Job) -> Path:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    sre_tg_id = os.getenv("SRE_TELEGRAM_ID", "")
    manager_url = os.getenv("MANAGER_URL", "")
    runner_id = os.getenv("RUNNER_ID", "")

    # Tìm đường dẫn tuyệt đối đến claude
    claude_bin = shutil.which("claude") or "claude"

    # Escape description để hiển thị an toàn trong bash (không inject command)
    desc_display = (
        job.description
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    # Phiên bản ngắn cho Claude startup prompt (1 dòng, tối đa 300 ký tự)
    desc_oneline = desc_display.replace("\n", " ")[:300]

    script = f"""#!/usr/bin/env bash
# FirstMate Runner — session startup

# ── Source shell profile để có đúng PATH (iTerm2/Terminal mở window mới
#    không inherit PATH của parent process) ─────────────────────────────
[ -f ~/.zprofile ]    && source ~/.zprofile    2>/dev/null
[ -f ~/.zshrc ]       && source ~/.zshrc       2>/dev/null
[ -f ~/.bash_profile ] && source ~/.bash_profile 2>/dev/null
[ -f ~/.bashrc ]      && source ~/.bashrc      2>/dev/null

# Đảm bảo claude binary tìm thấy
export PATH="{os.path.dirname(claude_bin)}:$PATH"

# ── Env vars cho hooks và runner ───────────────────────────────────────
export FIRSTMATE_SESSION_DIR="{d}"
export FIRSTMATE_JOB_ID="{job.id}"
export TELEGRAM_BOT_TOKEN="{bot_token}"
export SRE_TELEGRAM_ID="{sre_tg_id}"
export MANAGER_URL="{manager_url}"
export RUNNER_ID="{runner_id}"

cd "{d}"

clear
echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║          FirstMate Runner — Session mới           ║"
echo "╠═══════════════════════════════════════════════════╣"
printf "║  Job ID  : %-38s║\\n" "{job.id[:12]}..."
printf "║  Service : %-38s║\\n" "{job.service}"
printf "║  Env     : %-38s║\\n" "{job.env}"
echo "╠═══════════════════════════════════════════════════╣"
echo "║  ✅ kubectl get/describe/logs — auto approved     ║"
echo "║  ✅ cat/ls/find/grep/head/tail — auto approved    ║"
echo "║  ⚠️  Write ops → terminal prompt + Telegram alert ║"
echo "║  🚫 Dangerous ops — always blocked               ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""
echo "📋 Yêu cầu từ dev/QC:"
echo "───────────────────────────────────────────────────"
echo "{desc_display}"
echo "───────────────────────────────────────────────────"
echo ""

# Tự động trust session dir — tắt "Accessing workspace" prompt
# Claude Code lưu trust bằng cách tạo folder ~/.claude/projects/<path-with-/-as->
# Tạo folder đó trước khi launch là đủ, không cần settings.json
python3 - <<'TRUSTEOF'
import os
real_dir = os.path.realpath(os.environ.get('FIRSTMATE_SESSION_DIR', '.'))
# /private/tmp/firstmate-uuid → -private-tmp-firstmate-uuid
encoded = real_dir.replace('/', '-')
project_dir = os.path.expanduser(f'~/.claude/projects/{{encoded}}')
os.makedirs(project_dir, exist_ok=True)
print(f'[trust] pre-trusted: {{project_dir}}')
TRUSTEOF

# Kiểm tra claude binary
if ! command -v claude &>/dev/null; then
    echo "❌ Lỗi: không tìm thấy 'claude' trong PATH"
    echo "   Đang dùng binary: {claude_bin}"
    echo "   PATH = $PATH"
    echo ""
    echo "Nhấn Enter để đóng..."
    read
    exit 1
fi

# ── Background watcher: ngay khi summary.md xuất hiện → POST manager ──
# Dùng file _notify.py (thay vì bash heredoc) để tránh lỗi subshell scope
(
    _SENT=0
    while true; do
        if [ -f "summary.md" ] && [ "$_SENT" = "0" ]; then
            _SENT=1
            echo ""
            echo "📄 [watcher] summary.md đã tạo — đang báo manager..."
            python3 "{d}/_notify.py"
        fi
        sleep 3
    done
) &
_WATCHER_PID=$!

# Start Claude Code — đọc CLAUDE.md + .claude/settings.json tự động
"{claude_bin}" "Đọc CLAUDE.md và thực hiện task sau: {desc_oneline}"

# Dọn watcher khi Claude thoát
kill $_WATCHER_PID 2>/dev/null

# Fallback: nếu watcher chưa gửi kịp, gửi lần cuối
if [ -f "summary.md" ]; then
    echo ""
    echo "📤 [fallback] Đang gửi kết quả về manager..."
    python3 "{d}/_notify.py"
fi

echo ""
echo "─────────────────────────────────────────"
echo " FirstMate đã kết thúc. Nhấn Enter để đóng..."
echo "─────────────────────────────────────────"
read
"""
    path = d / "start.sh"
    path.write_text(script)
    path.chmod(0o755)
    return path


# ── Launch terminal ────────────────────────────────────────────────

def _launch_terminal(startup: Path, job: Job) -> None:
    platform = sys.platform
    if platform == "darwin":
        _launch_macos(startup)
    elif platform.startswith("linux"):
        _launch_linux(startup)
    else:
        logger.warning(f"Unsupported platform {platform}, running in current terminal")
        subprocess.Popen(["bash", str(startup)])


def _launch_macos(startup: Path) -> None:
    # Prefer iTerm2, fallback to Terminal.app
    has_iterm = subprocess.run(
        ["osascript", "-e",
         'tell application "Finder" to return exists POSIX file "/Applications/iTerm.app"'],
        capture_output=True, text=True,
    ).stdout.strip() == "true"

    if has_iterm:
        script = f'''
tell application "iTerm"
    create window with default profile command "bash {startup}"
    activate
end tell
'''
    else:
        script = f'''
tell application "Terminal"
    do script "bash {startup}"
    activate
end tell
'''
    subprocess.Popen(["osascript", "-e", script])


def _launch_linux(startup: Path) -> None:
    # Try common terminal emulators in order
    for term in ["gnome-terminal", "xterm", "konsole", "xfce4-terminal"]:
        try:
            if term == "gnome-terminal":
                subprocess.Popen([term, "--", "bash", str(startup)])
            else:
                subprocess.Popen([term, "-e", f"bash {startup}"])
            return
        except FileNotFoundError:
            continue
    # Last resort: tmux
    subprocess.Popen(["tmux", "new-window", f"bash {startup}"])
