"""
AI Orchestrator Server — FastAPI + WebSocket + Ollama
═══════════════════════════════════════════════════════
Architecture:
  User prompt
      │
      ▼
  OrchestratorModel   ← decides category (coding / git / generate / ...)
      │
      ├─ git     →  GitController  ← full helper-level git agent
      │                  │  (clone, commit, push, pull, branch, merge,
      │                  │   rebase, tag, stash, reset, diff, log,
      │                  │   version bump, changelog, release flow,
      │                  │   .gitignore gen, web research, project setup)
      │
      ├─ coding  →  CodingRouter  ← decides framework
      │                  │
      │                  ├─ nextjs   →  NextjsCreatorAgent
      │                  ├─ nuxt     →  NuxtCreatorAgent
      │                  ├─ vue      →  VueCreatorAgent
      │                  ├─ react    →  ReactCreatorAgent
      │                  ├─ vite     →  ViteCreatorAgent
      │                  ├─ angular  →  AngularCreatorAgent
      │                  ├─ python   →  PythonAgent
      │                  ├─ node     →  NodeAgent
      │                  └─ general  →  CodingGeneralAgent
      │
      ├─ generate →  GenerateAgent
      ├─ question →  QuestionAgent
      └─ ...
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

# ─── Pull in creator modules from the tasks/ directory ───────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ── Original creators ─────────────────────────────────────────────────────────
try:
    from tasks.nextjs_creator import start as nextjs_start, _terminal_cb as _njs_cb
    HAS_NEXTJS = True
except ImportError:
    HAS_NEXTJS = False

try:
    from tasks.vue_nuxt_creator import start as vue_nuxt_start, _terminal_cb as _vn_cb
    HAS_VUE_NUXT = True
except ImportError:
    HAS_VUE_NUXT = False

# ── New creators ──────────────────────────────────────────────────────────────
try:
    from tasks.vite_creator import start as vite_start, _terminal_cb as _vite_cb
    HAS_VITE = True
except ImportError:
    HAS_VITE = False

try:
    from tasks.react_creator import start as react_start, _terminal_cb as _react_cb
    HAS_REACT = True
except ImportError:
    HAS_REACT = False

try:
    from tasks.angular_creator import start as angular_start, _terminal_cb as _angular_cb
    HAS_ANGULAR = True
except ImportError:
    HAS_ANGULAR = False

try:
    from tasks.git_controller import start as git_start, _terminal_cb as _git_cb
    HAS_GIT = True
except ImportError:
    HAS_GIT = False

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
OLLAMA_URL         = os.getenv("OLLAMA_URL",         "http://localhost:11434")
ORCHESTRATOR_MODEL = os.getenv("ORCHESTRATOR_MODEL", "qwen2.5-coder:7b")
DEFAULT_MODEL      = os.getenv("DEFAULT_MODEL",      "qwen2.5-coder:7b")

CATEGORY_CONFIG: Dict[str, Dict[str, str]] = {
    "coding":   {"router_model": ORCHESTRATOR_MODEL, "worker_model": DEFAULT_MODEL},
    "git":      {"router_model": ORCHESTRATOR_MODEL, "worker_model": DEFAULT_MODEL},
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
    event:      str
    session_id: str   = ""
    data:       Any   = None
    step:       str   = ""
    ts:         float = field(default_factory=time.time)

    def to_json(self) -> str:
        d = asdict(self)
        d["ts"] = round(d["ts"], 3)
        return json.dumps(d, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Ollama streaming helpers
# ─────────────────────────────────────────────────────────────────────────────
async def ollama_chat_stream(
    model: str,
    messages: List[Dict],
    *,
    base_url: str = OLLAMA_URL,
    timeout: int  = 120,
) -> AsyncIterator[str]:
    url     = f"{base_url}/api/chat"
    payload = {"model": model, "messages": messages, "stream": True, "options": {"temperature": 0.3}}
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
    model: str,
    messages: List[Dict],
    *,
    base_url: str = OLLAMA_URL,
    timeout: int  = 60,
) -> str:
    result = ""
    async for chunk in ollama_chat_stream(model, messages, base_url=base_url, timeout=timeout):
        result += chunk
    return result.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
ORCHESTRATOR_SYSTEM = """You are a task-routing AI.
Your ONLY job: classify the user prompt into exactly ONE category.

