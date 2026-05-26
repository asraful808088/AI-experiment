import ollama
import subprocess
import os
import sys
import json
import hashlib
import re
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Callable, Optional

MAX_RETRIES = 5

TASKS_ROOT    = Path("agent_tasks")
META_FILE     = TASKS_ROOT / "_metadata" / "index.json"
SESSIONS_ROOT = Path("sessions")

SESSION_INSTALLED: set[str] = set()

# ─────────────────────────────────────────────
# CALLBACK / EVENT SYSTEM
# ─────────────────────────────────────────────

class EV:
    TASK_START    = "task_start"
    PLAN          = "plan"
    CODE_GEN      = "code_gen"
    CODE          = "code"
    RUN           = "run"
    RETRY         = "retry"
    REFLECT       = "reflect"
    SUMMARY       = "summary"
    CACHE_HIT     = "cache_hit"
    CACHE_SAVE    = "cache_save"
    SESSION_SAVE  = "session_save"
    SUCCESS       = "success"
    FAILURE       = "failure"
    PKG_INSTALL   = "pkg_install"
    PKG_REINSTALL = "pkg_reinstall"
    PKG_REMOVE    = "pkg_remove"
    PKG_ERROR     = "pkg_error"
    LLM_STREAM    = "llm_stream"
    LLM_DONE      = "llm_done"
    LLM_ERROR     = "llm_error"
    RESULT        = "result"
    WARN          = "warn"
    INFO          = "info"
    LLM_STREAM_START = "llm_stream_start"   
    LLM_STREAM_END   = "llm_stream_end"     


class Color:
    GREEN   = "green"
    RED     = "red"
    YELLOW  = "yellow"
    CYAN    = "cyan"
    BLUE    = "blue"
    MAGENTA = "magenta"
    WHITE   = "white"
    GRAY    = "gray"
    ORANGE  = "orange"


EVENT_COLORS: dict[str, str] = {
    EV.TASK_START:    Color.CYAN,
    EV.PLAN:          Color.BLUE,
    EV.CODE_GEN:      Color.BLUE,
    EV.CODE:          Color.WHITE,
    EV.RUN:           Color.CYAN,
    EV.RETRY:         Color.ORANGE,
    EV.REFLECT:       Color.BLUE,
    EV.SUMMARY:       Color.CYAN,
    EV.CACHE_HIT:     Color.MAGENTA,
    EV.CACHE_SAVE:    Color.MAGENTA,
    EV.SESSION_SAVE:  Color.MAGENTA,
    EV.SUCCESS:       Color.GREEN,
    EV.FAILURE:       Color.RED,
    EV.PKG_INSTALL:   Color.YELLOW,
    EV.PKG_REINSTALL: Color.YELLOW,
    EV.PKG_REMOVE:    Color.GRAY,
    EV.PKG_ERROR:     Color.RED,
    EV.LLM_STREAM:    Color.WHITE,
    EV.LLM_DONE:      Color.GRAY,
    EV.LLM_ERROR:     Color.RED,
    EV.RESULT:        Color.WHITE,
    EV.WARN:          Color.YELLOW,
    EV.INFO:          Color.GRAY,
}
EVENT_COLORS[EV.LLM_STREAM_START] = Color.CYAN
EVENT_COLORS[EV.LLM_STREAM_END]   = Color.GRAY

@dataclass
class AgentEvent:
    event:     str
    text:      str
    color:     str
    data:      dict
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


def make_event(event: str, text: str, **data) -> AgentEvent:
    return AgentEvent(
        event=event,
        text=text,
        color=EVENT_COLORS.get(event, Color.WHITE),
        data=data,
        timestamp=datetime.now().isoformat(),
    )


# ── Default terminal callback with ANSI colors ─────────────
ANSI = {
    Color.GREEN:   "\033[92m",
    Color.RED:     "\033[91m",
    Color.YELLOW:  "\033[93m",
    Color.CYAN:    "\033[96m",
    Color.BLUE:    "\033[94m",
    Color.MAGENTA: "\033[95m",
    Color.WHITE:   "\033[97m",
    Color.GRAY:    "\033[90m",
    Color.ORANGE:  "\033[33m",
}
ANSI_RESET = "\033[0m"


