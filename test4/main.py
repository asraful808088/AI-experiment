"""
Unified Hierarchical AI Router + Next.js (k9) + Nuxt.js (k10) Agentic Creator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ARCHITECTURE
────────────
User Prompt (WebSocket)
    │
    ▼
LAYER 1 — Main Router  (domain: coding / generation / analysis / conversation)
    │
    ▼
LAYER 2 — Sub-Router   (coding → nextjs | nuxtjs | react | python | generic)
    │                           ↑ k9              ↑ k10
    ▼
LAYER 3 — Worker
    • nextjs  → nextjs_start_sync()   REAL pipeline  (k9)
    • nuxtjs  → nuxtjs_start_sync()   REAL pipeline  (k10)
    • others  → streaming Ollama LLM

WebSocket /ws/chat
──────────────────
Client → {"prompt": "..."}
       → {"type": "ask_for_path",       "prompt": "C:\\...", "data": {...}}
       → {"type": "upgrade_permission",                      "data": {...}}

Server → {"type": "routing",  "data": {domain, subdomain, agent, ...}}
       → {"type": "token",    "data": "<streamed text>"}
       → {"type": "ask_path", "data": {task_id, config, ...}}
       → {"type": "upgrader", "data": {installed, latest, ...}}
       → {"type": "warning",  "data": "<message>"}
       → {"type": "error",    "data": "<message>"}
       → {"type": "done",     "data": ""}
"""

# ─────────────────────────────────────────────────────────────────────────────
# stdlib + third-party
# ─────────────────────────────────────────────────────────────────────────────
import os, re, sys, json, time, shutil, signal
import socket as _socket
import subprocess, threading, urllib.request, urllib.error
import webbrowser, uuid, asyncio, queue
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any, AsyncIterator
from enum import Enum

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Windows stdout fix
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
#  SHARED UTILITIES  (used by both k9 and k10)
# ═════════════════════════════════════════════════════════════════════════════

PrintCallback = Callable[[str, str, str, dict], None]

def _noop_cb(text="", color="white", msg_type="normal", data=None):
    pass

_HERE          = os.path.dirname(os.path.abspath(__file__))
IS_WIN         = sys.platform == "win32"
KEEP_ALIVE     = -1
DEV_PORT       = 3000
LAUNCH_TIMEOUT = 120
HEALTH_POLL    = 2
MAX_FIX_ROUNDS = 3

# Session store  { task_id: payload }  — shared between k9 and k10
user_csh: Dict[str, Dict[str, Any]] = {}

try:
    from project_fixer import ProjectFixer, fix_project
    HAS_FIXER = True
except ImportError:
    HAS_FIXER = False

try:
    import requests as _requests
    def _http_get(url, timeout=10):
        r = _requests.get(url, timeout=timeout)
        return r.status_code, r.text
except ImportError:
    def _http_get(url, timeout=10):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except Exception:
            return 0, ""

try:
    import ollama as _ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False


# ─── Path extraction ──────────────────────────────────────────────────────────
_REGEX_PATTERNS = [
    r'[A-Za-z]:\\(?:[^\\\s<>|:"]+\\)*[^\\\s<>|:"]*',
    r'[A-Za-z]:\\[^\s]+',
    r'(?:in|at|inside)\s+([A-Za-z]:\\[^\s]+)',
    r'\b(?:in|inside|at|into|under|within)\s+([A-Za-z]:[\\\/][^\s,;\"\']+)',
    r'\b(?:in|inside|at|into|under|within)\s+(\/[^\s,;\"\']+)',
    r'\b(?:in|inside|at|into|under|within)\s+(~[^\s,;\"\']*)',
    r'(?:path|dir(?:ectory)?|folder)\s*[=:]\s*([^\s,;\"\']+)',
]
_NL_PATH_PATTERNS = [
    (r'\b(?:in|on|at|inside)\s+(?:the\s+)?([A-Za-z])\s+drive\b',
     lambda m: m.group(1).upper() + ":\\"),
    (r'\b([A-Za-z]):[\\\/]',
     lambda m: m.group(1).upper() + ":\\"),
]
_TYPO_MAP = {
    'proect': 'project', 'Proect': 'Project',
    'foldr':  'folder',  'deskt0p': 'desktop',
}
_PATH_AI_PROMPT = """Extract ONLY the file path from the text.
Rules:
- Output ONLY the path, nothing else
- Fix typos (proect -> project)
- Convert natural language to path (e.g. "project in c drive" -> C:\\project)
- Windows format: drive:\\folder\\subfolder  |  Unix: /absolute/path  |  ~/relative
- If only a drive letter is mentioned (e.g. "c drive"), return just "C:\\"
- Never add explanation or extra text"""

def _fix_path_typos(path):
    for w, r in _TYPO_MAP.items():
        path = path.replace(w, r)
    return path

def _clean_extracted_path(path):
    path = path.strip()
    path = re.sub(r'[>|]+$', '', path)
    path = path.strip('"').strip("'")
    return _fix_path_typos(path)

def _regex_extract_path(text):
    for pattern, builder in _NL_PATH_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return builder(m)
    for pattern in _REGEX_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            path = matches[0]
            if isinstance(path, tuple):
                path = next((p for p in path if p), "")
            path = _clean_extracted_path(path)
            if re.match(r'[A-Za-z]:\\', path) or path.startswith('/') or path.startswith('~'):
                return path
    return None

def _ai_extract_path(text):
    try:
        response = _ollama.chat(
            model="qwen2.5-coder:7b", keep_alive=KEEP_ALIVE,
            messages=[
                {"role": "system", "content": _PATH_AI_PROMPT},
                {"role": "user",   "content": text},
            ],
        )
        raw  = response["message"]["content"].strip()
        path = _clean_extracted_path(raw)
        if re.match(r'[A-Za-z]:\\', path) or path.startswith('/') or path.startswith('~'):
            return path
    except Exception:
        pass
    return None

def extract_path_from_text(text):
    path = _regex_extract_path(text)
    if path: return path, "regex"
    path = _ai_extract_path(text)
    if path: return path, "ai"
    return None, "failed"

_PATH_STRIP_PATTERNS = [
    r'\b(?:in|on|at|inside)\s+(?:the\s+)?[A-Za-z]\s+(?:drive|disk|partition)\b',
    r'\b(?:in|at|inside|into|under|within)\s+[A-Za-z]:[\\\/][^\s,;]*',
    r'\b(?:in|at|inside|into|under|within)\s+\/[^\s,;]*',
    r'\b(?:in|at|inside|into|under|within)\s+~[^\s,;]*',
    r'[A-Za-z]:\\(?:[^\\\s<>|:"]+\\)*[^\\\s<>|:"]*',
    r'[A-Za-z]:\\[^\s]+',
    r'(?<!\w)\/[^\s,;\"\']+',
    r'(?<!\w)~[^\s,;\"\']+',
    r'(?:path|dir(?:ectory)?|folder)\s*[=:]\s*[^\s,;\"\']+',
]

def strip_path_from_prompt(text):
    result = text
    for pattern in _PATH_STRIP_PATTERNS:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    result = re.sub(r'\s{2,}', ' ', result).strip()
    return result if result else text


# ─── Environment helpers ──────────────────────────────────────────────────────
def _run_silent(cmd, cwd=None):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()

def _which(name): return shutil.which(name)

