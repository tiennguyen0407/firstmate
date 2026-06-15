# FirstMate — AI-Powered DevOps Collaboration Platform

> Kết nối Dev/QC — AI FirstMate-Manager — SRE thành một vòng xử lý sự cố thống nhất,  
> nơi con người luôn giữ quyền kiểm soát cuối cùng.

---

## Concept

### Vấn đề hiện tại

```
Dev/QC phát hiện lỗi / cần check data / đang debug
        ↓
Ping SRE → đợi SRE available → SRE SSH vào server / mở DB client
        ↓
SRE mò logs, check Redis, chạy SELECT thủ công
        ↓
Trung bình: 30-60 phút, SRE bị interrupt liên tục
```

Bottleneck: **SRE là cái cổ chai duy nhất** cho mọi thứ — kể cả những việc chỉ cần đọc log hay check một key Redis.

### Giải pháp: FirstMate

```
Dev/QC chat với FirstMate-Manager
        ↓
FirstMate-Manager tự xử lý những việc trong quyền (log, pod status, config)
        ↓
Việc cần credentials SRE → FirstMate-Manager tự tìm đúng SRE, assign Runner
        ↓
FirstMate-Runner trên máy SRE tự chạy, chỉ hỏi khi cần approve
        ↓
Trung bình: 5-10 phút, SRE chỉ approve — không cần manual
```

### 3 Use Case chính

#### 1. Incident Response
Xử lý sự cố đang xảy ra: pod crash, service down, disk đầy.
- Tính chất: khẩn cấp, cần action nhanh
- Flow: investigate → assign SRE → execute → report

#### 2. Debug Session
Dev/QC đang debug một issue, cần hỏi nhiều câu liên tiếp về hệ thống.
- Tính chất: **conversational, multi-turn** — hỏi → xem → hỏi tiếp
- Flow: hỏi đáp qua lại, FirstMate-Manager giữ context của session
- Ví dụ: "service payment đang trả lỗi 500" → xem log → "check config xem timeout bao nhiêu" → check configmap → "so với staging thì sao" → compare

#### 3. Data Check
Dev/QC cần verify dữ liệu: giá trị trong Redis, record trong DB, config đang apply.
- Tính chất: read-only, không cần action
- Flow: FirstMate-Manager tự làm nếu có quyền, hoặc assign Runner nếu cần credentials
- Ví dụ: "key session:user:123 trong Redis có value gì", "record order #456 trong DB trạng thái gì"

---

## Kiến trúc hệ thống

```
┌─────────────────────────────────────────────────────────────┐
│                      DEV / QC                               │
│              chat qua Telegram / Teams                       │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                 FirstMate-Manager                            │
│              (GreenNode AgentBase)                           │
│                                                             │
│  Quyền tự có:                                               │
│  ✓ Read-only K8s dev/QC env                                  │
│  ✓ CMDB public keys                                          │
│  ✗ Không có credentials production                           │
│                                                             │
│  Tự xử lý (read-only):    Cần SRE credentials:              │
│  • view pod logs          • delete / restart pod            │
│  • pod status / count     • scale deployment                │
│  • K8s events             • update configmap / secret       │
│  • configmap (public)     • exec vào pod                    │
│  • resource usage         • Redis GET/SCAN (prod)           │
│  • deployment history     • DB SELECT (prod)                │
│                           • check network trong pod         │
└───────────────────────┬─────────────────────────────────────┘
                        │  Job Assignment
                        │  (HTTP Long-polling)
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │  Runner  │  │  Runner  │  │  Runner  │
   │  SRE-A   │  │  SRE-B   │  │  SRE-C   │
   │ (online) │  │(offline) │  │ (online) │
   └──────────┘  └──────────┘  └──────────┘
   Máy của SRE    Máy của SRE   Máy của SRE
   Claude Code    Claude Code   Claude Code
```

---

## Hai thành phần chính

### FirstMate-Manager (Agent cha)

**Nơi chạy:** GreenNode AgentBase (cloud)

**Nhiệm vụ:**
- Tiếp nhận yêu cầu từ Dev/QC qua Telegram/Teams
- Tự động check những thứ trong quyền: logs, pod status, health
- Khi cần WRITE op: tìm SRE phù hợp và assign task
- Quản lý danh sách Runner đang online
- Track trạng thái task, báo cáo kết quả về cho Dev/QC

