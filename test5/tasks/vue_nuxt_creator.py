"""
Vue / Nuxt Agentic Creator v1.3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fixes on top of v1.2:

FIX-F  NUXT SCAFFOLD HANGS — TWO REMAINING INTERACTIVE PROMPTS
       Even with --no-install --gitInit false --packageManager <pm>,
       nuxi@latest init v3.35+ shows TWO more prompts in non-TTY envs:
         1. "Which template would you like to use?"  (missing --template)
         2. "Would you like to browse and install modules?" (missing --modules)
       FIX-1 → Always append --template minimal (skips template picker).
       FIX-2 → Always append --modules "" (empty string skips module browser).
       FIX-3 → _run() now redirects stdin from os.devnull so the child
               process never inherits a terminal that lets prompts block.
       FIX-4 → npm create nuxt@latest is REMOVED from strategies entirely;
               only npx nuxi@latest init and npx nuxi@3 init are used
               because npm create nuxt passes flags inconsistently.

FIX-G  AI PARSER ADDS --template typescript INSTEAD OF USING LIBRARIES
       The Nuxt AI prompt let the model pick --template as a flag, which
       clashes with --template minimal.  The system prompt now explicitly
       forbids --template and instead tells the model to put typescript
       in libraries as @nuxt/module-builder or use nuxt.config instead.
       For TypeScript in Nuxt the correct approach is just enabling it in
       nuxt.config.ts (already the default with minimal template).
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
# Session store
# ─────────────────────────────────────────────────────────────────────────────
user_csh: Dict[str, Dict[str, Any]] = {}

PrintCallback = Callable[[str, str, str, dict], None]

def _noop_cb(text="", color="white", msg_type="normal", data=None):
    pass


try:
    from rich.console import Console as _RC
    _rc = _RC()
    _CMAP = {
        "green": "green", "yellow": "yellow", "red": "red",
        "cyan": "cyan",   "dim": "dim",       "white": "white",
        "magenta": "magenta",
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


_HERE           = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_PATH  = os.path.join(_HERE, "vue_nuxt_creator_knowledge.json")
SELF_UPDATE_URL = ""

MODEL          = "qwen2.5-coder:7b"
KEEP_ALIVE     = -1
IS_WIN         = sys.platform == "win32"
DEV_PORT       = 3000
LAUNCH_TIMEOUT = 120
HEALTH_POLL    = 2
MAX_FIX_ROUNDS = 3
KNOWLEDGE_TTL  = 86400

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


_DEFAULT_KNOWLEDGE = {
    "_v": 3,
    "_fetched_at": 0,
    "latest_versions": {
        "vue":                  "3.5.x",
        "nuxt":                 "4.4.x",
        "vite":                 "6.x",
        "pinia":                "2.x",
        "vue-router":           "4.x",
        "vitest":               "3.x",
        "@nuxt/ui":             "3.x",
        "@nuxtjs/tailwindcss":  "6.x",
    },
    "vue_flags": {
        "--typescript":    "Add TypeScript support",
        "--jsx":           "Add JSX support",
        "--router":        "Add Vue Router",
        "--pinia":         "Add Pinia state management",
        "--vitest":        "Add Vitest unit testing",
        "--cypress":       "Add Cypress e2e testing",
        "--playwright":    "Add Playwright e2e testing",
        "--nightwatch":    "Add Nightwatch e2e testing",
        "--eslint":        "Add ESLint",
        "--prettier":      "Add Prettier",
        "--oxlint":        "Add Oxlint (faster linter)",
        "--rolldown-vite": "Use Rolldown-Vite (experimental)",
        "--default":       "Accept all defaults (non-interactive)",
    },
    "nuxt_flags": {
        "--no-install":      "Skip npm install after scaffold",
        "--offline":         "Use local cache only",
        "--preferOffline":   "Prefer local cache",
        "--nightly":         "Use nightly Nuxt build",
        "--force":           "Overwrite existing directory",
        "--packageManager":  "npm | pnpm | yarn | bun",
        "--modules":         "Comma-separated Nuxt modules to add",
    },
    "vue_create_cmds": {
        "npm":  "npm create vue@latest",
        "pnpm": "pnpm create vue@latest",
        "yarn": "yarn create vue@latest",
        "bun":  "bun create vue@latest",
    },
    # FIX-F: npm create nuxt@latest removed — use npx nuxi@latest init only
    "nuxt_create_cmds": {
        "npx_nuxi_latest": "npx nuxi@latest init",
        "npx_nuxi_3":      "npx nuxi@3 init",
    },
    "vue_dev_cmds":  {"npm": "npm run dev", "pnpm": "pnpm dev", "yarn": "yarn dev", "bun": "bun dev"},
    "nuxt_dev_cmds": {"npm": "npm run dev", "pnpm": "pnpm dev", "yarn": "yarn dev", "bun": "bun dev"},
    "install_cmds":  {"npm": "npm install", "pnpm": "pnpm install", "yarn": "yarn install", "bun": "bun install"},
    "pm_create_cmd":   {},
    "bad_strategies":  {},
    "yarn_version":    {},
    "project_journal": [],
}


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


_PACKAGES_TO_TRACK = [
    "vue", "nuxt", "vite", "pinia", "vue-router", "vitest",
    "@nuxt/ui", "@nuxtjs/tailwindcss", "@nuxtjs/color-mode",
    "@nuxt/content", "@nuxtjs/i18n", "@pinia/nuxt",
]

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
                    cb(f"  ✓ {pkg:<35s} {ver}", "green", "normal", {})
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
        staging = os.path.join(_HERE, "vue_nuxt_creator_v1_next.py")
        with open(staging, "w", encoding="utf-8") as f:
            f.write(remote_src)
        cb(f"New version available! Staged at {staging}", "magenta", "self_update", {})
    except Exception:
        pass


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
    (r'\b(?:in|on|at|inside)\s+(?:the\s+)?([A-Za-z])\s+(?:drive|vrive|disk|partition)\b',
     lambda m: m.group(1).upper() + ":\\"),
    (r'\b([A-Za-z]):[\\\/]',
     lambda m: m.group(1).upper() + ":\\"),
]

_TYPO_MAP = {
    "proect": "project", "Proect": "Project",
    "foldr":  "folder",  "deskt0p": "desktop",
    "vrive":  "drive",
}

_PATH_AI_PROMPT = """Extract ONLY the file path from the text.
Rules:
- Output ONLY the path, nothing else
- Fix typos (proect->project, vrive->drive)
- Convert natural language (e.g. "project in c drive" -> C:\\project)
- Windows: drive:\\folder  |  Unix: /absolute/path  |  ~/relative
- Drive only (e.g. "c drive") -> C:\\
- Never add explanation"""

_PATH_STRIP_PATTERNS = [
    r'\b(?:in|on|at|inside)\s+(?:the\s+)?[A-Za-z]\s+(?:drive|vrive|disk|partition)\b',
    r'\b(?:in|at|inside|into|under|within)\s+[A-Za-z]:[\\\/][^\s,;]*',
    r'\b(?:in|at|inside|into|under|within)\s+\/[^\s,;]*',
    r'\b(?:in|at|inside|into|under|within)\s+~[^\s,;]*',
    r'[A-Za-z]:\\(?:[^\\\s<>|:"]+\\)*[^\\\s<>|:"]*',
    r'[A-Za-z]:\\[^\s]+',
    r'(?<!\w)\/[^\s,;\"\']+',
    r'(?<!\w)~[^\s,;\"\']+',
    r'(?:path|dir(?:ectory)?|folder)\s*[=:]\s*[^\s,;\"\']+',
    r'\bin\s+[A-Za-z]\s+(?:drive|vrive|disk|partition)\b',
    r'\bin\s+(?:test|the\s+test)\b',
]

_PATH_HINT_PATTERNS = [
    r'[A-Za-z]:[\\\/]',
    r'(?<!\w)[\/~]',
    r'\b(?:in|at|inside|into|under|within)\b',
    r'\b(?:path|dir(?:ectory)?|folder|drive|disk|vrive)\b',
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
# FIX-F-3: _run() now redirects stdin from os.devnull so interactive prompts
# never block — the child process sees EOF on stdin immediately.
# ─────────────────────────────────────────────────────────────────────────────
def _decode(b: bytes) -> str:
    """Decode bytes to str, trying utf-8 first then cp1252, never raising."""
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
    """Run cmd in cwd with stdin from /dev/null (prevents interactive hangs).
    Uses binary stdout/stderr + explicit decode to avoid Windows cp1252 crash."""
    try:
        with open(os.devnull, "rb") as devnull:
            kwargs: dict = dict(
                shell=True, cwd=cwd,
                stdin=devnull,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # NOTE: text=False — we decode manually to avoid codec errors
            )
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            proc = subprocess.Popen(cmd, **kwargs)
            try:
                stdout_b, stderr_b = proc.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_b, stderr_b = proc.communicate()
            ok = proc.returncode == 0
        return ok, _decode(stdout_b), _decode(stderr_b)
    except Exception as e:
        return False, "", str(e)

def _run_silent(cmd: str, cwd=None) -> tuple:
    try:
        with open(os.devnull, "rb") as devnull:
            r = subprocess.run(
                cmd, shell=True, capture_output=True,
                cwd=cwd, stdin=devnull,
            )
        return r.returncode == 0, _decode(r.stdout).strip(), _decode(r.stderr).strip()
    except Exception as e:
        return False, "", str(e)

def _ver(cmd: str) -> str:
    try:
        kwargs: dict = dict(shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
        if IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
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
        ("pnpm",  "pnpm --version"),
        ("yarn",  "yarn --version"),
        ("bun",   "bun --version"),
    ]
    cb("🖥  Environment Health", "cyan", "normal", {})
    for name, cmd in checks:
        found = bool(_which(name))
        cb(
            f"  {'✓' if found else '✗'} {name:<10s} {_ver(cmd) if found else 'not found'}",
            "green" if found else "red", "normal", {},
        )


_PNPM_SOFT = [r"ERR_PNPM_IGNORED_BUILD_SCRIPTS", r"ERR_PNPM_NO_GLOBAL_BIN_DIR"]

def _is_pnpm_soft(t: str) -> bool:
    return any(re.search(p, t) for p in _PNPM_SOFT)

def _verify_installed(project_path: str, lib: str) -> bool:
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

def classify_error(stderr: str, stdout: str = "",
                   project_path: str = "", lib: str = "") -> str:
    stderr = stderr or ""
    stdout = stdout or ""
    if _is_pnpm_soft(stderr + stdout):
        if not lib or _verify_installed(project_path, lib):
            return EK.SOFT_SUCCESS
    c = (stderr + stdout).lower()
    if any(k in c for k in [
        'command "dlx" not found', "unknown command",
        "unknown subcommand", "is not a yarn command",
    ]):
        return EK.UNKNOWN_SUBCOMMAND
    if any(k in c for k in [
        "enotfound", "etimedout", "fetch failed", "econnrefused",
        "network error", "socket hang up",
    ]):
        return EK.NETWORK
    if any(k in c for k in ["eacces", "permission denied", "access denied"]):
        return EK.PERMISSION
    if any(k in c for k in ["peer dep", "incompatible", "conflict"]):
        return EK.VERSION_CONFLICT
    if any(k in c for k in ["404", "not found", "no matching version", "e404"]):
        return EK.NOT_FOUND
    return EK.GENERIC


PM_ALIASES = {"npm": "npm", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun"}
DEFAULT_PM = "npm"

def detect_pm(req: str) -> str:
    lo = req.lower()
    for alias, name in PM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lo):
            return name
    return DEFAULT_PM

def detect_yarn_version(k: dict) -> str:
    cached = k.get("yarn_version", {}).get(MK)
    if cached:
        return cached
    try:
        r     = subprocess.run("yarn --version", shell=True, capture_output=True, text=True)
        major = int(r.stdout.strip().split(".")[0]) if r.stdout.strip()[:1].isdigit() else 1
        ver   = "v4" if major >= 2 else "v1"
        k.setdefault("yarn_version", {})[MK] = ver
        _save_knowledge(k)
        return ver
    except Exception:
        return "v1"


@dataclass
class Intent:
    framework:    str
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


def _cache_key(framework: str, pm: str) -> str:
    return f"{framework}_{pm}"

def k_get_create(k, fw, pm):
    return k.get("pm_create_cmd", {}).get(MK, {}).get(_cache_key(fw, pm))

def k_set_create(k, fw, pm, cmd):
    k.setdefault("pm_create_cmd", {}).setdefault(MK, {})[_cache_key(fw, pm)] = cmd
    _save_knowledge(k)

def k_clear_create(k, fw, pm, cb):
    mk  = k.get("pm_create_cmd", {}).get(MK, {})
    key = _cache_key(fw, pm)
    if key in mk:
        del mk[key]
        _save_knowledge(k)
    cb(f"🗑️  Cleared cached create-cmd for {fw}/{pm}", "yellow", "warning", {})

def k_is_bad(k, key):
    return key in k.get("bad_strategies", {}).get(MK, [])

def k_mark_bad(k, key):
    b = k.setdefault("bad_strategies", {}).setdefault(MK, [])
    if key not in b:
        b.append(key)
        _save_knowledge(k)

def k_reset_strategies(k, fw, pm, cb):
    prefix = f"{fw}_{pm}"
    mk     = k.get("bad_strategies", {}).get(MK, [])
    k["bad_strategies"][MK] = [x for x in mk if prefix not in x]
    _save_knowledge(k)
    cb(f"🗑️  Reset bad strategies for {fw}/{pm}", "yellow", "warning", {})

def k_full_reset(k, cb):
    for key in ["pm_create_cmd", "bad_strategies"]:
        if MK in k.get(key, {}):
            del k[key][MK]
    _save_knowledge(k)
    cb("♻️  Full knowledge-cache reset for this machine", "yellow", "warning", {})

def k_add_journal(k, entry):
    k.setdefault("project_journal", []).append(entry)
    _save_knowledge(k)


def _build_vue_strategies(intent: Intent, k: dict) -> list:
    result = []
    pm     = intent.pm
    name   = intent.project_name

    flags = list(intent.flags)
    _FEATURE_FLAGS = {
        "--typescript", "--jsx", "--router", "--pinia", "--vitest",
        "--cypress", "--playwright", "--nightwatch", "--eslint",
        "--prettier", "--oxlint", "--rolldown-vite",
    }
    if not any(f in _FEATURE_FLAGS for f in flags):
        if "--default" not in flags:
            flags.append("--default")

    flag_str = " ".join(flags)

    def add(sname, base):
        full = f"{base} {name} {flag_str}".strip()
        if not k_is_bad(k, f"{MK}__{sname}"):
            result.append((sname, full))

    cached = k_get_create(k, "vue", pm)
    if cached:
        add(f"cached_vue_{pm}", cached)

    cmds = k.get("vue_create_cmds", _DEFAULT_KNOWLEDGE["vue_create_cmds"])
    if pm == "yarn":
        ver  = detect_yarn_version(k)
        base = "yarn dlx create-vue@latest" if ver == "v4" else "yarn create vue@latest"
        alt  = "yarn create vue@latest"     if ver == "v4" else "yarn dlx create-vue@latest"
        add(f"default_vue_{pm}", base)
        add(f"alt_vue_{pm}",     alt)
    else:
        add(f"default_vue_{pm}", cmds.get(pm, cmds["npm"]))

    add("npx_vue_fallback", "npx create-vue@latest")
    add("npx_vue_nolast",   "npx create-vue")
    return result


def _build_nuxt_strategies(intent: Intent, k: dict) -> list:
    """
    FIX-F: Build fully non-interactive nuxi init strategies.

    Flags appended automatically (never from user flags):
      --template minimal   → skips "Which template?" prompt
      --no-install         → skips dependency install (we run it separately)
      --gitInit false      → skips git init prompt
      --packageManager pm  → tells nuxi which PM to use
      --modules ""         → skips "Browse modules?" prompt (empty = none)

    FIX-F-4: Only npx nuxi@latest init and npx nuxi@3 init are tried.
    npm create nuxt@latest is unreliable with flags and is not used.
    """
    result = []
    pm     = intent.pm
    name   = intent.project_name

    # Strip any --template flag the AI may have added (FIX-G)
    user_flags = [f for f in intent.flags
                  if not f.startswith("--template") and f != "--gitInit"]

    # Build the non-interactive flag string
    # NOTE: --modules "" must be quoted properly on shell
    non_interactive = (
        f'--template minimal '
        f'--packageManager {pm} '
        f'--no-install '
        f'--gitInit false '
        f'--modules ""'
    )

    extra_flags = " ".join(user_flags)
    flag_str = f"{extra_flags} {non_interactive}".strip() if extra_flags else non_interactive

    def add(sname, base):
        full = f"{base} {name} {flag_str}".strip()
        if not k_is_bad(k, f"{MK}__{sname}"):
            result.append((sname, full))

    cached = k_get_create(k, "nuxt", pm)
    if cached:
        add(f"cached_nuxt_{pm}", cached)

    add("npx_nuxi_latest", "npx nuxi@latest init")
    add("npx_nuxi_3",      "npx nuxi@3 init")
    return result


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
        fw = self.intent.framework
        for fix_round in range(MAX_FIX_ROUNDS + 1):
            strats = (
                _build_vue_strategies  if fw == "vue" else
                _build_nuxt_strategies
            )(self.intent, self.k)

            if not strats:
                self._cb("No strategies left.", "red", "error", {})
                break

            for sname, cmd in strats:
                self._cb(f"  ▶ [{fix_round}] {sname}: {cmd}", "cyan", "normal", {})
                ok, out, err = _run(cmd, self.intent.install_dir)
                ek           = classify_error(err, out, self.intent.project_path)

                # Also check if the project dir was created (nuxi exits 0 but
                # sometimes with warnings we'd otherwise misclassify)
                project_exists = os.path.isdir(self.intent.project_path)

                if (ok or ek == EK.SOFT_SUCCESS) and project_exists:
                    if ek == EK.SOFT_SUCCESS:
                        self._cb("  ⚠  Non-zero exit but project created", "yellow", "warning", {})
                    self._cb(f"  ✓ Created with '{sname}'", "green", "normal", {})
                    idx = cmd.find(self.intent.project_name)
                    if idx > 0:
                        k_set_create(self.k, fw, self.intent.pm, cmd[:idx].strip())
                    self.log.append({"strategy": sname, "cmd": cmd, "ok": True})
                    return True

                combined = (err + out).strip()
                self._cb(f"  ✗ Failed ({ek}): {combined[:300]}", "red", "error", {})
                if sname.startswith("cached_") and ek in (EK.UNKNOWN_SUBCOMMAND, EK.GENERIC):
                    k_clear_create(self.k, fw, self.intent.pm, self._cb)
                k_mark_bad(self.k, f"{MK}__{sname}")
                self.log.append({"strategy": sname, "cmd": cmd, "ok": False, "ek": ek})
                if ek == EK.NETWORK:
                    self._cb("  ⏳ Retrying in 5s...", "yellow", "warning", {})
                    time.sleep(5)
                    ok2, out2, _ = _run(cmd, self.intent.install_dir)
                    if ok2 and os.path.isdir(self.intent.project_path):
                        return True

            if   fix_round == 0: k_reset_strategies(self.k, fw, self.intent.pm, self._cb)
            elif fix_round == 1: k_clear_create(self.k, fw, self.intent.pm, self._cb)
            elif fix_round >= 2: k_full_reset(self.k, self._cb)

        self._cb("❌ All strategies exhausted.", "red", "error", {})
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
        if lib and _verify_installed(wd, lib):
            return True, ""
        self._cb(f"  ✗ {label} permanently failed", "red", "error", {})
        return False, err + out


def get_latest_from_registry(pkg: str) -> str:
    return _fetch_latest_version(pkg) or "unknown"

def get_installed_version(project_path: str, pkg: str) -> str:
    parts = pkg.lstrip("@").replace("/", os.sep).split(os.sep)
    if pkg.startswith("@"):
        parts = [f"@{parts[0]}", *parts[1:]]
    p = os.path.join(project_path, "node_modules", *parts, "package.json")
    try:
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f).get("version", "unknown")
    except Exception:
        pass
    return "unknown"

def check_and_upgrade(intent: Intent, k: dict, cb: PrintCallback, task_id: str = ""):
    cb(f"🔄 Checking {intent.framework} version...", "cyan", "normal", {})
    main_pkg  = "vue" if intent.framework == "vue" else "nuxt"
    installed = get_installed_version(intent.project_path, main_pkg)
    latest    = k.get("latest_versions", {}).get(main_pkg, "unknown")
    if latest == "unknown":
        latest = get_latest_from_registry(main_pkg)
    cb(f"  Installed: {installed}  Latest: {latest}", "white", "normal", {})

    upgrade_data = {
        "task_id":       task_id,
        "framework":     intent.framework,
        "installed":     installed,
        "latest":        latest,
        "project_path":  intent.project_path,
        "project_name":  intent.project_name,
        "pm":            intent.pm,
        "needs_upgrade": installed not in ("unknown", latest),
    }
    cb(
        f"Upgrade available: {installed} → {latest}" if upgrade_data["needs_upgrade"]
        else f"{intent.framework} is up to date ({installed})",
        "cyan", "upgrader", upgrade_data,
    )
    if upgrade_data["needs_upgrade"] and not task_id:
        try:
            answer = input(f"\n  Upgrade to {latest}? (y/n): ").strip().lower()
        except EOFError:
            answer = "n"
        if answer == "y":
            _do_upgrade(intent, k, cb, task_id)

def _do_upgrade(intent: Intent, k: dict, cb: PrintCallback, task_id: str = ""):
    runner       = Runner(intent, k, cb)
    pkg          = "vue@latest" if intent.framework == "vue" else "nuxt@latest"
    install_base = k.get("install_cmds", _DEFAULT_KNOWLEDGE["install_cmds"]).get(
        intent.pm, "npm install"
    )
    ok, _ = runner.run_cmd(f"{install_base} {pkg}", label=f"upgrade {intent.framework}")
    cb(
        "✅ Upgrade complete" if ok else "❌ Upgrade failed",
        "green" if ok else "red", "upgrader",
        {"task_id": task_id, "project_path": intent.project_path, "success": ok},
    )


def _npm_exists(name: str) -> bool:
    try:
        s, _ = _http_get(f"https://registry.npmjs.org/{name}/latest", timeout=6)
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
    base   = k.get("install_cmds", _DEFAULT_KNOWLEDGE["install_cmds"]).get(intent.pm, "npm install")
    runner = Runner(intent, k, cb)
    cb(f"📦 Installing {len(libs)} librar{'y' if len(libs) == 1 else 'ies'}...", "cyan", "normal", {})
    for lib in libs:
        ok, _ = runner.run_cmd(f"{base} {lib}", label=f"install {lib}", lib=lib)
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
    dev_key = f"{intent.framework}_dev_cmds"
    dev_cmd = k.get(dev_key, _DEFAULT_KNOWLEDGE.get(dev_key, {})).get(intent.pm, "npm run dev")
    url     = f"http://localhost:{port}"
    cb(f"🚀 Launching {intent.framework} dev server on port {port}...", "cyan", "normal", {})
    env         = os.environ.copy()
    env["PORT"] = str(port)
    try:
        proc = subprocess.Popen(
            dev_cmd, shell=True, cwd=intent.project_path, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # binary mode — decode manually to avoid Windows cp1252 crash
        )
    except Exception as e:
        cb(f"  ✗ Could not start dev server: {e}", "red", "error", {})
        return False

    server_output = []

    def _cap(stream):
        for raw_line in stream:
            server_output.append(_decode(raw_line).rstrip())

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


# FIX-G: Nuxt system prompt updated — --template is now forbidden; TypeScript
# in Nuxt is built-in via the minimal template, not a flag.
_PARSE_SYSTEM_VUE = """You are a Vue.js CLI assistant.
Return ONLY valid JSON — no markdown, no explanation, no code fences.
Schema:
{
  "project_name": "kebab-case-name",
  "flags": ["--typescript"],
  "libraries": ["lib1"],
  "global_tools": [],
  "python_packages": []
}

