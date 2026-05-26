"""
Next.js Agentic Creator v13.1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Socket-ready architecture.
All output goes through a single 4-parameter PrintCallback.
WebSocket wiring is NOT included here — callers handle the transport layer.

KEY CHANGE vs v13.0
───────────────────
Path extraction now happens BEFORE AI parsing:
  1. extract_path_from_text(raw_prompt)   → finds install_dir (or None)
  2. strip_path_from_prompt(raw_prompt)   → removes path words → clean_prompt
  3. parse_user_request(clean_prompt)     → AI sees ONLY project properties
  4. If no path found  → emit ask_path, save clean_prompt in payload
  5. ask_for_path handler uses saved clean_prompt (no re-parse needed)

Callback signature:
    cb(text: str, color: str, msg_type: str, data: dict)

msg_type values:
    "normal"    — regular log line
    "warning"   — non-fatal issue
    "error"     — failure
    "upgrader"  — upgrade-related event (fires after project creation + verify)
    "ask_path"  — path not found; caller must re-prompt user and call start()
                  again with type="ask_for_path" and the same data payload

start() message parameter schema:
    {
        "type": "init" | "ask_for_path" | "upgrade_permission",
        "data": {}
    }

    init               — fresh run, data is empty
    ask_for_path       — user replied with a path; data = full project payload
    upgrade_permission — trigger upgrade flow; data = upgrade-related payload
"""

import os
import re
import sys
import json
import time
import shutil
import signal
import socket
import subprocess
import threading
import urllib.request
import urllib.error
import webbrowser
import uuid
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any


# ─────────────────────────────────────────────────────────────────────────────
# Temporary session store  { task_id: { ...payload } }
# Cleared automatically when a task fully completes.
# ─────────────────────────────────────────────────────────────────────────────
user_csh: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Callback type
#   cb(text, color, msg_type, data)
#   color    : "green" | "yellow" | "red" | "cyan" | "dim" | "white"
#   msg_type : "normal" | "warning" | "error" | "upgrader" | "ask_path"
#   data     : any dict the caller needs (empty {} when not relevant)
# ─────────────────────────────────────────────────────────────────────────────
PrintCallback = Callable[[str, str, str, dict], None]


def _noop_cb(text: str, color: str = "white",
             msg_type: str = "normal", data: dict = None) -> None:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Terminal callback  (used when running directly from CLI)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from rich.console import Console as _RichConsole
    _rc = _RichConsole()
    _COLOR_MAP = {
        "green": "green", "yellow": "yellow", "red": "red",
        "cyan":  "cyan",  "dim":    "dim",    "white": "white",
    }

    def _terminal_cb(text: str,
                     color: str    = "white",
                     msg_type: str = "normal",
                     data: dict    = None) -> None:
        data = data or {}
        tag  = _COLOR_MAP.get(color, "white")
        if msg_type == "ask_path":
            _rc.print(f"[yellow]⟳  PATH NEEDED — {text}[/yellow]")
            if data:
                _rc.print(f"[dim]  payload keys: {list(data.keys())}[/dim]")
            return
        if msg_type == "upgrader":
            _rc.print(f"[cyan]⬆  UPGRADE — {text}[/cyan]")
            if data:
                _rc.print(f"[dim]  {data}[/dim]")
            return
        if msg_type == "warning":
            _rc.print(f"[yellow]{text}[/yellow]")
            return
        if msg_type == "error":
            _rc.print(f"[red]{text}[/red]")
            return
        _rc.print(f"[{tag}]{text}[/{tag}]")

except ImportError:
    def _terminal_cb(text: str,
                     color: str    = "white",
                     msg_type: str = "normal",
                     data: dict    = None) -> None:
        prefix = {
            "ask_path": "⟳ PATH",
            "upgrader": "⬆ UPGRADE",
            "warning":  "⚠ WARN",
            "error":    "✗ ERR",
        }.get(msg_type, "")
        print(f"{prefix}  {text}" if prefix else text)


# ─────────────────────────────────────────────────────────────────────────────
# Path extractor  (regex first, AI fallback)
# ─────────────────────────────────────────────────────────────────────────────
_REGEX_PATTERNS = [
    r'[A-Za-z]:\\(?:[^\\\s<>|:"]+\\)*[^\\\s<>|:"]*',
    r'[A-Za-z]:\\[^\s]+',
    r'(?:in|at|inside)\s+([A-Za-z]:\\[^\s]+)',
    r'\b(?:in|inside|at|into|under|within)\s+([A-Za-z]:[\\\/][^\s,;\"\']+)',
    r'\b(?:in|inside|at|into|under|within)\s+(\/[^\s,;\"\']+)',
    r'\b(?:in|inside|at|into|under|within)\s+(~[^\s,;\"\']*)',
    r'(?:path|dir(?:ectory)?|folder)\s*[=:]\s*([^\s,;\"\']+)',
]