**Giới hạn quyền có chủ đích:**
- Chỉ read-only K8s của môi trường dev/QC
- Không bao giờ có credentials production trực tiếp
- Mọi write op phải qua SRE approve

---

### FirstMate-Runner (Agent con)

**Nơi chạy:** Máy local của SRE (Claude Code / Codex)

**Nhiệm vụ:**
- Duy trì kết nối với FirstMate-Manager (long-polling, giống GitLab Runner)
- Nhận task được assign, tự động chạy read ops
- Hỏi SRE confirm trước khi chạy write ops
- Dùng credentials đầy đủ của SRE trên máy SRE

**Quyền theo SRE:**
- Mỗi SRE có danh sách service mình là owner
- FirstMate-Manager chỉ assign task cho đúng SRE owner của service đó
- SRE có thể từ chối hoặc delegate

---

## Luồng xử lý chi tiết

### Luồng 1: Dev/QC hỏi — FirstMate-Manager tự xử lý

```
Dev: "service payment đang báo lỗi gì vậy?"
        ↓
FirstMate-Manager check quyền → có quyền read K8s dev
        ↓
FirstMate-Manager tự gọi: kubectl logs, kubectl get pods
        ↓
FirstMate-Manager: "Pod payment-xxx có 3 lần OOMKilled trong 30 phút.
          Log gần nhất: java.lang.OutOfMemoryError..."
        ↓
Dev nhận câu trả lời ngay — không cần SRE
```

### Luồng 2: Cần WRITE op — FirstMate-Manager contact SRE

```
Dev: "pod payment bị stuck, cần restart"
        ↓
FirstMate-Manager nhận diện: restart = WRITE op
        ↓
FirstMate-Manager check config:
  - Service "payment" → owner: SRE-Tien
  - SRE-Tien: Runner đang online ✓
        ↓
FirstMate-Manager gửi Telegram cho SRE-Tien:
  "📋 Task mới từ Dev-Nam:
   Pod payment-6d8f9b-xxx bị stuck (không nhận traffic 15 phút)
   Cần: kubectl rollout restart deployment/payment -n prod
   Accept? [✓ Yes] [✗ No] [👁 View first]"
        ↓
SRE-Tien nhấn [✓ Yes]
        ↓
Runner trên máy SRE-Tien tự chạy lệnh
        ↓
Runner báo kết quả về FirstMate-Manager
        ↓
FirstMate-Manager báo cho Dev-Nam: "Đã restart xong. Pod mới đang Running."
```

### Luồng 3: SRE bận / không phản hồi → escalate SRE khác

```
FirstMate-Manager gửi Telegram cho SRE-Tien
        ↓
[chờ 5 phút — không reply hoặc reply "bận"]
        ↓
FirstMate-Manager tự động tìm SRE tiếp theo có quyền với service payment
        ↓
FirstMate-Manager gửi Telegram cho SRE-Nam:
  "📟 Escalated từ SRE-Tien (không available)
   Service: payment | Task: rollout restart
   Accept? [✓ Yes] [✗ No]"
        ↓
SRE-Nam accept → Runner SRE-Nam chạy
        ↓
Nếu SRE-Nam cũng không reply sau 5 phút → ESCALATED
FirstMate-Manager báo Dev: "Không có SRE available, cần xử lý thủ công"
```

### Luồng 4: Cần SRE-Lead approve sau khi SRE action xong

```
Runner SRE-Tien chạy xong
        ↓
SRE-Tien báo FirstMate-Manager: "Done, nhưng cần Lead approve để confirm"
        ↓
FirstMate-Manager tự động tìm SRE-Lead có quyền với service payment
        ↓
FirstMate-Manager gửi Telegram cho SRE-Lead:
  "📋 Cần approve từ Lead
   SRE-Tien đã thực hiện: rollout restart payment
   Kết quả: 2/2 pods Running
   Ticket approve? [✓ Approve] [✗ Reject]"
        ↓
SRE-Lead approve → job COMPLETED, FirstMate-Manager báo Dev
SRE-Lead reject  → job REJECTED, FirstMate-Manager báo Dev + SRE-Tien
```

### Luồng 5: Debug session — multi-turn

