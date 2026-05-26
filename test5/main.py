"""
AI Orchestrator Server — FastAPI + WebSocket + Ollama
═══════════════════════════════════════════════════════
Architecture:
  User prompt
      │
      ▼
  OrchestratorModel   ← decides category (coding / generate / ...)
      │
      ├─ coding  →  CodingRouter  ← decides framework (nextjs / nuxt / vue / ...)
      │                  │
      │                  ├─ nextjs  →  NextjsCreatorAgent
      │                  ├─ nuxt   →  NuxtCreatorAgent
      │                  └─ vue    →  VueCreatorAgent
      │
      ├─ generate →  GenerateAgent   (text generation)
      ├─ question →  QuestionAgent   (Q&A, factual)
      └─ ...more categories you add later

All streaming goes back to the client via WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

# ─── Optional: pull in the creator modules if they exist alongside this file ──
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    from experiment.test5.tasks.nextjs_creator import start as nextjs_start, _terminal_cb as _njs_cb
    HAS_NEXTJS = True
except ImportError:
    HAS_NEXTJS = False

try:
    from experiment.test5.tasks.vue_nuxt_creator import start as vue_nuxt_start, _terminal_cb as _vn_cb
    HAS_VUE_NUXT = True
except ImportError:
    HAS_VUE_NUXT = False

# ─────────────────────────────────────────────────────────────────────────────
# Config — edit these to match your Ollama setup
# ─────────────────────────────────────────────────────────────────────────────
OLLAMA_URL      = os.getenv("OLLAMA_URL", "http://localhost:11434")
ORCHESTRATOR_MODEL = os.getenv("ORCHESTRATOR_MODEL", "qwen2.5-coder:7b")
DEFAULT_MODEL      = os.getenv("DEFAULT_MODEL",      "qwen2.5-coder:7b")

# Category → (routing_model, default_worker_model)
CATEGORY_CONFIG: Dict[str, Dict[str, str]] = {
    "coding":   {"router_model": ORCHESTRATOR_MODEL, "worker_model": DEFAULT_MODEL},
    "generate": {"router_model": ORCHESTRATOR_MODEL, "worker_model": DEFAULT_MODEL},
    "question": {"router_model": ORCHESTRATOR_MODEL, "worker_model": DEFAULT_MODEL},
    "image":    {"router_model": ORCHESTRATOR_MODEL, "worker_model": DEFAULT_MODEL},
    "data":     {"router_model": ORCHESTRATOR_MODEL, "worker_model": DEFAULT_MODEL},
    "unknown":  {"router_model": ORCHESTRATOR_MODEL, "worker_model": DEFAULT_MODEL},
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ai_server")

# ─────────────────────────────────────────────────────────────────────────────
# Message / event dataclasses
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class WsEvent:
    """Everything the server sends to the client."""
    event:      str                    # "thinking" | "routing" | "stream" | "done" | "error"
    session_id: str        = ""
    data:       Any        = None
    step:       str        = ""        # which pipeline step fired this
    ts:         float      = field(default_factory=time.time)

    def to_json(self) -> str:
        d = asdict(self)
        d["ts"] = round(d["ts"], 3)
        return json.dumps(d, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Ollama streaming helper
# ─────────────────────────────────────────────────────────────────────────────
async def ollama_chat_stream(
    model:    str,
    messages: List[Dict],
    *,
    base_url: str = OLLAMA_URL,
    timeout:  int = 120,
) -> AsyncIterator[str]:
    """Yield text chunks from Ollama /api/chat (streaming=true)."""
    url = f"{base_url}/api/chat"
    payload = {
        "model":    model,
        "messages": messages,
        "stream":   True,
        "options":  {"temperature": 0.3},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for raw_line in resp.aiter_lines():
                if not raw_line.strip():
                    continue
                try:
                    chunk = json.loads(raw_line)
                    text  = chunk.get("message", {}).get("content", "")
                    if text:
                        yield text
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue


async def ollama_chat_full(
    model:    str,
    messages: List[Dict],
    *,
    base_url: str = OLLAMA_URL,
    timeout:  int = 60,
) -> str:
    """Return the full response text (non-streaming, for routing decisions)."""
    result = ""
    async for chunk in ollama_chat_stream(model, messages, base_url=base_url, timeout=timeout):
        result += chunk
    return result.strip()


# ─────────────────────────────────────────────────────────────────────────────
# ════════════════  ORCHESTRATOR (MAIN MODEL)  ════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
ORCHESTRATOR_SYSTEM = """You are a task-routing AI.
Your ONLY job: classify the user prompt into exactly ONE category.

