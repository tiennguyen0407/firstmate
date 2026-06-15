#!/usr/bin/env bash
# FirstMate — Deploy Manager lên GreenNode AgentBase
#
# Usage:
#   bash deploy.sh              # build + push + update runtime
#   bash deploy.sh --skip-build # chỉ update runtime với image tag vừa push
#   bash deploy.sh --dry-run    # show commands, không chạy
#
# Config: copy deploy.config.example → deploy.config và điền giá trị thực tế.

set -euo pipefail
cd "$(dirname "$0")"

# ── Load config ───────────────────────────────────────────────────────────────
if [[ ! -f "deploy.config" ]]; then
  echo "❌ deploy.config không tồn tại."
  echo "   cp deploy.config.example deploy.config  rồi điền giá trị thực tế."
  exit 1
fi
# shellcheck source=deploy.config
source deploy.config

# ── Parse flags ───────────────────────────────────────────────────────────────
SKIP_BUILD=false
DRY_RUN=false
CUSTOM_TAG=""

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    --dry-run)    DRY_RUN=true ;;
    --tag=*)      CUSTOM_TAG="${arg#--tag=}" ;;
    *)            echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

TAG="${CUSTOM_TAG:-v$(date +%Y%m%d%H%M%S)}"
FULL_IMAGE="${REGISTRY}/${REPO}/${IMAGE_NAME}:${TAG}"

# ── Helpers ───────────────────────────────────────────────────────────────────
run() {
  echo "▶ $*"
  if [[ "$DRY_RUN" == "false" ]]; then
    "$@"
  fi
}

info() { echo ""; echo "  $*"; }
header() { echo ""; echo "══════════════════════════════════════"; echo "  $*"; echo "══════════════════════════════════════"; }

# ── Preflight checks ──────────────────────────────────────────────────────────
header "Preflight"

if ! command -v docker &>/dev/null; then
  echo "❌ docker không tìm thấy"; exit 1
fi
info "✅ docker OK"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ Env file không tồn tại: $ENV_FILE"; exit 1
fi
info "✅ env file: $ENV_FILE"

if [[ ! -f "Dockerfile" ]]; then
  echo "❌ Dockerfile không tìm thấy (chạy từ project root)"; exit 1
fi
info "✅ Dockerfile OK"

if [[ ! -f "$SCRIPTS_DIR/runtime.sh" ]]; then
  echo "❌ AgentBase scripts không tìm thấy: $SCRIPTS_DIR"
  echo "   Kiểm tra .claude/skills/agentbase/ đã được setup chưa"
  exit 1
fi
info "✅ AgentBase scripts OK"

# ── Build ─────────────────────────────────────────────────────────────────────
if [[ "$SKIP_BUILD" == "false" ]]; then
  header "Build Image"
  info "Tag: $FULL_IMAGE"
  info "Platform: linux/amd64"
  echo ""
  run docker build --platform linux/amd64 -t "${IMAGE_NAME}:${TAG}" .
fi

# ── Login to CR ───────────────────────────────────────────────────────────────
header "Registry Login"
info "Registry: $REGISTRY"
echo ""

if [[ "$DRY_RUN" == "false" ]]; then
  bash "$SCRIPTS_DIR/cr.sh" credentials docker-login
else
  echo "▶ bash $SCRIPTS_DIR/cr.sh credentials docker-login"
fi

# ── Tag + Push ────────────────────────────────────────────────────────────────
if [[ "$SKIP_BUILD" == "false" ]]; then
  header "Push Image"
  info "Destination: $FULL_IMAGE"
  echo ""
  run docker tag "${IMAGE_NAME}:${TAG}" "$FULL_IMAGE"
  run docker push "$FULL_IMAGE"
fi

# ── Update Runtime ────────────────────────────────────────────────────────────
header "Update Runtime"
info "Runtime ID : $RUNTIME_ID"
info "Image      : $FULL_IMAGE"
info "Flavor     : $FLAVOR"
info "Env file   : $ENV_FILE"
echo ""

run bash "$SCRIPTS_DIR/runtime.sh" update "$RUNTIME_ID" \
  --image "$FULL_IMAGE" \
  --flavor "$FLAVOR" \
  --from-cr \
  --env-file "$ENV_FILE"

# ── Wait for ACTIVE ───────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == "false" ]]; then
  header "Waiting for ACTIVE"
  TIMEOUT=120
  INTERVAL=5
  ELAPSED=0
  while true; do
    STATUS=$(bash "$SCRIPTS_DIR/runtime.sh" get "$RUNTIME_ID" 2>/dev/null \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    printf "\r  [%3ds] Status: %-20s" "$ELAPSED" "$STATUS"
    if [[ "$STATUS" == "ACTIVE" ]]; then
      echo ""
      info "✅ Runtime ACTIVE"
      break
    fi
    if [[ "$STATUS" == "ERROR" || "$STATUS" == "FAILED" ]]; then
      echo ""
      echo "❌ Runtime vào trạng thái $STATUS — deploy thất bại"
      exit 1
    fi
    if [[ $ELAPSED -ge $TIMEOUT ]]; then
      echo ""
      echo "⚠️  Timeout ${TIMEOUT}s — status vẫn là $STATUS"
      echo "   Kiểm tra thủ công: bash $SCRIPTS_DIR/runtime.sh get $RUNTIME_ID"
      exit 1
    fi
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
  done
fi

# ── Done ──────────────────────────────────────────────────────────────────────
header "Done ✅"
info "Image   : $FULL_IMAGE"
info "Runtime : $RUNTIME_ID"
info ""
info "Kiểm tra status:"
info "  bash $SCRIPTS_DIR/runtime.sh get $RUNTIME_ID"
info ""
info "Xem logs (endpoint cần biết trước):"
info "  bash $SCRIPTS_DIR/runtime.sh endpoints list $RUNTIME_ID"
echo ""
