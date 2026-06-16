# FirstMate — Use Cases

---

## Use Cases hiện tại

### 1. Incident Response — Pod crash / service down

Dev/QC phát hiện lỗi, cần điều tra ngay mà không muốn chờ SRE available.

```
Dev: "loyalty-biz-tool đang báo lỗi, check giúp"

Manager:
  → Tra Global KB: loyalty-biz-tool → zpp-loyalty-qc
  → Build job description, assign cho Runner đang online

Runner (máy SRE, tự động không cần approve):
  → kubectl get pods -n zpp-loyalty-qc -l app=loyalty-biz-tool
  → 1/2 pod CrashLoopBackOff
  → kubectl logs loyalty-biz-tool-xxx --previous → OOMKilled
  → Gửi kết quả về Manager

Manager → Dev: "loyalty-biz-tool có 1 pod OOMKilled (limit 512Mi).
                Cần tăng lên 768Mi. Bạn có muốn SRE patch không?"

Dev: "có"
  → Manager build write job, SRE nhấn Accept trên Telegram

Runner (SRE approve trong terminal):
  → kubectl patch deployment loyalty-biz-tool ...
  → Báo kết quả về Manager → Dev: "Đã patch, pod đang restart"
```

**Thời gian**: ~5 phút (vs 30–60 phút thủ công)

---

### 2. Debug Session — Multi-turn với context

Dev đang debug, hỏi nhiều câu liên tiếp — Manager nhớ context xuyên suốt nhờ AgentBase Memory.

```
Dev: "loyalty-tier-core đang lỗi 500"
  → Runner xem log, tìm database timeout → trả kết quả về Manager

Dev: "config timeout hiện tại là bao nhiêu?"
  → Manager nhớ service từ câu trước
  → Runner đọc configmap loyalty-tier-core

Dev: "so với namespace zpp-loyalty-prod thì sao?"
  → Runner compare configmap giữa 2 namespace

Dev: "restart service giúp tôi"
  → Manager nhận diện write op
  → SRE approve → Runner rollout restart
```

Context được lưu vào AgentBase Memory — Manager restart không mất conversation.

---

### 3. Read-only Check — Không cần SRE approve

Tác vụ chỉ đọc: Runner tự thực thi, không cần SRE bấm nút.

```
Dev: "namespace zpp-loyalty-qc có bao nhiêu deployment?"
  → Manager assign job read-only cho Runner
  → Runner: kubectl get deploy -n zpp-loyalty-qc
  → "36 deployments, 10 đang scale=0"

Dev: "loyalty-point-engine có bao nhiêu pod?"
  → Manager tra Global KB → loyalty-point-engine ở zpp-loyalty-qc
  → Runner: kubectl get pods -n zpp-loyalty-qc -l app=loyalty-point-engine
  → "2/2 Running"

Dev: "check log dev.zalopay.vn"
  → Manager tra Global KB → dev.zalopay.vn: ip=10.40.81.2, log=/zserver/nginx/logs/...
  → Runner fetch log từ gateway → trả 50 dòng cuối
```

---

### 4. Global Knowledge Base — Tự học từ conversation

Sau mỗi job, `firstmate-memory` đọc conversation, LLM trích xuất facts vào Global KB — chia sẻ cho tất cả user, persist qua restart.

**Service → Namespace** (tích lũy từ kubectl outputs của Runner):
```
loyalty-biz-tool     → zpp-loyalty-qc
loyalty-tier-core    → zpp-loyalty-qc
loyalty-point-engine → zpp-loyalty-qc
... (36 services, tự tăng khi team hỏi thêm)
```

**Gateway map** (tích lũy từ các lần check log):
```
dev.zalopay.vn → ip=10.40.81.2  env=dev  log=/zserver/nginx/logs/dev.zalopay.vn_access.log
```

Lần sau bất kỳ user nào hỏi "loyalty-biz-tool ở namespace nào?" — Manager trả lời ngay từ KB, không cần assign job cho Runner.

```bash
bash check_memory.sh <telegram_chat_id>   # kiểm tra Global KB
```

---

### 5. SRE Approval Flow — Write ops an toàn

```
Dev: "restart loyalty-gateway"

Manager:
  → Nhận diện write op (rollout restart)
  → Tìm SRE có Runner online: sre-tien
  → Gửi job: "Job #42: restart loyalty-gateway -n zpp-loyalty-qc"
  → SRE nhấn [Accept] trên Telegram

Runner (máy SRE):
  → Claude Code chuẩn bị: kubectl rollout restart deployment/loyalty-gateway
  → Pre-tool hook: Telegram alert "Sắp chạy lệnh này..."
  → SRE xác nhận trong terminal [y/N]
  → Thực thi, gửi kết quả về Manager

Manager → Dev: kết quả + trạng thái rolling update
```

Write ops rủi ro cao (scale=0, delete, patch resource limits): thêm tầng **Lead approval** trước bước Runner.

---

### 6. Escalation — Không có SRE online

```
Dev: "cần scale up loyalty-transaction khẩn"

Manager:
  → Kiểm tra runner_registry: không có Runner nào online
  → Dev: "Hiện không có SRE nào available.
          Vui lòng liên hệ trực tiếp hoặc đợi SRE online."
  → Gửi alert cho SRE Lead qua Telegram
```