PATH RULE: All path/directory references have already been removed.
Ignore any leftover location words (drive, folder, directory, test, vrive).
Extract ONLY project properties.

VALID VUE FLAGS (only include what the user explicitly asked for):
  --typescript   --jsx   --router   --pinia   --vitest
  --cypress      --playwright   --nightwatch
  --eslint       --prettier     --oxlint   --rolldown-vite

DO NOT add --default — the runner adds it automatically when needed.
"""

_PARSE_SYSTEM_NUXT = """You are a Nuxt.js CLI assistant.
Return ONLY valid JSON — no markdown, no explanation, no code fences.
Schema:
{
  "project_name": "kebab-case-name",
  "flags": [],
  "libraries": ["lib1"],
  "global_tools": [],
  "python_packages": []
}

PATH RULE: All path/directory references have already been removed.
Ignore leftover words like drive, folder, directory, test, vrive, c.
Extract ONLY project properties.

IMPORTANT RULES:
- "flags" must be an EMPTY ARRAY [] in almost all cases.
- NEVER put --template, --packageManager, --no-install, --gitInit, or
  --modules in flags — the runner adds these automatically.
- TypeScript is BUILT-IN to the minimal Nuxt template. Do NOT add any
  typescript flag. If the user asks for TypeScript, just ignore it
  (it is already included).