```
Dev: "service payment đang trả lỗi 500 lúc checkout"
        ↓
FirstMate-Manager check logs payment → tìm thấy timeout error kết nối DB
FirstMate-Manager: "Thấy lỗi timeout kết nối DB lúc 14:32. Chi tiết:
          connect ETIMEDOUT 10.0.1.5:5432 (timeout sau 3000ms)"

Dev: "timeout có vẻ thấp, config timeout hiện tại là bao nhiêu?"
        ↓
FirstMate-Manager check configmap payment → DB_TIMEOUT=3000
FirstMate-Manager: "DB_TIMEOUT đang set 3000ms trong configmap.
          Staging đang set 10000ms."

Dev: "so sánh toàn bộ config payment giữa prod và staging"
        ↓
FirstMate-Manager check cả 2 configmap → diff
FirstMate-Manager: "3 key khác nhau:
          DB_TIMEOUT: prod=3000 / staging=10000
          MAX_CONN:   prod=5    / staging=20
          RETRY:      prod=1    / staging=3"

Dev: "update DB_TIMEOUT lên 10000 giúp"
        ↓
FirstMate-Manager nhận diện WRITE op → assign SRE-Tien
[tiếp tục flow WRITE op như Luồng 2]
```

FirstMate-Manager **giữ context toàn bộ session** — Dev không cần nhắc lại "service payment" ở mỗi câu.

### Luồng 6: Data check — verify giá trị

```
Dev: "check xem session của user_id=123 còn trong Redis không"
        ↓
FirstMate-Manager nhận diện: cần Redis credentials → assign Runner đọc Redis
        ↓
Runner chạy: GET session:user:123
Runner: key tồn tại, TTL còn 1847s, value: { role: user, cart: [...] }
        ↓
FirstMate-Manager trả về Dev: "Session user 123 còn hạn (30 phút nữa hết).
                     Có 3 items trong cart."

Dev: "order #789 trong DB trạng thái gì"
        ↓
FirstMate-Manager assign Runner chạy SELECT (read-only)
Runner: SELECT status, updated_at FROM orders WHERE id=789
        → status=PENDING_PAYMENT, updated_at=2024-01-15 14:20:01
        ↓
FirstMate-Manager: "Order #789 đang ở PENDING_PAYMENT từ 14:20 hôm nay (43 phút)."
```

### Luồng 7: SRE follow task live

```
SRE mở Claude Code → thấy task đang assigned
SRE chọn "Follow live" → xem từng bước Runner đang làm
Khi Runner cần approve → SRE approve ngay trên Claude Code
Không cần qua Telegram
```

---

## State Machine — LangGraph + MemorySaver

### Tại sao LangGraph cho FirstMate-Manager

FirstMate-Manager có nhiều điểm dừng chờ async — chờ SRE reply Telegram, chờ Lead approve, chờ Runner xong. Mỗi callback Telegram là một HTTP request riêng biệt. LangGraph với checkpointer tự động lưu và resume đúng graph instance khi callback về.

### Checkpointer: MemorySaver (MVP)

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph

graph = builder.compile(checkpointer=MemorySaver())
```

**MemorySaver lưu state trong RAM của container.**

| | MVP (MemorySaver) | Production (PostgresSaver) |
|-|------------------|--------------------------|
| Setup | Không cần gì thêm | Cần Postgres instance |
| Persist qua restart | Không | Có |
| Phù hợp | Demo, job hoàn thành trong phút | Multi-instance, long-running |
| Upgrade path | Chỉ thay 1 dòng checkpointer | — |

Chấp nhận được cho MVP vì: job thường hoàn thành trong 2-5 phút, container không restart giữa demo. Khi production, chỉ cần thay `MemorySaver` → `PostgresSaver`.

### Full State Machine

```
RECEIVED
    │
    ▼
INVESTIGATING ──── (read only) ──────────────────────────────────────▶ COMPLETED
    │
    │ write op detected
    ▼
ASSIGNING
    │
    ▼
WAITING_SRE ──── timeout 5m / busy ──▶ [thử SRE tiếp theo]
    │                                         │ hết SRE
    │ accept                                  ▼
    ▼                                     ESCALATED
RUNNER_EXECUTING
    │
    ├──── done, không cần lead ──────────────▶ COMPLETED
    │
    └──── done, cần lead approve (optional) ──▶ WAITING_LEAD_APPROVAL
                                                      │
                                                      ├──── approve ──▶ COMPLETED
                                                      └──── reject/timeout ──▶ ESCALATED
```

`WAITING_LEAD_APPROVAL` chỉ xảy ra khi Runner báo về `needs_lead_approval: true`. Phần lớn task sẽ đi thẳng `RUNNER_EXECUTING → COMPLETED`.

### LangGraph Graph Definition

```python
# state.py
from typing import TypedDict, Optional
from datetime import datetime