# Natural-language → path patterns (e.g. "in c drive" → C:\)
_NL_PATH_PATTERNS = [
    # "in c drive", "in the c drive", "on c drive"
    (r'\b(?:in|on|at|inside)\s+(?:the\s+)?([A-Za-z])\s+drive\b',
     lambda m: m.group(1).upper() + ":\\"),
    # "c:\" or "c:/" standalone
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


def _fix_path_typos(path: str) -> str:
    for wrong, right in _TYPO_MAP.items():
        path = path.replace(wrong, right)
    return path


def _clean_extracted_path(path: str) -> str:
    path = path.strip()
    path = re.sub(r'[>|]+$', '', path)
    path = path.strip('"').strip("'")
    return _fix_path_typos(path)


def _regex_extract_path(text: str) -> Optional[str]:
    # 1. Try natural-language patterns first (e.g. "in c drive")
    for pattern, builder in _NL_PATH_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return builder(m)

    # 2. Try structural regex patterns
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


def _ai_extract_path(text: str) -> Optional[str]:
    """AI fallback — requires ollama with qwen2.5-coder:7b."""
    try:
        import ollama as _ollama
        response = _ollama.chat(
            model="qwen2.5-coder:7b",
            keep_alive=-1,
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


def extract_path_from_text(text: str) -> tuple:
    """
    Returns (path_or_None, method_str).
    method_str: "regex" | "ai" | "failed"
    """
    path = _regex_extract_path(text)
    if path:
        return path, "regex"
    path = _ai_extract_path(text)
    if path:
        return path, "ai"
    return None, "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Path stripper — removes path words from prompt before AI parsing
# ─────────────────────────────────────────────────────────────────────────────
_PATH_STRIP_PATTERNS = [
    # "in c drive", "on the c drive", "at c drive"
    r'\b(?:in|on|at|inside)\s+(?:the\s+)?[A-Za-z]\s+(?:drive|disk|partition)\b',
    # "in C:\some\path" or "at /some/path" or "inside ~/folder"
    r'\b(?:in|at|inside|into|under|within)\s+[A-Za-z]:[\\\/][^\s,;]*',
    r'\b(?:in|at|inside|into|under|within)\s+\/[^\s,;]*',
    r'\b(?:in|at|inside|into|under|within)\s+~[^\s,;]*',
    # bare Windows paths:  C:\foo\bar
    r'[A-Za-z]:\\(?:[^\\\s<>|:"]+\\)*[^\\\s<>|:"]*',
    r'[A-Za-z]:\\[^\s]+',
    # bare Unix paths: /foo/bar  ~/foo
    r'(?<!\w)\/[^\s,;\"\']+',
    r'(?<!\w)~[^\s,;\"\']+',
    # "path = ..." / "dir = ..." / "folder = ..."
    r'(?:path|dir(?:ectory)?|folder)\s*[=:]\s*[^\s,;\"\']+',
]


def strip_path_from_prompt(text: str) -> str:
    """
    Remove all path-related words/phrases from *text* so the AI parser
    sees only project properties (name, flags, libraries, etc.).

    Falls back to the original text if the result would be empty.
    """
    result = text
    for pattern in _PATH_STRIP_PATTERNS:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    # Collapse runs of whitespace / trim
    result = re.sub(r'\s{2,}', ' ', result).strip()
    # Safety: if we accidentally stripped everything, return original
    return result if result else text


# ─────────────────────────────────────────────────────────────────────────────
# Environment / stdlib helpers
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    from experiment.test5.tasks.project_fixer import ProjectFixer, detect_errors, FE, fix_project
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

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


MODEL          = "qwen2.5-coder:7b"
KEEP_ALIVE     = -1
IS_WIN         = sys.platform == "win32"
CACHE_PATH     = os.path.join(_HERE, "nextjs_creator_cache_v13.json")
DEV_PORT       = 3000
LAUNCH_TIMEOUT = 120
HEALTH_POLL    = 2
MAX_FIX_ROUNDS = 3


# ─────────────────────────────────────────────────────────────────────────────
# Intent dataclass
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Intent:
    project_name: str
    pm:           str
    flags:        list
    libraries:    list
    global_tools: list
    install_dir:  str

    @property
    def project_path(self) -> str:
        return os.path.join(self.install_dir, self.project_name)

    def flag_str(self) -> str:
        return " ".join(self.flags)


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────
def _machine_key() -> str:
    node_ver = "unknown"
    try:
        r = subprocess.run("node --version", shell=True, capture_output=True, text=True)
        node_ver = r.stdout.strip().lstrip("v").split(".")[0]
    except Exception:
        pass
    return f"{sys.platform}__node{node_ver}"

MK = _machine_key()


def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"_v": 13, "pm_create_cmd": {}, "bad_strategies": {},
            "yarn_version": {}, "project_journal": []}

def _save_cache(c: dict):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def cache_get_create(c, pm):       return c.get("pm_create_cmd", {}).get(MK, {}).get(pm)
def cache_set_create(c, pm, cmd):
    c.setdefault("pm_create_cmd", {}).setdefault(MK, {})[pm] = cmd
    _save_cache(c)
def cache_clear_create(c, pm, cb):
    mk = c.get("pm_create_cmd", {}).get(MK, {})
    if pm in mk:
        del mk[pm]
        _save_cache(c)
    cb(f"🗑️  Cleared broken cached create-cmd for {pm}", "yellow", "warning", {})
def cache_is_bad(c, key):          return key in c.get("bad_strategies", {}).get(MK, [])
def cache_mark_bad(c, key):
    bads = c.setdefault("bad_strategies", {}).setdefault(MK, [])
    if key not in bads:
        bads.append(key)
        _save_cache(c)
def cache_reset_strategies(c, pm, cb):
    mk = c.get("bad_strategies", {}).get(MK, [])
    c["bad_strategies"][MK] = [k for k in mk if pm not in k]
    _save_cache(c)
    cb(f"🗑️  Reset bad strategies for {pm}", "yellow", "warning", {})
def cache_full_reset(c, cb):
    for key in ["pm_create_cmd", "bad_strategies"]:
        if MK in c.get(key, {}):
            del c[key][MK]
    _save_cache(c)
    cb("♻️  Full cache reset for this machine", "yellow", "warning", {})
def cache_add_journal(c, entry):
    c.setdefault("project_journal", []).append(entry)
    _save_cache(c)


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess helpers
# ─────────────────────────────────────────────────────────────────────────────
def _run_silent(cmd: str, cwd=None) -> tuple:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()

def _which(name):  return shutil.which(name)

def _ver(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        return (r.stdout + r.stderr).strip().split("\n")[0]
    except Exception:
        return "?"

def _add_to_path(new_dir: str):
    path_var = os.environ.get("PATH", "")
    if new_dir not in path_var.split(os.pathsep):
        os.environ["PATH"] = new_dir + os.pathsep + path_var

def fix_path(new_dir: str):
    if not new_dir or not os.path.isdir(new_dir):
        return
    _add_to_path(new_dir)
    if IS_WIN:
        try:
            import winreg, ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0,
                                 winreg.KEY_READ | winreg.KEY_WRITE)
            try:
                current, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current = ""
            if new_dir.lower() not in current.lower():
                new_val = current.rstrip(";") + ";" + new_dir if current else new_dir
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_val)
                ctypes.windll.user32.SendMessageTimeoutW(
                    0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
        except Exception:
            pass
    else:
        for rc in ["~/.bashrc", "~/.zshrc", "~/.profile"]:
            rc_path = os.path.expanduser(rc)
            if not os.path.exists(rc_path):
                continue
            try:
                content = open(rc_path).read()
                if new_dir not in content:
                    with open(rc_path, "a") as f:
                        f.write(f'\nexport PATH="{new_dir}:$PATH"\n')
            except Exception:
                pass

def fix_pnpm_path():
    ok, out, _ = _run_silent("pnpm bin -g")
    if ok and out and out not in os.environ.get("PATH", ""):
        fix_path(out.strip())

def ensure_node(cb: PrintCallback) -> bool:
    if _which("node"):
        return True
    cb("⚠  Node.js not found — attempting install...", "yellow", "warning", {})
    if IS_WIN:
        ok, _, _ = _run_silent(
            "winget install OpenJS.NodeJS.LTS --silent "
            "--accept-source-agreements --accept-package-agreements"
        )
        if ok:
            return True
        webbrowser.open("https://nodejs.org/en/download")
    elif sys.platform == "darwin":
        ok, _, _ = _run_silent("brew install node")
        return ok
    else:
        ok, _, _ = _run_silent(
            "curl -fsSL https://deb.nodesource.com/setup_lts.x | "
            "sudo -E bash - && sudo apt-get install -y nodejs"
        )
        return ok
    return False

def ensure_pm_global(pm: str, cb: PrintCallback) -> bool:
    if _which(pm):
        return True
    cmds = {
        "pnpm": "npm install --global pnpm",
        "yarn": "npm install --global yarn",
        "bun":  "npm install --global bun",
    }
    cmd = cmds.get(pm)
    if not cmd:
        return False
    ok, _, _ = _run_silent(cmd)
    if ok and pm == "pnpm":
        fix_pnpm_path()
    return ok

def ensure_pip_packages(packages: list, cb: PrintCallback):
    if not packages:
        return
    for pkg in packages:
        ok, _, _ = _run_silent(f"python -c \"import {pkg.replace('-','_')}\"")
        if ok:
            continue
        ok2, _, _ = _run_silent(f"pip install {pkg} --break-system-packages")
        if not ok2:
            _run_silent(f"pip install {pkg}")

def bootstrap_environment(pm: str, cb: PrintCallback):
    if not ensure_node(cb):
        cb("❌ Cannot continue without Node.js.", "red", "error", {})
        sys.exit(1)
    if _which("pnpm"):
        fix_pnpm_path()
    ensure_pm_global(pm, cb)

def print_env_health(cb: PrintCallback):
    checks = [
        ("node",  "node --version"),
        ("npm",   "npm --version"),
        ("npx",   "npx --version"),
        ("pnpm",  "pnpm --version"),
        ("yarn",  "yarn --version"),
        ("bun",   "bun --version"),
    ]
    cb("🖥  Environment Health", "cyan", "normal", {})
    for name, cmd in checks:
        found = bool(_which(name))
        mark  = "✓" if found else "✗"
        color = "green" if found else "red"
        ver   = _ver(cmd) if found else "not found"
        cb(f"  {mark} {name:<10s} {ver}", color, "normal", {})


# ─────────────────────────────────────────────────────────────────────────────
# Error classification
# ─────────────────────────────────────────────────────────────────────────────
_PNPM_SOFT = [r"ERR_PNPM_IGNORED_BUILD_SCRIPTS", r"ERR_PNPM_NO_GLOBAL_BIN_DIR"]

def _is_pnpm_soft(text: str) -> bool:
    return any(re.search(p, text) for p in _PNPM_SOFT)

def _verify_installed(project_path: str, lib: str) -> bool:
    return os.path.exists(
        os.path.join(project_path, "node_modules", *lib.split("/"), "package.json")
    )


class EK:
    SOFT_SUCCESS       = "soft_success"
    UNKNOWN_SUBCOMMAND = "unknown_subcommand"
    NETWORK            = "network_error"
    PERMISSION         = "permission_error"
    VERSION_CONFLICT   = "version_conflict"
    NOT_FOUND          = "not_found"
    GENERIC            = "generic"

def classify_error(stderr: str, stdout: str = "",
                   project_path: str = "", lib: str = "") -> str:
    if _is_pnpm_soft(stderr + stdout):
        if not lib or _verify_installed(project_path, lib):
            return EK.SOFT_SUCCESS
    c = (stderr + stdout).lower()
    if any(k in c for k in ['command "dlx" not found', "unknown command",
                             "unknown subcommand", "is not a yarn command"]):
        return EK.UNKNOWN_SUBCOMMAND
    if any(k in c for k in ["enotfound", "etimedout", "fetch failed", "econnrefused",
                             "network error", "socket hang up"]):
        return EK.NETWORK
    if any(k in c for k in ["eacces", "permission denied", "access denied"]):
        return EK.PERMISSION
    if any(k in c for k in ["peer dep", "incompatible", "conflict"]):
        return EK.VERSION_CONFLICT
    if any(k in c for k in ["404", "not found", "no matching version", "e404"]):
        return EK.NOT_FOUND
    return EK.GENERIC


def detect_yarn_version(cache: dict) -> str:
    cached = cache.get("yarn_version", {}).get(MK)
    if cached:
        return cached
    try:
        r     = subprocess.run("yarn --version", shell=True, capture_output=True, text=True)
        major = int(r.stdout.strip().split(".")[0]) if r.stdout.strip()[0].isdigit() else 1
        ver   = "v4" if major >= 2 else "v1"
        cache.setdefault("yarn_version", {})[MK] = ver
        _save_cache(cache)
        return ver
    except Exception:
        return "v1"


# ─────────────────────────────────────────────────────────────────────────────
# Package manager constants
# ─────────────────────────────────────────────────────────────────────────────
PM_ALIASES    = {"npm": "npm", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun"}
DEFAULT_PM    = "npm"
_PM_CREATE    = {
    "npm":  "npx create-next-app@latest",
    "pnpm": "pnpm dlx create-next-app@latest",
    "yarn": "yarn create next-app",
    "bun":  "bunx create-next-app@latest",
}
_YARN4_CREATE = "yarn dlx create-next-app@latest"
PM_INSTALL    = {"npm": "npm install",  "pnpm": "pnpm add",  "yarn": "yarn add",  "bun": "bun add"}
PM_INSTALL_G  = {"npm": "npm install --global", "pnpm": "pnpm add -g",
                 "yarn": "yarn global add",     "bun":  "bun add --global"}
PM_RUN_DEV    = {"npm": "npm run dev",  "pnpm": "pnpm dev",  "yarn": "yarn dev",  "bun": "bun dev"}

def detect_pm(req: str) -> str:
    lowered = req.lower()
    for alias, name in PM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return name
    return DEFAULT_PM

def get_create_base(pm: str, cache: dict) -> str:
    cached = cache_get_create(cache, pm)
    if cached:
        return cached
    if pm == "yarn":
        ver = detect_yarn_version(cache)
        return _YARN4_CREATE if ver == "v4" else _PM_CREATE["yarn"]
    return _PM_CREATE.get(pm, _PM_CREATE["npm"])

def build_create_cmd(base: str, intent: Intent) -> str:
    return f"{base} {intent.project_name} {intent.flag_str()}".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Strategy builder
# ─────────────────────────────────────────────────────────────────────────────
def _strategies(intent: Intent, cache: dict) -> list:
    result = []
    pm     = intent.pm

    def add(name, base):
        if not cache_is_bad(cache, f"{MK}__{name}"):
            result.append((name, build_create_cmd(base, intent)))

    cached = cache_get_create(cache, pm)
    if cached:
        add(f"cached_{pm}", cached)

    if pm == "yarn":
        ver     = detect_yarn_version(cache)
        default = _YARN4_CREATE if ver == "v4" else _PM_CREATE["yarn"]
        alt     = _PM_CREATE["yarn"] if ver == "v4" else _YARN4_CREATE
        if default != cached:
            add(f"default_{pm}", default)
        add("yarn_alt", alt)
    else:
        db = _PM_CREATE.get(pm, _PM_CREATE["npm"])
        if db != cached:
            add(f"default_{pm}", db)

    add("npx_fallback", "npx create-next-app@latest")
    add("npx_nolast",   "npx create-next-app")
    return result


def _run(cmd: str, cwd: str) -> tuple:
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, "", str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────
class Runner:
    def __init__(self, intent: Intent, cache: dict, cb: PrintCallback):
        self.intent  = intent
        self.cache   = cache
        self.log     = []
        self._cb     = cb
        self._fixer: Optional["ProjectFixer"] = None

    def _get_fixer(self) -> Optional["ProjectFixer"]:
        if not HAS_FIXER:
            return None
        if self._fixer is None and os.path.isdir(self.intent.project_path):
            def _fixer_cb(text, color="white"):
                self._cb(text, color, "normal", {})
            self._fixer = ProjectFixer(self.intent.project_path, cb=_fixer_cb)
        return self._fixer

    def _run_fixer(self, error_output: str = "") -> bool:
        fixer = self._get_fixer()
        if fixer is None:
            return False
        self._cb("", "white", "normal", {})
        self._cb("🔧 Running auto-fixer...", "yellow", "warning", {})
        result = fixer.fix_all(error_output=error_output)
        return result.get("success", False)

    def create_project(self) -> bool:
        for fix_round in range(MAX_FIX_ROUNDS + 1):
            strats = _strategies(self.intent, self.cache)
            if not strats:
                self._cb("No strategies left.", "red", "error", {})
                break

            for name, cmd in strats:
                self._cb(f"  ▶ [{fix_round}] Strategy '{name}': {cmd}", "cyan", "normal", {})
                ok, out, err = _run(cmd, self.intent.install_dir)
                ek           = classify_error(err, out, self.intent.project_path)

                if ok or ek == EK.SOFT_SUCCESS:
                    if ek == EK.SOFT_SUCCESS:
                        self._cb("  ⚠  Non-zero exit but project created", "yellow", "warning", {})
                    self._cb(f"  ✓ Created with '{name}'", "green", "normal", {})
                    if "fallback" not in name and "nolast" not in name:
                        idx = cmd.find(self.intent.project_name)
                        if idx > 0:
                            cache_set_create(self.cache, self.intent.pm, cmd[:idx].strip())
                    self.log.append({"strategy": name, "cmd": cmd, "ok": True})
                    return True

                self._cb(f"  ✗ Failed ({ek})", "red", "error", {})
                self._cb(f"  {(err + out).strip()[:250]}", "red", "error", {})

                if name.startswith("cached_") and ek in (EK.UNKNOWN_SUBCOMMAND, EK.GENERIC):
                    cache_clear_create(self.cache, self.intent.pm, self._cb)

                cache_mark_bad(self.cache, f"{MK}__{name}")
                self.log.append({"strategy": name, "cmd": cmd, "ok": False, "ek": ek})

                if ek == EK.NETWORK:
                    self._cb("  ⏳ Network error — retrying in 5s...", "yellow", "warning", {})
                    time.sleep(5)
                    ok2, out2, err2 = _run(cmd, self.intent.install_dir)
                    if ok2:
                        return True

            if fix_round == 0:
                self._cb("⚡ Round 0 failed — resetting strategies", "yellow", "warning", {})
                cache_reset_strategies(self.cache, self.intent.pm, self._cb)
            elif fix_round == 1:
                self._cb("⚡ Round 1 failed — clearing cached create-cmd", "yellow", "warning", {})
                cache_clear_create(self.cache, self.intent.pm, self._cb)
            elif fix_round >= 2:
                self._cb(f"⚡ Round {fix_round} failed — full cache reset", "yellow", "warning", {})
                cache_full_reset(self.cache, self._cb)

        self._cb("❌ All strategies exhausted.", "red", "error", {})
        self._cb(
            f"  Manual: npx create-next-app@latest "
            f"{self.intent.project_name} {self.intent.flag_str()}",
            "yellow", "warning", {},
        )
        return False

    def run_cmd(self, cmd: str, *, label="command", cwd=None, lib="") -> tuple:
        wd = cwd or self.intent.project_path

        for attempt in range(1, MAX_FIX_ROUNDS + 2):
            self._cb(f"  ▶ {label} (attempt {attempt}): {cmd}", "cyan", "normal", {})
            ok, out, err = _run(cmd, wd)
            ek           = classify_error(err, out, wd, lib)

            if ok or ek == EK.SOFT_SUCCESS:
                if ek == EK.SOFT_SUCCESS:
                    self._cb("  ⚠  Non-fatal warning (checking node_modules...)", "yellow", "warning", {})
                    if lib and not _verify_installed(wd, lib):
                        self._cb(f"  ✗ {lib} missing from node_modules", "red", "error", {})
                    else:
                        self._cb(f"  ✓ {label} done", "green", "normal", {})
                        return True, out
                else:
                    self._cb(f"  ✓ {label} done", "green", "normal", {})
                    return True, out

            if attempt > MAX_FIX_ROUNDS:
                break

            self._cb(f"  ✗ {label} failed ({ek}) — running fixer...", "red", "error", {})
            fixed = self._run_fixer(error_output=err + out)
            if not fixed:
                self._cb("  Fixer had nothing to apply", "yellow", "warning", {})
                if ek == EK.NETWORK:
                    time.sleep(5 * attempt)
                    continue
                break

        if lib and _verify_installed(wd, lib):
            self._cb(f"  ⚠  {lib} found in node_modules despite errors", "yellow", "warning", {})
            return True, ""

        self._cb(f"  ✗ {label} permanently failed", "red", "error", {})
        self._cb(f"  {(err + out).strip()[:250]}", "red", "error", {})
        return False, err + out


# ─────────────────────────────────────────────────────────────────────────────
# Version checking
# ─────────────────────────────────────────────────────────────────────────────
def get_latest_nextjs() -> str:
    try:
        status, text = _http_get("https://registry.npmjs.org/next/latest", timeout=8)
        if status == 200:
            return json.loads(text).get("version", "unknown")
    except Exception:
        pass
    return "unknown"

def get_installed_nextjs(project_path: str) -> str:
    try:
        p = os.path.join(project_path, "node_modules", "next", "package.json")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f).get("version", "unknown")
    except Exception:
        pass
    return "unknown"

def check_and_upgrade(intent: Intent, cache: dict, cb: PrintCallback, task_id: str = ""):
    """
    Always runs after project creation + server verify.
    Fires upgrade-related data through msg_type='upgrader'.
    Interactive (no task_id): prompts the user directly.
    Socket (task_id provided): emits upgrader event; caller decides.
    """
    cb("", "white", "normal", {})
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
    }

    cb(
        f"Upgrade available: {installed} → {latest}" if upgrade_data["needs_upgrade"]
        else f"Next.js is up to date ({installed})",
        "cyan", "upgrader", upgrade_data,
    )

    if upgrade_data["needs_upgrade"] and not task_id:
        try:
            answer = input(f"\n  Upgrade to {latest}? (y/n): ").strip().lower()
        except EOFError:
            answer = "n"
        if answer == "y":
            _do_upgrade(intent, cache, cb, task_id)


