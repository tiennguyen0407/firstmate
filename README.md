# FirstMate — AI-Powered DevOps Collaboration Platform

> Kết nối Dev/QC — AI Manager — SRE thành một vòng xử lý sự cố thống nhất,  
> nơi con người luôn giữ quyền kiểm soát cuối cùng.

→ Xem [USECASE.md](./USECASE.md) để biết chi tiết các tình huống sử dụng.

---

## Vấn đề

SRE là cổ chai duy nhất — kể cả những tác vụ read-only đơn giản như đọc log hay check pod status. Dev/QC phải chờ SRE available, SRE phải SSH thủ công, trung bình mất 30–60 phút mỗi sự cố.

## Giải pháp

```
Dev/QC chat với FirstMate qua Telegram
    ↓
Manager (AI) phân tích yêu cầu, tra cứu Global KB
    ↓
Assign job cho Runner đang online trên máy SRE
    ↓
Runner chạy Claude Code: kubectl / log / redis / db — tự động nếu read-only
    ↓
Write op → Runner yêu cầu SRE approve trong terminal
    ↓
Kết quả gửi về Manager → báo lại Dev qua Telegram
```

**Manager không có quyền truy cập trực tiếp vào hệ thống.** Mọi lệnh thực tế (kubectl, log, DB) đều chạy trên máy SRE qua Runner.

---

## Kiến trúc

```
┌─────────────────────────────────────────────────────┐
│                    Dev / QC                          │
│              chat qua Telegram                       │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│        FirstMate-Manager (AgentBase Runtime)         │
│                                                     │
│  • Phân loại yêu cầu (LLM)                         │
│  • Tra cứu Global KB: service→namespace, gateway    │
│  • Trả lời trực tiếp nếu đủ thông tin từ KB/memory │
│  • Nếu cần live data → build job, assign Runner     │
│  • Nhận kết quả từ Runner → báo lại Dev            │
│                                                     │
│  AgentBase Memory: conversation log + Global KB     │
└──────────────────────┬──────────────────────────────┘
                       │ job description (Telegram)
                       ▼
┌─────────────────────────────────────────────────────┐
│         FirstMate-Runner (máy SRE, local)            │
│                                                     │
│  • Claude Code thực thi: kubectl, log, redis, db   │
│  • Read-only job → tự chạy, không cần approve      │
│  • Write op → Pre-tool hook alert Telegram          │
│              SRE approve trong terminal             │
└──────────────────────┬──────────────────────────────┘
                       │ kết quả → POST /webhook/job-complete
                       ▼
┌─────────────────────────────────────────────────────┐
│     firstmate-memory (Memory Consolidator Agent)     │
│                                                     │
│  • Đọc conversation events sau mỗi job             │
│  • LLM trích xuất: service→namespace, gateway map, │
│    known issues, environment notes                  │
│  • Persist Global KB → AgentBase Memory            │
└─────────────────────────────────────────────────────┘
```

---

## Phân chia trách nhiệm

| Thành phần | Có thể làm | Không thể làm |
|------------|-----------|--------------|
| **Manager** | Phân loại intent, tra KB, assign job, tổng hợp kết quả, trả lời từ memory | Chạy kubectl, đọc log, truy cập DB/Redis |
| **Runner** | Chạy mọi lệnh trên máy SRE (kubectl, log, redis, db) — read và write | — |
| **SRE** | Approve write ops qua Telegram button + terminal confirm | — |

---

## Tech Stack

| Layer | Công nghệ |
|-------|-----------|
| AI orchestration | **LangGraph** — StateGraph với `interrupt()` cho human-in-the-loop |
| API | **FastAPI** — Manager + Memory Agent |
| Messaging | **python-telegram-bot** — Dev/QC và SRE |
| Runner | **Claude Code** — chạy trên máy SRE với kubectl/redis/db/log tools |
| LLM | **GreenNode MaaS** — `qwen/qwen3-5-27b` |
| Memory | **AgentBase Memory** — conversation log + Global KB |

---

## AgentBase Integration

| Service | Cách dùng |
|---------|-----------|
| **AgentBase Memory** | Lưu conversation events theo actor/session; Global KB persist qua restart |
| **AgentBase Runtime** | Deploy Manager + firstmate-memory dưới dạng scalable endpoint |
| **Memory Consolidator** | `firstmate-memory` — đọc events sau job, LLM build Global KB |
| **GreenNode LLM (MaaS)** | `qwen/qwen3-5-27b` cho phân loại intent + memory consolidation |
| **IAM Credentials** | `GREENNODE_CLIENT_ID/SECRET` inject tự động vào mọi runtime |

---

## Cài đặt

### Yêu cầu