- For Tailwind CSS, put "@nuxtjs/tailwindcss" in "libraries".
- ONLY valid flags to include (rare):
    --nightly   --force   --offline

NUXT MODULES (put in "libraries" if the user mentioned them):
  @nuxt/ui  @nuxt/content  @nuxtjs/tailwindcss  @nuxtjs/color-mode
  @nuxtjs/i18n  @pinia/nuxt  nuxt-icon  @vueuse/nuxt
"""

def _detect_framework(req: str) -> str:
    lo = req.lower()
    if re.search(r'\bnuxt\b', lo):
        return "nuxt"
    if re.search(r'\bvue\b', lo):
        return "vue"
    return "vue"

def parse_user_request(req: str, framework: str) -> dict:
    if not HAS_OLLAMA:
        name = re.sub(r"[^a-z0-9-]", "-", req.lower().split()[0])[:30] or "my-app"
        return {
            "project_name": name, "flags": [],
            "libraries": [], "global_tools": [], "python_packages": [],
        }
    system = _PARSE_SYSTEM_NUXT if framework == "nuxt" else _PARSE_SYSTEM_VUE
    r      = _ollama.chat(
        model=MODEL, keep_alive=KEEP_ALIVE,
        messages=[
            {"role": "system", "content": system},
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
    return name or "my-app"


def _gen_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"

def _store_session(tid, d):
    user_csh[tid] = d

def _get_session(tid):
    return user_csh.get(tid)

def _clear_session(tid):
    user_csh.pop(tid, None)


def _run_pipeline(
    clean_request: str,
    install_dir:   str,
    framework:     str,
    config:        dict,
    k:             dict,
    cb:            PrintCallback,
    task_id:       str,
) -> tuple:
    pm           = detect_pm(clean_request)
    project_name = _sanitize_name(config.get("project_name", "my-app"))
    flags        = config.get("flags", [])
    raw_libs     = config.get("libraries", [])
    raw_globals  = config.get("global_tools", [])
    py_pkgs      = config.get("python_packages", [])

    good_libs, bad_libs = validate_libraries(raw_libs, cb)

    intent = Intent(
        framework=framework, project_name=project_name, pm=pm,
        flags=flags, libraries=good_libs, global_tools=raw_globals,
        install_dir=install_dir,
    )

    cb(f"📋 Plan [{framework.upper()}]", "green", "normal", {})
    cb(f"  PM          : {pm}",                                        "cyan", "normal", {})
    cb(f"  Project     : {project_name}",                              "cyan", "normal", {})
    cb(f"  Flags       : {intent.flag_str() or 'none (using defaults)'}", "cyan", "normal", {})
    cb(f"  Install dir : {install_dir}",                               "cyan", "normal", {})
    cb(f"  Libraries   : {', '.join(good_libs) or 'none'}",            "cyan", "normal", {})
    cb(f"  Fixer       : {'enabled' if HAS_FIXER else 'disabled'}",    "cyan", "normal", {})
    if bad_libs:
        cb(f"  Skipped     : {', '.join(b['original'] for b in bad_libs)}", "yellow", "warning", {})

    runner = Runner(intent, k, cb)
    if not runner.create_project():
        k_add_journal(k, {
            "machine": MK, "fw": framework, "pm": pm, "project": project_name,
            "worked": False, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        return False, None

    # Always run bare install after scaffold (resolves peer deps)
    install_cmd = k.get("install_cmds", _DEFAULT_KNOWLEDGE["install_cmds"]).get(pm, "npm install")
    cb(f"📦 Running base dependency install ({install_cmd})...", "cyan", "normal", {})
    ok_install, _ = runner.run_cmd(install_cmd, label="base install")
    if not ok_install:
        cb("  ⚠  Base install had issues — continuing anyway", "yellow", "warning", {})

    install_libraries(intent, good_libs, k, cb)
    install_global_tools(intent, raw_globals, k, cb)
    ensure_pip_packages(py_pkgs, cb)

    cb("🔬 Verifying project health...", "cyan", "normal", {})
    healthy = launch_and_verify(intent, k, cb)

    k_add_journal(k, {
        "machine": MK, "fw": framework, "pm": pm, "project": project_name,
        "flags": flags, "worked": True, "landing_healthy": healthy,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    dev_cmd = k.get(f"{framework}_dev_cmds", {}).get(pm, "npm run dev")
    cb(f"🎉 '{project_name}' [{framework}] is ready!", "green", "normal", {})
    cb(f"  cd {intent.project_path}", "white", "normal", {})
    cb(f"  {dev_cmd}",                "white", "normal", {})
    if bad_libs:
        cb(
            f"Unresolved libraries: {', '.join(b['original'] for b in bad_libs)}",
            "yellow", "warning", {},
        )

    return healthy, intent


def start(
    user_request: str,
    cb:           PrintCallback = _noop_cb,
    message:      Optional[dict] = None,
) -> bool:
    if message is None:
        message = {"type": "init", "data": {}}
    msg_type = message.get("type", "init")
    msg_data = message.get("data", {}) or {}
    k        = _load_knowledge()

    if msg_type == "upgrade_permission":
        task_id = msg_data.get("task_id", "")
        fw      = msg_data.get("framework", "vue")
        intent  = Intent(
            framework=fw,
            project_name=msg_data.get("project_name", ""),
            pm=msg_data.get("pm", "npm"),
            flags=[], libraries=[], global_tools=[],
            install_dir=os.path.dirname(msg_data.get("project_path", os.getcwd())),
        )
        _do_upgrade(intent, k, cb, task_id=task_id)
        if task_id:
            _clear_session(task_id)
        return True

    if msg_type == "ask_for_path":
        task_id = msg_data.get("task_id", "")
        cb("🔍 Extracting path from reply...", "dim", "normal", {})
        path, method = extract_path_from_text(user_request)
        if not path:
            cb("  Could not extract a path.", "yellow", "warning", {})
            ask_payload = dict(msg_data)
            ask_payload["task_id"] = task_id or _gen_task_id()
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
        framework     = msg_data.get("framework", "vue")
        if task_id:
            _clear_session(task_id)

        refresh_knowledge(k, cb)

        healthy, intent = _run_pipeline(
            clean_request, install_dir, framework, config, k, cb, task_id or ""
        )
        if intent:
            check_and_upgrade(intent, k, cb, task_id=task_id or "")
        return healthy

    task_id = _gen_task_id()
    cb("⚡ Vue / Nuxt Agentic Creator v1.3", "cyan", "normal", {})
    cb("   Self-Learning · Non-Interactive Scaffold · Auto-Updater", "cyan", "normal", {})

    pm = detect_pm(user_request)
    bootstrap_environment(pm, cb)
    print_env_health(cb)

    check_self_update(cb)

    cb("🔍 Extracting install path...", "dim", "normal", {})
    path, method = extract_path_from_text(user_request)
    install_dir: Optional[str] = None
    if path:
        install_dir = os.path.abspath(os.path.expanduser(path.rstrip("/\\")))
        os.makedirs(install_dir, exist_ok=True)
        cb(f"  ✓ Install dir ({method}): {install_dir}", "green", "normal", {})
    else:
        cb("  No path found in prompt.", "yellow", "warning", {})

    clean_request = strip_path_from_prompt(user_request)
    cb(f"  🧹 Cleaned prompt: {clean_request}", "dim", "normal", {})

    framework = _detect_framework(clean_request)
    cb(f"  🔧 Framework detected: {framework.upper()}", "cyan", "normal", {})

    cb("🤖 Parsing request...", "dim", "normal", {})
    try:
        config = parse_user_request(clean_request, framework)
    except Exception as e:
        cb(f"⚠  AI parse failed ({e}) — using defaults", "yellow", "warning", {})
        config = {
            "project_name": "my-app", "flags": [],
            "libraries": [], "global_tools": [], "python_packages": [],
        }

    if not install_dir:
        ask_payload = {
            "task_id":      task_id,
            "user_request": clean_request,
            "config":       config,
            "pm":           pm,
            "framework":    framework,
        }
        _store_session(task_id, ask_payload)
        cb(
            "📂 Please reply with the full path where you want to create the project.",
            "yellow", "ask_path", ask_payload,
        )
        return False

    refresh_knowledge(k, cb)

    healthy, intent = _run_pipeline(
        clean_request, install_dir, framework, config, k, cb, task_id
    )
    if intent:
        check_and_upgrade(intent, k, cb, task_id=task_id)
    return healthy


if __name__ == "__main__":
    try:
        import readline  
    except ImportError:
        pass

    def _cli_cb(text="", color="white", msg_type="normal", data=None):
        _terminal_cb(text, color, msg_type, data)

    print("\n💡 Vue / Nuxt Agentic Creator v1.3")
    print("   Say 'vue' or 'nuxt' anywhere in your description.\n")
    user_request = input("   Describe your project: ").strip()
    if not user_request:
        print("No input.")
        sys.exit(1)

    ok = start(user_request, cb=_cli_cb, message={"type": "init", "data": {}})

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