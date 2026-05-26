# Hierarchical AI Router Server

A FastAPI + Ollama server where a **main model routes prompts** through a layered
chain of specialist AI models — each doing one job well.

---

## Architecture

```
User Prompt
    │
    ▼
┌─────────────────────────────────┐
│  Main Router Model              │  ← classifies domain
│  (qwen2.5-coder:7b)             │
└────────────┬────────────────────┘
             │
     ┌───────┴──────────┬──────────────┬──────────────┐
     ▼                  ▼              ▼               ▼
  coding            generation      analysis      conversation
     │                  │
     ▼                  ▼
┌──────────┐      ┌──────────┐
│ Coding   │      │ Gen Sub- │   ← sub-router classifies further
│ Sub-     │      │ Router   │
│ Router   │      └────┬─────┘
└────┬─────┘           │
     │           ┌─────┴──────────┬─────────────┐
     │           ▼                ▼              ▼
     │         text          code_gen         creative
     │
  ┌──┴──────────────┬─────────────┬──────────┐
  ▼                 ▼             ▼          ▼
nextjs            nuxtjs        react      python
  │
  ▼
Worker Agent streams response via WebSocket
```

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Make sure Ollama is running
```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

### 3. Start the server
```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Test it
```bash
# WebSocket stream test
python test_client.py "create a next.js app with tailwind"
python test_client.py "generate 10 sentences about computers"
python test_client.py "write a python flask REST API"

# REST API
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "create a next.js project"}'

# Just routing (no generation)
curl -X POST http://localhost:8000/api/route \
  -H "Content-Type: application/json" \
  -d '{"prompt": "write a poem about the ocean"}'
```

---

## WebSocket Protocol

Connect to: `ws://localhost:8000/ws/chat`

**Send:**
```json
{"prompt": "create a next.js app with tailwind and typescript"}
```

**Receive (sequence):**
```json
{"type": "routing", "data": {
    "domain": "coding",
    "domain_reason": "User wants to scaffold a Next.js project.",
    "subdomain": "nextjs",
    "subdomain_reason": "Explicitly mentions Next.js.",
    "agent": "nextjs"
}}

{"type": "token", "data": "Here"}
{"type": "token", "data": " are"}
{"type": "token", "data": " the steps..."}

{"type": "done", "data": ""}
```

---

## REST Endpoints

| Method | Path          | Description                        |
|--------|---------------|------------------------------------|
| GET    | `/`           | Server info + endpoint list        |
| GET    | `/api/health` | Ollama connectivity check          |
| GET    | `/api/agents` | List all domains and agents        |
| POST   | `/api/route`  | Classify prompt, no generation     |
| POST   | `/api/chat`   | Full chat (non-streaming)          |
| WS     | `/ws/chat`    | Streaming chat via WebSocket       |
| GET    | `/docs`       | Swagger UI                         |

---

## Adding New Agents

1. Add a new subdomain to the relevant `Enum` in `server.py`
2. Add routing examples to the sub-router system prompt
3. Add an agent system prompt to `AGENT_SYSTEMS`
4. Map it in `resolve_agent_key()`

Example — adding a `vuejs` agent:
```python
# 1. Add to enum
class CodingSubDomain(str, Enum):
    VUEJS = "vuejs"   # ← new

# 2. Add to _CODING_ROUTER_SYSTEM examples:
#   "build a vue 3 component" → {"subdomain": "vuejs", ...}

# 3. Add system prompt
AGENT_SYSTEMS["vuejs"] = """You are an expert Vue 3 developer..."""

# resolve_agent_key() already handles coding subdomains automatically
```

---

## Changing the Model

Edit these constants at the top of `server.py`:

```python
ROUTER_MODEL = "qwen2.5-coder:7b"   # for classification (fast, accurate)
WORKER_MODEL = "qwen2.5-coder:7b"   # for generation (can be larger)
```

You can also use different models per agent by modifying `ollama_stream()` calls inside the WebSocket handler.
