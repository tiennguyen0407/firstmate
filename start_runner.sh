#!/usr/bin/env bash
# Khởi động FirstMate Runner trên máy local SRE
set -e
cd "$(dirname "$0")"

# Load runner env
export $(grep -v '^#' runner/.env | xargs)

# Python path
export PYTHONPATH="$(pwd):${PYTHONPATH}"

# Activate venv nếu có
[[ -f .venv/bin/activate ]] && source .venv/bin/activate

# Install runner deps nếu chưa có
pip install -q -r runner/requirements.txt

echo ""
echo "Đã load config:"
echo "  RUNNER_ID     = $RUNNER_ID"
echo "  SRE_ID        = $SRE_ID"
echo "  SRE_TELEGRAM  = $SRE_TELEGRAM_ID"
echo "  MANAGER_URL   = $MANAGER_URL"
echo ""

python -m runner.main