def default_callback(ev: AgentEvent):
    color = ANSI.get(ev.color, "")
    if ev.event == EV.LLM_STREAM:
        print(f"{color}{ev.text}{ANSI_RESET}", end="", flush=True)
    else:
        print(f"{color}{ev.text}{ANSI_RESET}")


# ─────────────────────────────────────────────
# SESSION SYSTEM
# ─────────────────────────────────────────────

def create_session(task: str) -> tuple[Path, str]:
    session_id  = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + _task_id(task)
    session_dir = SESSIONS_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir, session_id


def save_session(
    session_dir: Path, session_id: str, task: str,
    events: list[AgentEvent], final_code: str,
    final_report: str, success: bool, summary: str
) -> Path:
    history = {
        "session_id":   session_id,
        "task":         task,
        "started_at":   events[0].timestamp if events else datetime.now().isoformat(),
        "finished_at":  datetime.now().isoformat(),
        "success":      success,
        "summary":      summary,
        "final_code":   final_code,
        "final_report": final_report,
        "events":       [e.to_dict() for e in events],
    }
    out = session_dir / "history.json"
    out.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ─────────────────────────────────────────────
# TASK CACHE
# ─────────────────────────────────────────────

def _slug(text: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9 ]', '', text).strip().lower()
    return (re.sub(r'\s+', '_', s)[:50]) or "unnamed_task"


def _task_id(task: str) -> str:
    return hashlib.md5(task.strip().lower().encode()).hexdigest()[:8]


def get_task_dir(task: str) -> Path:
    folder = TASKS_ROOT / f"{_slug(task)}__{_task_id(task)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_metadata() -> dict:
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_metadata(meta: dict):
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def find_similar_task(task: str, meta: dict) -> dict | None:
    task_words = set(re.findall(r'\b\w{4,}\b', task.lower()))
    tid = _task_id(task)
    if tid in meta:
        return meta[tid]
    best, best_score = None, 0
    for entry in meta.values():
        known_words = set(re.findall(r'\b\w{4,}\b', entry.get("task", "").lower()))
        overlap = len(task_words & known_words)
        if overlap > best_score:
            best_score, best = overlap, entry
    return best if best_score >= 3 else None


def save_task_result(task: str, attempt: int, code: str, report: str, success: bool, summary: str):
    task_dir  = get_task_dir(task)
    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    step_file = task_dir / f"step_{attempt:02d}_{ts}.py"
    step_file.write_text(code, encoding="utf-8")
    result = {
        "task": task, "timestamp": ts, "attempts": attempt,
        "success": success, "report": report,
        "summary": summary, "best_code": str(step_file),
    }
    (task_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    meta = load_metadata()
    meta[_task_id(task)] = {
        "task": task, "task_dir": str(task_dir),
        "timestamp": ts, "success": success, "summary": summary,
    }
    save_metadata(meta)


def load_cached_code(entry: dict) -> str | None:
    try:
        r = json.loads((Path(entry["task_dir"]) / "result.json").read_text(encoding="utf-8"))
        p = Path(r.get("best_code", ""))
        return p.read_text(encoding="utf-8") if p.exists() else None
    except Exception:
        return None


# ─────────────────────────────────────────────
# PACKAGE MANAGER
# ─────────────────────────────────────────────

IMPORT_TO_PIP = {
    "bs4":          "beautifulsoup4",
    "cv2":          "opencv-python",
    "sklearn":      "scikit-learn",
    "PIL":          "Pillow",
    "dotenv":       "python-dotenv",
    "yaml":         "PyYAML",
    "serial":       "pyserial",
    "dateutil":     "python-dateutil",
    "attr":         "attrs",
    "toml":         "toml",
    "nacl":         "PyNaCl",
    "cryptography": "cryptography",
    "boto3":        "boto3",
    "usb":          "pyusb",
}

STDLIB = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
    "os","sys","re","json","math","time","datetime","pathlib","shutil",
    "subprocess","tempfile","hashlib","random","string","io","csv",
    "collections","itertools","functools","typing","abc","copy","gc",
    "inspect","logging","argparse","threading","multiprocessing","socket",
    "http","urllib","email","html","xml","sqlite3","struct","ctypes",
    "unittest","traceback","warnings","enum","dataclasses","contextlib",
    "textwrap","pprint","ast","base64","binascii","codecs","glob",
    "fnmatch","stat","zipfile","tarfile","gzip","bz2","zlib",
    "pickle","shelve","dbm","calendar","locale","platform","signal",
    "errno","builtins","types","weakref","uuid","queue","heapq",
}


