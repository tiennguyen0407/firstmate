#!/usr/bin/env bash
# check_memory.sh — Xem toàn bộ memory của FirstMate
# Usage: bash check_memory.sh [actor_id]
#        bash check_memory.sh all         — xem tất cả actors

set -euo pipefail

MEMORY_AGENT_URL="${MEMORY_AGENT_URL:?MEMORY_AGENT_URL is required (set in .env.local)}"
MEMORY_ID="${AGENTBASE_MEMORY_ID:?AGENTBASE_MEMORY_ID is required (set in .env.local)}"
SCRIPTS_DIR=".claude/skills/agentbase/scripts"

# ── Helpers ───────────────────────────────────────────────────────

fetch_all_events() {
  local actor_id="$1" session_id="$2"
  local page=1 all_events="[]"
  while true; do
    result=$(bash "${SCRIPTS_DIR}/memory.sh" events list "${MEMORY_ID}" "${actor_id}" "${session_id}" \
      --page "$page" --size 50 2>/dev/null)
    chunk=$(echo "$result" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(json.dumps(d.get('listData',[])))
" 2>/dev/null)
    count=$(echo "$chunk" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
    all_events=$(python3 -c "
import json,sys
a=json.loads('${all_events}')
b=json.loads(sys.stdin.read())
print(json.dumps(a+b))
" <<< "$chunk")
    [ "$count" -lt 50 ] && break
    ((page++))
  done
  echo "$all_events"
}

print_events() {
  local actor_id="$1" session_id="$2"
  echo "  Session: ${session_id}"
  all=$(fetch_all_events "$actor_id" "$session_id")
  total=$(echo "$all" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
  echo "  Total events: ${total}"
  echo ""
  echo "$all" | python3 -c "
import json, sys
events = json.load(sys.stdin)
for e in reversed(events):
    payload = e.get('payload', {})
    role = payload.get('role', '?').upper()
    msg = payload.get('message', '')
    short = msg[:160].replace('\n',' ')
    suffix = f' ... (+{len(msg)-160}c)' if len(msg) > 160 else ''
    print(f'  [{role:>9}] {short}{suffix}')
  "
}

print_global_kb() {
  echo "── Global KB ──"
  curl -sf "${MEMORY_AGENT_URL}/kb/global" | python3 -c "
import json, sys
data = json.load(sys.stdin)
svc_map = data.get('service_namespace_map', {})
gw_map = data.get('gateway_map', {})
if svc_map:
    print(f'  service_namespace_map ({len(svc_map)} entries):')
    for svc, ns in svc_map.items():
        print(f'    {svc} → {ns}')
else:
    print('  service_namespace_map: (trống)')
if gw_map:
    print(f'  gateway_map ({len(gw_map)} entries):')
    for domain, info in gw_map.items():
        ip = info.get('ip', '?')
        env = info.get('env', '')
        log = info.get('log_path', '')
        parts = [f'ip={ip}']
        if env: parts.append(f'env={env}')
        if log: parts.append(f'log={log}')
        print(f'    {domain} → {\"  \".join(parts)}')
else:
    print('  gateway_map: (trống)')
  "
}

print_user_kb() {
  local actor_id="$1"
  echo "── Per-user KB (actor=${actor_id}) ──"
  curl -sf "${MEMORY_AGENT_URL}/kb/${actor_id}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
svc_map = data.get('service_namespace_map', {})
known_issues = data.get('known_issues', [])
env_notes = data.get('environment_notes', '')
active = data.get('active_services', [])
if svc_map:
    print(f'  service_namespace_map ({len(svc_map)} entries):')
    for svc, ns in svc_map.items():
        print(f'    {svc} → {ns}')
else:
    print('  service_namespace_map: (trống)')
if active:
    print(f'  active_services: {active}')
if env_notes:
    print(f'  environment_notes: {env_notes[:200]}')
if known_issues:
    print(f'  known_issues ({len(known_issues)}):')
    for issue in known_issues[:5]:
        print(f'    - {issue[:120]}')
  "
}

# ── Main ──────────────────────────────────────────────────────────

MODE="${1:-743616350}"

echo "════════════════════════════════════════════════"
echo "  FirstMate Memory Status"
echo "════════════════════════════════════════════════"
echo ""

print_global_kb
echo ""

if [ "$MODE" = "all" ]; then
  # Lấy tất cả actors
  actors=$(bash "${SCRIPTS_DIR}/memory.sh" actors "${MEMORY_ID}" --size 100 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(a.get('actorId','') for a in d.get('listData',[])))" 2>/dev/null)
  echo "── All actors ──"
  echo "$actors" | while IFS= read -r actor; do
    [ -z "$actor" ] && continue
    echo ""
    print_user_kb "$actor"
    echo ""
    echo "  AgentBase events (actor=${actor}, session=chat-${actor}):"
    print_events "$actor" "chat-${actor}"
    echo ""
    echo "  ────────────────────────────────────────"
  done
else
  ACTOR_ID="$MODE"
  print_user_kb "$ACTOR_ID"
  echo ""
  echo "── AgentBase Memory events (all) ──"
  print_events "$ACTOR_ID" "chat-${ACTOR_ID}"
fi

echo ""
echo "════════════════════════════════════════════════"