- Docker (build Manager + Memory Agent)
- GreenNode AgentBase account + IAM credentials
- Telegram Bot token (từ @BotFather)
- kubectl config trên máy SRE

### 1. Cấu hình

```bash
# Environment
cp .env.example .env.local
# Điền: GREENNODE_API_KEY, TELEGRAM_BOT_TOKEN
# AGENTBASE_MEMORY_ID và MEMORY_AGENT_URL đã có sẵn trong .env.example

# Manager config (team, SRE IDs, service mapping)
cp manager/config/services.yaml.example manager/config/services.yaml
cp manager/config/knowledge_base.yaml.example manager/config/knowledge_base.yaml
```

### 2. Deploy Manager lên AgentBase Runtime

```bash
docker build --platform linux/amd64 -t <registry>/firstmate-manager:latest .
docker push <registry>/firstmate-manager:latest
# Tạo runtime trên AgentBase Portal với env-file .env.local
```

### 3. Deploy firstmate-memory (Memory Consolidator)

```bash
docker build --platform linux/amd64 -t <registry>/firstmate-memory:latest memory_agent/
docker push <registry>/firstmate-memory:latest
# Env vars cần thiết (.env.memory):
#   GREENNODE_API_KEY=<key>
#   AGENTBASE_MEMORY_ID=<memory-id>
```

Sau khi deploy, cập nhật `MEMORY_AGENT_URL` trong `.env.local` với endpoint URL vừa tạo.

### 4. Chạy Runner trên máy SRE

```bash
cp runner/.env.example runner/.env.local
# Điền: MANAGER_URL, RUNNER_ID, SRE_ID, SRE_TELEGRAM_ID, TELEGRAM_BOT_TOKEN

bash start_runner.sh
```

### 5. Kiểm tra Memory & Global KB

```bash
# Global KB + events của một user (auto-load .env.local)
bash check_memory.sh <telegram_chat_id>

# Tất cả users
bash check_memory.sh all
```

Output: Global KB (service→namespace, gateway→IP+log), per-user facts, toàn bộ AgentBase events.

---

## Cấu trúc project

```
firstmate/
├── manager/                  # FirstMate-Manager (AgentBase Runtime)
│   ├── api/
│   │   ├── telegram_webhook.py   # Nhận message, phân loại, assign job, nhận kết quả
│   │   ├── runner_api.py         # Runner register / heartbeat / job result
│   │   └── memory_api.py         # Memory debug endpoints
│   ├── nodes/                # LangGraph nodes (dự phòng, hiện chưa dùng chính)
│   │   ├── investigate.py        # (unused) LLM classify node
│   │   ├── assign_sre.py         # Tìm SRE có Runner online
│   │   ├── waiting_nodes.py      # interrupt() — chờ SRE / Runner / Lead
│   │   └── reporter.py           # Tạo final report gửi Telegram
│   ├── services/
│   │   ├── memory_save.py        # Lưu conversation events → AgentBase Memory
│   │   ├── runner_registry.py    # Registry + heartbeat tracking
│   │   └── config_loader.py      # Load services.yaml
│   ├── config/
│   │   ├── services.yaml.example
│   │   └── knowledge_base.yaml.example
│   └── graph.py              # LangGraph StateGraph definition
├── runner/                   # FirstMate-Runner (local trên máy SRE)
│   ├── executor.py               # Chạy job với Claude Code
│   ├── approval.py               # Terminal prompt cho SRE approve write ops
│   ├── hooks/notify_sre.sh       # Pre-tool hook — Telegram alert trước write op
│   └── rules/commands.yaml       # Whitelist / denylist commands
├── memory_agent/             # firstmate-memory (AgentBase Runtime)
│   ├── main.py                   # FastAPI — /consolidate, /kb/global, /kb/{chat_id}
│   └── Dockerfile
├── check_memory.sh           # CLI: xem Global KB + AgentBase events
├── .env.example              # Template env vars (có sẵn MEMORY_AGENT_URL, AGENTBASE_MEMORY_ID)
└── .env.memory               # Env riêng cho firstmate-memory runtime
```

---

## Security model

| Layer | Cơ chế |
|-------|--------|
| Read ops | Runner tự thực thi — không cần SRE approve |
| Write ops | Phải qua SRE approve (Telegram button + terminal confirm) |
| Write ops rủi ro cao | Thêm tầng Lead approval trước khi Runner thực thi |
| Không có SRE online | Escalation — Manager báo không có Runner available |
| Hook kiểm soát | Pre-tool hook trong Claude Code chặn lệnh nguy hiểm, alert Telegram |
| Runtime credentials | IAM service account inject tự động, không hardcode trong image |