Categories:
  coding   — creating, building, scaffolding, or fixing code / projects / apps
  generate — writing text: articles, stories, essays, sentences, summaries, emails
  question — answering factual questions, explanations, definitions, research
  image    — anything about images, drawing, visual generation descriptions
  data     — data analysis, CSV, SQL, spreadsheets, charts, graphs
  unknown  — does not fit any above

Respond ONLY with a valid JSON object — no markdown, no explanation:
{"category": "<one of the above>", "confidence": 0.95, "reason": "short reason"}
"""

async def orchestrate(prompt: str) -> Dict[str, Any]:
    """Ask the orchestrator model to classify the prompt."""
    messages = [
        {"role": "system",  "content": ORCHESTRATOR_SYSTEM},
        {"role": "user",    "content": prompt},
    ]
    raw = await ollama_chat_full(ORCHESTRATOR_MODEL, messages)
    # strip markdown fences if model misbehaves
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        result = json.loads(raw)
        result.setdefault("category",   "unknown")
        result.setdefault("confidence", 0.0)
        result.setdefault("reason",     "")
        return result
    except Exception:
        return {"category": "unknown", "confidence": 0.0, "reason": raw[:200]}


# ─────────────────────────────────────────────────────────────────────────────
# ════════════════  CODING ROUTER  ════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
CODING_ROUTER_SYSTEM = """You are a coding-project-type classifier.
Classify the user prompt into one of these project types:

  nextjs   — Next.js project
  nuxt     — Nuxt.js / Nuxt3 project
  vue      — Vue.js (without Nuxt)
  react    — React (without Next.js)
  python   — Python script / FastAPI / Django / Flask
  node     — Node.js / Express backend
  general  — generic coding task (not a specific framework scaffold)

