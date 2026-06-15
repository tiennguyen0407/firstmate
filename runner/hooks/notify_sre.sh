#!/usr/bin/env bash
# FirstMate — PreToolUse hook
#   - Whitelist commands  → exit 0 (auto-approve, không hỏi)
#   - Write/unknown cmds  → Telegram alert + exit 0 (Claude Code hiện permission dialog)
#   - Dangerous commands  → deny list trong settings.json chặn trước hook này

INPUT=$(cat)

TOOL_NAME=$(python3 -c "
import sys, json
try:
    print(json.load(sys.stdin).get('tool_name', ''))
except:
    print('')
" <<< "$INPUT")

COMMAND=$(python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('command', ''))
except:
    print('')
" <<< "$INPUT")

TOOL_NAME=$(echo "$TOOL_NAME" | xargs)

# Chỉ xử lý Bash tool
[[ "$TOOL_NAME" != "Bash" ]] && exit 0
[[ -z "$COMMAND" ]] && exit 0

# Load session config
CONFIG_FILE="${FIRSTMATE_SESSION_DIR:-}/.firstmate.json"
[[ ! -f "$CONFIG_FILE" ]] && exit 0

BOT_TOKEN=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['bot_token'])" 2>/dev/null)
SRE_ID=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['sre_telegram_id'])" 2>/dev/null)
JOB_ID=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['job_id'])" 2>/dev/null)
SERVICE=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['service'])" 2>/dev/null)

# Check whitelist
WHITELIST_FILE="${FIRSTMATE_SESSION_DIR:-}/.whitelist.txt"
IS_ALLOWED=false

if [[ -f "$WHITELIST_FILE" ]]; then
    while IFS= read -r pattern; do
        [[ -z "$pattern" ]] && continue
        if [[ "$COMMAND" == "$pattern"* ]]; then
            IS_ALLOWED=true
            break
        fi
    done < "$WHITELIST_FILE"
fi

# Whitelisted → allow ngay, không cần hỏi
[[ "$IS_ALLOWED" == "true" ]] && exit 0

# Không trong whitelist → gửi Telegram alert để SRE biết trước
# rồi exit 0 để Claude Code hiện permission dialog trong terminal
SHORT_JOB="${JOB_ID:0:8}"
SHORT_CMD="${COMMAND:0:400}"

[[ -n "$BOT_TOKEN" && -n "$SRE_ID" ]] && python3 -c "
import json, urllib.request

msg = (
    '⚠️ *FirstMate: Cần approve write op*\n\n'
    'Service: \`$SERVICE\`  Job: \`${SHORT_JOB}...\`\n\n'
    'FirstMate muốn chạy:\n'
    '\`\`\`\n${SHORT_CMD}\n\`\`\`\n\n'
    '👉 Approve hoặc Deny trong terminal FirstMate.'
)

payload = json.dumps({
    'chat_id': '$SRE_ID',
    'text': msg,
    'parse_mode': 'Markdown'
}).encode()

try:
    req = urllib.request.Request(
        'https://api.telegram.org/bot${BOT_TOKEN}/sendMessage',
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    urllib.request.urlopen(req, timeout=5)
except:
    pass
" &

# exit 0 → Claude Code tiếp tục và hiện permission dialog cho SRE
exit 0