def _pip_list() -> set[str]:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=freeze"],
            capture_output=True, text=True
        )
        return {line.split("==")[0].strip().lower() for line in r.stdout.splitlines() if "==" in line}
    except Exception:
        return set()


def _pip_install(pip_name: str, force: bool = False) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", pip_name,
           "--quiet", "--disable-pip-version-check"]
    if force:
        cmd.insert(4, "--force-reinstall")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


def _pip_uninstall(pip_name: str) -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", pip_name, "-y",
             "--quiet", "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=60
        )
        return r.returncode == 0
    except Exception:
        return False


def extract_imports(code: str) -> list[str]:
    names = []
    for line in code.splitlines():
        line = line.strip()
        m = re.match(r'^import\s+([\w]+)', line) or re.match(r'^from\s+([\w]+)', line)
        if m:
            names.append(m.group(1))
    return list(set(names))


def auto_install_imports(code: str, cb: Callable):
    installed = _pip_list()
    for imp in extract_imports(code):
        if imp in STDLIB:
            continue
        pip_name = IMPORT_TO_PIP.get(imp, imp)
        if pip_name.lower() in installed:
            continue
        cb(make_event(EV.PKG_INSTALL, f"[PKG] '{imp}' missing → installing '{pip_name}' ...", pkg=pip_name))
        ok = _pip_install(pip_name)
        if ok:
            cb(make_event(EV.PKG_INSTALL, f"[PKG] ✅ Installed: {pip_name}", pkg=pip_name, status="ok"))
            SESSION_INSTALLED.add(pip_name)
        else:
            cb(make_event(EV.PKG_ERROR, f"[PKG] ❌ Could not install: {pip_name}", pkg=pip_name, status="fail"))


def auto_fix_runtime_import(report: str, cb: Callable) -> bool:
    m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", report)
    if not m:
        m = re.search(r"ImportError:.*'([^']+)'", report)
    if not m:
        return False
    imp      = m.group(1).split(".")[0]
    pip_name = IMPORT_TO_PIP.get(imp, imp)
    cb(make_event(EV.PKG_REINSTALL, f"[PKG] Runtime error: '{imp}' → reinstalling '{pip_name}' ...", pkg=pip_name))
    ok = _pip_install(pip_name, force=True)
    if ok:
        cb(make_event(EV.PKG_REINSTALL, f"[PKG] ✅ Reinstalled: {pip_name}", pkg=pip_name, status="ok"))
        SESSION_INSTALLED.add(pip_name)
        return True
    cb(make_event(EV.PKG_ERROR, f"[PKG] ❌ Reinstall failed: {pip_name}", pkg=pip_name, status="fail"))
    return False


def cleanup_session_packages(cb: Callable):
    if not SESSION_INSTALLED:
        return
    cb(make_event(EV.PKG_REMOVE, f"[PKG] Cleaning up {len(SESSION_INSTALLED)} package(s): {SESSION_INSTALLED}"))
    for pkg in list(SESSION_INSTALLED):
        ok = _pip_uninstall(pkg)
        cb(make_event(
            EV.PKG_REMOVE if ok else EV.PKG_ERROR,
            f"[PKG] {'🗑️  Removed' if ok else '⚠️  Could not remove'}: {pkg}",
            pkg=pkg, status="ok" if ok else "fail"
        ))
    SESSION_INSTALLED.clear()


