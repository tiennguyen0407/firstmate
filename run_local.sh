#!/usr/bin/env bash
# Chạy FirstMate-Manager local để test trước khi deploy
# Usage: bash run_local.sh

set -e
cd "$(dirname "$0")"

# Load .env
export $(grep -v '^#' .env | xargs)

# Thêm cả thư mục gốc vào PYTHONPATH để import được shared/ và manager/
export PYTHONPATH="$(pwd):${PYTHONPATH}"

# Dùng venv nếu có
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi

# Cài deps nếu chưa có
pip install -q -r manager/requirements.txt

echo ""
echo "==============================="
echo " FirstMate-Manager (LOCAL)"
echo " DEBUG=$DEBUG"
echo " PORT=$PORT"
echo "==============================="
echo ""
echo "Bot đang chạy ở http://localhost:$PORT"
echo "Status: http://localhost:$PORT/status"
echo "Health: http://localhost:$PORT/health"
echo ""
echo "⚠️  Telegram webhook hiện đang trỏ về production."
echo "   Để test local, chạy lệnh sau để chuyển webhook về ngrok:"
echo "   curl -s https://ngrok-api.../your-url  (hoặc dùng polling — xem README)"
echo ""

python -m manager.main