class FirstMateState(TypedDict):
    # Input
    job_id: str
    alert_message: str
    service: str
    env: str

    # Investigation
    findings: list[str]
    hypothesis: str
    write_ops: list[dict]

    # SRE assignment
    assigned_sre: Optional[str]
    assignment_attempts: list[str]   # SREs đã thử
    runner_result: Optional[str]

    # Escalation
    needs_lead_approval: bool
    assigned_lead: Optional[str]

    # Telegram context
    requester_telegram_id: str
    status: str                      # track state hiện tại

# graph.py
from langgraph.checkpoint.memory import MemorySaver

builder = StateGraph(FirstMateState)

builder.add_node("investigate",            investigate)
builder.add_node("assign_sre",             assign_sre)
builder.add_node("waiting_sre",            waiting_sre)      # interrupt()
builder.add_node("waiting_lead_approval",  waiting_lead)     # interrupt()
builder.add_node("escalated",              escalated)
builder.add_node("completed",              completed)

builder.add_conditional_edges("investigate", route_after_investigate, {
    "read_only":  "completed",
    "write_op":   "assign_sre",
})

builder.add_conditional_edges("waiting_sre", route_sre_response, {
    "accepted":    "runner_executing",
    "busy":        "assign_sre",       # thử SRE khác
    "timeout":     "assign_sre",       # thử SRE khác
    "no_more_sre": "escalated",
})

# Sau khi Runner xong: cần lead approve hay không?
builder.add_conditional_edges("runner_executing", route_after_runner, {
    "done":         "completed",              # phần lớn task
    "need_lead":    "waiting_lead_approval",  # optional
})

builder.add_conditional_edges("waiting_lead_approval", route_lead_response, {
    "approved": "completed",
    "rejected": "escalated",
    "timeout":  "escalated",
})

graph = builder.compile(
    checkpointer=MemorySaver(),
    interrupt_before=["waiting_sre", "waiting_lead_approval"]
    # waiting_lead_approval chỉ bị interrupt khi graph thực sự đi vào node đó
)
```

```python
# Telegram callback resume graph
async def telegram_callback(job_id: str, action: str):
    thread_config = {"configurable": {"thread_id": job_id}}
    graph.invoke(
        Command(resume=action),   # action = "accepted" / "busy" / "approved" / ...
        config=thread_config
    )
```

---

## Giải pháp kết nối FirstMate-Manager — Runner

### Vấn đề

Runner chạy trên máy SRE ở local network, không có public IP. FirstMate-Manager không thể chủ động connect vào Runner.

### Giải pháp: HTTP Long-Polling (GitLab Runner model)

```
Runner                          FirstMate-Manager
  │                               │
  │── POST /api/runner/poll ──────▶│  "Tôi là runner-sre-tien,
  │   { runner_id, capabilities } │   có task nào cho tôi không?"
  │                               │
  │        (FirstMate-Manager giữ request)  │  ... chờ tối đa 30s ...
  │                               │
  │◀── 200 { job } ───────────────│  "Có task: restart pod X"
  │                               │
  │── (chạy task) ────────────────│
  │                               │
  │── POST /api/runner/result ────▶│  "Xong rồi, kết quả là..."
  │                               │
  │── POST /api/runner/poll ──────▶│  "Có task mới không?"
  │        (lặp lại)              │