# ─────────────────────────────────────────────
# LLM CALL
# ─────────────────────────────────────────────
def llm_call(prompt: str, system_p: str, cb: Callable, history: list = []) -> str:
    messages = [{'role': 'system', 'content': system_p}] + history
    messages.append({'role': 'user', 'content': prompt})

    cb(make_event(EV.INFO, "[LLM] Thinking...\n" + "─"*50))
    try:
        stream = ollama.chat(
            model='qwen2.5-coder:3b',
            messages=messages,
            stream=True,
            keep_alive=-1
        )
        full = ""

        # Signal stream start — callback can switch to streaming color
        cb(make_event(EV.LLM_STREAM_START, ""))

        for chunk in stream:
            c = chunk['message']['content']
            # LLM_STREAM goes to callback only — NOT accumulated into all_events
            cb(make_event(EV.LLM_STREAM, c))
            full += c

        # Signal stream end — one event with the complete text saved to history
        cb(make_event(EV.LLM_STREAM_END, full))
        cb(make_event(EV.LLM_DONE, "\n" + "─"*50))
        return full

    except Exception as e:
        cb(make_event(EV.LLM_ERROR, f"[LLM ERROR] {e}\n" + "─"*50, error=str(e)))
        return ""


# ─────────────────────────────────────────────
# EXTRACT CODE
# ─────────────────────────────────────────────

def extract_code(response: str) -> str:
    if "```python" in response:
        start = response.index("```python") + len("```python")
        end   = response.index("```", start)
        return response[start:end].strip()
    if "```" in response:
        start = response.index("```") + 3
        end   = response.index("```", start)
        return response[start:end].strip()
    return response.strip()


# ─────────────────────────────────────────────
# RUN CODE
# ─────────────────────────────────────────────