def _ver(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        return (r.stdout + r.stderr).strip().split("\n")[0]
    except Exception:
        return "?"

def _add_to_path(new_dir):
    path_var = os.environ.get("PATH", "")
    if new_dir not in path_var.split(os.pathsep):
        os.environ["PATH"] = new_dir + os.pathsep + path_var

def fix_path_env(new_dir):
    if not new_dir or not os.path.isdir(new_dir): return
    _add_to_path(new_dir)
    if IS_WIN:
        try:
            import winreg, ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0,
                                 winreg.KEY_READ | winreg.KEY_WRITE)
            try: current, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError: current = ""
            if new_dir.lower() not in current.lower():
                new_val = current.rstrip(";") + ";" + new_dir if current else new_dir
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_val)
                ctypes.windll.user32.SendMessageTimeoutW(
                    0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
        except Exception: pass
    else:
        for rc in ["~/.bashrc", "~/.zshrc", "~/.profile"]:
            rc_path = os.path.expanduser(rc)
            if not os.path.exists(rc_path): continue
            try:
                content = open(rc_path).read()
                if new_dir not in content:
                    with open(rc_path, "a") as f:
                        f.write(f'\nexport PATH="{new_dir}:$PATH"\n')
            except Exception: pass

def fix_pnpm_path():
    ok, out, _ = _run_silent("pnpm bin -g")
    if ok and out and out not in os.environ.get("PATH", ""):
        fix_path_env(out.strip())

def ensure_node(cb):
    if _which("node"): return True
    cb("⚠  Node.js not found — attempting install...", "yellow", "warning", {})
    if IS_WIN:
        ok, _, _ = _run_silent(
            "winget install OpenJS.NodeJS.LTS --silent "
            "--accept-source-agreements --accept-package-agreements")
        if ok: return True
        webbrowser.open("https://nodejs.org/en/download")
    elif sys.platform == "darwin":
        ok, _, _ = _run_silent("brew install node"); return ok
    else:
        ok, _, _ = _run_silent(
            "curl -fsSL https://deb.nodesource.com/setup_lts.x | "
            "sudo -E bash - && sudo apt-get install -y nodejs")
        return ok
    return False

def ensure_pm_global(pm, cb):
    if _which(pm): return True
    cmds = {"pnpm": "npm install --global pnpm",
            "yarn": "npm install --global yarn",
            "bun":  "npm install --global bun"}
    cmd = cmds.get(pm)
    if not cmd: return False
    ok, _, _ = _run_silent(cmd)
    if ok and pm == "pnpm": fix_pnpm_path()
    return ok

def ensure_pip_packages(packages, cb):
    for pkg in packages:
        ok, _, _ = _run_silent(f"python -c \"import {pkg.replace('-','_')}\"")
        if ok: continue
        ok2, _, _ = _run_silent(f"pip install {pkg} --break-system-packages")
        if not ok2: _run_silent(f"pip install {pkg}")

def bootstrap_environment(pm, cb):
    """Returns True/False instead of sys.exit() so FastAPI keeps running."""
    if not ensure_node(cb):
        cb("❌ Cannot continue without Node.js.", "red", "error", {})
        return False
    if _which("pnpm"): fix_pnpm_path()
    ensure_pm_global(pm, cb)
    return True

def print_env_health(cb):
    checks = [("node","node --version"),("npm","npm --version"),
              ("npx","npx --version"),("pnpm","pnpm --version"),
              ("yarn","yarn --version"),("bun","bun --version")]
    cb("🖥  Environment Health", "cyan", "normal", {})
    for name, cmd in checks:
        found = bool(_which(name))
        mark  = "✓" if found else "✗"
        color = "green" if found else "red"
        ver   = _ver(cmd) if found else "not found"
        cb(f"  {mark} {name:<10s} {ver}", color, "normal", {})


# ─── Error classification ─────────────────────────────────────────────────────
_PNPM_SOFT = [r"ERR_PNPM_IGNORED_BUILD_SCRIPTS", r"ERR_PNPM_NO_GLOBAL_BIN_DIR"]

def _is_pnpm_soft(text): return any(re.search(p, text) for p in _PNPM_SOFT)

def _verify_installed(project_path, lib):
    return os.path.exists(
        os.path.join(project_path, "node_modules", *lib.split("/"), "package.json"))

class EK:
    SOFT_SUCCESS       = "soft_success"
    UNKNOWN_SUBCOMMAND = "unknown_subcommand"
    NETWORK            = "network_error"
    PERMISSION         = "permission_error"
    VERSION_CONFLICT   = "version_conflict"
    NOT_FOUND          = "not_found"
    GENERIC            = "generic"

def classify_error(stderr, stdout="", project_path="", lib=""):
    if _is_pnpm_soft(stderr + stdout):
        if not lib or _verify_installed(project_path, lib): return EK.SOFT_SUCCESS
    c = (stderr + stdout).lower()
    if any(k in c for k in ['command "dlx" not found',"unknown command",
                             "unknown subcommand","is not a yarn command"]):
        return EK.UNKNOWN_SUBCOMMAND
    if any(k in c for k in ["enotfound","etimedout","fetch failed","econnrefused",
                             "network error","socket hang up"]):
        return EK.NETWORK
    if any(k in c for k in ["eacces","permission denied","access denied"]):
        return EK.PERMISSION
    if any(k in c for k in ["peer dep","incompatible","conflict"]):
        return EK.VERSION_CONFLICT
    if any(k in c for k in ["404","not found","no matching version","e404"]):
        return EK.NOT_FOUND
    return EK.GENERIC


# ─── Package manager helpers ──────────────────────────────────────────────────
PM_ALIASES = {"npm":"npm","pnpm":"pnpm","yarn":"yarn","bun":"bun"}
DEFAULT_PM  = "npm"
PM_INSTALL  = {"npm":"npm install","pnpm":"pnpm add","yarn":"yarn add","bun":"bun add"}
PM_INSTALL_G= {"npm":"npm install --global","pnpm":"pnpm add -g",
               "yarn":"yarn global add","bun":"bun add --global"}
PM_RUN_DEV  = {"npm":"npm run dev","pnpm":"pnpm dev","yarn":"yarn dev","bun":"bun dev"}

def detect_pm(req):
    lowered = req.lower()
    for alias, name in PM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered): return name
    return DEFAULT_PM

def detect_yarn_version(cache):
    MK_ = _machine_key()
    cached = cache.get("yarn_version", {}).get(MK_)
    if cached: return cached
    try:
        r = subprocess.run("yarn --version", shell=True, capture_output=True, text=True)
        major = int(r.stdout.strip().split(".")[0]) if r.stdout.strip() and r.stdout.strip()[0].isdigit() else 1
        ver = "v4" if major >= 2 else "v1"
        cache.setdefault("yarn_version", {})[MK_] = ver
        return ver
    except Exception:
        return "v1"


# ─── Cache helpers ────────────────────────────────────────────────────────────
def _machine_key():
    node_ver = "unknown"
    try:
        r = subprocess.run("node --version", shell=True, capture_output=True, text=True)
        node_ver = r.stdout.strip().lstrip("v").split(".")[0]
    except Exception:
        pass
    return f"{sys.platform}__node{node_ver}"

MK = _machine_key()

def _load_cache(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"_v": 13, "pm_create_cmd": {}, "bad_strategies": {},
            "yarn_version": {}, "project_journal": []}