def _do_upgrade(intent: Intent, cache: dict, cb: PrintCallback, task_id: str = ""):
    runner = Runner(intent, cache, cb)
    upgrade_cmds = {
        "npm":  "npm install next@latest react@latest react-dom@latest",
        "pnpm": "pnpm add next@latest react@latest react-dom@latest",
        "yarn": "yarn add next@latest react@latest react-dom@latest",
        "bun":  "bun add next@latest react@latest react-dom@latest",
    }
    ok, _ = runner.run_cmd(upgrade_cmds[intent.pm], label="upgrade next.js")
    result_data = {
        "task_id":      task_id,
        "project_path": intent.project_path,
        "pm":           intent.pm,
        "success":      ok,
    }
    cb(
        "✅ Upgrade complete" if ok else "❌ Upgrade failed",
        "green" if ok else "red",
        "upgrader",
        result_data,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Library helpers
# ─────────────────────────────────────────────────────────────────────────────
def _npm_exists(name: str) -> bool:
    try:
        status, _ = _http_get(f"https://registry.npmjs.org/{name}/latest", timeout=6)
        return status == 200
    except Exception:
        return False

def validate_libraries(raw_libs: list, cb: PrintCallback) -> tuple:
    if not raw_libs:
        return [], []
    cb("", "white", "normal", {})
    cb(f"🔎 Validating {len(raw_libs)} libraries...", "cyan", "normal", {})
    ok_libs, failed = [], []
    for lib in raw_libs:
        if _npm_exists(lib):
            cb(f"  ✓ {lib}", "green", "normal", {})
            ok_libs.append(lib)
        else:
            cb(f"  ⚠  {lib} not found on npm", "yellow", "warning", {})
            failed.append({"original": lib, "reason": "not on npm registry"})
    return ok_libs, failed

def install_libraries(intent: Intent, libs: list, cache: dict, cb: PrintCallback):
    if not libs:
        return
    base   = PM_INSTALL[intent.pm]
    runner = Runner(intent, cache, cb)
    cb("", "white", "normal", {})
    cb(f"📦 Installing {len(libs)} librar{'y' if len(libs) == 1 else 'ies'}...", "cyan", "normal", {})
    for lib in libs:
        ok, _ = runner.run_cmd(f"{base} {lib}", label=f"install {lib}", lib=lib)
        if not ok:
            cb(f"  ✗ {lib} could not be installed", "red", "error", {})

def install_global_tools(intent: Intent, tools: list, cache: dict, cb: PrintCallback):
    if not tools:
        return
    runner = Runner(intent, cache, cb)
    for tool in tools:
        runner.run_cmd(
            f"{PM_INSTALL_G[intent.pm]} {tool}",
            label=f"global {tool}",
            cwd=os.getcwd(),
        )
        fix_pnpm_path()


# ─────────────────────────────────────────────────────────────────────────────
# Dev server launch
# ─────────────────────────────────────────────────────────────────────────────
def _find_free_port(preferred=3000) -> int:
    for port in range(preferred, preferred + 20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    return preferred

def _wait_for_server(url: str, timeout: int, cb: PrintCallback) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = _http_get(url, timeout=3)
            if status == 200:
                cb(f"  ✓ Server responded 200 at {url}", "green", "normal", {})
                return True
        except Exception:
            pass
        time.sleep(HEALTH_POLL)
    return False

def launch_and_verify(intent: Intent, cb: PrintCallback) -> bool:
    port    = _find_free_port(DEV_PORT)
    dev_cmd = PM_RUN_DEV[intent.pm]
    url     = f"http://localhost:{port}"
    cb("", "white", "normal", {})
    cb(f"🚀 Launching dev server on port {port}...", "cyan", "normal", {})
    env         = os.environ.copy()
    env["PORT"] = str(port)
    try:
        proc = subprocess.Popen(
            dev_cmd, shell=True, cwd=intent.project_path, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except Exception as e:
        cb(f"  ✗ Could not start dev server: {e}", "red", "error", {})
        return False

    server_output = []

    def _capture(stream):
        for line in stream:
            server_output.append(line.rstrip())

    threading.Thread(target=_capture, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=_capture, args=(proc.stderr,), daemon=True).start()

    healthy = _wait_for_server(url, LAUNCH_TIMEOUT, cb)

    try:
        if IS_WIN:
            subprocess.run(f"taskkill /PID {proc.pid} /T /F",
                           shell=True, capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    proc.wait(timeout=10)

    if healthy:
        cb(f"✅ Landing page verified at {url}", "green", "normal", {})
    else:
        cb(f"❌ Dev server did not respond in {LAUNCH_TIMEOUT}s", "red", "error", {})
        if HAS_FIXER and os.path.isdir(intent.project_path):
            err_text = "\n".join(server_output[-40:])
            cb("🔧 Running fixer on startup failure...", "yellow", "warning", {})
            def _fix_cb(text, color="white"):
                cb(text, color, "normal", {})
            fix_project(intent.project_path, error_output=err_text, cb=_fix_cb)
        for line in server_output[-20:]:
            cb(f"  {line}", "red", "error", {})
    return healthy


# ─────────────────────────────────────────────────────────────────────────────
# AI request parser
# NOTE: always receives the PATH-STRIPPED prompt so the AI never sees
#       directory words like "c drive", "in /home/user", etc.
# ─────────────────────────────────────────────────────────────────────────────
_PARSE_SYSTEM = """You are a Next.js CLI assistant.
Return ONLY valid JSON — no markdown, no explanation, no code fences.
Schema:
{
  "project_name": "kebab-case-name",
  "flags": ["--flag1"],
  "libraries": ["lib1"],
  "global_tools": [],
  "python_packages": []
}

PATH RULE: All path/directory references have already been removed from the
prompt before it reaches you. Ignore any leftover location words
(drive, folder, directory, path). Extract ONLY project properties.

FLAG RULES: only include flags the user explicitly mentioned.
  --typescript OR --javascript
  --tailwind OR --no-tailwind
  --eslint OR --no-eslint
  --app OR --no-app
  --turbopack OR --no-turbopack
  --src-dir OR --no-src-dir
  --no-git (only if user says "no git")

ALWAYS add --yes to flags.
"""

def parse_user_request(req: str) -> dict:
    if not HAS_OLLAMA:
        name = re.sub(r"[^a-z0-9-]", "-", req.lower().split()[0])[:30] or "my-next-app"
        return {"project_name": name, "flags": ["--yes"],
                "libraries": [], "global_tools": [], "python_packages": []}
    r   = _ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": _PARSE_SYSTEM},
            {"role": "user",   "content": req},
        ],
        keep_alive=KEEP_ALIVE,
    )
    raw = r["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$",          "", raw, flags=re.MULTILINE)
    return json.loads(raw.strip())

def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "my-next-app"


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────
def _generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"

def _store_session(task_id: str, payload: dict):
    user_csh[task_id] = payload

def _get_session(task_id: str) -> Optional[dict]:
    return user_csh.get(task_id)

def _clear_session(task_id: str):
    user_csh.pop(task_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# Core pipeline
# ─────────────────────────────────────────────────────────────────────────────
def _run_pipeline(
    clean_request: str,
    install_dir:   str,
    config:        dict,
    cache:         dict,
    cb:            PrintCallback,
    task_id:       str,
) -> tuple:
    """
    Builds intent, creates project, installs libs, verifies server.
    clean_request is the PATH-STRIPPED prompt (used for PM detection).
    Returns (healthy: bool, intent: Intent | None).
    intent is None on failure so the caller can skip check_and_upgrade().
    """
    pm              = detect_pm(clean_request)
    project_name    = _sanitize_name(config.get("project_name", "my-next-app"))
    flags           = config.get("flags", [])
    raw_libs        = config.get("libraries", [])
    raw_globals     = config.get("global_tools", [])
    python_packages = config.get("python_packages", [])
    if "--yes" not in flags:
        flags.append("--yes")

    good_libs, bad_libs = validate_libraries(raw_libs, cb)

    intent = Intent(
        project_name=project_name, pm=pm, flags=flags,
        libraries=good_libs, global_tools=raw_globals,
        install_dir=install_dir,
    )

    cb("", "white", "normal", {})
    cb("📋 Plan", "green", "normal", {})
    cb(f"  PM          : {intent.pm}",          "cyan", "normal", {})
    cb(f"  Project     : {intent.project_name}", "cyan", "normal", {})
    cb(f"  Flags       : {intent.flag_str()}",   "cyan", "normal", {})
    cb(f"  Install dir : {intent.install_dir}",  "cyan", "normal", {})
    cb(f"  Libraries   : {', '.join(good_libs) or 'none'}", "cyan", "normal", {})
    cb(f"  Fixer       : {'enabled' if HAS_FIXER else 'disabled'}", "cyan", "normal", {})
    if bad_libs:
        cb(f"  Skipped     : {', '.join(b['original'] for b in bad_libs)}",
           "yellow", "warning", {})

    runner = Runner(intent, cache, cb)
    ok     = runner.create_project()
    if not ok:
        cache_add_journal(cache, {
            "machine": MK, "pm": pm, "project": project_name,
            "worked": False, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        return False, None

    install_libraries(intent, good_libs, cache, cb)
    install_global_tools(intent, raw_globals, cache, cb)
    ensure_pip_packages(python_packages, cb)

    cb("", "white", "normal", {})
    cb("🔬 Verifying project health...", "cyan", "normal", {})
    healthy = launch_and_verify(intent, cb)

    cache_add_journal(cache, {
        "machine": MK, "pm": pm, "project": project_name, "flags": flags,
        "worked": True, "landing_healthy": healthy,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    cb("", "white", "normal", {})
    cb(f"🎉 '{intent.project_name}' is ready!", "green", "normal", {})
    cb(f"  cd {intent.project_path}",            "white", "normal", {})
    cb(f"  {PM_RUN_DEV[pm]}",                    "white", "normal", {})
    if bad_libs:
        cb(f"Unresolved libraries: {', '.join(b['original'] for b in bad_libs)}",
           "yellow", "warning", {})

    return healthy, intent


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def start(
    user_request: str,
    cb:           PrintCallback = _noop_cb,
    message:      Optional[dict] = None,
) -> bool:
    """
    Entry point for all callers (CLI, WebSocket server, etc.).

    Flow (init):
      1. extract_path_from_text(raw)   → install_dir or None
      2. strip_path_from_prompt(raw)   → clean_request
      3. parse_user_request(clean)     → config  (AI never sees path words)
      4a. path found    → _run_pipeline(clean, install_dir, config, ...)
      4b. path missing  → emit ask_path with clean_request saved in payload
    """
    if message is None:
        message = {"type": "init", "data": {}}

    msg_type = message.get("type", "init")
    msg_data = message.get("data", {}) or {}
    cache    = _load_cache()

    # ── upgrade_permission ────────────────────────────────────────────────────
    if msg_type == "upgrade_permission":
        task_id = msg_data.get("task_id", "")
        intent  = Intent(
            project_name = msg_data.get("project_name", ""),
            pm           = msg_data.get("pm", "npm"),
            flags        = [],
            libraries    = [],
            global_tools = [],
            install_dir  = os.path.dirname(msg_data.get("project_path", os.getcwd())),
        )
        _do_upgrade(intent, cache, cb, task_id=task_id)
        if task_id:
            _clear_session(task_id)
        return True

    # ── ask_for_path ──────────────────────────────────────────────────────────
    if msg_type == "ask_for_path":
        # user_request  = the user's path reply (only path words here)
        # msg_data      = saved payload containing the already-clean project prompt
        task_id = msg_data.get("task_id", "")

        cb("", "white", "normal", {})
        cb("🔍 Extracting path from reply...", "dim", "normal", {})
        path, method = extract_path_from_text(user_request)

        if not path:
            cb("  Could not extract a path from your reply.", "yellow", "warning", {})
            ask_payload = dict(msg_data)
            ask_payload["task_id"] = task_id or _generate_task_id()
            _store_session(ask_payload["task_id"], ask_payload)
            cb(
                "📂 Please reply with the full path where you want to create the project.",
                "yellow", "ask_path", ask_payload,
            )
            return False

        cb(f"  ✓ Path found via {method}: {path}", "green", "normal", {})
        install_dir = os.path.abspath(os.path.expanduser(path.rstrip("/\\")))
        os.makedirs(install_dir, exist_ok=True)

        # config and clean_request were already set during the init pass
        config        = msg_data.get("config", {})
        clean_request = msg_data.get("user_request", user_request)

        if task_id:
            _clear_session(task_id)

        healthy, intent = _run_pipeline(clean_request, install_dir, config, cache, cb, task_id or "")
        if intent:
            check_and_upgrade(intent, cache, cb, task_id=task_id or "")
        return healthy

    # ── init ──────────────────────────────────────────────────────────────────
    task_id = _generate_task_id()

    cb("", "white", "normal", {})
    cb("⚡ Next.js Agentic Creator v13.1", "cyan", "normal", {})
    cb("   Auto-Fixer · Smart Cache Recovery · Retry Loops · Socket-Ready", "cyan", "normal", {})

    # Detect PM from the raw prompt (before stripping, so "pnpm" etc. still match)
    pm = detect_pm(user_request)
    bootstrap_environment(pm, cb)
    print_env_health(cb)

    # ── STEP 1: extract path FIRST ────────────────────────────────────────────
    cb("", "white", "normal", {})
    cb("🔍 Extracting install path...", "dim", "normal", {})
    path, method = extract_path_from_text(user_request)

    install_dir: Optional[str] = None
    if path:
        install_dir = os.path.abspath(os.path.expanduser(path.rstrip("/\\")))
        os.makedirs(install_dir, exist_ok=True)
        cb(f"  ✓ Install dir ({method}): {install_dir}", "green", "normal", {})
    else:
        cb("  No path found in prompt.", "yellow", "warning", {})

    # ── STEP 2: strip path words → clean prompt ───────────────────────────────
    clean_request = strip_path_from_prompt(user_request)
    cb(f"  🧹 Cleaned prompt: {clean_request}", "dim", "normal", {})

    # ── STEP 3: parse ONLY project properties (AI never sees path words) ──────
    cb("", "white", "normal", {})
    cb("🤖 Parsing request...", "dim", "normal", {})
    try:
        config = parse_user_request(clean_request)
    except Exception as e:
        cb(f"⚠  AI parse failed ({e}) — using defaults", "yellow", "warning", {})
        config = {
            "project_name": "my-next-app",
            "flags":        ["--yes"],
            "libraries":    [],
            "global_tools": [],
            "python_packages": [],
        }

    # ── STEP 4a: no path → ask user, save clean_request in payload ────────────
    if not install_dir:
        ask_payload = {
            "task_id":      task_id,
            "user_request": clean_request,   # ← clean version, not raw
            "config":       config,
            "pm":           pm,
        }
        _store_session(task_id, ask_payload)
        cb(
            "📂 Please reply with the full path where you want to create the project.",
            "yellow", "ask_path", ask_payload,
        )
        return False

    # ── STEP 4b: path found → run pipeline ───────────────────────────────────
    healthy, intent = _run_pipeline(clean_request, install_dir, config, cache, cb, task_id)
    if intent:
        check_and_upgrade(intent, cache, cb, task_id=task_id)
    return healthy


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import readline  # noqa: F401  (improves input() on Unix)

    def _cli_cb(text: str,
                color: str    = "white",
                msg_type: str = "normal",
                data: dict    = None) -> None:
        _terminal_cb(text, color, msg_type, data)

    print("\n💡 Next.js Agentic Creator v13.1")
    user_request = input("   Describe your project: ").strip()
    if not user_request:
        print("No input.")
        sys.exit(1)

    ok = start(user_request, cb=_cli_cb, message={"type": "init", "data": {}})

    # If path was not found in the prompt, handle the interactive ask_path loop.
    # The saved session already holds the clean_request — no re-parse on retry.
    while not ok and user_csh:
        path_reply = input("\n   Your path reply: ").strip()
        if not path_reply:
            break

        # Find the pending session for this run (only one in CLI mode)
        pending = next(iter(user_csh.items()), None)
        if not pending:
            print("No pending session found. Exiting.")
            break

        tid, saved = pending
        ok = start(
            path_reply,
            cb      = _cli_cb,
            message = {"type": "ask_for_path", "data": saved},
        )