def run_code(code: str, cb: Callable) -> tuple[str, bool]:
    tmp_path = None
    auto_install_imports(code, cb)

    try:
        task_tmp_dir = TASKS_ROOT / "_tmp"
        task_tmp_dir.mkdir(parents=True, exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        tmp_path = task_tmp_dir / f"run_{ts}.py"
        tmp_path.write_text(code, encoding="utf-8")

        cb(make_event(EV.RUN, f"[AGENT] Running: {tmp_path}\n" + "─"*50, path=str(tmp_path)))

        def _run(path: Path):
            return subprocess.run(
                [sys.executable, str(path)],
                capture_output=True, text=True, timeout=60
            )

        result = _run(tmp_path)
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        report = ""
        if stdout: report += f"=== STDOUT ===\n{stdout}\n"
        if stderr: report += f"=== STDERR ===\n{stderr}\n"
        if not report: report = "(no output)"

        success = result.returncode == 0 and "Traceback" not in stderr

        if not success and ("ModuleNotFoundError" in report or "ImportError" in report):
            fixed = auto_fix_runtime_import(report, cb)
            if fixed:
                cb(make_event(EV.PKG_REINSTALL, "[PKG] Retrying script after reinstall...\n" + "─"*50))
                result2  = _run(tmp_path)
                stdout2  = result2.stdout.strip()
                stderr2  = result2.stderr.strip()
                report   = ""
                if stdout2: report += f"=== STDOUT ===\n{stdout2}\n"
                if stderr2: report += f"=== STDERR ===\n{stderr2}\n"
                if not report: report = "(no output)"
                success = result2.returncode == 0 and "Traceback" not in stderr2

    except subprocess.TimeoutExpired:
        report, success = "ERROR: Script timed out after 60 seconds.", False
    except Exception as e:
        report, success = f"ERROR launching script: {e}", False
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception as e:
                cb(make_event(EV.WARN, f"[WARN] Could not delete temp file: {e}"))

    return report, success


# ─────────────────────────────────────────────
# SYSTEM PROMPTS
# ─────────────────────────────────────────────

CODER_SYSTEM = """
You are an expert Python developer acting as an autonomous agent.
Rules:
- Respond with ONLY a complete runnable Python script wrapped in ```python ... ```
- No text outside the code block.
- The script must print its result/report clearly to stdout.
- Handle ALL exceptions inside the script and print them clearly.
- You may freely use ANY third-party packages (requests, bs4, pandas, etc.) — they will be auto-installed.
- If the task involves the filesystem, use os, pathlib, shutil.
"""

FIX_SYSTEM = """
You are an expert Python debugger and autonomous agent.
You receive: 1. The original task. 2. The previous code. 3. The error output.
Fix the root cause completely.
Return ONLY the corrected full Python script in ```python ... ```
No explanation outside the code block.
You may use any third-party packages — they will be auto-installed.
"""

REFLECT_SYSTEM = """
You are an AI agent reviewer. Write a plain-English summary (3-8 sentences):
- What the script did step by step.
- Whether it succeeded or partially succeeded.
- Key results or findings.
- Any remaining issues.
"""

PLAN_SYSTEM = """
You are an expert software architect and AI agent planner.
Given a user task, break it into clear numbered sub-steps a Python script should follow.
Be specific and practical. Output only the numbered plan, no code.
"""


# ─────────────────────────────────────────────
# AGENT
# ─────────────────────────────────────────────

def agent(
    task: str,
    keep_packages: bool = False,
    callback: Optional[Callable[[AgentEvent], None]] = None,
) -> str:
    """
    callback      → receives every AgentEvent as a rich object.
                    Defaults to built-in ANSI terminal printer.
    keep_packages → if False (default) uninstall packages we installed this session.

    AgentEvent fields:
        .event      str   — EV.* constant  (e.g. EV.SUCCESS, EV.PKG_INSTALL)
        .text       str   — human-readable message
        .color      str   — Color.* constant (green/red/yellow/…)
        .data       dict  — extra info (attempt, pkg, code, report, …)
        .timestamp  str   — ISO datetime string
    """
    cb = callback or default_callback

    # ── Session ────────────────────────────────────────────────
    session_dir, session_id = create_session(task)
    all_events: list[AgentEvent] = []

    def emit(ev: AgentEvent):
        # LLM_STREAM is fire-and-forget — never stored
        if ev.event != EV.LLM_STREAM:
            all_events.append(ev)
        cb(ev)

    emit(make_event(EV.TASK_START,
        f"\n{'='*60}\n  TASK: {task}\n{'='*60}",
        task=task, session_id=session_id))

    # ── Cache check ────────────────────────────────────────────
    meta        = load_metadata()
    similar     = find_similar_task(task, meta)
    cached_code = None
    if similar:
        emit(make_event(EV.CACHE_HIT,
            f"[CACHE] Similar past task: \"{similar['task']}\"  ({similar['task_dir']})",
            cached_task=similar["task"], cached_dir=similar["task_dir"]))
        cached_code = load_cached_code(similar)
        if cached_code:
            emit(make_event(EV.CACHE_HIT, "[CACHE] Using previous code as starting point."))

    # ── Plan ───────────────────────────────────────────────────
    emit(make_event(EV.PLAN, "[STEP 0] Planning..."))
    plan = llm_call(task, PLAN_SYSTEM, emit)
    emit(make_event(EV.PLAN, f"\n[PLAN]\n{plan}", plan=plan))

    # ── Generate code ──────────────────────────────────────────
    emit(make_event(EV.CODE_GEN, "[STEP 1] Generating initial code..."))
    code_prompt = (
        f"Task:\n{task}\n\nPlan:\n{plan}"
        + (f"\n\nAdapt this similar working code:\n```python\n{cached_code}\n```" if cached_code else "")
    )
    code = extract_code(llm_call(code_prompt, CODER_SYSTEM, emit))
    emit(make_event(EV.CODE, f"\n[CODE v1]\n{'─'*50}\n{code}\n{'─'*50}", code=code, version=1))

    # ── Run + retry loop ───────────────────────────────────────
    attempt         = 1
    report, success = run_code(code, emit)
    emit(make_event(
        EV.SUCCESS if success else EV.FAILURE,
        f"[RESULT attempt {attempt}]\n{report}[SUCCESS: {success}]",
        attempt=attempt, report=report, success=success
    ))

    task_dir = get_task_dir(task)
    (task_dir / f"step_{attempt:02d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py").write_text(code, encoding="utf-8")

    fix_history = []

    while not success and attempt < MAX_RETRIES:
        attempt += 1
        emit(make_event(EV.RETRY,
            f"{'─'*60}\n[RETRY {attempt}/{MAX_RETRIES}] Fixing...",
            attempt=attempt, max_retries=MAX_RETRIES))

        fix_prompt = (
            f"Original task:\n{task}\n\n"
            f"Previous code (attempt {attempt-1}):\n```python\n{code}\n```\n\n"
            f"Error:\n{report}\n\nFix it completely."
        )
        fix_history.append({'role': 'user',      'content': fix_prompt})
        raw = llm_call(fix_prompt, FIX_SYSTEM, emit, history=fix_history[:-1])
        fix_history.append({'role': 'assistant', 'content': raw})

        code            = extract_code(raw)
        report, success = run_code(code, emit)

        (task_dir / f"step_{attempt:02d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py").write_text(code, encoding="utf-8")
        emit(make_event(EV.CODE, f"\n[CODE v{attempt}]\n{'─'*50}\n{code}\n{'─'*50}", code=code, version=attempt))
        emit(make_event(
            EV.SUCCESS if success else EV.FAILURE,
            f"\n[RESULT attempt {attempt}]\n{report}[SUCCESS: {success}]",
            attempt=attempt, report=report, success=success
        ))

    emit(make_event(
        EV.SUCCESS if success else EV.FAILURE,
        f"\n{'✅' if success else '❌'}  {'Solved' if success else 'Could not solve'} in {attempt} attempt(s).",
        attempts=attempt, success=success
    ))

    # ── Reflect ────────────────────────────────────────────────
    emit(make_event(EV.REFLECT, "[STEP 4] Summarising..."))
    summary = llm_call(
        f"Task:\n{task}\n\nFinal code:\n```python\n{code}\n```\n\nOutput:\n{report}",
        REFLECT_SYSTEM, emit
    )
    emit(make_event(EV.SUMMARY,
        f"\n{'='*60}\n[FINAL SUMMARY]\n{'='*60}\n{summary}\n{'='*60}",
        summary=summary))

    # ── Save task cache ────────────────────────────────────────
    save_task_result(task, attempt, code, report, success, summary)
    emit(make_event(EV.CACHE_SAVE, f"[CACHE] Task saved → {task_dir}", task_dir=str(task_dir)))

    # ── Save session history ───────────────────────────────────
    session_file = save_session(
        session_dir, session_id, task,
        all_events, code, report, success, summary
    )
    emit(make_event(EV.SESSION_SAVE,
        f"[SESSION] History saved → {session_file}",
        session_id=session_id, session_file=str(session_file)))

    # ── Package cleanup ────────────────────────────────────────
    if not keep_packages:
        cleanup_session_packages(emit)
    elif SESSION_INSTALLED:
        emit(make_event(EV.PKG_INSTALL, f"[PKG] Keeping installed: {SESSION_INSTALLED}"))

    return summary


def my_callback(ev: AgentEvent):
    color = ANSI.get(ev.color, "")
    reset = ANSI_RESET

    if ev.event == EV.LLM_STREAM_START:
        # Switch terminal to streaming color (e.g. white)
        print(f"{ANSI[Color.WHITE]}", end="", flush=True)

    elif ev.event == EV.LLM_STREAM:
        # Raw token — no newline, no color switch (already set by START)
        print(f"{ev.text}", end="", flush=True)

    elif ev.event == EV.LLM_STREAM_END:
        # Stream finished — reset color
        print(f"{reset}", end="", flush=True)

    elif ev.event in (EV.SUCCESS, EV.FAILURE):
        border = "=" * 60
        print(f"\n{color}{border}\n  {ev.text}\n{border}{reset}\n")

    elif ev.event == EV.PKG_INSTALL:
        print(f"{color}[PKG] {ev.text}{reset}")

    elif ev.event == EV.CODE:
        code = ev.data.get("code", "")
        version = ev.data.get("version", "?")
        border = "-" * 50
        print(f"{color}\n[CODE v{version}]\n{border}\n{code}\n{border}{reset}")

    else:
        print(f"{color}{ev.text}{reset}")
    
agent("you create a next.js project name will be mama in E:\project\shop_m ", callback=my_callback)