def _save_cache(c, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def cache_get_create(c, pm):       return c.get("pm_create_cmd", {}).get(MK, {}).get(pm)
def cache_set_create(c, pm, cmd, path):
    c.setdefault("pm_create_cmd", {}).setdefault(MK, {})[pm] = cmd
    _save_cache(c, path)
def cache_clear_create(c, pm, cb, path):
    mk = c.get("pm_create_cmd", {}).get(MK, {})
    if pm in mk:
        del mk[pm]; _save_cache(c, path)
    cb(f"🗑️  Cleared broken cached create-cmd for {pm}", "yellow", "warning", {})
def cache_is_bad(c, key):          return key in c.get("bad_strategies", {}).get(MK, [])
def cache_mark_bad(c, key, path):
    bads = c.setdefault("bad_strategies", {}).setdefault(MK, [])
    if key not in bads:
        bads.append(key); _save_cache(c, path)
def cache_reset_strategies(c, pm, cb, path):
    mk = c.get("bad_strategies", {}).get(MK, [])
    c["bad_strategies"][MK] = [k for k in mk if pm not in k]
    _save_cache(c, path)
    cb(f"🗑️  Reset bad strategies for {pm}", "yellow", "warning", {})
def cache_full_reset(c, cb, path):
    for key in ["pm_create_cmd", "bad_strategies"]:
        if MK in c.get(key, {}): del c[key][MK]
    _save_cache(c, path)
    cb("♻️  Full cache reset for this machine", "yellow", "warning", {})
def cache_add_journal(c, entry, path):
    c.setdefault("project_journal", []).append(entry); _save_cache(c, path)


# ─── Runner (shared) ──────────────────────────────────────────────────────────
@dataclass
class Intent:
    project_name: str
    pm:           str
    flags:        list
    libraries:    list
    global_tools: list
    install_dir:  str

    @property
    def project_path(self): return os.path.join(self.install_dir, self.project_name)
    def flag_str(self):     return " ".join(self.flags)

def _run_proc(cmd, cwd):
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, "", str(e)

class Runner:
    def __init__(self, intent, cache, cb, cache_path):
        self.intent     = intent
        self.cache      = cache
        self.cache_path = cache_path
        self.log        = []
        self._cb        = cb
        self._fixer     = None

    def _get_fixer(self):
        if not HAS_FIXER: return None
        if self._fixer is None and os.path.isdir(self.intent.project_path):
            def _fcb(text, color="white"): self._cb(text, color, "normal", {})
            self._fixer = ProjectFixer(self.intent.project_path, cb=_fcb)
        return self._fixer

    def _run_fixer(self, error_output=""):
        fixer = self._get_fixer()
        if fixer is None: return False
        self._cb("🔧 Running auto-fixer...", "yellow", "warning", {})
        result = fixer.fix_all(error_output=error_output)
        return result.get("success", False)

    def run_strategies(self, strategies):
        """Try a list of (name, cmd) strategies with cache + fixer logic."""
        for fix_round in range(MAX_FIX_ROUNDS + 1):
            strats = [s for s in strategies() if not cache_is_bad(self.cache, f"{MK}__{s[0]}")]
            if not strats:
                self._cb("No strategies left.", "red", "error", {}); break
            for name, cmd in strats:
                self._cb(f"  ▶ [{fix_round}] Strategy '{name}': {cmd}", "cyan", "normal", {})
                ok, out, err = _run_proc(cmd, self.intent.install_dir)
                ek = classify_error(err, out, self.intent.project_path)
                if ok or ek == EK.SOFT_SUCCESS:
                    if ek == EK.SOFT_SUCCESS:
                        self._cb("  ⚠  Non-zero exit but project created", "yellow", "warning", {})
                    self._cb(f"  ✓ Created with '{name}'", "green", "normal", {})
                    self.log.append({"strategy": name, "cmd": cmd, "ok": True})
                    return True
                self._cb(f"  ✗ Failed ({ek})", "red", "error", {})
                self._cb(f"  {(err+out).strip()[:250]}", "red", "error", {})
                if name.startswith("cached_") and ek in (EK.UNKNOWN_SUBCOMMAND, EK.GENERIC):
                    cache_clear_create(self.cache, self.intent.pm, self._cb, self.cache_path)
                cache_mark_bad(self.cache, f"{MK}__{name}", self.cache_path)
                self.log.append({"strategy": name, "cmd": cmd, "ok": False, "ek": ek})
                if ek == EK.NETWORK:
                    self._cb("  ⏳ Network error — retrying in 5s...", "yellow", "warning", {})
                    time.sleep(5)
                    ok2, _, _ = _run_proc(cmd, self.intent.install_dir)
                    if ok2: return True
            if fix_round == 0:
                cache_reset_strategies(self.cache, self.intent.pm, self._cb, self.cache_path)
            elif fix_round == 1:
                cache_clear_create(self.cache, self.intent.pm, self._cb, self.cache_path)
            else:
                cache_full_reset(self.cache, self._cb, self.cache_path)
        self._cb("❌ All strategies exhausted.", "red", "error", {})
        return False

    def run_cmd(self, cmd, *, label="command", cwd=None, lib=""):
        wd = cwd or self.intent.project_path
        for attempt in range(1, MAX_FIX_ROUNDS + 2):
            self._cb(f"  ▶ {label} (attempt {attempt}): {cmd}", "cyan", "normal", {})
            ok, out, err = _run_proc(cmd, wd)
            ek = classify_error(err, out, wd, lib)
            if ok or ek == EK.SOFT_SUCCESS:
                if ek == EK.SOFT_SUCCESS:
                    self._cb("  ⚠  Non-fatal warning (checking node_modules...)", "yellow", "warning", {})
                    if lib and not _verify_installed(wd, lib):
                        self._cb(f"  ✗ {lib} missing from node_modules", "red", "error", {})
                    else:
                        self._cb(f"  ✓ {label} done", "green", "normal", {}); return True, out
                else:
                    self._cb(f"  ✓ {label} done", "green", "normal", {}); return True, out
            if attempt > MAX_FIX_ROUNDS: break
            fixed = self._run_fixer(error_output=err + out)
            if not fixed:
                self._cb("  Fixer had nothing to apply", "yellow", "warning", {})
                if ek == EK.NETWORK:
                    time.sleep(5 * attempt); continue
                break
        if lib and _verify_installed(wd, lib):
            self._cb(f"  ⚠  {lib} found in node_modules despite errors", "yellow", "warning", {})
            return True, ""
        self._cb(f"  ✗ {label} permanently failed", "red", "error", {})
        self._cb(f"  {(err+out).strip()[:250]}", "red", "error", {})
        return False, err + out


# ─── Dev server launch ────────────────────────────────────────────────────────
def _find_free_port(preferred=3000):
    for port in range(preferred, preferred + 20):
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.bind(("", port)); return port
        except OSError: continue
    return preferred

def _wait_for_server(url, timeout, cb):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = _http_get(url, timeout=3)
            if status == 200:
                cb(f"  ✓ Server responded 200 at {url}", "green", "normal", {}); return True
        except Exception: pass
        time.sleep(HEALTH_POLL)
    return False

def launch_and_verify(intent, cb):
    port    = _find_free_port(DEV_PORT)
    dev_cmd = PM_RUN_DEV[intent.pm]
    url     = f"http://localhost:{port}"
    cb(f"🚀 Launching dev server on port {port}...", "cyan", "normal", {})
    env = os.environ.copy(); env["PORT"] = str(port)
    try:
        proc = subprocess.Popen(dev_cmd, shell=True, cwd=intent.project_path, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except Exception as e:
        cb(f"  ✗ Could not start dev server: {e}", "red", "error", {}); return False
    server_output = []
    def _capture(stream):
        for line in stream: server_output.append(line.rstrip())
    threading.Thread(target=_capture, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=_capture, args=(proc.stderr,), daemon=True).start()
    healthy = _wait_for_server(url, LAUNCH_TIMEOUT, cb)
    try:
        if IS_WIN: subprocess.run(f"taskkill /PID {proc.pid} /T /F", shell=True, capture_output=True)
        else: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try: proc.terminate()
        except Exception: pass
    proc.wait(timeout=10)
    if healthy:
        cb(f"✅ Landing page verified at {url}", "green", "normal", {})
    else:
        cb(f"❌ Dev server did not respond in {LAUNCH_TIMEOUT}s", "red", "error", {})
        if HAS_FIXER and os.path.isdir(intent.project_path):
            err_text = "\n".join(server_output[-40:])
            cb("🔧 Running fixer on startup failure...", "yellow", "warning", {})
            def _fix_cb(text, color="white"): cb(text, color, "normal", {})
            fix_project(intent.project_path, error_output=err_text, cb=_fix_cb)
        for line in server_output[-20:]:
            cb(f"  {line}", "red", "error", {})
    return healthy


# ─── Library helpers ──────────────────────────────────────────────────────────
def _npm_exists(name):
    try:
        status, _ = _http_get(f"https://registry.npmjs.org/{name}/latest", timeout=6)
        return status == 200
    except Exception:
        return False

def validate_libraries(raw_libs, cb):
    if not raw_libs: return [], []
    cb(f"🔎 Validating {len(raw_libs)} libraries...", "cyan", "normal", {})
    ok_libs, failed = [], []
    for lib in raw_libs:
        if _npm_exists(lib):
            cb(f"  ✓ {lib}", "green", "normal", {}); ok_libs.append(lib)
        else:
            cb(f"  ⚠  {lib} not found on npm", "yellow", "warning", {})
            failed.append({"original": lib, "reason": "not on npm registry"})
    return ok_libs, failed

def install_libraries(intent, libs, cache, cb, cache_path):
    if not libs: return
    base   = PM_INSTALL[intent.pm]
    runner = Runner(intent, cache, cb, cache_path)
    cb(f"📦 Installing {len(libs)} librar{'y' if len(libs)==1 else 'ies'}...", "cyan", "normal", {})
    for lib in libs:
        ok, _ = runner.run_cmd(f"{base} {lib}", label=f"install {lib}", lib=lib)
        if not ok: cb(f"  ✗ {lib} could not be installed", "red", "error", {})

def install_global_tools(intent, tools, cache, cb, cache_path):
    if not tools: return
    runner = Runner(intent, cache, cb, cache_path)
    for tool in tools:
        runner.run_cmd(f"{PM_INSTALL_G[intent.pm]} {tool}",
                       label=f"global {tool}", cwd=os.getcwd())
        fix_pnpm_path()


# ─── Session helpers ──────────────────────────────────────────────────────────
def _generate_task_id():       return f"task_{uuid.uuid4().hex[:12]}"
def _store_session(tid, p):    user_csh[tid] = p
def _get_session(tid):         return user_csh.get(tid)
def _clear_session(tid):       user_csh.pop(tid, None)
def _sanitize_name(name):
    name = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "my-app"


# ═════════════════════════════════════════════════════════════════════════════
#  K9 — Next.js Agentic Creator
# ═════════════════════════════════════════════════════════════════════════════

K9_MODEL      = "qwen2.5-coder:7b"
K9_CACHE_PATH = os.path.join(_HERE, "nextjs_creator_cache_v13.json")

_NEXTJS_PM_CREATE = {
    "npm":  "npx create-next-app@latest",
    "pnpm": "pnpm dlx create-next-app@latest",
    "yarn": "yarn create next-app",
    "bun":  "bunx create-next-app@latest",
}
_NEXTJS_YARN4_CREATE = "yarn dlx create-next-app@latest"

_NEXTJS_PARSE_SYSTEM = """You are a Next.js CLI assistant.
Return ONLY valid JSON — no markdown, no explanation, no code fences.
Schema:
{
  "project_name": "kebab-case-name",
  "flags": ["--flag1"],
  "libraries": ["lib1"],
  "global_tools": [],
  "python_packages": []
}
PATH RULE: All path/directory references have already been removed.
FLAG RULES: only include flags the user explicitly mentioned.
  --typescript OR --javascript
  --tailwind OR --no-tailwind
  --eslint OR --no-eslint
  --app OR --no-app
  --turbopack OR --no-turbopack
  --src-dir OR --no-src-dir
  --no-git (only if user says "no git")
ALWAYS add --yes to flags."""

def _nextjs_parse(req):
    if not HAS_OLLAMA:
        name = re.sub(r"[^a-z0-9-]", "-", req.lower().split()[0])[:30] or "my-next-app"
        return {"project_name": name, "flags": ["--yes"],
                "libraries": [], "global_tools": [], "python_packages": []}
    r = _ollama.chat(
        model=K9_MODEL,
        messages=[{"role":"system","content":_NEXTJS_PARSE_SYSTEM},
                  {"role":"user","content":req}],
        keep_alive=KEEP_ALIVE,
    )
    raw = r["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$",           "", raw, flags=re.MULTILINE)
    return json.loads(raw.strip())

def _nextjs_strategies(intent, cache):
    result = []
    pm = intent.pm
    def add(name, base):
        if not cache_is_bad(cache, f"{MK}__{name}"):
            result.append((name, f"{base} {intent.project_name} {intent.flag_str()}".strip()))
    cached = cache_get_create(cache, pm)
    if cached: add(f"cached_{pm}", cached)
    if pm == "yarn":
        ver     = detect_yarn_version(cache)
        default = _NEXTJS_YARN4_CREATE if ver == "v4" else _NEXTJS_PM_CREATE["yarn"]
        alt     = _NEXTJS_PM_CREATE["yarn"] if ver == "v4" else _NEXTJS_YARN4_CREATE
        if default != cached: add(f"default_{pm}", default)
        add("yarn_alt", alt)
    else:
        db = _NEXTJS_PM_CREATE.get(pm, _NEXTJS_PM_CREATE["npm"])
        if db != cached: add(f"default_{pm}", db)
    add("npx_fallback", "npx create-next-app@latest")
    add("npx_nolast",   "npx create-next-app")
    return result

def get_latest_nextjs():
    try:
        status, text = _http_get("https://registry.npmjs.org/next/latest", timeout=8)
        if status == 200: return json.loads(text).get("version", "unknown")
    except Exception: pass
    return "unknown"

def get_installed_nextjs(project_path):
    try:
        p = os.path.join(project_path, "node_modules", "next", "package.json")
        if os.path.exists(p):
            with open(p) as f: return json.load(f).get("version", "unknown")
    except Exception: pass
    return "unknown"

def _nextjs_check_upgrade(intent, cache, cb, task_id=""):
    cb("🔄 Checking Next.js version...", "cyan", "normal", {})
    installed = get_installed_nextjs(intent.project_path)
    latest    = get_latest_nextjs()
    cb(f"  Installed: {installed}  Latest: {latest}", "white", "normal", {})
    upgrade_data = {
        "task_id":       task_id,
        "installed":     installed,
        "latest":        latest,
        "project_path":  intent.project_path,
        "project_name":  intent.project_name,
        "pm":            intent.pm,
        "needs_upgrade": installed not in ("unknown", latest),
        "framework":     "nextjs",
    }
    cb(
        f"Upgrade available: {installed} → {latest}" if upgrade_data["needs_upgrade"]
        else f"Next.js is up to date ({installed})",
        "cyan", "upgrader", upgrade_data,
    )

def _nextjs_do_upgrade(intent, cache, cb, task_id=""):
    runner = Runner(intent, cache, cb, K9_CACHE_PATH)
    upgrade_cmds = {
        "npm":  "npm install next@latest react@latest react-dom@latest",
        "pnpm": "pnpm add next@latest react@latest react-dom@latest",
        "yarn": "yarn add next@latest react@latest react-dom@latest",
        "bun":  "bun add next@latest react@latest react-dom@latest",
    }
    ok, _ = runner.run_cmd(upgrade_cmds[intent.pm], label="upgrade next.js")
    cb("✅ Next.js upgrade complete" if ok else "❌ Next.js upgrade failed",
       "green" if ok else "red", "upgrader",
       {"task_id": task_id, "project_path": intent.project_path,
        "pm": intent.pm, "success": ok, "framework": "nextjs"})

def _nextjs_run_pipeline(clean_request, install_dir, config, cache, cb, task_id):
    pm           = detect_pm(clean_request)
    project_name = _sanitize_name(config.get("project_name", "my-next-app"))
    flags        = config.get("flags", [])
    raw_libs     = config.get("libraries", [])
    raw_globals  = config.get("global_tools", [])
    python_pkgs  = config.get("python_packages", [])
    if "--yes" not in flags: flags.append("--yes")

    good_libs, bad_libs = validate_libraries(raw_libs, cb)
    intent = Intent(project_name=project_name, pm=pm, flags=flags,
                    libraries=good_libs, global_tools=raw_globals,
                    install_dir=install_dir)

    cb("📋 Plan (Next.js)", "green", "normal", {})
    cb(f"  PM          : {intent.pm}",          "cyan", "normal", {})
    cb(f"  Project     : {intent.project_name}", "cyan", "normal", {})
    cb(f"  Flags       : {intent.flag_str()}",   "cyan", "normal", {})
    cb(f"  Install dir : {intent.install_dir}",  "cyan", "normal", {})
    cb(f"  Libraries   : {', '.join(good_libs) or 'none'}", "cyan", "normal", {})
    if bad_libs:
        cb(f"  Skipped     : {', '.join(b['original'] for b in bad_libs)}",
           "yellow", "warning", {})

    runner = Runner(intent, cache, cb, K9_CACHE_PATH)
    ok = runner.run_strategies(lambda: _nextjs_strategies(intent, cache))
    if not ok:
        cache_add_journal(cache, {"machine": MK, "pm": pm, "project": project_name,
                                  "worked": False, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "framework": "nextjs"}, K9_CACHE_PATH)
        return False, None

    install_libraries(intent, good_libs, cache, cb, K9_CACHE_PATH)
    install_global_tools(intent, raw_globals, cache, cb, K9_CACHE_PATH)
    ensure_pip_packages(python_pkgs, cb)

    cb("🔬 Verifying project health...", "cyan", "normal", {})
    healthy = launch_and_verify(intent, cb)

    cache_add_journal(cache, {"machine": MK, "pm": pm, "project": project_name,
                              "flags": flags, "worked": True,
                              "landing_healthy": healthy,
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "framework": "nextjs"}, K9_CACHE_PATH)
    cb(f"🎉 Next.js '{intent.project_name}' is ready!", "green", "normal", {})
    cb(f"  cd {intent.project_path}", "white", "normal", {})
    cb(f"  {PM_RUN_DEV[pm]}", "white", "normal", {})
    if bad_libs:
        cb(f"Unresolved libraries: {', '.join(b['original'] for b in bad_libs)}",
           "yellow", "warning", {})
    return healthy, intent


def nextjs_start_sync(user_request, cb, message=None):
    """K9 entry point — called from WebSocket handler thread."""
    if message is None:
        message = {"type": "init", "data": {}}
    msg_type = message.get("type", "init")
    msg_data = message.get("data", {}) or {}
    cache    = _load_cache(K9_CACHE_PATH)

    # upgrade_permission
    if msg_type == "upgrade_permission":
        task_id = msg_data.get("task_id", "")
        intent  = Intent(
            project_name = msg_data.get("project_name", ""),
            pm           = msg_data.get("pm", "npm"),
            flags=[], libraries=[], global_tools=[],
            install_dir  = os.path.dirname(msg_data.get("project_path", os.getcwd())),
        )
        _nextjs_do_upgrade(intent, cache, cb, task_id=task_id)
        if task_id: _clear_session(task_id)
        return True

    # ask_for_path
    if msg_type == "ask_for_path":
        task_id = msg_data.get("task_id", "")
        cb("🔍 Extracting path from reply...", "dim", "normal", {})
        path, method = extract_path_from_text(user_request)
        if not path:
            cb("  Could not extract a path from your reply.", "yellow", "warning", {})
            ask_payload = dict(msg_data)
            ask_payload["task_id"] = task_id or _generate_task_id()
            ask_payload["framework"] = "nextjs"
            _store_session(ask_payload["task_id"], ask_payload)
            cb("📂 Please reply with the full path where you want to create the project.",
               "yellow", "ask_path", ask_payload)
            return False
        cb(f"  ✓ Path found via {method}: {path}", "green", "normal", {})
        install_dir   = os.path.abspath(os.path.expanduser(path.rstrip("/\\")))
        os.makedirs(install_dir, exist_ok=True)
        config        = msg_data.get("config", {})
        clean_request = msg_data.get("user_request", user_request)
        if task_id: _clear_session(task_id)
        healthy, intent = _nextjs_run_pipeline(
            clean_request, install_dir, config, cache, cb, task_id or "")
        if intent: _nextjs_check_upgrade(intent, cache, cb, task_id=task_id or "")
        return healthy

    # init
    task_id = _generate_task_id()
    cb("⚡ Next.js Agentic Creator v13.1 (k9)", "cyan", "normal", {})
    pm = detect_pm(user_request)
    if not bootstrap_environment(pm, cb):
        return False
    print_env_health(cb)

    cb("🔍 Extracting install path...", "dim", "normal", {})
    path, method = extract_path_from_text(user_request)
    install_dir = None
    if path:
        install_dir = os.path.abspath(os.path.expanduser(path.rstrip("/\\")))
        os.makedirs(install_dir, exist_ok=True)
        cb(f"  ✓ Install dir ({method}): {install_dir}", "green", "normal", {})
    else:
        cb("  No path found in prompt.", "yellow", "warning", {})

    clean_request = strip_path_from_prompt(user_request)
    cb(f"  🧹 Cleaned prompt: {clean_request}", "dim", "normal", {})

    cb("🤖 Parsing request...", "dim", "normal", {})
    try:
        config = _nextjs_parse(clean_request)
    except Exception as e:
        cb(f"⚠  AI parse failed ({e}) — using defaults", "yellow", "warning", {})
        config = {"project_name":"my-next-app","flags":["--yes"],
                  "libraries":[],"global_tools":[],"python_packages":[]}

    if not install_dir:
        ask_payload = {"task_id": task_id, "user_request": clean_request,
                       "config": config, "pm": pm, "framework": "nextjs"}
        _store_session(task_id, ask_payload)
        cb("📂 Please reply with the full path where you want to create the project.",
           "yellow", "ask_path", ask_payload)
        return False

    healthy, intent = _nextjs_run_pipeline(clean_request, install_dir, config, cache, cb, task_id)
    if intent: _nextjs_check_upgrade(intent, cache, cb, task_id=task_id)
    return healthy


# ═════════════════════════════════════════════════════════════════════════════
#  K10 — Nuxt.js Agentic Creator
# ═════════════════════════════════════════════════════════════════════════════

K10_MODEL      = "qwen2.5-coder:7b"
K10_CACHE_PATH = os.path.join(_HERE, "nuxtjs_creator_cache_v1.json")

# Nuxt 3 scaffold commands per package manager
_NUXT_PM_CREATE = {
    "npm":  "npx nuxi@latest init",
    "pnpm": "pnpm dlx nuxi@latest init",
    "yarn": "yarn dlx nuxi@latest init",
    "bun":  "bunx nuxi@latest init",
}
# Nuxt dev commands
_NUXT_PM_RUN_DEV = {
    "npm":  "npm run dev",
    "pnpm": "pnpm dev",
    "yarn": "yarn dev",
    "bun":  "bun run dev",
}

_NUXT_PARSE_SYSTEM = """You are a Nuxt.js CLI assistant.
Return ONLY valid JSON — no markdown, no explanation, no code fences.
Schema:
{
  "project_name": "kebab-case-name",
  "flags": [],
  "libraries": ["lib1"],
  "global_tools": [],
  "python_packages": [],
  "modules": ["@nuxtjs/tailwindcss"]
}
PATH RULE: All path/directory references have already been removed.
MODULES: Nuxt modules the user asked for (e.g. @nuxtjs/tailwindcss, @pinia/nuxt,
  @nuxt/image, @nuxtjs/color-mode, @nuxt/content, @nuxtjs/i18n).
  Only include modules the user explicitly requested.
FLAGS: nuxi init flags like --git-init, --package-manager npm|pnpm|yarn|bun.
  Always add --no-install if you want to control install manually (default: omit).
ALWAYS include an empty flags list if none requested."""

def _nuxt_parse(req):
    if not HAS_OLLAMA:
        name = re.sub(r"[^a-z0-9-]", "-", req.lower().split()[0])[:30] or "my-nuxt-app"
        return {"project_name": name, "flags": [], "libraries": [],
                "global_tools": [], "python_packages": [], "modules": []}
    r = _ollama.chat(
        model=K10_MODEL,
        messages=[{"role":"system","content":_NUXT_PARSE_SYSTEM},
                  {"role":"user","content":req}],
        keep_alive=KEEP_ALIVE,
    )
    raw = r["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$",           "", raw, flags=re.MULTILINE)
    return json.loads(raw.strip())

def _nuxt_strategies(intent, cache):
    """Build ordered list of (name, cmd) strategies for nuxi init."""
    result = []
    pm = intent.pm

    def add(name, base_cmd):
        if not cache_is_bad(cache, f"{MK}__{name}"):
            # nuxi init <name> [flags]
            flags = intent.flag_str()
            cmd   = f"{base_cmd} {intent.project_name}" + (f" {flags}" if flags else "")
            result.append((name, cmd.strip()))

    cached_base = cache_get_create(cache, f"nuxt_{pm}")
    if cached_base:
        add(f"cached_{pm}", cached_base)

    default = _NUXT_PM_CREATE.get(pm, _NUXT_PM_CREATE["npm"])
    if default != cached_base:
        add(f"default_{pm}", default)

    # npx fallbacks
    add("npx_latest",  "npx nuxi@latest init")
    add("npx_nolast",  "npx nuxi init")
    return result

def get_latest_nuxt():
    try:
        status, text = _http_get("https://registry.npmjs.org/nuxt/latest", timeout=8)
        if status == 200: return json.loads(text).get("version", "unknown")
    except Exception: pass
    return "unknown"

def get_installed_nuxt(project_path):
    try:
        p = os.path.join(project_path, "node_modules", "nuxt", "package.json")
        if os.path.exists(p):
            with open(p) as f: return json.load(f).get("version", "unknown")
    except Exception: pass
    return "unknown"

def _nuxt_check_upgrade(intent, cache, cb, task_id=""):
    cb("🔄 Checking Nuxt.js version...", "cyan", "normal", {})
    installed = get_installed_nuxt(intent.project_path)
    latest    = get_latest_nuxt()
    cb(f"  Installed: {installed}  Latest: {latest}", "white", "normal", {})
    upgrade_data = {
        "task_id":       task_id,
        "installed":     installed,
        "latest":        latest,
        "project_path":  intent.project_path,
        "project_name":  intent.project_name,
        "pm":            intent.pm,
        "needs_upgrade": installed not in ("unknown", latest),
        "framework":     "nuxtjs",
    }
    cb(
        f"Upgrade available: {installed} → {latest}" if upgrade_data["needs_upgrade"]
        else f"Nuxt is up to date ({installed})",
        "cyan", "upgrader", upgrade_data,
    )

def _nuxt_do_upgrade(intent, cache, cb, task_id=""):
    runner = Runner(intent, cache, cb, K10_CACHE_PATH)
    upgrade_cmds = {
        "npm":  "npm install nuxt@latest",
        "pnpm": "pnpm add nuxt@latest",
        "yarn": "yarn add nuxt@latest",
        "bun":  "bun add nuxt@latest",
    }
    ok, _ = runner.run_cmd(upgrade_cmds[intent.pm], label="upgrade nuxt")
    cb("✅ Nuxt upgrade complete" if ok else "❌ Nuxt upgrade failed",
       "green" if ok else "red", "upgrader",
       {"task_id": task_id, "project_path": intent.project_path,
        "pm": intent.pm, "success": ok, "framework": "nuxtjs"})

def _nuxt_install_modules(intent, modules, cache, cb):
    """Install Nuxt modules and register them in nuxt.config.ts."""
    if not modules: return
    base   = PM_INSTALL[intent.pm]
    runner = Runner(intent, cache, cb, K10_CACHE_PATH)
    cb(f"🧩 Installing {len(modules)} Nuxt module(s)...", "cyan", "normal", {})
    installed_modules = []
    for mod in modules:
        if _npm_exists(mod):
            ok, _ = runner.run_cmd(f"{base} {mod}", label=f"install module {mod}", lib=mod)
            if ok:
                installed_modules.append(mod)
                cb(f"  ✓ {mod} installed", "green", "normal", {})
            else:
                cb(f"  ✗ {mod} install failed", "red", "error", {})
        else:
            cb(f"  ⚠  {mod} not found on npm — skipping", "yellow", "warning", {})

    if not installed_modules: return

    # Patch nuxt.config.ts to register installed modules
    config_path = os.path.join(intent.project_path, "nuxt.config.ts")
    if not os.path.exists(config_path):
        cb("  ⚠  nuxt.config.ts not found — skipping module registration", "yellow", "warning", {})
        return
    try:
        content = open(config_path).read()
        mods_str = ", ".join(f'"{m}"' for m in installed_modules)
        if "modules:" in content:
            # append to existing modules array
            content = re.sub(
                r'(modules\s*:\s*\[)(.*?)(\])',
                lambda m: m.group(1) + m.group(2).rstrip() +
                          (", " if m.group(2).strip() else "") + mods_str + m.group(3),
                content, flags=re.DOTALL
            )
        else:
            # inject modules key before closing brace of defineNuxtConfig
            content = re.sub(
                r'(defineNuxtConfig\s*\(\s*\{)(.*?)(\}\s*\))',
                lambda m: m.group(1) + m.group(2) + f'  modules: [{mods_str}],\n' + m.group(3),
                content, flags=re.DOTALL
            )
        with open(config_path, "w") as f:
            f.write(content)
        cb(f"  ✓ Registered modules in nuxt.config.ts", "green", "normal", {})
    except Exception as e:
        cb(f"  ⚠  Could not patch nuxt.config.ts: {e}", "yellow", "warning", {})

def _nuxt_run_pipeline(clean_request, install_dir, config, cache, cb, task_id):
    pm           = detect_pm(clean_request)
    project_name = _sanitize_name(config.get("project_name", "my-nuxt-app"))
    flags        = config.get("flags", [])
    raw_libs     = config.get("libraries", [])
    raw_globals  = config.get("global_tools", [])
    python_pkgs  = config.get("python_packages", [])
    modules      = config.get("modules", [])

    good_libs, bad_libs = validate_libraries(raw_libs, cb)

    # For Nuxt, we don't pass flags to the Intent flag_str for create — nuxi
    # handles interactivity differently. We pass them separately.
    intent = Intent(project_name=project_name, pm=pm, flags=flags,
                    libraries=good_libs, global_tools=raw_globals,
                    install_dir=install_dir)
    # Override dev command for Nuxt
    intent._dev_cmd = _NUXT_PM_RUN_DEV.get(pm, "npm run dev")

    cb("📋 Plan (Nuxt.js)", "green", "normal", {})
    cb(f"  PM          : {intent.pm}",          "cyan", "normal", {})
    cb(f"  Project     : {intent.project_name}", "cyan", "normal", {})
    cb(f"  Modules     : {', '.join(modules) or 'none'}", "cyan", "normal", {})
    cb(f"  Install dir : {intent.install_dir}",  "cyan", "normal", {})
    cb(f"  Libraries   : {', '.join(good_libs) or 'none'}", "cyan", "normal", {})
    if bad_libs:
        cb(f"  Skipped     : {', '.join(b['original'] for b in bad_libs)}",
           "yellow", "warning", {})

    runner = Runner(intent, cache, cb, K10_CACHE_PATH)
    ok = runner.run_strategies(lambda: _nuxt_strategies(intent, cache))
    if not ok:
        cache_add_journal(cache, {"machine": MK, "pm": pm, "project": project_name,
                                  "worked": False, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "framework": "nuxtjs"}, K10_CACHE_PATH)
        return False, None

    # Install dependencies (nuxi init --no-install skips them; run install now)
    cb(f"📦 Running {PM_INSTALL[pm]}...", "cyan", "normal", {})
    runner.run_cmd(PM_INSTALL[pm], label="install deps")

    # Install Nuxt modules
    _nuxt_install_modules(intent, modules, cache, cb)

    # Install extra npm libraries
    install_libraries(intent, good_libs, cache, cb, K10_CACHE_PATH)
    install_global_tools(intent, raw_globals, cache, cb, K10_CACHE_PATH)
    ensure_pip_packages(python_pkgs, cb)

    # Verify dev server
    cb("🔬 Verifying project health...", "cyan", "normal", {})
    # Temporarily patch PM_RUN_DEV lookup so launch_and_verify uses correct cmd
    _orig = PM_RUN_DEV.get(pm)
    PM_RUN_DEV[pm] = _NUXT_PM_RUN_DEV.get(pm, _orig)
    healthy = launch_and_verify(intent, cb)
    PM_RUN_DEV[pm] = _orig   # restore

    cache_add_journal(cache, {"machine": MK, "pm": pm, "project": project_name,
                              "flags": flags, "worked": True,
                              "landing_healthy": healthy,
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "framework": "nuxtjs"}, K10_CACHE_PATH)
    cb(f"🎉 Nuxt.js '{intent.project_name}' is ready!", "green", "normal", {})
    cb(f"  cd {intent.project_path}", "white", "normal", {})
    cb(f"  {_NUXT_PM_RUN_DEV.get(pm, 'npm run dev')}", "white", "normal", {})
    if bad_libs:
        cb(f"Unresolved libraries: {', '.join(b['original'] for b in bad_libs)}",
           "yellow", "warning", {})
    return healthy, intent


def nuxtjs_start_sync(user_request, cb, message=None):
    """K10 entry point — called from WebSocket handler thread."""
    if message is None:
        message = {"type": "init", "data": {}}
    msg_type = message.get("type", "init")
    msg_data = message.get("data", {}) or {}
    cache    = _load_cache(K10_CACHE_PATH)

    # upgrade_permission
    if msg_type == "upgrade_permission":
        task_id = msg_data.get("task_id", "")
        intent  = Intent(
            project_name = msg_data.get("project_name", ""),
            pm           = msg_data.get("pm", "npm"),
            flags=[], libraries=[], global_tools=[],
            install_dir  = os.path.dirname(msg_data.get("project_path", os.getcwd())),
        )
        _nuxt_do_upgrade(intent, cache, cb, task_id=task_id)
        if task_id: _clear_session(task_id)
        return True

    # ask_for_path
    if msg_type == "ask_for_path":
        task_id = msg_data.get("task_id", "")
        cb("🔍 Extracting path from reply...", "dim", "normal", {})
        path, method = extract_path_from_text(user_request)
        if not path:
            cb("  Could not extract a path from your reply.", "yellow", "warning", {})
            ask_payload = dict(msg_data)
            ask_payload["task_id"]   = task_id or _generate_task_id()
            ask_payload["framework"] = "nuxtjs"
            _store_session(ask_payload["task_id"], ask_payload)
            cb("📂 Please reply with the full path where you want to create the project.",
               "yellow", "ask_path", ask_payload)
            return False
        cb(f"  ✓ Path found via {method}: {path}", "green", "normal", {})
        install_dir   = os.path.abspath(os.path.expanduser(path.rstrip("/\\")))
        os.makedirs(install_dir, exist_ok=True)
        config        = msg_data.get("config", {})
        clean_request = msg_data.get("user_request", user_request)
        if task_id: _clear_session(task_id)
        healthy, intent = _nuxt_run_pipeline(
            clean_request, install_dir, config, cache, cb, task_id or "")
        if intent: _nuxt_check_upgrade(intent, cache, cb, task_id=task_id or "")
        return healthy

    # init
    task_id = _generate_task_id()
    cb("⚡ Nuxt.js Agentic Creator v1.0 (k10)", "cyan", "normal", {})
    pm = detect_pm(user_request)
    if not bootstrap_environment(pm, cb):
        return False
    print_env_health(cb)

    cb("🔍 Extracting install path...", "dim", "normal", {})
    path, method = extract_path_from_text(user_request)
    install_dir = None
    if path:
        install_dir = os.path.abspath(os.path.expanduser(path.rstrip("/\\")))
        os.makedirs(install_dir, exist_ok=True)
        cb(f"  ✓ Install dir ({method}): {install_dir}", "green", "normal", {})
    else:
        cb("  No path found in prompt.", "yellow", "warning", {})

    clean_request = strip_path_from_prompt(user_request)
    cb(f"  🧹 Cleaned prompt: {clean_request}", "dim", "normal", {})

    cb("🤖 Parsing request...", "dim", "normal", {})
    try:
        config = _nuxt_parse(clean_request)
    except Exception as e:
        cb(f"⚠  AI parse failed ({e}) — using defaults", "yellow", "warning", {})
        config = {"project_name":"my-nuxt-app","flags":[],"libraries":[],
                  "global_tools":[],"python_packages":[],"modules":[]}

    if not install_dir:
        ask_payload = {"task_id": task_id, "user_request": clean_request,
                       "config": config, "pm": pm, "framework": "nuxtjs"}
        _store_session(task_id, ask_payload)
        cb("📂 Please reply with the full path where you want to create the project.",
           "yellow", "ask_path", ask_payload)
        return False

    healthy, intent = _nuxt_run_pipeline(clean_request, install_dir, config, cache, cb, task_id)
    if intent: _nuxt_check_upgrade(intent, cache, cb, task_id=task_id)
    return healthy


# ═════════════════════════════════════════════════════════════════════════════
#  HIERARCHICAL AI ROUTER  (Layer 1 + Layer 2)
# ═════════════════════════════════════════════════════════════════════════════

OLLAMA_BASE  = "http://localhost:11434"
ROUTER_MODEL = "qwen2.5-coder:7b"
WORKER_MODEL = "qwen2.5-coder:7b"

class Domain(str, Enum):
    CODING       = "coding"
    GENERATION   = "generation"
    ANALYSIS     = "analysis"
    CONVERSATION = "conversation"

class CodingSubDomain(str, Enum):
    NEXTJS  = "nextjs"
    NUXTJS  = "nuxtjs"
    REACT   = "react"
    PYTHON  = "python"
    GENERIC = "generic_coding"

class GenerationSubDomain(str, Enum):
    TEXT     = "text"
    CODE     = "code_generation"
    SUMMARY  = "summary"
    CREATIVE = "creative"


async def ollama_classify(system, user, model=ROUTER_MODEL):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OLLAMA_BASE}/api/chat",
            json={"model": model, "keep_alive": KEEP_ALIVE, "stream": False,
                  "messages": [{"role":"system","content":system},
                                {"role":"user","content":user}]},
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()

async def ollama_stream(system, user, model=WORKER_MODEL):
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", f"{OLLAMA_BASE}/api/chat",
            json={"model": model, "keep_alive": KEEP_ALIVE, "stream": True,
                  "messages": [{"role":"system","content":system},
                                {"role":"user","content":user}]},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip(): continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token: yield token
                except json.JSONDecodeError: continue


_MAIN_ROUTER_SYSTEM = """You are a top-level AI task router.
Classify the user's request into EXACTLY ONE domain. Return ONLY valid JSON.
No markdown, no explanation, no code fences.

Schema: {"domain": "<domain>", "reason": "<one sentence>"}

Domains:
  coding       — building, debugging, scaffolding software/projects/code
  generation   — generating text, paragraphs, stories, descriptions, lists
  analysis     — analyzing data, summarizing documents, comparing options
  conversation — general chat, questions, greetings, everything else

Examples:
  "create a next.js app"         → {"domain":"coding","reason":"User wants to scaffold a Next.js project."}
  "create a nuxt app"            → {"domain":"coding","reason":"User wants to scaffold a Nuxt project."}
  "write 10 sentences about AI"  → {"domain":"generation","reason":"User wants generated text."}
  "summarize this article"       → {"domain":"analysis","reason":"User wants content summarized."}
  "how are you?"                 → {"domain":"conversation","reason":"General greeting."}
"""

_CODING_ROUTER_SYSTEM = """You are a coding sub-router.
Classify the coding request. Return ONLY valid JSON.

Schema: {"subdomain": "<subdomain>", "reason": "<one sentence>"}

Subdomains:
  nextjs         — Next.js framework project or feature
  nuxtjs         — Nuxt.js / Vue 3 framework project or feature
  react          — React (without Next.js) component or app
  python         — Python script, API, or package
  generic_coding — anything else coding-related

Examples:
  "create a next.js project with tailwind" → {"subdomain":"nextjs",...}
  "build a nuxt3 blog"                     → {"subdomain":"nuxtjs",...}
  "create a nuxt app"                      → {"subdomain":"nuxtjs",...}
  "write a python flask API"               → {"subdomain":"python",...}
"""

_GENERATION_ROUTER_SYSTEM = """You are a content-generation sub-router.
Return ONLY valid JSON.

Schema: {"subdomain": "<subdomain>", "reason": "<one sentence>"}

Subdomains:
  text            — sentences, paragraphs, descriptions, lists
  code_generation — generating standalone code snippets (not a full project)
  summary         — condensing or summarizing provided content
  creative        — creative writing, poems, stories
"""

async def route_main(prompt):
    raw = await ollama_classify(_MAIN_ROUTER_SYSTEM, prompt)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        data   = json.loads(raw)
        domain = Domain(data.get("domain", "conversation"))
        return domain, data.get("reason", "")
    except Exception:
        return Domain.CONVERSATION, "fallback"

async def route_coding(prompt):
    raw = await ollama_classify(_CODING_ROUTER_SYSTEM, prompt)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        data = json.loads(raw)
        sub  = CodingSubDomain(data.get("subdomain", "generic_coding"))
        return sub, data.get("reason", "")
    except Exception:
        return CodingSubDomain.GENERIC, "fallback"

async def route_generation(prompt):
    raw = await ollama_classify(_GENERATION_ROUTER_SYSTEM, prompt)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        data = json.loads(raw)
        sub  = GenerationSubDomain(data.get("subdomain", "text"))
        return sub, data.get("reason", "")
    except Exception:
        return GenerationSubDomain.TEXT, "fallback"

async def full_route(prompt):
    domain, d_reason = await route_main(prompt)
    info = {"domain": domain.value, "domain_reason": d_reason}

    if domain == Domain.CODING:
        sub, s_reason = await route_coding(prompt)
        info.update({"subdomain": sub.value, "subdomain_reason": s_reason})
        agent_key = sub.value
    elif domain == Domain.GENERATION:
        sub, s_reason = await route_generation(prompt)
        info.update({"subdomain": sub.value, "subdomain_reason": s_reason})
        agent_key = sub.value
    elif domain == Domain.ANALYSIS:
        agent_key = "analysis"
        info["subdomain"] = "analysis"
    else:
        agent_key = "conversation"
        info["subdomain"] = "conversation"

    info["agent"] = agent_key
    return agent_key, info


AGENT_SYSTEMS = {
    "react":          "You are an expert React developer. Provide clean, modern React with TypeScript. Explain component structure and best practices.",
    "python":         "You are an expert Python developer. Write idiomatic Python with requirements.txt, venv setup, and usage examples.",
    "generic_coding": "You are an expert software engineer. Provide well-commented code adapted to whatever language the user needs.",
    "text":            "You are a professional content writer. Generate high-quality, engaging text exactly as requested.",
    "code_generation": "You are a code snippet generator. Write clean, well-commented, production-ready snippets with language labels and brief explanations.",
    "summary":         "You are a summarization expert. Produce concise, accurate summaries capturing the key points.",
    "creative":        "You are a creative writer. Write imaginative, engaging content with vivid language.",
    "analysis":        "You are a data and content analyst. Provide structured, insightful analysis with headings and clear reasoning.",
    "conversation":    "You are a helpful, friendly AI assistant. Engage naturally and answer questions directly.",
}


# ═════════════════════════════════════════════════════════════════════════════
#  FASTAPI APP
# ═════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Hierarchical AI Router + Next.js (k9) + Nuxt.js (k10) Agentic Creator",
    version="3.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


def _make_ws_cb(ws_queue: queue.Queue) -> PrintCallback:
    """Bridges 4-param PrintCallback → WebSocket message queue."""
    def cb(text="", color="white", msg_type="normal", data=None):
        data = data or {}
        if msg_type in ("ask_path", "upgrader"):
            ws_queue.put({"type": msg_type, "data": data})
        else:
            ws_queue.put({"type": msg_type, "data": text})
    return cb


async def _run_real_agent(websocket, prompt, framework, message=None):
    """
    Run k9 (nextjs) or k10 (nuxtjs) pipeline in a thread,
    streaming all output to the WebSocket.
    framework: "nextjs" | "nuxtjs"
    """
    msg_q: queue.Queue = queue.Queue()
    cb    = _make_ws_cb(msg_q)
    loop  = asyncio.get_event_loop()

    start_fn = nextjs_start_sync if framework == "nextjs" else nuxtjs_start_sync
    init_msg = message or {"type": "init", "data": {}}

    fut = loop.run_in_executor(None, start_fn, prompt, cb, init_msg)

    while not fut.done():
        try:
            wsmsg = msg_q.get_nowait()
            await websocket.send_json(wsmsg)
        except queue.Empty:
            await asyncio.sleep(0.05)

    # flush remaining
    while not msg_q.empty():
        await websocket.send_json(msg_q.get_nowait())

    await websocket.send_json({"type": "done", "data": ""})


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    """
    Unified WebSocket endpoint.

    Client message formats:
        {"prompt": "..."}                                 ← new prompt
        {"type": "ask_for_path",  "prompt": "...", "data": {...}}
        {"type": "upgrade_permission",               "data": {...}}
    """
    await websocket.accept()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                msg = {"prompt": raw}

            msg_type_in = msg.get("type", "init")
            prompt      = msg.get("prompt", "").strip()

            # ── Follow-up flows (ask_for_path / upgrade_permission) ────────
            if msg_type_in in ("ask_for_path", "upgrade_permission"):
                saved_data = msg.get("data", {})
                framework  = saved_data.get("framework", "nextjs")
                agent_msg  = {"type": msg_type_in, "data": saved_data}
                await _run_real_agent(websocket, prompt, framework, message=agent_msg)
                continue

            # ── Fresh prompt ───────────────────────────────────────────────
            if not prompt:
                await websocket.send_json({"type": "error", "data": "Empty prompt"})
                continue

            # Layer 1 + 2 routing
            try:
                agent_key, routing_info = await full_route(prompt)
                await websocket.send_json({"type": "routing", "data": routing_info})
            except Exception as e:
                await websocket.send_json({"type": "error",
                                           "data": f"Routing failed: {e}"})
                continue

            # ── k9: Next.js real pipeline ──────────────────────────────────
            if agent_key == "nextjs":
                await _run_real_agent(websocket, prompt, "nextjs")

            # ── k10: Nuxt.js real pipeline ─────────────────────────────────
            elif agent_key == "nuxtjs":
                await _run_real_agent(websocket, prompt, "nuxtjs")

            # ── LLM streaming agents ───────────────────────────────────────
            else:
                system = AGENT_SYSTEMS.get(agent_key, AGENT_SYSTEMS["conversation"])
                try:
                    async for token in ollama_stream(system, prompt):
                        await websocket.send_json({"type": "token", "data": token})
                    await websocket.send_json({"type": "done", "data": ""})
                except Exception as e:
                    await websocket.send_json({"type": "error",
                                               "data": f"Stream error: {e}"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": str(e)})
        except Exception:
            pass


# ─── REST endpoints ───────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    prompt: str

class RouteRequest(BaseModel):
    prompt: str


@app.post("/api/route")
async def api_route(req: RouteRequest):
    try:
        agent_key, info = await full_route(req.prompt)
        return {"ok": True, "routing": info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    try:
        agent_key, routing_info = await full_route(req.prompt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Routing failed: {e}")

    if agent_key in ("nextjs", "nuxtjs"):
        fw = "Next.js (k9)" if agent_key == "nextjs" else "Nuxt.js (k10)"
        return {
            "ok":      True,
            "routing": routing_info,
            "reply":   (
                f"{fw} project creation requires a WebSocket connection. "
                f"Connect to ws://<host>/ws/chat and send: "
                f'{{\"prompt\": \"{req.prompt}\"}}'
            ),
        }

    system   = AGENT_SYSTEMS.get(agent_key, AGENT_SYSTEMS["conversation"])
    response = []
    async for token in ollama_stream(system, req.prompt):
        response.append(token)
    return {"ok": True, "routing": routing_info, "reply": "".join(response)}


@app.get("/api/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r      = await client.get(f"{OLLAMA_BASE}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
        return {"ok": True, "ollama": "reachable", "models": models}
    except Exception as e:
        return {"ok": False, "ollama": "unreachable", "error": str(e)}


@app.get("/api/agents")
async def list_agents():
    return {
        "real_pipelines": {
            "nextjs": "k9 — Next.js Agentic Creator v13.1",
            "nuxtjs": "k10 — Nuxt.js Agentic Creator v1.0",
        },
        "llm_agents": list(AGENT_SYSTEMS.keys()),
        "domains": {
            "coding":       [s.value for s in CodingSubDomain],
            "generation":   [s.value for s in GenerationSubDomain],
            "analysis":     ["analysis"],
            "conversation": ["conversation"],
        },
    }


@app.get("/api/sessions")
async def list_sessions():
    return {"pending_sessions": list(user_csh.keys()), "count": len(user_csh)}


@app.get("/")
async def root():
    return {
        "name":    "Hierarchical AI Router + k9 (Next.js) + k10 (Nuxt.js)",
        "version": "3.0.0",
        "layers": {
            "1": "Main Router — coding / generation / analysis / conversation",
            "2": "Sub-Routers — nextjs(k9) | nuxtjs(k10) | react | python | generic",
            "3": "Workers — nextjs/nuxtjs=REAL pipeline, others=Ollama LLM",
        },
        "websocket":  "ws://host/ws/chat",
        "docs":       "/docs",
    }