Respond ONLY with valid JSON:
{"framework": "<one of the above>", "confidence": 0.9, "reason": "short reason"}
"""

async def route_coding(prompt: str) -> Dict[str, Any]:
    messages = [
        {"role": "system", "content": CODING_ROUTER_SYSTEM},
        {"role": "user",   "content": prompt},
    ]
    raw = await ollama_chat_full(ORCHESTRATOR_MODEL, messages)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        result = json.loads(raw)
        result.setdefault("framework",  "general")
        result.setdefault("confidence", 0.0)
        result.setdefault("reason",     "")
        return result
    except Exception:
        return {"framework": "general", "confidence": 0.0, "reason": raw[:200]}


# ─────────────────────────────────────────────────────────────────────────────
# ════════════════  WORKER AGENTS  ════════════════════════════════════════════
# Each agent is an async generator that yields text chunks.
# ─────────────────────────────────────────────────────────────────────────────

async def agent_generate(prompt: str) -> AsyncIterator[str]:
    """General text-generation agent."""
    system = (
        "You are a skilled writer. Fulfil the user's generation request "
        "accurately, creatively, and in the right format."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


async def agent_question(prompt: str) -> AsyncIterator[str]:
    """Q&A / factual / explanation agent."""
    system = (
        "You are a knowledgeable assistant. Answer the user's question "
        "clearly, concisely, and accurately."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


async def agent_data(prompt: str) -> AsyncIterator[str]:
    """Data analysis agent."""
    system = (
        "You are a data-analysis expert. Help the user with data tasks, "
        "SQL, CSV, charts, or analysis. Provide code when needed."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


async def agent_image(prompt: str) -> AsyncIterator[str]:
    """Image/visual generation description agent."""
    system = (
        "You are a visual-design assistant. Help with image-related prompts, "
        "descriptions, or visual generation instructions."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


async def agent_coding_general(prompt: str) -> AsyncIterator[str]:
    """General coding agent (non-framework-specific)."""
    system = (
        "You are an expert software engineer. Help the user write, debug, "
        "explain, or improve code. Provide clear, working solutions."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


async def agent_nextjs(prompt: str, send: Callable) -> AsyncIterator[str]:
    """
    Next.js creator agent.
    If the nextjs_creator module is present, runs the full agentic scaffold.
    Otherwise falls back to a coding-advice agent.
    """
    if HAS_NEXTJS:
        # The creator uses a sync callback; bridge it to WebSocket via a queue
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _cb(text="", color="white", msg_type="normal", data=None):
            loop.call_soon_threadsafe(q.put_nowait, {"text": text, "msg_type": msg_type, "data": data or {}})

        async def _run_creator():
            await asyncio.to_thread(nextjs_start, prompt, _cb, {"type": "init", "data": {}})
            await q.put(None)  # sentinel

        task = asyncio.create_task(_run_creator())
        while True:
            item = await q.get()
            if item is None:
                break
            yield f"[{item['msg_type'].upper()}] {item['text']}\n"
        await task
    else:
        # Fallback: just give Next.js scaffold advice
        system = (
            "You are a Next.js expert. Help the user scaffold, build, or fix "
            "their Next.js project. Give step-by-step CLI commands and explanations."
        )
        messages = [{"role": "system", "content": system},
                    {"role": "user",   "content": prompt}]
        async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
            yield chunk


async def agent_vue_nuxt(prompt: str, framework: str, send: Callable) -> AsyncIterator[str]:
    """
    Vue / Nuxt creator agent.
    Uses the vue_nuxt_creator module if present, otherwise fallback.
    """
    if HAS_VUE_NUXT:
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _cb(text="", color="white", msg_type="normal", data=None):
            loop.call_soon_threadsafe(q.put_nowait, {"text": text, "msg_type": msg_type, "data": data or {}})

        async def _run_creator():
            await asyncio.to_thread(vue_nuxt_start, prompt, _cb, {"type": "init", "data": {}})
            await q.put(None)

        task = asyncio.create_task(_run_creator())
        while True:
            item = await q.get()
            if item is None:
                break
            yield f"[{item['msg_type'].upper()}] {item['text']}\n"
        await task
    else:
        system = (
            f"You are a {framework.capitalize()} expert. Help the user scaffold, "
            f"build, or fix their {framework.capitalize()} project."
        )
        messages = [{"role": "system", "content": system},
                    {"role": "user",   "content": prompt}]
        async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
            yield chunk


async def agent_python(prompt: str) -> AsyncIterator[str]:
    system = (
        "You are a Python expert. Help the user build Python scripts, "
        "FastAPI/Django/Flask apps, or debug Python code."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


async def agent_node(prompt: str) -> AsyncIterator[str]:
    system = (
        "You are a Node.js expert. Help the user build Express APIs, "
        "Node scripts, or debug backend JavaScript."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


async def agent_react(prompt: str) -> AsyncIterator[str]:
    system = (
        "You are a React expert. Help the user build React components, "
        "apps, hooks, or fix React issues."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


async def agent_unknown(prompt: str) -> AsyncIterator[str]:
    system = "You are a helpful AI assistant. Answer the user as best you can."
    messages = [{"role": "system", "content": system},
                {"role": "user",   "content": prompt}]
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, messages):
        yield chunk


# ─────────────────────────────────────────────────────────────────────────────
# ════════════════  PIPELINE  ═════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
async def run_pipeline(
    prompt:     str,
    session_id: str,
    send:       Callable,           # async fn(WsEvent)
):
    """
    Full routing pipeline:
      1. Orchestrator → category
      2. Category router → sub-type (e.g. coding → framework)
      3. Worker agent → streams response
    """

    async def emit(event: str, data: Any, step: str = ""):
        await send(WsEvent(event=event, session_id=session_id, data=data, step=step))

    # ── Step 1: Orchestrate ───────────────────────────────────────────────────
    await emit("thinking", {"message": "Analysing your request…"}, "orchestrator")
    try:
        orch = await orchestrate(prompt)
    except Exception as e:
        await emit("error", {"message": f"Orchestrator error: {e}"}, "orchestrator")
        return

    category = orch.get("category", "unknown")
    await emit("routing", {
        "level":    "orchestrator",
        "decision": category,
        "reason":   orch.get("reason", ""),
        "confidence": orch.get("confidence", 0),
    }, "orchestrator")

    # ── Step 2: Sub-routing for coding ────────────────────────────────────────
    framework = None
    if category == "coding":
        await emit("thinking", {"message": "Identifying project type…"}, "coding_router")
        try:
            code_route = await route_coding(prompt)
        except Exception as e:
            await emit("error", {"message": f"Coding router error: {e}"}, "coding_router")
            code_route = {"framework": "general", "confidence": 0, "reason": str(e)}

        framework = code_route.get("framework", "general")
        await emit("routing", {
            "level":      "coding_router",
            "decision":   framework,
            "reason":     code_route.get("reason", ""),
            "confidence": code_route.get("confidence", 0),
        }, "coding_router")

    # ── Step 3: Select & run worker agent ─────────────────────────────────────
    agent_label = framework or category
    await emit("thinking", {"message": f"Handing off to [{agent_label}] agent…"}, "dispatch")

    # Collect agent async generator and stream chunks
    try:
        if category == "generate":
            gen = agent_generate(prompt)
        elif category == "question":
            gen = agent_question(prompt)
        elif category == "data":
            gen = agent_data(prompt)
        elif category == "image":
            gen = agent_image(prompt)
        elif category == "coding":
            if framework == "nextjs":
                gen = agent_nextjs(prompt, send)
            elif framework in ("nuxt", "vue"):
                gen = agent_vue_nuxt(prompt, framework, send)
            elif framework == "python":
                gen = agent_python(prompt)
            elif framework == "node":
                gen = agent_node(prompt)
            elif framework == "react":
                gen = agent_react(prompt)
            else:
                gen = agent_coding_general(prompt)
        else:
            gen = agent_unknown(prompt)

        full_response = []
        async for chunk in gen:
            await emit("stream", {"chunk": chunk, "agent": agent_label}, "worker")
            full_response.append(chunk)

        await emit("done", {
            "agent":    agent_label,
            "category": category,
            "framework": framework,
            "length":   sum(len(c) for c in full_response),
        }, "worker")

    except Exception as e:
        log.exception("Worker agent error")
        await emit("error", {"message": str(e), "agent": agent_label}, "worker")


# ─────────────────────────────────────────────────────────────────────────────
# ════════════════  FASTAPI APP  ══════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AI Orchestrator Server",
    description="Multi-level AI routing: orchestrator → category router → worker agents",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active sessions { session_id: {"prompt": ..., "started_at": ...} }
_sessions: Dict[str, Dict] = {}


@app.get("/")
async def root():
    return {
        "name":    "AI Orchestrator Server",
        "version": "1.0.0",
        "status":  "running",
        "ws_endpoint": "/ws",
        "docs":    "/docs",
    }


@app.get("/health")
async def health():
    """Check Ollama connectivity."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
        return {"status": "ok", "ollama": "connected", "models": models}
    except Exception as e:
        return JSONResponse({"status": "degraded", "ollama": str(e)}, status_code=503)