Categories:
  git      — anything git related: clone, commit, push, pull, branch, merge,
             version bump, changelog, release, .gitignore, repo analysis,
             project setup from a repo URL, or any git workflow question
  coding   — creating, building, scaffolding, or fixing code / projects / apps
             (NOT git operations — pure code writing/scaffolding)
  generate — writing text: articles, stories, essays, sentences, summaries, emails
  question — answering factual questions, explanations, definitions, research
  image    — anything about images, drawing, visual generation descriptions
  data     — data analysis, CSV, SQL, spreadsheets, charts, graphs
  unknown  — does not fit any above

Respond ONLY with a valid JSON object — no markdown, no explanation:
{"category": "<one of the above>", "confidence": 0.95, "reason": "short reason"}
"""


async def orchestrate(prompt: str) -> Dict[str, Any]:
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM},
        {"role": "user",   "content": prompt},
    ]
    raw = await ollama_chat_full(ORCHESTRATOR_MODEL, messages)
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
# Coding Router  — updated to include react / vite / angular
# ─────────────────────────────────────────────────────────────────────────────
CODING_ROUTER_SYSTEM = """You are a coding-project-type classifier.
Classify the user prompt into one of these project types:

  nextjs   — Next.js project (SSR / SSG with React)
  nuxt     — Nuxt.js / Nuxt3 project (SSR / SSG with Vue)
  vue      — Vue.js (without Nuxt, pure SPA)
  react    — React SPA with routing / state / testing (NOT Next.js)
  vite     — Generic Vite project (vanilla, preact, lit, solid, svelte, qwik, or unspecified)
  angular  — Angular project
  python   — Python script / FastAPI / Django / Flask
  node     — Node.js / Express backend
  general  — generic coding task (not a specific framework scaffold)

Decision rules:
- If the user says "Next.js" or "nextjs" → nextjs
- If the user says "Nuxt" → nuxt
- If the user says "Vue" without "Nuxt" → vue
- If the user says "React" without "Next.js" → react
- If the user says "Angular" → angular
- If the user says "Vite" with a non-React/Vue template (svelte, solid, vanilla, lit, preact) → vite
- If the user says "Vite" + "React" → react  (we handle Vite internally)
- If it's a generic scaffold or mixed → general

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
# Shared helper — bridge a sync creator to WebSocket via asyncio.Queue
# ─────────────────────────────────────────────────────────────────────────────
async def _run_creator(start_fn, prompt: str) -> AsyncIterator[str]:
    """
    Wraps any sync creator's `start()` function so it streams chunks
    back to the async pipeline.
    """
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _cb(text="", color="white", msg_type="normal", data=None):
        loop.call_soon_threadsafe(
            q.put_nowait, {"text": text, "msg_type": msg_type, "data": data or {}}
        )

    async def _runner():
        await asyncio.to_thread(start_fn, prompt, _cb, {"type": "init", "data": {}})
        await q.put(None)  # sentinel

    task = asyncio.create_task(_runner())
    while True:
        item = await q.get()
        if item is None:
            break
        yield f"[{item['msg_type'].upper()}] {item['text']}\n"
    await task


# ─────────────────────────────────────────────────────────────────────────────
# Worker agents
# ─────────────────────────────────────────────────────────────────────────────

async def agent_generate(prompt: str) -> AsyncIterator[str]:
    system = "You are a skilled writer. Fulfil the user's generation request accurately, creatively, and in the right format."
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
        yield chunk


async def agent_question(prompt: str) -> AsyncIterator[str]:
    system = "You are a knowledgeable assistant. Answer the user's question clearly, concisely, and accurately."
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
        yield chunk


async def agent_data(prompt: str) -> AsyncIterator[str]:
    system = "You are a data-analysis expert. Help the user with data tasks, SQL, CSV, charts, or analysis. Provide code when needed."
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
        yield chunk


async def agent_image(prompt: str) -> AsyncIterator[str]:
    system = "You are a visual-design assistant. Help with image-related prompts, descriptions, or visual generation instructions."
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
        yield chunk


async def agent_coding_general(prompt: str) -> AsyncIterator[str]:
    system = "You are an expert software engineer. Help the user write, debug, explain, or improve code. Provide clear, working solutions."
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
        yield chunk


async def agent_python(prompt: str) -> AsyncIterator[str]:
    system = "You are a Python expert. Help the user build Python scripts, FastAPI/Django/Flask apps, or debug Python code."
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
        yield chunk


async def agent_node(prompt: str) -> AsyncIterator[str]:
    system = "You are a Node.js expert. Help the user build Express APIs, Node scripts, or debug backend JavaScript."
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
        yield chunk


async def agent_unknown(prompt: str) -> AsyncIterator[str]:
    system = "You are a helpful AI assistant. Answer the user as best you can."
    async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
        yield chunk


async def agent_git(prompt: str, send: Callable, session_cwd: Optional[str] = None) -> AsyncIterator[str]:
    """
    Git Controller agent — full helper-level git operations.
    Uses git_controller.py when available, falls back to LLM git advice.
    """
    if HAS_GIT:
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _cb(text="", color="white", msg_type="normal", data=None):
            loop.call_soon_threadsafe(
                q.put_nowait, {"text": text, "msg_type": msg_type, "data": data or {}}
            )

        async def _runner():
            init_data = {"cwd": session_cwd or os.getcwd()}
            await asyncio.to_thread(git_start, prompt, _cb, init_data)
            await q.put(None)

        task = asyncio.create_task(_runner())
        while True:
            item = await q.get()
            if item is None:
                break
            yield f"[{item['msg_type'].upper()}] {item['text']}\n"
        await task
    else:
        system = (
            "You are a Git expert and project setup assistant. "
            "Help the user with git operations, version control workflows, "
            "project cloning, dependency installation, changelog generation, "
            "and release management. Be specific with commands."
        )
        async for chunk in ollama_chat_stream(
            DEFAULT_MODEL,
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        ):
            yield chunk


# ── Creator-backed agents (use full agentic pipelines when available) ─────────

async def agent_nextjs(prompt: str, send: Callable) -> AsyncIterator[str]:
    if HAS_NEXTJS:
        async for chunk in _run_creator(nextjs_start, prompt):
            yield chunk
    else:
        system = "You are a Next.js expert. Help the user scaffold, build, or fix their Next.js project."
        async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
            yield chunk


async def agent_vue_nuxt(prompt: str, framework: str, send: Callable) -> AsyncIterator[str]:
    if HAS_VUE_NUXT:
        async for chunk in _run_creator(vue_nuxt_start, prompt):
            yield chunk
    else:
        system = f"You are a {framework.capitalize()} expert. Help the user scaffold, build, or fix their {framework.capitalize()} project."
        async for chunk in ollama_chat_stream(DEFAULT_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}]):
            yield chunk


async def agent_vite(prompt: str, send: Callable) -> AsyncIterator[str]:
    """Vite creator agent — uses vite_creator.py if available."""
    if HAS_VITE:
        async for chunk in _run_creator(vite_start, prompt):
            yield chunk
    else:
        system = (
            "You are a Vite expert. Help the user scaffold a Vite project "
            "(vanilla, react, svelte, solid, preact, lit, or qwik). "
            "Provide step-by-step CLI commands and explanations."
        )
        async for chunk in ollama_chat_stream(
            DEFAULT_MODEL,
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        ):
            yield chunk


async def agent_react(prompt: str, send: Callable) -> AsyncIterator[str]:
    """React creator agent — uses react_creator.py if available."""
    if HAS_REACT:
        async for chunk in _run_creator(react_start, prompt):
            yield chunk
    else:
        system = (
            "You are a React expert. Help the user build a React application "
            "with routing, state management, and styling. Provide step-by-step "
            "CLI commands and code examples."
        )
        async for chunk in ollama_chat_stream(
            DEFAULT_MODEL,
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        ):
            yield chunk


async def agent_angular(prompt: str, send: Callable) -> AsyncIterator[str]:
    """Angular creator agent — uses angular_creator.py if available."""
    if HAS_ANGULAR:
        async for chunk in _run_creator(angular_start, prompt):
            yield chunk
    else:
        system = (
            "You are an Angular expert. Help the user scaffold an Angular project "
            "with routing, components, services, and optional Angular Material or NgRx. "
            "Provide step-by-step Angular CLI commands and explanations."
        )
        async for chunk in ollama_chat_stream(
            DEFAULT_MODEL,
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        ):
            yield chunk


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────
async def run_pipeline(prompt: str, session_id: str, send: Callable):
    async def emit(event: str, data: Any, step: str = ""):
        await send(WsEvent(event=event, session_id=session_id, data=data, step=step))

    # Step 1: Orchestrate
    await emit("thinking", {"message": "Analysing your request…"}, "orchestrator")
    try:
        orch = await orchestrate(prompt)
    except Exception as e:
        await emit("error", {"message": f"Orchestrator error: {e}"}, "orchestrator")
        return

    category = orch.get("category", "unknown")
    await emit("routing", {
        "level":      "orchestrator",
        "decision":   category,
        "reason":     orch.get("reason", ""),
        "confidence": orch.get("confidence", 0),
    }, "orchestrator")

    # Step 2: Sub-routing for coding
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

    # Step 3: Select & run worker agent
    agent_label = framework or category
    await emit("thinking", {"message": f"Handing off to [{agent_label}] agent…"}, "dispatch")

    try:
        if category == "git":
            session_cwd = _sessions.get(session_id, {}).get("cwd")
            gen = agent_git(prompt, send, session_cwd)
        elif category == "generate":
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
            elif framework == "react":
                gen = agent_react(prompt, send)          # ← NEW
            elif framework == "vite":
                gen = agent_vite(prompt, send)           # ← NEW
            elif framework == "angular":
                gen = agent_angular(prompt, send)        # ← NEW
            elif framework == "python":
                gen = agent_python(prompt)
            elif framework == "node":
                gen = agent_node(prompt)
            else:
                gen = agent_coding_general(prompt)
        else:
            gen = agent_unknown(prompt)

        full_response = []
        async for chunk in gen:
            await emit("stream", {"chunk": chunk, "agent": agent_label}, "worker")
            full_response.append(chunk)

        await emit("done", {
            "agent":     agent_label,
            "category":  category,
            "framework": framework,
            "length":    sum(len(c) for c in full_response),
        }, "worker")

    except Exception as e:
        log.exception("Worker agent error")
        await emit("error", {"message": str(e), "agent": agent_label}, "worker")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AI Orchestrator Server",
    description="Multi-level AI routing: orchestrator → category router → worker agents",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_sessions: Dict[str, Dict] = {}


@app.get("/")
async def root():
    return {
        "name":        "AI Orchestrator Server",
        "version":     "1.2.0",
        "status":      "running",
        "ws_endpoint": "/ws",
        "docs":        "/docs",
    }


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
        return {"status": "ok", "ollama": "connected", "models": models}
    except Exception as e:
        return JSONResponse({"status": "degraded", "ollama": str(e)}, status_code=503)


@app.get("/models")
async def list_models():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return r.json()
    except Exception as e:
        raise HTTPException(503, str(e))


@app.get("/sessions")
async def list_sessions():
    return {"active_sessions": len(_sessions), "sessions": list(_sessions.keys())}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
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
            cwd        = msg.get("cwd", "").strip() or None   # optional working dir for git ops

            if not prompt:
                await send_event(WsEvent("error", session_id=session_id, data={"message": "Empty prompt"}))
                continue

            _sessions[session_id] = {"prompt": prompt, "started_at": time.time(), "cwd": cwd}
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


@app.post("/chat")
async def chat_rest(body: dict):
    prompt     = (body.get("prompt") or "").strip()
    session_id = body.get("session_id") or f"rest_{uuid.uuid4().hex[:8]}"
    cwd        = body.get("cwd", "").strip() or None
    if not prompt:
        raise HTTPException(400, "prompt is required")

    events: List[Dict] = []

    async def collect(event: WsEvent):
        events.append(asdict(event))

    _sessions[session_id] = {"prompt": prompt, "started_at": time.time(), "cwd": cwd}
    await run_pipeline(prompt, session_id, collect)
    _sessions.pop(session_id, None)

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


@app.get("/agents")
async def list_agents():
    return {
        "categories": {
            "git": {
                "description": "Full helper-level Git controller: all git ops + web research + project setup",
                "capabilities": [
                    "git init/clone/clone+setup",
                    "commit (smart message generation)",
                    "push/pull/fetch",
                    "branch/merge/rebase/cherry-pick",
                    "tag/stash/reset/revert/diff/log",
                    "remote management",
                    "semver version bump (major/minor/patch)",
                    "CHANGELOG.md generation",
                    "full release flow (bump+changelog+tag+push)",
                    ".gitignore generation (Python/JS/TS/Flutter/Java/PHP/Go/Rust)",
                    "repo analysis (language/framework/dep detection)",
                    "web research (GitHub API / npm / PyPI / pub.dev / Google)",
                    "README.md + doc page crawling",
                    "project setup (npm/pip/flutter/composer/mvn/gradle/go/cargo)",
                    ".env.example auto-generation",
                    "git help / concept explanation",
                ],
            },
            "coding": {
                "description": "Code scaffolding, building, debugging",
                "sub_routes": [
                    "nextjs", "nuxt", "vue", "react", "vite", "angular",
                    "python", "node", "general",
                ],
            },
            "generate": {"description": "Text generation, writing, essays, stories"},
            "question": {"description": "Q&A, explanations, research"},
            "data":     {"description": "Data analysis, SQL, CSV"},
            "image":    {"description": "Image / visual generation descriptions"},
            "unknown":  {"description": "Fallback for unclassified prompts"},
        },
        "models": {
            "orchestrator":   ORCHESTRATOR_MODEL,
            "default_worker": DEFAULT_MODEL,
        },
        "creator_modules": {
            "nextjs_creator":   HAS_NEXTJS,
            "vue_nuxt_creator": HAS_VUE_NUXT,
            "vite_creator":     HAS_VITE,
            "react_creator":    HAS_REACT,
            "angular_creator":  HAS_ANGULAR,
            "git_controller":   HAS_GIT,
        },
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI Orchestrator Server")
    parser.add_argument("--host",   default="0.0.0.0")
    parser.add_argument("--port",   type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════╗
║          AI Orchestrator Server v1.2.0               ║
║  WebSocket : ws://{args.host}:{args.port}/ws              ║
║  REST      : http://{args.host}:{args.port}/chat           ║
║  Docs      : http://{args.host}:{args.port}/docs           ║
║  Health    : http://{args.host}:{args.port}/health         ║
╚══════════════════════════════════════════════════════╝
  Creator / Controller modules loaded:
    git     : {HAS_GIT}   ← GitController (clone/commit/push/release/research)
    nextjs  : {HAS_NEXTJS}
    vue/nuxt: {HAS_VUE_NUXT}
    vite    : {HAS_VITE}
    react   : {HAS_REACT}
    angular : {HAS_ANGULAR}

  Env vars (optional):
    GITHUB_TOKEN      — for private repos + higher API rate limits
    GOOGLE_API_KEY    — Google Custom Search
    GOOGLE_SEARCH_CX  — Google Custom Search engine ID
""")
    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )