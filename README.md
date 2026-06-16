# FirstMate — AI-Powered DevOps Collaboration Platform

> Kết nối Dev/QC — AI Manager — SRE thành một vòng xử lý sự cố thống nhất,  
> nơi con người luôn giữ quyền kiểm soát cuối cùng.

---

## Vấn đề

```
Dev/QC phát hiện lỗi
    ↓
Ping SRE → đợi SRE available → SRE SSH vào server, mò logs thủ công
    ↓
Trung bình: 30–60 phút. SRE bị interrupt liên tục cho cả những việc chỉ cần đọc log.
```

SRE là cổ chai duy nhất — kể cả những tác vụ read-only đơn giản.

## Giải pháp

```
Dev/QC chat với FirstMate qua Telegram
    ↓
FirstMate-Manager (AI) tự điều tra: xem log, pod status, K8s events
    ↓
Nếu cần write op → tìm đúng SRE có Runner online, gửi job qua Telegram
    ↓
Runner trên máy SRE tự chạy Claude Code, hỏi approve khi cần
    ↓
Trung bình: 5–10 phút. SRE chỉ approve — không cần manual.
```

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
│           FirstMate-Manager (AgentBase Runtime)      │
│                                                     │
│  LangGraph StateGraph:                              │
│  investigate → assign_sre → waiting_sre             │
│             → waiting_runner → waiting_lead         │
│             → report_result / escalated             │
│                                                     │
│  AgentBase Memory (checkpointer + events)           │
└──────────────────────┬──────────────────────────────┘
                       │ job assignment (Telegram)
                       ▼
┌─────────────────────────────────────────────────────┐
│         FirstMate-Runner (máy SRE, local)            │
│                                                     │
│  Claude Code + kubectl/redis/db tools               │
│  Pre-tool hook → Telegram alert trước write ops     │
│  SRE approve trong terminal                         │
└──────────────────────┬──────────────────────────────┘
                       │ summary.md → POST /webhook/job-complete
                       ▼
┌─────────────────────────────────────────────────────┐
│        Memory Consolidator Agent (AgentBase Runtime) │
│                                                     │
│  LLM trích xuất structured facts từ conversation    │
│  → service→namespace map, known issues, env notes   │
│  → persist vào AgentBase Memory (global KB)         │
└─────────────────────────────────────────────────────┘
```

---

## Use Cases

### 1. Incident Response
Pod crash, service down, disk đầy — cần action nhanh.

```
Dev: "payment service đang 500, check giúp"
FirstMate: điều tra log → tìm OOMKilled → đề xuất increase memory limit
SRE approves → Runner chạy kubectl patch → báo lại Dev trong 5 phút
```

### 2. Debug Session (multi-turn)
Dev đang debug, cần hỏi nhiều câu liên tiếp — FirstMate giữ context xuyên suốt.

```
Dev: "service payment đang lỗi 500"
→ FirstMate xem log
Dev: "check config timeout bao nhiêu"
→ FirstMate đọc configmap (nhớ service từ câu trước)
Dev: "so với staging thì sao"
→ FirstMate compare cả 2 namespace
```

### 3. Data Check (read-only)
Verify dữ liệu không cần action — FirstMate tự xử lý không cần SRE.

```
Dev: "key session:user:123 trong Redis có value gì"
→ FirstMate dùng Runner read-only, trả kết quả trực tiếp
```

---

## AgentBase Integration

| Service | Cách dùng |
|---------|-----------|
| **AgentBase Memory** | LangGraph checkpointer — lưu state graph qua restart; conversation events per actor/session |
| **Memory Consolidator** | Agent riêng: đọc events, LLM trích xuất facts → global service→namespace KB |
| **AgentBase Runtime** | Deploy Manager + Memory Agent dưới dạng runtime endpoint |
| **GreenNode LLM (MaaS)** | `qwen/qwen3-5-27b` cho investigation + memory consolidation |
| **IAM Credentials** | `GREENNODE_CLIENT_ID/SECRET` inject tự động vào runtime |

---

## Tech Stack

- **LangGraph** — StateGraph với `interrupt()` cho human-in-the-loop approval
- **FastAPI** — Manager API + Memory Agent API
- **python-telegram-bot** — giao tiếp với Dev/QC và SRE
- **kubectl** — K8s read-only tools (list_pods, get_logs, events, describe)
- **Claude Code** — Runner chạy trên máy SRE, có pre-tool hook kiểm soát write ops

---

## Cài đặt

### Yêu cầu
- Docker
- GreenNode AgentBase account + IAM credentials
- Telegram Bot token
- kubectl config (trên máy SRE)

### 1. Clone & cấu hình

```bash
git clone https://github.com/<your-repo>/firstmate.git
cd firstmate

# Manager config
cp manager/config/services.yaml.example manager/config/services.yaml
cp manager/config/knowledge_base.yaml.example manager/config/knowledge_base.yaml
# Điền thông tin team, SRE IDs, Telegram IDs, service names

# Environment
cp .env.example .env.local
# Điền GREENNODE_API_KEY, TELEGRAM_BOT_TOKEN, AGENTBASE_MEMORY_ID
```

### 2. Deploy Manager lên AgentBase

```bash
cp deploy.config.example deploy.config
# Điền REGISTRY, REPO, IMAGE_NAME, RUNTIME_ID, FLAVOR

bash deploy.sh
```

### 3. Chạy Runner trên máy SRE

```bash
cp runner/.env.example runner/.env.local
# Điền MANAGER_URL, RUNNER_ID, SRE_ID, SRE_TELEGRAM_ID, TELEGRAM_BOT_TOKEN

bash start_runner.sh
```

### 4. Deploy Memory Agent (optional, recommended)

```bash
# Build và push memory_agent lên AgentBase Runtime
# Dockerfile.memory ở root, hoặc memory_agent/Dockerfile
docker build -f Dockerfile.memory -t <registry>/firstmate-memory .
# Deploy tương tự Manager
```

---

## Cấu trúc project

```
firstmate/
├── manager/                  # FirstMate-Manager (AgentBase Runtime)
│   ├── api/
│   │   ├── telegram_webhook.py   # Main webhook — nhận message + callback
│   │   ├── runner_api.py         # Runner register/heartbeat/result
│   │   └── memory_api.py         # Memory debug endpoints
│   ├── nodes/                # LangGraph nodes
│   │   ├── investigate.py        # LLM + K8s tools, phân tích yêu cầu
│   │   ├── assign_sre.py         # Tìm SRE có Runner online
│   │   ├── waiting_nodes.py      # interrupt() — chờ SRE/Runner/Lead
│   │   └── reporter.py           # Tạo final report
│   ├── services/
│   │   ├── memory_save.py        # Lưu conversation events vào AgentBase Memory
│   │   ├── runner_registry.py    # Registry + heartbeat runners
│   │   └── config_loader.py      # Load services.yaml
│   ├── tools/
│   │   └── k8s_tool.py           # kubectl read-only tools (LangChain)
│   ├── config/
│   │   ├── services.yaml.example
│   │   └── knowledge_base.yaml.example
│   └── graph.py              # Build LangGraph StateGraph
├── runner/                   # FirstMate-Runner (chạy local trên máy SRE)
│   ├── terminal.py               # Mở terminal mới với Claude Code
│   ├── executor.py               # Chạy commands, ask_confirm write ops
│   ├── approval.py               # Terminal prompt cho SRE approve
│   ├── hooks/notify_sre.sh       # Pre-tool hook gửi Telegram alert
│   └── rules/commands.yaml       # Whitelist/denylist commands
├── memory_agent/             # Memory Consolidator Agent (AgentBase Runtime)
│   └── main.py                   # FastAPI — consolidate events → structured KB
├── shared/
│   └── models.py             # Pydantic models dùng chung
├── Dockerfile                # Manager
├── Dockerfile.memory         # Memory Agent
└── deploy.sh                 # Deploy script (AgentBase)
```

---

## Security model

- **Read ops**: Manager tự chạy — không cần SRE approve
- **Write ops**: Phải qua SRE approve (Telegram button + terminal confirm)
- **Lead approval**: Write ops rủi ro cao → thêm tầng approve từ SRE Lead
- **Escalation**: Không có SRE online → báo cần xử lý thủ công
- **Hook kiểm soát**: Pre-tool hook trong Claude Code chặn lệnh nguy hiểm, alert Telegram trước mọi write op

---

## Demo

Xem video demo: [link video]

Thử agent (nếu có endpoint public): [link endpoint]