@app.get("/models")
async def list_models():
    """List available Ollama models."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return r.json()
    except Exception as e:
        raise HTTPException(503, str(e))


@app.get("/sessions")
async def list_sessions():
    return {"active_sessions": len(_sessions), "sessions": list(_sessions.keys())}


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    WebSocket protocol:
      Client → server:
        { "prompt": "create a next.js app", "session_id": "optional-uuid" }

      Server → client (multiple messages):
        { "event": "thinking",  "session_id": "...", "data": {...}, "step": "orchestrator" }
        { "event": "routing",   "session_id": "...", "data": {...}, "step": "orchestrator" }
        { "event": "routing",   "session_id": "...", "data": {...}, "step": "coding_router" }
        { "event": "thinking",  "session_id": "...", "data": {...}, "step": "dispatch"     }
        { "event": "stream",    "session_id": "...", "data": {"chunk": "..."}, "step": "worker" }
        { "event": "done",      "session_id": "...", "data": {...}, "step": "worker" }
        or:
        { "event": "error",     "session_id": "...", "data": {"message": "..."} }
    """
    await ws.accept()
    log.info("WebSocket client connected")

    async def send_event(event: WsEvent):
        try:
            await ws.send_text(event.to_json())
        except Exception:
            pass

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send_event(WsEvent("error", data={"message": "Invalid JSON"}))
                continue

            prompt     = msg.get("prompt", "").strip()
            session_id = msg.get("session_id") or f"sess_{uuid.uuid4().hex[:10]}"

            if not prompt:
                await send_event(WsEvent("error", session_id=session_id,
                                         data={"message": "Empty prompt"}))
                continue

            _sessions[session_id] = {"prompt": prompt, "started_at": time.time()}
            log.info(f"[{session_id}] prompt={prompt[:80]!r}")

            try:
                await run_pipeline(prompt, session_id, send_event)
            finally:
                _sessions.pop(session_id, None)

    except WebSocketDisconnect:
        log.info("WebSocket client disconnected")
    except Exception as e:
        log.exception("WebSocket error")
        try:
            await ws.send_text(WsEvent("error", data={"message": str(e)}).to_json())
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# ════════════════  REST FALLBACK  ════════════════════════════════════════════
# For clients that can't use WebSocket — returns full response at once.
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat_rest(body: dict):
    """
    REST fallback — blocks until the full response is assembled.
    Body: { "prompt": "...", "session_id": "optional" }
    """
    prompt     = (body.get("prompt") or "").strip()
    session_id = body.get("session_id") or f"rest_{uuid.uuid4().hex[:8]}"
    if not prompt:
        raise HTTPException(400, "prompt is required")

    events: List[Dict] = []

    async def collect(event: WsEvent):
        events.append(asdict(event))

    await run_pipeline(prompt, session_id, collect)

    # Combine stream chunks into full text
    full_text = "".join(
        e["data"].get("chunk", "")
        for e in events
        if e["event"] == "stream"
    )
    routing = [e for e in events if e["event"] == "routing"]
    done    = next((e for e in events if e["event"] == "done"), {})

    return {
        "session_id": session_id,
        "prompt":     prompt,
        "response":   full_text,
        "routing":    routing,
        "meta":       done.get("data", {}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ════════════════  AGENT REGISTRY  (for future extensibility)  ═══════════════
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/agents")
async def list_agents():
    """
    Returns the routing map so clients know what categories and frameworks
    the server currently supports.
    """
    return {
        "categories": {
            "coding":   {
                "description": "Code scaffolding, building, debugging",
                "sub_routes": ["nextjs", "nuxt", "vue", "react", "python", "node", "general"],
            },
            "generate": {"description": "Text generation, writing, essays, stories"},
            "question": {"description": "Q&A, explanations, research"},
            "data":     {"description": "Data analysis, SQL, CSV"},
            "image":    {"description": "Image / visual generation descriptions"},
            "unknown":  {"description": "Fallback for unclassified prompts"},
        },
        "models": {
            "orchestrator": ORCHESTRATOR_MODEL,
            "default_worker": DEFAULT_MODEL,
        },
        "creator_modules": {
            "nextjs_creator":   HAS_NEXTJS,
            "vue_nuxt_creator": HAS_VUE_NUXT,
        },
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI Orchestrator Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════╗
║          AI Orchestrator Server v1.0.0               ║
║  WebSocket : ws://{args.host}:{args.port}/ws              ║
║  REST      : http://{args.host}:{args.port}/chat           ║
║  Docs      : http://{args.host}:{args.port}/docs           ║
║  Health    : http://{args.host}:{args.port}/health         ║
╚══════════════════════════════════════════════════════╝
""")
    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )