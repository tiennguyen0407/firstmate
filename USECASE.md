# FirstMate — Use Cases & Roadmap

---

## Use Cases hiện tại

### 1. Incident Response — Pod crash / service down

Dev/QC phát hiện lỗi, cần điều tra ngay mà không muốn chờ SRE available.

```
Dev: "loyalty-biz-tool đang báo lỗi, check giúp"

FirstMate:
  → Tìm namespace từ Global KB: loyalty-biz-tool → zpp-loyalty-qc
  → kubectl get pods -n zpp-loyalty-qc -l app=loyalty-biz-tool
  → 1/2 pod đang CrashLoopBackOff
  → kubectl logs loyalty-biz-tool-xxx --previous → OOMKilled

FirstMate: "loyalty-biz-tool đang có 1 pod OOMKilled (memory limit 512Mi).
            Cần tăng lên 768Mi. Bạn có muốn SRE patch không?"

Dev: "có"
  → Manager tìm SRE có Runner online
  → Runner chạy kubectl patch deployment... trên máy SRE
  → SRE approve trong terminal
  → Báo lại Dev: "Đã patch, pod đang restart"
```

**Thời gian**: ~5 phút (vs 30–60 phút thủ công)

---

### 2. Debug Session — Multi-turn với context

Dev đang debug, hỏi nhiều câu liên tiếp — FirstMate nhớ context xuyên suốt nhờ AgentBase Memory.

```
Dev: "loyalty-tier-core đang lỗi 500"
  → FirstMate xem log, tìm database timeout

Dev: "config timeout hiện tại là bao nhiêu?"
  → FirstMate đọc configmap (nhớ service từ câu trước, không cần nhắc lại)

Dev: "so với namespace zpp-loyalty-prod thì sao?"
  → FirstMate compare configmap giữa 2 namespace

Dev: "restart service giúp tôi"
  → Write op → SRE approve → Runner rollout restart
```

Context được lưu vào AgentBase Memory — restart Manager không mất conversation.

---

### 3. Read-only Check — Không cần SRE

Những tác vụ chỉ đọc dữ liệu, FirstMate tự xử lý hoàn toàn.

```
Dev: "namespace zpp-loyalty-qc có bao nhiêu deployment?"
  → FirstMate: kubectl get deploy -n zpp-loyalty-qc
  → "36 deployments, 10 đang scale=0 (job hoặc inactive)"

Dev: "loyalty-point-engine có bao nhiêu pod đang chạy?"
  → Tìm namespace từ Global KB (không cần kubectl lookup namespace)
  → kubectl get pods → "2/2 Running, uptime 3d"

Dev: "check log dev.zalopay.vn"
  → Tìm gateway từ Global KB: dev.zalopay.vn → ip=10.40.81.2
  → Fetch /zserver/nginx/logs/dev.zalopay.vn_access.log
  → Trả về 50 dòng cuối
```

---

### 4. Global Knowledge Base — Tự học từ conversation

Sau mỗi job hoàn thành, `firstmate-memory` đọc conversation, dùng LLM trích xuất facts và cập nhật Global KB. KB này được chia sẻ cho tất cả user, persist qua restart.

**Service → Namespace map** (tích lũy tự động từ kubectl outputs):
```
loyalty-biz-tool        → zpp-loyalty-qc
loyalty-tier-core       → zpp-loyalty-qc
loyalty-point-engine    → zpp-loyalty-qc
... (36 services, tự tăng khi team hỏi thêm)
```

**Gateway map** (tích lũy từ các lần check log):
```
dev.zalopay.vn  → ip=10.40.81.2  env=dev  log=/zserver/nginx/logs/dev.zalopay.vn_access.log
```

Lần tiếp theo bất kỳ user nào hỏi "loyalty-biz-tool ở namespace nào?" — FirstMate trả lời ngay, không cần kubectl lookup. Kể cả user khác hỏi lần đầu.

Kiểm tra Global KB:
```bash
bash check_memory.sh <telegram_chat_id>
```

---

### 5. SRE Approval Flow — Write ops an toàn

Mọi write operation đều đi qua 2 lớp kiểm soát.

```
Dev: "restart loyalty-gateway"

Manager:
  → Phát hiện write op (rollout restart)
  → Tìm SRE có Runner online: sre-tien
  → Gửi job qua Telegram: "Job #42: restart loyalty-gateway"
  → SRE nhấn [Accept] trên Telegram

Runner (máy SRE):
  → Claude Code chạy: kubectl rollout restart deployment/loyalty-gateway -n zpp-loyalty-qc
  → Pre-tool hook: alert Telegram "Sắp chạy: kubectl rollout restart..."
  → SRE confirm trong terminal: [y/N]
  → Thực thi, ghi summary

Manager:
  → Nhận kết quả, gửi báo cáo về Telegram cho Dev
```

Write ops rủi ro cao (scale=0, delete, patch resource limits): thêm tầng **Lead approval** trước bước Runner.

---

### 6. Escalation — Không có SRE online

```
Dev: "cần scale up loyalty-transaction khẩn"

Manager:
  → Kiểm tra runner_registry: không có SRE nào online
  → "Hiện không có SRE nào available.
     Đã ghi lại job #43. Vui lòng liên hệ trực tiếp hoặc đợi SRE online."
  → Gửi alert đến SRE Lead qua Telegram
```