```

**Tại sao chọn Long-Polling:**

| | Long-Polling | WebSocket | Regular Polling |
|-|-------------|-----------|----------------|
| Độ phức tạp | Thấp | Cao | Thấp |
| Realtime | Gần như realtime | Realtime | Chậm (5-10s lag) |
| Firewall/NAT | Không cần mở port | Không cần mở port | Không cần mở port |
| Implement trong 7 ngày | Dễ | Khó | Dễ nhưng lag |

Long-polling: Runner giữ HTTP request mở, FirstMate-Manager respond ngay khi có job. Nếu không có job sau 30s, trả 204 và Runner poll lại. Đây chính xác là cách GitLab Runner hoạt động.

### Runner Registration

```python
# Runner khởi động, đăng ký với FirstMate-Manager
POST /api/runner/register
{
  "runner_id": "sre-tien-macbook",
  "sre_id": "tien@company.com",
  "capabilities": ["kubectl", "helm", "ssh"],
  "status": "online"
}
```

FirstMate-Manager lưu trạng thái runner online/offline. Khi SRE tắt máy → runner không poll nữa → FirstMate-Manager tự động mark offline sau 60s.

---

## Phân chia task 3 người — 7 ngày

### Nguyên tắc chia

API contract giữa FirstMate-Manager và Runner phải được define **ngay Day 1** để 3 người làm song song không block nhau.

---

### Người 1 — FirstMate-Manager Core

**Phụ trách:** FirstMate-Manager, LangGraph graph, K8s tools, escalation logic

| Ngày | Việc làm |
|------|---------|
| **Day 1** | Setup project FirstMate-Manager, define API contract với Runner (OpenAPI spec), `FirstMateState` + LangGraph graph skeleton với MemorySaver |
| **Day 2** | K8s read tools: `list_pods`, `get_logs`, `get_events`, `health_check` |
| **Day 3** | `investigate` node: LLM + tools + system prompt, phân loại READ vs WRITE |
| **Day 4** | `assign_sre` node: check ownership từ config, tìm runner online, escalation logic (thử SRE-1 → SRE-2 → ESCALATED) |
| **Day 5** | `waiting_lead_approval` node + `interrupt()` resume flow, nhận kết quả từ Runner |
| **Day 6** | Integration test với Runner thật, test full escalation flow |
| **Day 7** | Deploy lên GreenNode AgentBase, final test |

**Deliverable:** FirstMate-Manager tự xử lý log/health check, escalate đúng SRE, handle timeout + lead approval.

---

### Người 2 — Runner Core

**Phụ trách:** FirstMate-Runner, long-polling, tool execution, SRE approval flow

| Ngày | Việc làm |
|------|---------|
| **Day 1** | Setup project Runner, implement long-polling client theo API contract đã define |
| **Day 2** | Tool executor: nhận job từ FirstMate-Manager, parse command, chạy kubectl |
| **Day 3** | Permission classifier: auto-run READ ops, flag WRITE ops cần approve |
| **Day 4** | SRE approval flow: hiện prompt confirm trên terminal, nhận y/n, báo kết quả về FirstMate-Manager |
| **Day 5** | Runner registration + heartbeat: đăng ký với FirstMate-Manager khi start, heartbeat mỗi 30s |
| **Day 6** | Integration test với FirstMate-Manager thật, fix bugs |
| **Day 7** | Test end-to-end flow đầy đủ với môi trường thật |

**Deliverable:** Runner poll FirstMate-Manager, nhận task, tự chạy READ ops, hỏi SRE confirm WRITE ops.

---

### Người 3 — Platform & Integration

**Phụ trách:** Telegram bot, GreenNode deploy, config/permissions, integration glue

| Ngày | Việc làm |
|------|---------|
| **Day 1** | Setup Telegram bot (BotFather), webhook handler, format message template |
| **Day 2** | Telegram: nhận message từ Dev/QC, gửi về FirstMate-Manager; nhận job notification, gửi cho SRE |
| **Day 3** | Config system: `services.yaml` (service → SRE owner mapping), runner registry |
| **Day 4** | FirstMate-Manager API server: REST endpoints cho Runner poll, submit result, register |
| **Day 5** | GreenNode AgentBase: Dockerfile, deploy FirstMate-Manager lên cloud |
| **Day 6** | Integration test toàn bộ flow: Telegram → FirstMate-Manager → Runner → kết quả → Telegram |
| **Day 7** | Demo preparation, record video, submit |

**Deliverable:** Telegram bot nhận được message, FirstMate-Manager chạy trên cloud, toàn bộ flow hoạt động.

---

## API Contract (define Day 1, tất cả follow)

```
# Runner → FirstMate-Manager
POST /api/runner/register          Đăng ký runner khi start
POST /api/runner/poll              Long-poll lấy job (timeout 30s)
POST /api/runner/heartbeat         Báo alive mỗi 30s
POST /api/runner/job/{id}/result   Trả kết quả sau khi chạy xong

# FirstMate-Manager → Telegram (qua Person 3)
POST /api/notify/sre               Gửi notification + approval request cho SRE
POST /api/notify/dev               Gửi kết quả về cho Dev/QC

# Telegram → FirstMate-Manager (inbound)
POST /webhook/telegram             Nhận message từ user
POST /webhook/telegram/callback    Nhận button click (Yes/No/View)
```

---

## Config System

```yaml
# services.yaml — Person 3 maintain
services:
  payment:
    owner_sre: tien@company.com
    namespace: production
    k8s_cluster: prod-cluster
    tier: critical
    # Data sources Runner có thể query (read-only)
    redis:
      host: redis-prod.internal
      db: 2
      key_prefix: "session:*"        # chỉ cho phép GET key bắt đầu bằng prefix này
    database:
      host: pg-prod.internal
      name: payment_db
      allowed_tables:                # whitelist — chỉ SELECT các table này
        - orders
        - payments
        - refunds

  user-service:
    owner_sre: nam@company.com
    namespace: production
    k8s_cluster: prod-cluster
    tier: high
    redis:
      host: redis-prod.internal
      db: 0
      key_prefix: "user:*"
    database:
      host: pg-prod.internal
      name: user_db
      allowed_tables:
        - users
        - sessions

runners:
  sre-tien:
    sre_id: tien@company.com
    telegram_id: "@tien_sre"
    services_owned: [payment, checkout, cart]
    is_lead: false

  sre-nam:
    sre_id: nam@company.com
    telegram_id: "@nam_sre"
    services_owned: [user-service, auth, notification]
    is_lead: false

  sre-lead:
    sre_id: lead@company.com
    telegram_id: "@sre_lead"
    services_owned: []               # Lead approve tất cả
    is_lead: true
```

---

## Telegram Flow (MVP)

### Dev/QC hỏi FirstMate-Manager

```
Dev nhắn vào Telegram group hoặc bot:
  "payment service logs bị gì không?"

FirstMate-Manager reply (30s):
  ✅ FirstMate-Manager
  Service: payment | Env: production

  📋 FINDINGS
  • Pod payment-6d8f9b: Running (2/2)
  • Pod payment-7c9e1a: CrashLoopBackOff — 5 restarts
  • Last error: Connection refused to redis:6379

  💡 HYPOTHESIS
  Redis connection issue. Pod đang crash do không connect được Redis.

  🔧 SUGGESTED ACTION
  [Cần SRE approve] kubectl describe pod payment-7c9e1a
```

### FirstMate-Manager contact SRE

```
SRE-Tien nhận Telegram:
  📟 FirstMate — Task mới

  Từ: Dev-Nam
  Service: payment (bạn là owner)
  Vấn đề: Pod crashloop do Redis connection refused

  Cần chạy:
  • kubectl describe pod payment-7c9e1a -n prod
  • kubectl rollout restart deployment/payment -n prod

  [✅ Accept & Auto-run]  [👁 Accept & Follow live]  [❌ Decline]
```

### Sau khi SRE accept

```
FirstMate-Runner (máy SRE-Tien) tự chạy.

SRE nhận kết quả:
  ✅ Đã hoàn thành

  Đã chạy:
  • kubectl describe → Pod thiếu env REDIS_URL
  • kubectl rollout restart → deployment restarted, 2/2 pods Running

  Thời gian: 45 giây
  [Xem full log]
