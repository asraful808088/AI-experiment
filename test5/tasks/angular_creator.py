"""
Angular Agentic Creator v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Socket-ready architecture.  Mirrors Next.js v13.1 and Vue/Nuxt v1.3.
All output goes through a single 4-parameter PrintCallback.
WebSocket wiring is NOT included — callers handle the transport layer.

KEY DESIGN NOTES
────────────────
• Angular's `ng new` is highly interactive by default.
  Every scaffold command MUST include --defaults --skip-git (or --no-git
  depending on the version) together with --package-manager <pm> so the
  child process never blocks waiting for stdin.
• stdin is always redirected from os.devnull for the same reason.
• Scaffold strategies (in order):
    1. npm init @angular@latest  (no global @angular/cli required)
    2. npx @angular/cli@latest new
    3. npx @angular/cli@next new  (pre-release fallback)
    4. ng new  (only if @angular/cli is already installed globally)
• Self-update: set SELF_UPDATE_URL to a raw URL of this file to enable.
  On startup the creator SHA-256-compares itself against the remote copy.
  If they differ, the new version is staged next to this file and the
  caller is notified via msg_type='self_update'.

Callback signature:
    cb(text: str, color: str, msg_type: str, data: dict)

msg_type values:
    "normal"    — regular log line
    "warning"   — non-fatal issue
    "error"     — failure
    "upgrader"  — upgrade-related event
    "ask_path"  — path not found; caller must re-prompt then call start()
                  again with type="ask_for_path" and the same data payload
    "self_update" — a newer version of this script was staged

start() message parameter schema:
    { "type": "init" | "ask_for_path" | "upgrade_permission", "data": {} }
"""

import os
import re
import sys
import json
import time
import shutil
import signal
import hashlib
import socket
import subprocess
import threading
import urllib.request
import urllib.error
import webbrowser
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any, List


# ─────────────────────────────────────────────────────────────────────────────
# Session store  { task_id: payload }
# ─────────────────────────────────────────────────────────────────────────────
user_csh: Dict[str, Dict[str, Any]] = {}

PrintCallback = Callable[[str, str, str, dict], None]


def _noop_cb(text="", color="white", msg_type="normal", data=None):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Terminal callback
# ─────────────────────────────────────────────────────────────────────────────
try:
    from rich.console import Console as _RC
    _rc = _RC()
    _CMAP = {
        "green": "green", "yellow": "yellow", "red": "red",
        "cyan": "cyan", "dim": "dim", "white": "white", "magenta": "magenta",
    }

    def _terminal_cb(text="", color="white", msg_type="normal", data=None):
        data = data or {}
        tag  = _CMAP.get(color, "white")
        if msg_type == "ask_path":
            _rc.print(f"[yellow]⟳  PATH NEEDED — {text}[/yellow]")
            return
        if msg_type == "upgrader":
            _rc.print(f"[cyan]⬆  UPGRADE — {text}[/cyan]")
            return
        if msg_type == "self_update":
            _rc.print(f"[magenta]🔄 SELF-UPDATE — {text}[/magenta]")
            return
        if msg_type == "warning":
            _rc.print(f"[yellow]{text}[/yellow]")
            return
        if msg_type == "error":
            _rc.print(f"[red]{text}[/red]")
            return
        _rc.print(f"[{tag}]{text}[/{tag}]")

except ImportError:
    def _terminal_cb(text="", color="white", msg_type="normal", data=None):
        prefix = {
            "ask_path":    "⟳ PATH",
            "upgrader":    "⬆ UPGRADE",
            "self_update": "🔄 UPDATE",
            "warning":     "⚠ WARN",
            "error":       "✗ ERR",
        }.get(msg_type, "")
        print(f"{prefix}  {text}" if prefix else text)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
_HERE           = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_PATH  = os.path.join(_HERE, "angular_creator_knowledge.json")
SELF_UPDATE_URL = ""          # Set to a raw URL to enable self-update

MODEL          = "qwen2.5-coder:7b"
KEEP_ALIVE     = -1
IS_WIN         = sys.platform == "win32"
DEV_PORT       = 4200          # Angular default port
LAUNCH_TIMEOUT = 120
HEALTH_POLL    = 2
MAX_FIX_ROUNDS = 3
KNOWLEDGE_TTL  = 86400         # 24 hours

sys.path.insert(0, _HERE)
try:
    from experiment.test5.tasks.project_fixer import ProjectFixer, fix_project
    HAS_FIXER = True
except ImportError:
    HAS_FIXER = False

try:
    import requests as _req
    def _http_get(url, timeout=10):
        r = _req.get(url, timeout=timeout)
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

if IS_WIN:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _machine_key() -> str:
    nv = "unknown"
    try:
        r  = subprocess.run("node --version", shell=True, capture_output=True, text=True)
        nv = r.stdout.strip().lstrip("v").split(".")[0]
    except Exception:
        pass
    return f"{sys.platform}__node{nv}"

MK = _machine_key()


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge base (self-updating via npm registry)
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_KNOWLEDGE: Dict[str, Any] = {
    "_v": 1,
    "_fetched_at": 0,
    "latest_versions": {
        "@angular/core":           "21.2.x",
        "@angular/cli":            "21.2.x",
        "@angular/material":       "21.x",
        "@angular/cdk":            "21.x",
        "@angular/pwa":            "21.x",
        "@ngrx/store":             "19.x",
        "@ngrx/effects":           "19.x",
        "@ngrx/entity":            "19.x",
        "@ngrx/component-store":   "19.x",
        "rxjs":                    "7.x",
        "zone.js":                 "0.15.x",
    },
    # Scaffold strategy bases — order matters (first = preferred)
    # The runner appends name + non-interactive flags automatically.
    "scaffold_bases": [
        "npm init @angular@latest",     # No global CLI needed
        "npx @angular/cli@latest new",
        "npx @angular/cli@next new",    # Pre-release fallback
    ],
    # ng new flags the AI may emit
    "ng_new_flags": {
        "--routing":              "Add Angular Router",
        "--standalone":           "Use standalone component API (default true in v17+)",
        "--strict":               "Enable strict type-checking (default true)",
        "--style=css":            "Use CSS stylesheets",
        "--style=scss":           "Use SCSS stylesheets",
        "--style=sass":           "Use Sass stylesheets",
        "--style=less":           "Use Less stylesheets",
        "--style=tailwind":       "Use Tailwind CSS (v19+)",
        "--ssr":                  "Enable Server-Side Rendering / SSG",
        "--zoneless":             "Disable zone.js (Angular 18+)",
        "--minimal":              "Minimal workspace (no test framework)",
        "--skip-tests":           "Skip spec.ts generation",
        "--inline-style":         "Inline styles in component file",
        "--inline-template":      "Inline template in component file",
        "--prefix=<prefix>":      "Component selector prefix (default: app)",
        "--view-encapsulation=":  "Emulated | None | ShadowDom",
        "--test-runner=vitest":   "Use Vitest instead of Karma (v19+)",
        "--file-name-style-guide=2016": "Use 2016-style filenames (app.component.ts)",
        "--file-name-style-guide=2025": "Use 2025-style filenames (app.ts)",
    },
    "dev_cmds":     {"npm": "npm start",  "pnpm": "pnpm start", "yarn": "yarn start", "bun": "bun start"},
    "install_cmds": {"npm": "npm install","pnpm": "pnpm install","yarn": "yarn install","bun": "bun install"},
    # For non-interactive scaffold: flags appended unconditionally
    # --defaults  → accept all prompts with defaults (routing=false,style=css,ssr=false)
    # --skip-git  → don't run git init
    # --package-manager <pm> → tell ng new which PM to use
    # --no-create-application is NOT used here (we always want the app)
    "non_interactive_flags": "--defaults --skip-git",
    "pm_create_cmd":   {},
    "bad_strategies":  {},
    "yarn_version":    {},
    "project_journal": [],
}

_PACKAGES_TO_TRACK = [
    "@angular/core", "@angular/cli", "@angular/material", "@angular/cdk",
    "@angular/pwa", "@ngrx/store", "@ngrx/effects", "rxjs", "zone.js",
]


def _load_knowledge() -> dict:
    if os.path.exists(KNOWLEDGE_PATH):
        try:
            with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
                k = json.load(f)
            for key, val in _DEFAULT_KNOWLEDGE.items():
                if key not in k:
                    k[key] = val
            return k
        except Exception:
            pass
    import copy
    return copy.deepcopy(_DEFAULT_KNOWLEDGE)


def _save_knowledge(k: dict):
    try:
        with open(KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
            json.dump(k, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _fetch_latest_version(pkg: str) -> Optional[str]:
    try:
        encoded      = pkg.replace("/", "%2F")
        status, text = _http_get(f"https://registry.npmjs.org/{encoded}/latest", timeout=6)
        if status == 200:
            return json.loads(text).get("version", None)
    except Exception:
        pass
    return None


def refresh_knowledge(k: dict, cb: PrintCallback, force: bool = False):
    try:
        now = time.time()
        if not force and (now - k.get("_fetched_at", 0)) < KNOWLEDGE_TTL:
            return
        cb("🧠 Refreshing knowledge base from npm registry...", "cyan", "normal", {})
        fetched = 0
        for pkg in _PACKAGES_TO_TRACK:
            try:
                ver = _fetch_latest_version(pkg)
                if ver:
                    k.setdefault("latest_versions", {})[pkg] = ver
                    cb(f"  ✓ {pkg:<40s} {ver}", "green", "normal", {})
                    fetched += 1
                else:
                    cb(f"  ⚠  {pkg} — registry unreachable", "yellow", "warning", {})
            except Exception:
                cb(f"  ⚠  {pkg} — fetch error (skipped)", "yellow", "warning", {})
        k["_fetched_at"] = now
        _save_knowledge(k)
        cb(f"  📚 Knowledge updated ({fetched}/{len(_PACKAGES_TO_TRACK)} packages)", "cyan", "normal", {})
    except Exception as e:
        cb(f"  ⚠  Knowledge refresh failed ({e}) — using cached data", "yellow", "warning", {})


# ─────────────────────────────────────────────────────────────────────────────
# Self-update
# ─────────────────────────────────────────────────────────────────────────────
def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        pass
    return h.hexdigest()


def check_self_update(cb: PrintCallback):
    if not SELF_UPDATE_URL:
        return
    try:
        status, remote_src = _http_get(SELF_UPDATE_URL, timeout=10)
        if status != 200:
            return
        remote_sha = hashlib.sha256(remote_src.encode()).hexdigest()
        local_sha  = _sha256_file(__file__)
        if remote_sha == local_sha:
            return
        staging = os.path.join(_HERE, "angular_creator_v1_next.py")
        with open(staging, "w", encoding="utf-8") as f:
            f.write(remote_src)
        cb(
            f"New version available! Staged at {staging}",
            "magenta", "self_update",
            {"staged_path": staging},
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Path extraction  (identical logic to Vue/Nuxt creator)
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

_NL_PATH_PATTERNS = [
    (r'\b(?:in|on|at|inside)\s+(?:the\s+)?([A-Za-z])\s+(?:drive|disk|partition)\b',
     lambda m: m.group(1).upper() + ":\\"),
    (r'\b([A-Za-z]):[\\\/]',
     lambda m: m.group(1).upper() + ":\\"),
]

_TYPO_MAP = {
    "proect": "project", "Proect": "Project",
    "foldr":  "folder",  "deskt0p": "desktop",
}

_PATH_AI_PROMPT = """Extract ONLY the file path from the text.
Rules:
- Output ONLY the path, nothing else
- Fix typos (proect->project)
- Convert natural language (e.g. "project in c drive" -> C:\\project)
- Windows: drive:\\folder  |  Unix: /absolute/path  |  ~/relative
- Drive only (e.g. "c drive") -> C:\\
- Never add explanation"""

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

_PATH_HINT_PATTERNS = [
    r'[A-Za-z]:[\\\/]',
    r'(?<!\w)[\/~]',
    r'\b(?:in|at|inside|into|under|within)\b',
    r'\b(?:path|dir(?:ectory)?|folder|drive|disk)\b',
    r'\\',
]


def _prompt_has_path_hint(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in _PATH_HINT_PATTERNS)


def _fix_typos(s: str) -> str:
    for w, r in _TYPO_MAP.items():
        s = s.replace(w, r)
    return s


def _clean_path(p: str) -> str:
    p = p.strip()
    p = re.sub(r'[>|]+$', '', p).strip('"').strip("'")
    return _fix_typos(p)


def _regex_extract_path(text: str) -> Optional[str]:
    for pat, builder in _NL_PATH_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return builder(m)
    for pat in _REGEX_PATTERNS:
        ms = re.findall(pat, text, re.IGNORECASE)
        if ms:
            p = ms[0]
            if isinstance(p, tuple):
                p = next((x for x in p if x), "")
            p = _clean_path(p)
            if re.match(r'[A-Za-z]:\\', p) or p.startswith('/') or p.startswith('~'):
                return p
    return None


def _ai_path_is_grounded(path: str, text: str) -> bool:
    segments = [s.lower() for s in re.split(r'[\\\/]', path) if len(s) > 1]
    _HALLUCINATED = {
        "users", "user", "home", "tmp", "temp", "appdata", "local",
        "roaming", "documents", "desktop", "downloads", "projects",
        "project", "c:", "d:", "e:", "f:",
    }
    text_lower = text.lower()
    grounded   = [s for s in segments if s not in _HALLUCINATED]
    if not grounded:
        return False
    return any(seg in text_lower for seg in grounded)


def _ai_extract_path(text: str) -> Optional[str]:
    if not _prompt_has_path_hint(text):
        return None
    if not HAS_OLLAMA:
        return None
    try:
        r = _ollama.chat(
            model=MODEL, keep_alive=KEEP_ALIVE,
            messages=[
                {"role": "system", "content": _PATH_AI_PROMPT},
                {"role": "user",   "content": text},
            ],
        )
        p = _clean_path(r["message"]["content"].strip())
        if not (re.match(r'[A-Za-z]:\\', p) or p.startswith('/') or p.startswith('~')):
            return None
        if not _ai_path_is_grounded(p, text):
            return None
        return p
    except Exception:
        pass
    return None


def extract_path_from_text(text: str) -> tuple:
    p = _regex_extract_path(text)
    if p:
        return p, "regex"
    p = _ai_extract_path(text)
    if p:
        return p, "ai"
    return None, "failed"


def strip_path_from_prompt(text: str) -> str:
    result = text
    for pat in _PATH_STRIP_PATTERNS:
        result = re.sub(pat, '', result, flags=re.IGNORECASE)
    result = re.sub(r'\s{2,}', ' ', result).strip()
    return result if result else text


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess helpers
# stdin always comes from os.devnull to prevent interactive-prompt hangs
# ─────────────────────────────────────────────────────────────────────────────
def _decode(b) -> str:
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode("latin-1", errors="replace")


def _run(cmd: str, cwd: str) -> tuple:
    try:
        with open(os.devnull, "rb") as devnull:
            kwargs: dict = dict(
                shell=True, cwd=cwd,
                stdin=devnull,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore
            proc = subprocess.Popen(cmd, **kwargs)
            try:
                stdout_b, stderr_b = proc.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_b, stderr_b = proc.communicate()
        return proc.returncode == 0, _decode(stdout_b), _decode(stderr_b)
    except Exception as e:
        return False, "", str(e)


def _run_silent(cmd: str, cwd=None) -> tuple:
    try:
        with open(os.devnull, "rb") as devnull:
            r = subprocess.run(cmd, shell=True, capture_output=True, cwd=cwd, stdin=devnull)
        return r.returncode == 0, _decode(r.stdout).strip(), _decode(r.stderr).strip()
    except Exception as e:
        return False, "", str(e)


def _ver(cmd: str) -> str:
    try:
        kwargs: dict = dict(shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore
        proc = subprocess.Popen(cmd, **kwargs)
        try:
            out, err = proc.communicate(timeout=6)
            return (out + err).strip().split("\n")[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return "timeout"
    except Exception:
        return "?"


def _which(name):
    return shutil.which(name)


def _add_to_path(d: str):
    pv = os.environ.get("PATH", "")
    if d not in pv.split(os.pathsep):
        os.environ["PATH"] = d + os.pathsep + pv


def fix_path(d: str):
    if not d or not os.path.isdir(d):
        return
    _add_to_path(d)
    if IS_WIN:
        try:
            import winreg, ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0,
                                 winreg.KEY_READ | winreg.KEY_WRITE)
            try:
                cur, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                cur = ""
            if d.lower() not in cur.lower():
                nv = cur.rstrip(";") + ";" + d if cur else d
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, nv)
                ctypes.windll.user32.SendMessageTimeoutW(
                    0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
        except Exception:
            pass
    else:
        for rc in ["~/.bashrc", "~/.zshrc", "~/.profile"]:
            rcp = os.path.expanduser(rc)
            if not os.path.exists(rcp):
                continue
            try:
                if d not in open(rcp).read():
                    with open(rcp, "a") as f:
                        f.write(f'\nexport PATH="{d}:$PATH"\n')
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
        ("ng",    "ng version --skip-confirmation 2>&1 | head -1"),
        ("pnpm",  "pnpm --version"),
        ("yarn",  "yarn --version"),
        ("bun",   "bun --version"),
    ]
    cb("🖥  Environment Health", "cyan", "normal", {})
    for name, cmd in checks:
        found = bool(_which(name.split()[0]))
        cb(
            f"  {'✓' if found else '✗'} {name:<10s} {_ver(cmd) if found else 'not found'}",
            "green" if found else "red", "normal", {},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Error classification
# ─────────────────────────────────────────────────────────────────────────────
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
    stderr = stderr or ""
    stdout = stdout or ""
    c = (stderr + stdout).lower()
    if any(k in c for k in ["unknown command", "unknown subcommand", "is not a yarn command",
                             "command not found"]):
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


# ─────────────────────────────────────────────────────────────────────────────
# Package manager detection
# ─────────────────────────────────────────────────────────────────────────────
PM_ALIASES = {"npm": "npm", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun"}
DEFAULT_PM = "npm"


def detect_pm(req: str) -> str:
    lo = req.lower()
    for alias, name in PM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lo):
            return name
    return DEFAULT_PM


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge-cache helpers
# ─────────────────────────────────────────────────────────────────────────────
def _cache_key(pm: str) -> str:
    return f"angular_{pm}"


def k_get_create(k, pm):
    return k.get("pm_create_cmd", {}).get(MK, {}).get(_cache_key(pm))


def k_set_create(k, pm, cmd):
    k.setdefault("pm_create_cmd", {}).setdefault(MK, {})[_cache_key(pm)] = cmd
    _save_knowledge(k)


def k_clear_create(k, pm, cb):
    mk  = k.get("pm_create_cmd", {}).get(MK, {})
    key = _cache_key(pm)
    if key in mk:
        del mk[key]
        _save_knowledge(k)
    cb(f"🗑️  Cleared cached create-cmd for angular/{pm}", "yellow", "warning", {})


def k_is_bad(k, key):
    return key in k.get("bad_strategies", {}).get(MK, [])


def k_mark_bad(k, key):
    b = k.setdefault("bad_strategies", {}).setdefault(MK, [])
    if key not in b:
        b.append(key)
        _save_knowledge(k)


def k_reset_strategies(k, pm, cb):
    prefix = f"angular_{pm}"
    mk     = k.get("bad_strategies", {}).get(MK, [])
    k["bad_strategies"][MK] = [x for x in mk if prefix not in x]
    _save_knowledge(k)
    cb(f"🗑️  Reset bad strategies for angular/{pm}", "yellow", "warning", {})


def k_full_reset(k, cb):
    for key in ["pm_create_cmd", "bad_strategies"]:
        if MK in k.get(key, {}):
            del k[key][MK]
    _save_knowledge(k)
    cb("♻️  Full knowledge-cache reset for this machine", "yellow", "warning", {})


def k_add_journal(k, entry):
    k.setdefault("project_journal", []).append(entry)
    _save_knowledge(k)


# ─────────────────────────────────────────────────────────────────────────────
# Intent dataclass
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Intent:
    project_name: str
    pm:           str
    flags:        List[str]
    libraries:    List[str]
    global_tools: List[str]
    install_dir:  str

    @property
    def project_path(self) -> str:
        return os.path.join(self.install_dir, self.project_name)

    def flag_str(self) -> str:
        return " ".join(self.flags)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy builder
#
# Angular scaffold is fundamentally different from Next/Vue because:
#   • `npm init @angular@latest` is the preferred zero-global approach
#   • The name comes AFTER the base command (like `ng new <name>`)
#   • We MUST always pass  --defaults --skip-git --package-manager <pm>
#     to suppress ALL interactive prompts
# ─────────────────────────────────────────────────────────────────────────────
def _non_interactive_suffix(pm: str) -> str:
    """
    Flags appended to every scaffold command unconditionally.
    These suppress all interactive prompts in `ng new`.
    """
    return f"--defaults --skip-git --package-manager {pm}"


def _build_strategies(intent: Intent, k: dict) -> list:
    """
    Build ordered list of (strategy_name, full_command) tuples.
    User flags (style, routing, ssr, etc.) are sandwiched between the
    project name and the non-interactive suffix.
    """
    result      = []
    pm          = intent.pm
    name        = intent.project_name
    user_flags  = intent.flag_str()
    ni_suffix   = _non_interactive_suffix(pm)
    bases       = k.get("scaffold_bases", _DEFAULT_KNOWLEDGE["scaffold_bases"])

    def _full(base: str) -> str:
        parts = [base, name]
        if user_flags:
            parts.append(user_flags)
        parts.append(ni_suffix)
        return " ".join(parts)

    def add(sname, cmd):
        if not k_is_bad(k, f"{MK}__{sname}"):
            result.append((sname, cmd))

    # 1. Cached winning command (with name+flags appended fresh)
    cached_base = k_get_create(k, pm)
    if cached_base:
        add(f"cached_angular_{pm}", _full(cached_base))

    # 2. Official scaffold order
    for i, base in enumerate(bases):
        label = f"base{i}_{pm}"
        if base != cached_base:
            add(label, _full(base))

    # 3. If ng is installed globally, try it directly
    if _which("ng"):
        add("global_ng", _full("ng new"))

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────
class Runner:
    def __init__(self, intent: Intent, k: dict, cb: PrintCallback):
        self.intent = intent
        self.k      = k
        self.log    = []
        self._cb    = cb
        self._fixer = None

    def _get_fixer(self):
        if not HAS_FIXER:
            return None
        if self._fixer is None and os.path.isdir(self.intent.project_path):
            def _fcb(t, c="white"):
                self._cb(t, c, "normal", {})
            self._fixer = ProjectFixer(self.intent.project_path, cb=_fcb)
        return self._fixer

    def _run_fixer(self, error_output="") -> bool:
        fixer = self._get_fixer()
        if not fixer:
            return False
        self._cb("🔧 Running auto-fixer...", "yellow", "warning", {})
        return fixer.fix_all(error_output=error_output).get("success", False)

    def create_project(self) -> bool:
        for fix_round in range(MAX_FIX_ROUNDS + 1):
            strats = _build_strategies(self.intent, self.k)
            if not strats:
                self._cb("No strategies left.", "red", "error", {})
                break

            for sname, cmd in strats:
                self._cb(f"  ▶ [{fix_round}] {sname}: {cmd}", "cyan", "normal", {})
                ok, out, err = _run(cmd, self.intent.install_dir)
                ek           = classify_error(err, out, self.intent.project_path)
                project_exists = os.path.isdir(self.intent.project_path)

                if ok and project_exists:
                    self._cb(f"  ✓ Created with '{sname}'", "green", "normal", {})
                    # Cache the base command (everything before the project name)
                    idx = cmd.find(self.intent.project_name)
                    if idx > 0:
                        k_set_create(self.k, self.intent.pm, cmd[:idx].strip())
                    self.log.append({"strategy": sname, "cmd": cmd, "ok": True})
                    return True

                # Handle the edge case where Angular emits non-zero but did create
                if project_exists and not ok:
                    combined = (err + out).lower()
                    if not any(fatal in combined for fatal in
                               ["error:", "err!", "failed to", "cannot find"]):
                        self._cb("  ⚠  Non-zero exit but project dir found — continuing",
                                 "yellow", "warning", {})
                        idx = cmd.find(self.intent.project_name)
                        if idx > 0:
                            k_set_create(self.k, self.intent.pm, cmd[:idx].strip())
                        self.log.append({"strategy": sname, "cmd": cmd, "ok": True})
                        return True

                combined_out = (err + out).strip()
                self._cb(f"  ✗ Failed ({ek}): {combined_out[:300]}", "red", "error", {})

                if sname.startswith("cached_") and ek in (EK.UNKNOWN_SUBCOMMAND, EK.GENERIC):
                    k_clear_create(self.k, self.intent.pm, self._cb)

                k_mark_bad(self.k, f"{MK}__{sname}")
                self.log.append({"strategy": sname, "cmd": cmd, "ok": False, "ek": ek})

                if ek == EK.NETWORK:
                    self._cb("  ⏳ Retrying in 5s...", "yellow", "warning", {})
                    time.sleep(5)
                    ok2, out2, _ = _run(cmd, self.intent.install_dir)
                    if ok2 and os.path.isdir(self.intent.project_path):
                        return True

            if   fix_round == 0: k_reset_strategies(self.k, self.intent.pm, self._cb)
            elif fix_round == 1: k_clear_create(self.k, self.intent.pm, self._cb)
            elif fix_round >= 2: k_full_reset(self.k, self._cb)

        self._cb("❌ All strategies exhausted.", "red", "error", {})
        self._cb(
            f"  Manual: npm init @angular@latest {self.intent.project_name} "
            f"{self.intent.flag_str()} {_non_interactive_suffix(self.intent.pm)}",
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
                self._cb(f"  ✓ {label} done", "green", "normal", {})
                return True, out
            if attempt > MAX_FIX_ROUNDS:
                break
            if not self._run_fixer(error_output=err + out):
                if ek == EK.NETWORK:
                    time.sleep(5 * attempt)
                    continue
                break
        self._cb(f"  ✗ {label} permanently failed", "red", "error", {})
        return False, err + out


# ─────────────────────────────────────────────────────────────────────────────
# Version helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_installed_version(project_path: str, pkg: str) -> str:
    pkg_path = pkg.replace("/", os.sep)
    p = os.path.join(project_path, "node_modules", pkg_path, "package.json")
    try:
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f).get("version", "unknown")
    except Exception:
        pass
    return "unknown"


def check_and_upgrade(intent: Intent, k: dict, cb: PrintCallback, task_id: str = ""):
    cb("🔄 Checking Angular version...", "cyan", "normal", {})
    installed_core = get_installed_version(intent.project_path, "@angular/core")
    installed_cli  = get_installed_version(intent.project_path, "@angular/cli")
    latest_core    = k.get("latest_versions", {}).get("@angular/core", "unknown")
    latest_cli     = k.get("latest_versions", {}).get("@angular/cli",  "unknown")
    if latest_core == "unknown":
        latest_core = _fetch_latest_version("@angular/core") or "unknown"
    if latest_cli == "unknown":
        latest_cli = _fetch_latest_version("@angular/cli") or "unknown"

    cb(f"  @angular/core  installed: {installed_core}  latest: {latest_core}", "white", "normal", {})
    cb(f"  @angular/cli   installed: {installed_cli}   latest: {latest_cli}",  "white", "normal", {})

    needs_upgrade = (
        installed_core not in ("unknown", latest_core) or
        installed_cli  not in ("unknown", latest_cli)
    )

    upgrade_data = {
        "task_id":        task_id,
        "installed_core": installed_core,
        "installed_cli":  installed_cli,
        "latest_core":    latest_core,
        "latest_cli":     latest_cli,
        "project_path":   intent.project_path,
        "project_name":   intent.project_name,
        "pm":             intent.pm,
        "needs_upgrade":  needs_upgrade,
    }

    cb(
        f"Upgrade available: core {installed_core} → {latest_core}" if needs_upgrade
        else f"Angular is up to date (core {installed_core})",
        "cyan", "upgrader", upgrade_data,
    )

    if needs_upgrade and not task_id:
        try:
            answer = input(f"\n  Upgrade Angular to latest? (y/n): ").strip().lower()
        except EOFError:
            answer = "n"
        if answer == "y":
            _do_upgrade(intent, k, cb, task_id)


def _do_upgrade(intent: Intent, k: dict, cb: PrintCallback, task_id: str = ""):
    """
    Use `ng update` (the Angular-correct migration path) which also runs
    automated code-mods (schematics) for breaking changes.
    Falls back to a bare `npm install @latest` if ng update fails.
    """
    runner  = Runner(intent, k, cb)
    ng_bin  = os.path.join(intent.project_path, "node_modules", ".bin", "ng")
    ng_cmd  = ng_bin if os.path.exists(ng_bin) else "ng"

    # Primary: ng update (handles schematics / breaking-change migrations)
    update_cmd = (
        f"{ng_cmd} update @angular/core@latest @angular/cli@latest "
        "--allow-dirty --force"
    )
    ok, _ = runner.run_cmd(update_cmd, label="ng update angular")

    if not ok:
        cb("  ⚠  ng update failed — falling back to direct install", "yellow", "warning", {})
        install_base = k.get("install_cmds", _DEFAULT_KNOWLEDGE["install_cmds"]).get(
            intent.pm, "npm install"
        )
        ok, _ = runner.run_cmd(
            f"{install_base} @angular/core@latest @angular/cli@latest",
            label="install angular@latest",
        )

    cb(
        "✅ Angular upgrade complete" if ok else "❌ Angular upgrade failed",
        "green" if ok else "red",
        "upgrader",
        {"task_id": task_id, "project_path": intent.project_path, "success": ok},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Library helpers
# ─────────────────────────────────────────────────────────────────────────────
def _npm_exists(name: str) -> bool:
    try:
        s, _ = _http_get(f"https://registry.npmjs.org/{name.replace('/','%2F')}/latest", timeout=6)
        return s == 200
    except Exception:
        return False


def validate_libraries(libs: list, cb: PrintCallback) -> tuple:
    if not libs:
        return [], []
    cb(f"🔎 Validating {len(libs)} libraries...", "cyan", "normal", {})
    ok_libs, failed = [], []
    for lib in libs:
        if _npm_exists(lib):
            cb(f"  ✓ {lib}", "green", "normal", {})
            ok_libs.append(lib)
        else:
            cb(f"  ⚠  {lib} not on npm", "yellow", "warning", {})
            failed.append({"original": lib, "reason": "not on npm registry"})
    return ok_libs, failed


def install_libraries(intent: Intent, libs: list, k: dict, cb: PrintCallback):
    if not libs:
        return
    install_base = k.get("install_cmds", _DEFAULT_KNOWLEDGE["install_cmds"]).get(
        intent.pm, "npm install"
    )
    runner = Runner(intent, k, cb)
    cb(f"📦 Installing {len(libs)} librar{'y' if len(libs) == 1 else 'ies'}...", "cyan", "normal", {})
    for lib in libs:
        ok, _ = runner.run_cmd(f"{install_base} {lib}", label=f"install {lib}")
        if not ok:
            cb(f"  ✗ {lib} could not be installed", "red", "error", {})


def install_global_tools(intent: Intent, tools: list, k: dict, cb: PrintCallback):
    if not tools:
        return
    g_cmds = {
        "npm":  "npm install --global",
        "pnpm": "pnpm add -g",
        "yarn": "yarn global add",
        "bun":  "bun add --global",
    }
    runner = Runner(intent, k, cb)
    for tool in tools:
        runner.run_cmd(f"{g_cmds[intent.pm]} {tool}", label=f"global {tool}", cwd=os.getcwd())
        fix_pnpm_path()


# ─────────────────────────────────────────────────────────────────────────────
# Dev-server launch & verify
# Angular listens on :4200 by default (ng serve --port <n> --no-open)
# ─────────────────────────────────────────────────────────────────────────────
def _find_free_port(preferred=4200) -> int:
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
            s, _ = _http_get(url, timeout=3)
            if s == 200:
                cb(f"  ✓ Server responded 200 at {url}", "green", "normal", {})
                return True
        except Exception:
            pass
        time.sleep(HEALTH_POLL)
    return False


def launch_and_verify(intent: Intent, k: dict, cb: PrintCallback) -> bool:
    port    = _find_free_port(DEV_PORT)
    dev_cmd = k.get("dev_cmds", _DEFAULT_KNOWLEDGE["dev_cmds"]).get(intent.pm, "npm start")
    # Angular CLI's dev server: override the port and disable auto-open browser
    # `npm start` passes through to `ng serve` via package.json
    # We override via env PORT — Angular respects it via @angular-devkit/build-angular
    url     = f"http://localhost:{port}"

    cb(f"🚀 Launching Angular dev server on port {port}...", "cyan", "normal", {})
    env              = os.environ.copy()
    env["PORT"]      = str(port)
    # Angular v14+ also respects NG_CLI_ANALYTICS=false
    env["NG_CLI_ANALYTICS"] = "false"

    try:
        proc = subprocess.Popen(
            dev_cmd, shell=True, cwd=intent.project_path, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except Exception as e:
        cb(f"  ✗ Could not start dev server: {e}", "red", "error", {})
        return False

    server_output = []

    def _cap(stream):
        for raw in stream:
            server_output.append(_decode(raw).rstrip())

    threading.Thread(target=_cap, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=_cap, args=(proc.stderr,), daemon=True).start()

    healthy = _wait_for_server(url, LAUNCH_TIMEOUT, cb)

    try:
        if IS_WIN:
            subprocess.run(f"taskkill /PID {proc.pid} /T /F", shell=True, capture_output=True)
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
            def _fcb(t, c="white"):
                cb(t, c, "normal", {})
            fix_project(intent.project_path, error_output=err_text, cb=_fcb)
        for line in server_output[-20:]:
            cb(f"  {line}", "red", "error", {})
    return healthy


# ─────────────────────────────────────────────────────────────────────────────
# AI request parser
# The system prompt instructs the model to ONLY return project properties.
# Path words have already been stripped before this function is called.
# ─────────────────────────────────────────────────────────────────────────────
_PARSE_SYSTEM_ANGULAR = """You are an Angular CLI assistant.
Return ONLY valid JSON — no markdown, no explanation, no code fences.
Schema:
{
  "project_name": "kebab-case-name",
  "flags": [],
  "libraries": ["@angular/material"],
  "global_tools": [],
  "python_packages": []
}

PATH RULE: All path/directory references have already been removed.
Ignore leftover words like drive, folder, directory, path, vrive, c.
Extract ONLY project properties.

VALID ng new FLAGS (only include what the user explicitly asked for):
  --routing                  → add Angular Router
  --standalone               → standalone components (default true in v17+)
  --style=css                → CSS stylesheets (default)
  --style=scss               → SCSS stylesheets
  --style=sass               → Sass stylesheets
  --style=less               → Less stylesheets
  --style=tailwind           → Tailwind CSS (v19+)
  --ssr                      → Server-Side Rendering / SSG
  --zoneless                 → disable zone.js (v18+)
  --minimal                  → minimal workspace (no testing)
  --skip-tests               → skip spec.ts generation
  --inline-style             → inline styles in component .ts
  --inline-template          → inline template in component .ts
  --strict                   → strict type-checking (default true — only add if user says disable)
  --no-strict                → disable strict mode
  --prefix=<prefix>          → selector prefix (default: app)
  --view-encapsulation=Emulated|None|ShadowDom
  --test-runner=vitest        → use Vitest (v19+)
  --file-name-style-guide=2016|2025

NEVER add: --defaults, --skip-git, --package-manager, --directory
(the runner adds these automatically for non-interactive operation).

POPULAR ANGULAR LIBRARIES (put in "libraries"):
  @angular/material    → Angular Material UI
  @angular/cdk         → Component Dev Kit
  @angular/pwa         → Progressive Web App support
  @ngrx/store          → NgRx state management
  @ngrx/effects        → NgRx effects
  @ngrx/entity         → NgRx entity
  @ngrx/component-store → NgRx component store
  @angular/fire        → Firebase integration
  @ng-bootstrap/ng-bootstrap → Bootstrap UI components
  primeng              → PrimeNG UI components
  @tanstack/angular-query-experimental → TanStack Query
"""


def parse_user_request(req: str) -> dict:
    if not HAS_OLLAMA:
        name = re.sub(r"[^a-z0-9-]", "-", req.lower().split()[0])[:30] or "my-angular-app"
        return {
            "project_name": name, "flags": [],
            "libraries": [], "global_tools": [], "python_packages": [],
        }
    r = _ollama.chat(
        model=MODEL, keep_alive=KEEP_ALIVE,
        messages=[
            {"role": "system", "content": _PARSE_SYSTEM_ANGULAR},
            {"role": "user",   "content": req},
        ],
    )
    raw = r["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$",          "", raw, flags=re.MULTILINE)
    return json.loads(raw.strip())


def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "my-angular-app"


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────
def _gen_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


def _store_session(tid, d):
    user_csh[tid] = d


def _get_session(tid):
    return user_csh.get(tid)


def _clear_session(tid):
    user_csh.pop(tid, None)


# ─────────────────────────────────────────────────────────────────────────────
# Core pipeline
# ─────────────────────────────────────────────────────────────────────────────
def _run_pipeline(
    clean_request: str,
    install_dir:   str,
    config:        dict,
    k:             dict,
    cb:            PrintCallback,
    task_id:       str,
) -> tuple:
    pm           = detect_pm(clean_request)
    project_name = _sanitize_name(config.get("project_name", "my-angular-app"))
    flags        = config.get("flags", [])
    raw_libs     = config.get("libraries", [])
    raw_globals  = config.get("global_tools", [])
    py_pkgs      = config.get("python_packages", [])

    good_libs, bad_libs = validate_libraries(raw_libs, cb)

    intent = Intent(
        project_name=project_name, pm=pm, flags=flags,
        libraries=good_libs, global_tools=raw_globals,
        install_dir=install_dir,
    )

    cb("📋 Plan [ANGULAR]", "green", "normal", {})
    cb(f"  PM          : {pm}",                                          "cyan", "normal", {})
    cb(f"  Project     : {project_name}",                                "cyan", "normal", {})
    cb(f"  Flags       : {intent.flag_str() or 'defaults'}",            "cyan", "normal", {})
    cb(f"  Install dir : {install_dir}",                                 "cyan", "normal", {})
    cb(f"  Libraries   : {', '.join(good_libs) or 'none'}",             "cyan", "normal", {})
    cb(f"  Fixer       : {'enabled' if HAS_FIXER else 'disabled'}",     "cyan", "normal", {})
    if bad_libs:
        cb(
            f"  Skipped     : {', '.join(b['original'] for b in bad_libs)}",
            "yellow", "warning", {},
        )

    runner = Runner(intent, k, cb)
    if not runner.create_project():
        k_add_journal(k, {
            "machine": MK, "pm": pm, "project": project_name,
            "worked": False, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        return False, None

    # Angular's scaffold installs deps by default; run again only if skip-install was set
    if "--skip-install" in flags:
        install_cmd = k.get("install_cmds", _DEFAULT_KNOWLEDGE["install_cmds"]).get(
            pm, "npm install"
        )
        cb(f"📦 Running dependency install ({install_cmd})...", "cyan", "normal", {})
        ok_install, _ = runner.run_cmd(install_cmd, label="base install")
        if not ok_install:
            cb("  ⚠  Base install had issues — continuing anyway", "yellow", "warning", {})

    install_libraries(intent, good_libs, k, cb)
    install_global_tools(intent, raw_globals, k, cb)
    ensure_pip_packages(py_pkgs, cb)

    cb("🔬 Verifying project health...", "cyan", "normal", {})
    healthy = launch_and_verify(intent, k, cb)

    k_add_journal(k, {
        "machine": MK, "pm": pm, "project": project_name, "flags": flags,
        "worked": True, "landing_healthy": healthy,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    dev_cmd = k.get("dev_cmds", _DEFAULT_KNOWLEDGE["dev_cmds"]).get(pm, "npm start")
    cb(f"🎉 '{project_name}' [Angular] is ready!", "green", "normal", {})
    cb(f"  cd {intent.project_path}", "white", "normal", {})
    cb(f"  {dev_cmd}",                "white", "normal", {})
    if bad_libs:
        cb(
            f"Unresolved libraries: {', '.join(b['original'] for b in bad_libs)}",
            "yellow", "warning", {},
        )

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
      1. extract_path_from_text(raw)  → install_dir or None
      2. strip_path_from_prompt(raw)  → clean_request
      3. parse_user_request(clean)    → config  (AI never sees path words)
      4a. path found  → _run_pipeline(clean, install_dir, config, ...)
      4b. no path     → emit ask_path with clean_request saved in payload
    """
    if message is None:
        message = {"type": "init", "data": {}}

    msg_type = message.get("type", "init")
    msg_data = message.get("data", {}) or {}
    k        = _load_knowledge()

    # ── upgrade_permission ────────────────────────────────────────────────────
    if msg_type == "upgrade_permission":
        task_id = msg_data.get("task_id", "")
        intent  = Intent(
            project_name=msg_data.get("project_name", ""),
            pm=msg_data.get("pm", "npm"),
            flags=[], libraries=[], global_tools=[],
            install_dir=os.path.dirname(msg_data.get("project_path", os.getcwd())),
        )
        _do_upgrade(intent, k, cb, task_id=task_id)
        if task_id:
            _clear_session(task_id)
        return True

    # ── ask_for_path ──────────────────────────────────────────────────────────
    if msg_type == "ask_for_path":
        task_id = msg_data.get("task_id", "")
        cb("🔍 Extracting path from reply...", "dim", "normal", {})
        path, method = extract_path_from_text(user_request)

        if not path:
            cb("  Could not extract a path.", "yellow", "warning", {})
            ask_payload              = dict(msg_data)
            ask_payload["task_id"]   = task_id or _gen_task_id()
            _store_session(ask_payload["task_id"], ask_payload)
            cb(
                "📂 Please reply with the full path where you want to create the project.",
                "yellow", "ask_path", ask_payload,
            )
            return False

        cb(f"  ✓ Path via {method}: {path}", "green", "normal", {})
        install_dir   = os.path.abspath(os.path.expanduser(path.rstrip("/\\")))
        os.makedirs(install_dir, exist_ok=True)
        config        = msg_data.get("config", {})
        clean_request = msg_data.get("user_request", user_request)
        if task_id:
            _clear_session(task_id)

        refresh_knowledge(k, cb)
        healthy, intent = _run_pipeline(clean_request, install_dir, config, k, cb, task_id or "")
        if intent:
            check_and_upgrade(intent, k, cb, task_id=task_id or "")
        return healthy

    # ── init ──────────────────────────────────────────────────────────────────
    task_id = _gen_task_id()
    cb("⚡ Angular Agentic Creator v1.0", "cyan", "normal", {})
    cb("   Smart Cache · Retry Loops · Self-Update · Socket-Ready", "cyan", "normal", {})

    pm = detect_pm(user_request)
    bootstrap_environment(pm, cb)
    print_env_health(cb)
    check_self_update(cb)

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

    # ── STEP 2: strip path → clean prompt ─────────────────────────────────────
    clean_request = strip_path_from_prompt(user_request)
    cb(f"  🧹 Cleaned prompt: {clean_request}", "dim", "normal", {})

    # ── STEP 3: AI parses ONLY project properties ─────────────────────────────
    cb("", "white", "normal", {})
    cb("🤖 Parsing request...", "dim", "normal", {})
    try:
        config = parse_user_request(clean_request)
    except Exception as e:
        cb(f"⚠  AI parse failed ({e}) — using defaults", "yellow", "warning", {})
        config = {
            "project_name": "my-angular-app", "flags": [],
            "libraries": [], "global_tools": [], "python_packages": [],
        }

    # ── STEP 4a: no path → ask user ────────────────────────────────────────────
    if not install_dir:
        ask_payload = {
            "task_id":      task_id,
            "user_request": clean_request,
            "config":       config,
            "pm":           pm,
        }
        _store_session(task_id, ask_payload)
        cb(
            "📂 Please reply with the full path where you want to create the project.",
            "yellow", "ask_path", ask_payload,
        )
        return False

    # ── STEP 4b: path found → run pipeline ────────────────────────────────────
    refresh_knowledge(k, cb)
    healthy, intent = _run_pipeline(clean_request, install_dir, config, k, cb, task_id)
    if intent:
        check_and_upgrade(intent, k, cb, task_id=task_id)
    return healthy


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        import readline  # noqa: F401  (better input() on Unix)
    except ImportError:
        pass

    def _cli_cb(text="", color="white", msg_type="normal", data=None):
        _terminal_cb(text, color, msg_type, data)

    print("\n💡 Angular Agentic Creator v1.0")
    print("   Describe your Angular project (routing, material, SSR, etc.)\n")
    user_request = input("   Describe your project: ").strip()
    if not user_request:
        print("No input.")
        sys.exit(1)

    ok = start(user_request, cb=_cli_cb, message={"type": "init", "data": {}})

    # Interactive ask_path loop — no re-parse on retry (clean_request is saved)
    while not ok and user_csh:
        path_reply = input("\n   Your path reply: ").strip()
        if not path_reply:
            break
        pending = next(iter(user_csh.items()), None)
        if not pending:
            print("No pending session. Exiting.")
            break
        tid, saved = pending
        ok = start(
            path_reply,
            cb      = _cli_cb,
            message = {"type": "ask_for_path", "data": saved},
        )