```

---

## MVP Scope 7 ngày (cụ thể)

### FirstMate-Manager tự xử lý (không cần SRE)

| Use Case | Actions |
|----------|---------|
| **Incident** | view logs, pod status/count, K8s events, health check |
| **Debug** | configmap diff (prod vs staging), deployment history, resource usage |
| **Data check** | không có — data check luôn cần Runner credentials |

### Cần SRE — qua Runner (auto-run, không cần confirm)
- Redis GET/SCAN key (read-only)
- DB SELECT query (read-only, giới hạn table whitelist)
- kubectl exec để check network/process trong pod

### Cần SRE — qua Runner (phải confirm)
- Delete / restart pod
- Scale deployment
- Update configmap / secret
- Rollout restart

### Không làm trong 7 ngày
- CMDB integration → dùng `services.yaml` tĩnh thay thế
- DB write (INSERT/UPDATE/DELETE)
- Multi-cluster
- Teams integration → Telegram đủ cho demo
- Web UI

---

## Project Structure

```
firstmate/
├── manager/                        # Person 1 + Person 3
│   ├── main.py                     # FastAPI app
│   ├── graph.py                    # LangGraph StateGraph
│   ├── state.py                    # FirstMateState
│   ├── nodes/
│   │   ├── investigate.py          # LLM + tools, giữ session context
│   │   ├── assign_sre.py           # Tìm SRE phù hợp, escalation logic
│   │   ├── waiting_sre.py          # interrupt() — chờ Telegram callback
│   │   ├── waiting_lead.py         # interrupt() — chờ Lead approve (optional)
│   │   └── reporter.py             # Format kết quả trả về Dev/QC
│   ├── tools/
│   │   ├── k8s_tool.py             # read-only: logs, pods, events, configmap
│   │   └── intent_classifier.py    # phân loại: incident / debug / data_check
│   ├── api/
│   │   ├── runner_api.py           # /runner/poll, /runner/result
│   │   └── telegram_webhook.py     # /webhook/telegram, /webhook/telegram/callback
│   ├── services/
│   │   ├── job_queue.py            # Tạo, assign, track jobs
│   │   ├── runner_registry.py      # Danh sách runner online/offline
│   │   ├── session_store.py        # Lưu conversation context per Dev/QC user
│   │   └── telegram_notify.py      # Gửi message cho SRE/Dev
│   └── config/
│       └── services.yaml           # Service → SRE owner + DB/Redis config
│
├── runner/                         # Person 2
│   ├── main.py                     # Entry point
│   ├── poller.py                   # Long-polling client
│   ├── executor.py                 # Route job đến đúng tool
│   ├── approval.py                 # Confirm gate — hỏi SRE
│   └── tools/
│       ├── k8s_tool.py             # Full kubectl (credentials SRE)
│       ├── redis_tool.py           # Redis GET/SCAN (read-only)
│       └── db_tool.py              # SELECT query (read-only, whitelist tables)
│
└── shared/
    └── models.py                   # Job, RunnerInfo, TaskResult, JobType schemas
```

---

## Định nghĩa "Done" sau 7 ngày

**3 scenario demo — cover cả 3 use case:**

**Scenario A — Incident:**
1. Dev nhắn: *"pod payment bị gì vậy?"*
2. FirstMate-Manager tự check logs + events → reply: OOMKilled, 5 restarts
3. Dev nhắn: *"restart giúp"* → FirstMate-Manager assign SRE-Tien
4. SRE nhấn Accept → Runner restart → FirstMate-Manager báo Dev xong

**Scenario B — Debug session:**
1. Dev nhắn: *"checkout đang lỗi 500, check giúp"*
2. FirstMate-Manager check logs → thấy DB timeout → reply kèm log line
3. Dev hỏi tiếp: *"config timeout bao nhiêu?"* → FirstMate-Manager check configmap
4. Dev hỏi: *"staging config thế nào?"* → FirstMate-Manager diff prod vs staging
5. Dev: *"update timeout lên 10000"* → WRITE op → assign SRE

**Scenario C — Data check:**
1. Dev nhắn: *"user 123 còn session không?"*
2. FirstMate-Manager assign Runner check Redis (auto-run, không cần confirm)
3. Runner: `GET session:user:123` → trả về TTL + value
4. FirstMate-Manager reply Dev: "Còn 30 phút, có 3 items trong cart"

**Tổng thời gian mỗi scenario:** dưới 2 phút.

---

## Tech Stack

| Component | Stack |
|-----------|-------|
| FirstMate-Manager — agent | Python, LangGraph, langchain-anthropic |
| FirstMate-Manager — server | FastAPI (webhook + runner API) |
| FirstMate-Manager — checkpointer | `MemorySaver` (MVP) → `PostgresSaver` (production) |
| Runner | Pure Python, httpx (long-polling), subprocess (kubectl) |
| Telegram bot | python-telegram-bot |
| Deploy FirstMate-Manager | GreenNode AgentBase (Docker) |
| Run Runner | Local machine (plain Python, không cần framework) |
| Config | YAML (`services.yaml`) |
| Shared schemas | Pydantic (`shared/models.py`) |

```bash
# FirstMate-Manager dependencies
pip install langgraph langchain-anthropic fastapi uvicorn python-telegram-bot pyyaml httpx pydantic

# Runner dependencies (nhẹ hơn nhiều)
pip install httpx pydantic pyyaml
```

### Upgrade path checkpointer (chỉ thay 1 dòng)

```python
# MVP
from langgraph.checkpoint.memory import MemorySaver
checkpointer = MemorySaver()

# Production — khi cần persist qua restart
from langgraph.checkpoint.postgres import PostgresSaver
checkpointer = PostgresSaver.from_conn_string(os.getenv("PG_CONN"))

graph = builder.compile(checkpointer=checkpointer)  # không đổi gì khác
```
