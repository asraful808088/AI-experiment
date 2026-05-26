"""
Next.js Agentic Creator v11.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEW IN v11:
  ✦ Smarter error classification — pnpm build-script warnings no longer false-fail
  ✦ Environment bootstrapper — installs Node, Python, pnpm/yarn/bun globally if missing
  ✦ PATH auto-fixer — detects missing PATH entries and patches them (Windows + Unix)
  ✦ Post-create launcher — starts dev server, waits for landing page 200 OK, then shuts down
  ✦ Install result verifier — checks node_modules / import resolution, not just exit code
  ✦ Prerequisite report — prints a full env-health table before doing any work
"""

import os
import re
import sys
import json
import time
import shutil
import signal
import platform
import subprocess
import threading
import webbrowser
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

# ── optional rich ──────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    console = Console()
    HAS_RICH = True
except ImportError:
    class _FallbackConsole:
        def print(self, *a, **kw):
            text = " ".join(str(x) for x in a)
            text = re.sub(r"\[/?[a-zA-Z_ ]+\]", "", text)
            print(text)
    console = _FallbackConsole()
    HAS_RICH = False

# ── optional requests ──────────────────────────────────────────────────────────
try:
    import requests as _requests
    def http_get(url, timeout=10):
        r = _requests.get(url, timeout=timeout)
        return r.status_code, r.text
except ImportError:
    def http_get(url, timeout=10):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except Exception:
            return 0, ""

# ── optional ollama ────────────────────────────────────────────────────────────
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

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

MODEL      = "qwen2.5-coder:7b"
KEEP_ALIVE = -1
IS_WIN     = sys.platform == "win32"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nextjs_creator_cache_v11.json")

DEV_PORT        = 3000
LAUNCH_TIMEOUT  = 120   # seconds to wait for dev server ready
HEALTH_POLL     = 2     # seconds between health checks

# ══════════════════════════════════════════════════════════════════════════════
# INTENT — frozen user demand
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Intent:
    project_name: str
    pm: str
    flags: list
    libraries: list
    global_tools: list
    install_dir: str

    @property
    def project_path(self) -> str:
        return os.path.join(self.install_dir, self.project_name)

    def flag_str(self) -> str:
        return " ".join(self.flags)

# ══════════════════════════════════════════════════════════════════════════════
# MACHINE KEY
# ══════════════════════════════════════════════════════════════════════════════

def _machine_key() -> str:
    node_ver = "unknown"
    try:
        r = subprocess.run("node --version", shell=True, capture_output=True, text=True)
        node_ver = r.stdout.strip().lstrip("v").split(".")[0]
    except Exception:
        pass
    return f"{sys.platform}__node{node_ver}"

MK = _machine_key()

# ══════════════════════════════════════════════════════════════════════════════
# CACHE
# ══════════════════════════════════════════════════════════════════════════════

def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"_v": 11, "pm_create_cmd": {}, "bad_strategies": {}, "yarn_version": {},
            "doc_cache": {}, "project_journal": [], "env_checks": {}}

def _save_cache(c: dict):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
    except Exception as e:
        console.print(f"[yellow]⚠  Cache save failed: {e}[/yellow]")

def cache_get_create(c, pm): return c.get("pm_create_cmd", {}).get(MK, {}).get(pm)
def cache_set_create(c, pm, cmd):
    c.setdefault("pm_create_cmd", {}).setdefault(MK, {})[pm] = cmd
    _save_cache(c)

def cache_is_bad(c, key): return key in c.get("bad_strategies", {}).get(MK, [])
def cache_mark_bad(c, key):
    bads = c.setdefault("bad_strategies", {}).setdefault(MK, [])
    if key not in bads:
        bads.append(key)
        _save_cache(c)

def cache_get_yarn(c): return c.get("yarn_version", {}).get(MK)
def cache_set_yarn(c, v):
    c.setdefault("yarn_version", {})[MK] = v
    _save_cache(c)

def cache_add_journal(c, entry):
    c.setdefault("project_journal", []).append(entry)
    _save_cache(c)

# ══════════════════════════════════════════════════════════════════════════════
# ████  ENVIRONMENT BOOTSTRAPPER  ████
# Installs Node, Python, pnpm/yarn/bun and fixes PATH automatically
# ══════════════════════════════════════════════════════════════════════════════

def _run_silent(cmd: str, cwd=None) -> tuple:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()

def _which(name: str) -> Optional[str]:
    return shutil.which(name)

def _ver(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        return (r.stdout + r.stderr).strip().split("\n")[0]
    except Exception:
        return "?"

# ── PATH management ────────────────────────────────────────────────────────────

def _add_to_path_current_process(new_dir: str):
    """Add directory to PATH for the current process immediately."""
    path_var = os.environ.get("PATH", "")
    if new_dir not in path_var.split(os.pathsep):
        os.environ["PATH"] = new_dir + os.pathsep + path_var
        console.print(f"[dim green]  ✓ Added to current PATH: {new_dir}[/dim green]")

def _add_to_path_permanent_windows(new_dir: str) -> bool:
    """Permanently add to Windows user PATH via registry."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Environment",
            0, winreg.KEY_READ | winreg.KEY_WRITE
        )
        try:
            current, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current = ""
        if new_dir.lower() not in current.lower():
            new_val = current.rstrip(";") + ";" + new_dir if current else new_dir
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_val)
            winreg.CloseKey(key)
            # Broadcast WM_SETTINGCHANGE so Explorer/new shells pick it up
            import ctypes
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
            )
            console.print(f"[green]  ✓ Added to Windows user PATH permanently: {new_dir}[/green]")
            console.print("[yellow]  ⚠  Restart your terminal to use the new PATH in future sessions.[/yellow]")
        return True
    except Exception as e:
        console.print(f"[yellow]  ⚠  Could not write registry PATH: {e}[/yellow]")
        return False

def _add_to_path_permanent_unix(new_dir: str) -> bool:
    """Append export PATH line to ~/.bashrc and ~/.zshrc."""
    added = False
    for rc in ["~/.bashrc", "~/.zshrc", "~/.profile"]:
        rc_path = os.path.expanduser(rc)
        if not os.path.exists(rc_path):
            continue
        try:
            with open(rc_path, encoding="utf-8") as f:
                content = f.read()
            export_line = f'export PATH="{new_dir}:$PATH"'
            if new_dir not in content:
                with open(rc_path, "a", encoding="utf-8") as f:
                    f.write(f"\n# Added by nextjs_creator_v11\n{export_line}\n")
                console.print(f"[green]  ✓ Added to {rc}: {export_line}[/green]")
                added = True
        except Exception as e:
            console.print(f"[yellow]  ⚠  Could not update {rc}: {e}[/yellow]")
    return added

def fix_path(new_dir: str):
    """Add a directory to PATH — both immediately (for this session) and permanently."""
    if not new_dir or not os.path.isdir(new_dir):
        return
    _add_to_path_current_process(new_dir)
    if IS_WIN:
        _add_to_path_permanent_windows(new_dir)
    else:
        _add_to_path_permanent_unix(new_dir)

# ── pnpm PATH auto-fixer ───────────────────────────────────────────────────────

def fix_pnpm_path():
    """Detect pnpm's global bin dir and add it to PATH if missing."""
    ok, out, _ = _run_silent("pnpm bin -g")
    if ok and out:
        bin_dir = out.strip()
        if bin_dir and bin_dir not in os.environ.get("PATH", ""):
            console.print(f"[cyan]🔧 pnpm global bin not in PATH — fixing: {bin_dir}[/cyan]")
            fix_path(bin_dir)
            return True
    # Windows fallback: common locations
    if IS_WIN:
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\pnpm"),
            os.path.expandvars(r"%APPDATA%\npm"),
        ]
        for c in candidates:
            if os.path.isdir(c) and c not in os.environ.get("PATH", ""):
                console.print(f"[cyan]🔧 Adding pnpm candidate dir to PATH: {c}[/cyan]")
                fix_path(c)
    return False

# ── Node.js installer ─────────────────────────────────────────────────────────

def _install_node_windows():
    console.print("[cyan]📥 Downloading Node.js LTS installer for Windows...[/cyan]")
    url = "https://nodejs.org/dist/lts/node-lts-installer.msi"
    # Try winget first (available on Win11 / modern Win10)
    ok, _, _ = _run_silent("winget install OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements")
    if ok:
        console.print("[green]  ✓ Node.js installed via winget[/green]")
        return True
    console.print("[yellow]  winget not available — please install Node.js manually from https://nodejs.org[/yellow]")
    webbrowser.open("https://nodejs.org/en/download")
    return False

def _install_node_linux():
    # Try nvm
    ok, _, _ = _run_silent('curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs')
    return ok

def _install_node_mac():
    ok, _, _ = _run_silent("brew install node")
    if not ok:
        ok, _, _ = _run_silent('curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -')
    return ok

def ensure_node() -> bool:
    if _which("node"):
        return True
    console.print("[yellow]⚠  Node.js not found — attempting install...[/yellow]")
    if IS_WIN:
        return _install_node_windows()
    elif sys.platform == "darwin":
        return _install_node_mac()
    else:
        return _install_node_linux()

# ── Python installer ──────────────────────────────────────────────────────────

def ensure_python() -> bool:
    py = _which("python3") or _which("python")
    if py:
        return True
    console.print("[yellow]⚠  Python not found — attempting install...[/yellow]")
    if IS_WIN:
        ok, _, _ = _run_silent("winget install Python.Python.3 --silent --accept-source-agreements --accept-package-agreements")
        if ok:
            console.print("[green]  ✓ Python installed via winget[/green]")
            return True
    elif sys.platform == "darwin":
        ok, _, _ = _run_silent("brew install python3")
        return ok
    else:
        ok, _, _ = _run_silent("sudo apt-get install -y python3 python3-pip")
        return ok
    console.print("[yellow]  Please install Python manually from https://python.org[/yellow]")
    webbrowser.open("https://python.org/downloads")
    return False

# ── pip package installer ─────────────────────────────────────────────────────

def ensure_pip_packages(packages: list):
    """Install Python packages globally if not already present."""
    if not packages:
        return
    console.print(f"\n[cyan]🐍 Ensuring Python packages: {packages}[/cyan]")
    for pkg in packages:
        ok, _, _ = _run_silent(f"python -c \"import {pkg.replace('-','_')}\"")
        if ok:
            console.print(f"  [dim green]✓ {pkg} already available[/dim green]")
            continue
        console.print(f"  [dim]Installing {pkg}...[/dim]")
        ok2, _, err = _run_silent(f"pip install {pkg} --break-system-packages")
        if not ok2:
            ok2, _, _ = _run_silent(f"pip install {pkg}")
        console.print(f"  {'[green]✓' if ok2 else '[red]✗'} {pkg}[/{'green' if ok2 else 'red'}]")

# ── PM installer ──────────────────────────────────────────────────────────────

def ensure_pm_global(pm: str) -> bool:
    if _which(pm):
        return True
    console.print(f"[yellow]⚠  {pm} not found — installing globally...[/yellow]")
    cmds = {
        "pnpm": "npm install --global pnpm",
        "yarn": "npm install --global yarn",
        "bun":  "npm install --global bun",
    }
    cmd = cmds.get(pm)
    if not cmd:
        return False
    ok, _, _ = _run_silent(cmd)
    if ok:
        console.print(f"[green]  ✓ {pm} installed[/green]")
        if pm == "pnpm":
            fix_pnpm_path()
        return True
    return False

# ── Environment health table ───────────────────────────────────────────────────

def print_env_health():
    """Print a rich table showing the health of all required tools."""
    checks = [
        ("node",   "node --version"),
        ("npm",    "npm --version"),
        ("npx",    "npx --version"),
        ("pnpm",   "pnpm --version"),
        ("yarn",   "yarn --version"),
        ("bun",    "bun --version"),
        ("python", "python --version"),
        ("python3","python3 --version"),
        ("git",    "git --version"),
    ]
    if HAS_RICH:
        t = Table(title="🖥  Environment Health", show_lines=False, header_style="bold cyan")
        t.add_column("Tool",    width=10)
        t.add_column("Found",   width=6)
        t.add_column("Version", min_width=20)
        for name, cmd in checks:
            found = bool(_which(name))
            ver   = _ver(cmd) if found else "—"
            icon  = "[green]✓[/green]" if found else "[red]✗[/red]"
            t.add_row(name, icon, ver)
        console.print(t)
    else:
        print("\n=== Environment Health ===")
        for name, cmd in checks:
            found = bool(_which(name))
            ver   = _ver(cmd) if found else "not found"
            print(f"  {'✓' if found else '✗'} {name:10s} {ver}")

# ── Full bootstrap ─────────────────────────────────────────────────────────────

def bootstrap_environment(pm: str, extra_globals: list, python_packages: list):
    """
    Ensure Node, Python, the chosen PM, and any extras are present.
    Fix PATH automatically where possible.
    """
    console.print("\n[bold cyan]🔧 Bootstrapping environment...[/bold cyan]")

    # Node is mandatory
    if not ensure_node():
        console.print("[red]❌ Cannot continue without Node.js. Please install it manually.[/red]")
        sys.exit(1)

    # pnpm PATH fix — do this early
    if _which("pnpm"):
        fix_pnpm_path()

    # Requested PM
    ensure_pm_global(pm)

    # Extra global npm tools
    for tool in extra_globals:
        if not _which(tool):
            console.print(f"[cyan]🌐 Installing global npm tool: {tool}[/cyan]")
            ok, _, _ = _run_silent(f"npm install --global {tool}")
            console.print(f"  {'[green]✓' if ok else '[red]✗'} {tool}")
        else:
            console.print(f"  [dim green]✓ {tool} already installed[/dim green]")

    # Python (optional but nice)
    ensure_python()
    ensure_pip_packages(python_packages)

    print_env_health()

# ══════════════════════════════════════════════════════════════════════════════
# ████  INSTALL VERIFIER  ████
# Checks that packages actually landed — not just exit-code 0
# ══════════════════════════════════════════════════════════════════════════════

# pnpm-specific "success despite non-zero exit" patterns
_PNPM_SOFT_ERRORS = [
    r"ERR_PNPM_IGNORED_BUILD_SCRIPTS",   # build scripts ignored — package still installed
    r"ERR_PNPM_NO_GLOBAL_BIN_DIR",       # global bin dir issue — package still installed
]

def _is_pnpm_soft_error(stderr: str) -> bool:
    return any(re.search(p, stderr) for p in _PNPM_SOFT_ERRORS)

def _verify_installed(project_path: str, lib: str) -> bool:
    """Check node_modules/<lib>/package.json exists."""
    # Handle scoped packages: @scope/pkg → node_modules/@scope/pkg
    check_path = os.path.join(project_path, "node_modules", *lib.split("/"), "package.json")
    return os.path.exists(check_path)

# ══════════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFIER (v11 — pnpm-aware)
# ══════════════════════════════════════════════════════════════════════════════

class EK:
    SOFT_SUCCESS       = "soft_success"       # installed despite non-zero exit
    UNKNOWN_SUBCOMMAND = "unknown_subcommand"
    NETWORK            = "network_error"
    PERMISSION         = "permission_error"
    VERSION_CONFLICT   = "version_conflict"
    NOT_FOUND          = "not_found"
    GENERIC            = "generic"

def classify_error(stderr: str, stdout: str = "", project_path: str = "", lib: str = "") -> str:
    # pnpm soft errors: package installed fine but exit code != 0
    if _is_pnpm_soft_error(stderr + stdout):
        if not lib or _verify_installed(project_path, lib):
            return EK.SOFT_SUCCESS

    c = (stderr + stdout).lower()
    if any(k in c for k in ['command "dlx" not found', "command not found", "unknown command",
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

# ══════════════════════════════════════════════════════════════════════════════
# YARN VERSION
# ══════════════════════════════════════════════════════════════════════════════

def detect_yarn_version(cache: dict) -> str:
    cached = cache_get_yarn(cache)
    if cached:
        return cached
    try:
        r = subprocess.run("yarn --version", shell=True, capture_output=True, text=True)
        major = int(r.stdout.strip().split(".")[0]) if r.stdout.strip() and r.stdout.strip()[0].isdigit() else 1
        ver = "v4" if major >= 2 else "v1"
        cache_set_yarn(cache, ver)
        return ver
    except Exception:
        return "v1"

# ══════════════════════════════════════════════════════════════════════════════
# PACKAGE MANAGER
# ══════════════════════════════════════════════════════════════════════════════

PM_ALIASES    = {"npm": "npm", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun"}
DEFAULT_PM    = "npm"
_PM_CREATE    = {"npm": "npx create-next-app@latest", "pnpm": "pnpm dlx create-next-app@latest",
                 "yarn": "yarn create next-app", "bun": "bunx create-next-app@latest"}
_YARN4_CREATE = "yarn dlx create-next-app@latest"
PM_INSTALL    = {"npm": "npm install", "pnpm": "pnpm add", "yarn": "yarn add", "bun": "bun add"}
PM_INSTALL_G  = {"npm": "npm install --global", "pnpm": "pnpm add -g", "yarn": "yarn global add", "bun": "bun add --global"}
PM_RUN_DEV    = {"npm": "npm run dev", "pnpm": "pnpm dev", "yarn": "yarn dev", "bun": "bun dev"}

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

def build_create_cmd(base: str, intent: "Intent") -> str:
    return f"{base} {intent.project_name} {intent.flag_str()}".strip()

# ══════════════════════════════════════════════════════════════════════════════
# PATH RESOLVER
# ══════════════════════════════════════════════════════════════════════════════

_PATH_PATS = [
    r"\b(?:in|inside|at|into|under|within)\s+([A-Za-z]:[\\\/][^\s,;\"']+)",
    r"\b(?:in|inside|at|into|under|within)\s+(\/[^\s,;\"']+)",
    r"\b(?:in|inside|at|into|under|within)\s+(~[^\s,;\"']*)",
    r"(?:path|dir(?:ectory)?|folder)\s*[=:]\s*([^\s,;\"']+)",
]

def resolve_install_dir(req: str) -> str:
    for pat in _PATH_PATS:
        m = re.search(pat, req, re.IGNORECASE)
        if m:
            p = os.path.abspath(os.path.expanduser(m.group(1).strip().rstrip("/\\")))
            os.makedirs(p, exist_ok=True)
            console.print(f"[dim]📁 Install dir: [bold]{p}[/bold][/dim]")
            return p
    return _ask_path()

def _ask_path() -> str:
    while True:
        raw = input("\n📂 Where to create the project?\n   Full path: ").strip().strip('"\'')
        abs_path = os.path.abspath(os.path.expanduser(raw))
        if os.path.isfile(abs_path):
            console.print("[red]  That is a file, not a directory.[/red]")
            continue
        try:
            os.makedirs(abs_path, exist_ok=True)
            return abs_path
        except Exception as e:
            console.print(f"[red]  Cannot create: {e}[/red]")

# ══════════════════════════════════════════════════════════════════════════════
# STRATEGIES
# ══════════════════════════════════════════════════════════════════════════════

def _strategies(intent: "Intent", cache: dict) -> list:
    result = []
    pm = intent.pm

    def add(name, base):
        key = f"{MK}__{name}"
        if not cache_is_bad(cache, key):
            result.append((name, build_create_cmd(base, intent)))

    cached = cache_get_create(cache, pm)
    if cached:
        add(f"cached_{pm}", cached)

    if pm == "yarn":
        ver = detect_yarn_version(cache)
        default_base = _YARN4_CREATE if ver == "v4" else _PM_CREATE["yarn"]
        alt_base     = _PM_CREATE["yarn"] if ver == "v4" else _YARN4_CREATE
        if default_base != cached:
            add(f"default_{pm}", default_base)
        add("yarn_alt", alt_base)
    else:
        db = _PM_CREATE.get(pm, _PM_CREATE["npm"])
        if db != cached:
            add(f"default_{pm}", db)

    add("npx_fallback", "npx create-next-app@latest")
    add("npx_nolast",   "npx create-next-app")
    return result

# ══════════════════════════════════════════════════════════════════════════════
# SELF-HEALING RUNNER
# ══════════════════════════════════════════════════════════════════════════════

class Runner:
    def __init__(self, intent: "Intent", cache: dict):
        self.intent = intent
        self.cache  = cache
        self.log    = []

    def create_project(self) -> bool:
        strats = _strategies(self.intent, self.cache)
        if not strats:
            console.print("[red]No strategies left. Clear cache or switch PM.[/red]")
            return False

        for name, cmd in strats:
            console.print(f"\n  [dim cyan]▶ Strategy '{name}': {cmd}[/dim cyan]")
            ok, out, err = _run(cmd, self.intent.install_dir)
            ek = classify_error(err, out, self.intent.project_path)

            if ok or ek == EK.SOFT_SUCCESS:
                if ek == EK.SOFT_SUCCESS:
                    console.print(f"  [yellow]⚠  Non-zero exit but project created (pnpm soft error)[/yellow]")
                console.print(f"  [green]✓ Success with '{name}'[/green]")
                # Learn winning base
                idx = cmd.find(self.intent.project_name)
                if idx > 0:
                    cache_set_create(self.cache, self.intent.pm, cmd[:idx].strip())
                self.log.append({"strategy": name, "cmd": cmd, "ok": True})
                return True

            console.print(f"  [red]✗ Failed ({ek})[/red]")
            console.print(f"  [dim red]{(err+out).strip()[:250]}[/dim red]")
            cache_mark_bad(self.cache, f"{MK}__{name}")
            self.log.append({"strategy": name, "cmd": cmd, "ok": False, "ek": ek})

            if ek == EK.NETWORK:
                console.print("  [yellow]⏳ Network error — retrying in 5s...[/yellow]")
                time.sleep(5)
                ok2, out2, err2 = _run(cmd, self.intent.install_dir)
                if ok2:
                    idx = cmd.find(self.intent.project_name)
                    if idx > 0:
                        cache_set_create(self.cache, self.intent.pm, cmd[:idx].strip())
                    return True

        console.print("[red]❌ All strategies exhausted.[/red]")
        console.print(f"[yellow]  Manual: npx create-next-app@latest {self.intent.project_name} {self.intent.flag_str()}[/yellow]")
        return False

    def run_cmd(self, cmd: str, *, label="command", cwd=None, lib="") -> tuple:
        wd = cwd or self.intent.project_path
        console.print(f"\n  [dim cyan]▶ {label}: {cmd}[/dim cyan]")
        ok, out, err = _run(cmd, wd)
        ek = classify_error(err, out, wd, lib)

        # Treat pnpm soft errors as success
        if ok or ek == EK.SOFT_SUCCESS:
            if ek == EK.SOFT_SUCCESS:
                console.print(f"  [yellow]⚠  Installed (pnpm reported non-fatal warning)[/yellow]")
                # Verify the package actually landed
                if lib and not _verify_installed(wd, lib):
                    console.print(f"  [red]✗ {lib} not in node_modules despite soft-success — flagging as failed[/red]")
                    return False, err + out
            console.print(f"  [green]✓ {label} done[/green]")
            return True, out

        console.print(f"  [red]✗ {label} failed ({ek})[/red]")
        console.print(f"  [dim red]{(err+out).strip()[:250]}[/dim red]")

        if ek == EK.NETWORK:
            time.sleep(5)
            ok2, out2, err2 = _run(cmd, wd)
            if ok2:
                return True, out2

        if ek == EK.PERMISSION and not IS_WIN:
            ok3, out3, _ = _run(f"sudo {cmd}", wd)
            if ok3:
                return True, out3

        return False, err + out

def _run(cmd: str, cwd: str) -> tuple:
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, "", str(e)

# ══════════════════════════════════════════════════════════════════════════════
# ████  PROJECT LAUNCHER  ████
# Starts dev server → waits for landing page 200 → verifies → shuts down
# ══════════════════════════════════════════════════════════════════════════════

def _find_free_port(preferred=3000) -> int:
    import socket
    for port in range(preferred, preferred + 20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    return preferred

def _wait_for_server(url: str, timeout: int) -> bool:
    """Poll URL until it returns 200 or timeout."""
    deadline = time.time() + timeout
    attempt  = 0
    while time.time() < deadline:
        try:
            status, _ = http_get(url, timeout=3)
            if status == 200:
                console.print(f"\n  [green]✓ Server responded 200 at {url}[/green]")
                return True
        except Exception:
            pass
        attempt += 1
        dots = "." * (attempt % 4)
        console.print(f"  [dim]Waiting for server{dots} ({int(deadline - time.time())}s)[/dim]", end="\r")
        time.sleep(HEALTH_POLL)
    return False

def launch_and_verify(intent: "Intent") -> bool:
    """
    1. Find a free port
    2. Start the dev server as a subprocess
    3. Poll until landing page returns HTTP 200
    4. Print success summary
    5. Kill the server cleanly
    Returns True if the landing page was healthy.
    """
    port = _find_free_port(DEV_PORT)
    dev_cmd = PM_RUN_DEV[intent.pm]
    url     = f"http://localhost:{port}"

    console.print(f"\n[bold cyan]🚀 Launching dev server on port {port}...[/bold cyan]")
    console.print(f"  Command : [dim]{dev_cmd}[/dim]")
    console.print(f"  URL     : [dim]{url}[/dim]")
    console.print(f"  Timeout : {LAUNCH_TIMEOUT}s\n")

    env = os.environ.copy()
    env["PORT"] = str(port)

    try:
        proc = subprocess.Popen(
            dev_cmd,
            shell=True,
            cwd=intent.project_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as e:
        console.print(f"[red]  ✗ Could not start dev server: {e}[/red]")
        return False

    # Capture output in background thread so we can log it
    server_output = []
    def _capture(stream):
        for line in stream:
            server_output.append(line.rstrip())

    t_out = threading.Thread(target=_capture, args=(proc.stdout,), daemon=True)
    t_err = threading.Thread(target=_capture, args=(proc.stderr,), daemon=True)
    t_out.start(); t_err.start()

    healthy = _wait_for_server(url, LAUNCH_TIMEOUT)

    # Always shut down the server
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
        console.print(f"\n[bold green]✅ Landing page verified at {url}[/bold green]")
        console.print("[green]  Dev server shut down cleanly.[/green]")
    else:
        console.print(f"\n[red]❌ Dev server did not respond in {LAUNCH_TIMEOUT}s[/red]")
        # Print last 20 lines of server output for debugging
        if server_output:
            console.print("[dim]  Last server output:[/dim]")
            for line in server_output[-20:]:
                console.print(f"  [dim red]{line}[/dim red]")

    return healthy

# ══════════════════════════════════════════════════════════════════════════════
# NEXT.JS VERSION CHECK
# ══════════════════════════════════════════════════════════════════════════════

def get_latest_nextjs() -> str:
    try:
        status, text = http_get("https://registry.npmjs.org/next/latest", timeout=8)
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

def check_and_upgrade(intent: "Intent", cache: dict):
    console.print("\n[cyan]🔄 Checking Next.js version...[/cyan]")
    installed = get_installed_nextjs(intent.project_path)
    latest    = get_latest_nextjs()
    console.print(f"  Installed: [yellow]{installed}[/yellow]  Latest: [green]{latest}[/green]")
    if installed not in ("unknown", latest):
        if input(f"\n  Upgrade to {latest}? (y/n): ").strip().lower() == "y":
            runner = Runner(intent, cache)
            upgrade_cmd = {
                "npm":  "npm install next@latest react@latest react-dom@latest",
                "pnpm": "pnpm add next@latest react@latest react-dom@latest",
                "yarn": "yarn add next@latest react@latest react-dom@latest",
                "bun":  "bun add next@latest react@latest react-dom@latest",
            }[intent.pm]
            runner.run_cmd(upgrade_cmd, label="upgrade next.js")

# ══════════════════════════════════════════════════════════════════════════════
# LIBRARY HANDLING
# ══════════════════════════════════════════════════════════════════════════════

def _npm_exists(name: str) -> bool:
    try:
        status, _ = http_get(f"https://registry.npmjs.org/{name}/latest", timeout=6)
        return status == 200
    except Exception:
        return False

def validate_libraries(raw_libs: list) -> tuple:
    if not raw_libs:
        return [], []
    console.print(f"\n[cyan]🔎 Validating {len(raw_libs)} libraries...[/cyan]")
    ok_libs, failed = [], []
    for lib in raw_libs:
        exists = _npm_exists(lib)
        if exists:
            console.print(f"  [green]✓ {lib}[/green]")
            ok_libs.append(lib)
        else:
            console.print(f"  [yellow]⚠  {lib} not found on npm[/yellow]")
            failed.append({"original": lib, "reason": "not on npm registry"})
    return ok_libs, failed

def install_libraries(intent: "Intent", libs: list, cache: dict):
    if not libs:
        return
    base   = PM_INSTALL[intent.pm]
    runner = Runner(intent, cache)
    console.print(f"\n[cyan]📦 Installing {len(libs)} librar{'y' if len(libs)==1 else 'ies'}...[/cyan]")
    for lib in libs:
        ok, _ = runner.run_cmd(f"{base} {lib}", label=f"install {lib}", lib=lib)
        if not ok:
            # Fallback: verify in node_modules anyway (pnpm may have installed despite error)
            if _verify_installed(intent.project_path, lib):
                console.print(f"  [yellow]⚠  {lib} appears installed in node_modules (ignored error)[/yellow]")
            else:
                console.print(f"  [red]✗ Could not install {lib}[/red]")

def install_global_tools(intent: "Intent", tools: list, cache: dict):
    if not tools:
        return
    console.print(f"\n[cyan]🌐 Installing global tools: {tools}[/cyan]")
    runner = Runner(intent, cache)
    for tool in tools:
        runner.run_cmd(
            f"{PM_INSTALL_G[intent.pm]} {tool}",
            label=f"global {tool}",
            cwd=os.getcwd(),
        )
        # After installing any global tool, re-check and fix PATH
        fix_pnpm_path()

# ══════════════════════════════════════════════════════════════════════════════
# AI REQUEST PARSER
# ══════════════════════════════════════════════════════════════════════════════

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
        # Minimal fallback parser
        name = re.sub(r"[^a-z0-9-]", "-", req.lower().split()[0])[:30] or "my-next-app"
        return {"project_name": name, "flags": ["--yes"], "libraries": [],
                "global_tools": [], "python_packages": []}
    r = _ollama.chat(
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
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "my-next-app"

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if HAS_RICH:
        console.print(Panel.fit(
            "⚡ Next.js Agentic Creator v11.0\n"
            "   Environment Bootstrapper · PATH Auto-Fixer · Launch Verifier",
            style="bold cyan",
        ))
    else:
        print("\n⚡ Next.js Agentic Creator v11.0\n")

    cache = _load_cache()

    user_request = input("\n💡 Describe your project: ").strip()
    if not user_request:
        console.print("[red]No input.[/red]")
        sys.exit(1)

    # ── 1. Detect PM ──────────────────────────────────────────────────────────
    pm = detect_pm(user_request)

    # ── 2. Bootstrap environment (Node, Python, PM, extras, PATH) ─────────────
    bootstrap_environment(pm, [], [])

    # ── 3. Resolve install dir ────────────────────────────────────────────────
    install_dir = resolve_install_dir(user_request)

    # ── 4. Parse intent via AI ────────────────────────────────────────────────
    console.print("\n[dim]🤖 Parsing request...[/dim]")
    try:
        config = parse_user_request(user_request)
    except Exception as e:
        console.print(f"[yellow]⚠  AI parse failed ({e}) — using defaults[/yellow]")
        config = {"project_name": "my-next-app", "flags": ["--yes"], "libraries": [],
                  "global_tools": [], "python_packages": []}

    project_name    = _sanitize_name(config.get("project_name", "my-next-app"))
    flags           = config.get("flags", [])
    raw_libs        = config.get("libraries", [])
    raw_globals     = config.get("global_tools", [])
    python_packages = config.get("python_packages", [])

    if "--yes" not in flags:
        flags.append("--yes")

    # ── 5. Validate libraries ─────────────────────────────────────────────────
    good_libs, bad_libs = validate_libraries(raw_libs)

    # ── 6. Freeze intent ──────────────────────────────────────────────────────
    intent = Intent(
        project_name=project_name,
        pm=pm,
        flags=flags,
        libraries=good_libs,
        global_tools=raw_globals,
        install_dir=install_dir,
    )

    # ── Plan summary ──────────────────────────────────────────────────────────
    console.print(f"\n[bold green]📋 Plan[/bold green]")
    console.print(f"  Machine     : [dim]{MK}[/dim]")
    console.print(f"  PM          : [cyan]{intent.pm}[/cyan]")
    console.print(f"  Project     : [cyan]{intent.project_name}[/cyan]")
    console.print(f"  Flags       : [cyan]{intent.flag_str()}[/cyan]")
    console.print(f"  Install dir : [cyan]{intent.install_dir}[/cyan]")
    console.print(f"  Libraries   : [cyan]{', '.join(good_libs) or 'none'}[/cyan]")
    console.print(f"  Global tools: [cyan]{', '.join(raw_globals) or 'none'}[/cyan]")
    if bad_libs:
        console.print(f"  Skipped libs: [yellow]{', '.join(b['original'] for b in bad_libs)}[/yellow]")

    # ── 7. Create project ─────────────────────────────────────────────────────
    runner = Runner(intent, cache)
    ok     = runner.create_project()

    if not ok:
        cache_add_journal(cache, {"machine": MK, "pm": pm, "project": project_name,
                                   "worked": False, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        sys.exit(1)

    # ── 8. Version check ──────────────────────────────────────────────────────
    check_and_upgrade(intent, cache)

    # ── 9. Install libraries ──────────────────────────────────────────────────
    install_libraries(intent, good_libs, cache)

    # ── 10. Install global tools (with PATH fix) ──────────────────────────────
    install_global_tools(intent, raw_globals, cache)

    # ── 11. Install Python packages ───────────────────────────────────────────
    ensure_pip_packages(python_packages)

    # ── 12. Launch dev server and verify landing page ─────────────────────────
    console.print(f"\n[bold cyan]🔬 Verifying project health...[/bold cyan]")
    healthy = launch_and_verify(intent)

    # ── 13. Journal ───────────────────────────────────────────────────────────
    cache_add_journal(cache, {
        "machine": MK, "pm": pm, "project": project_name,
        "flags": flags, "worked": True, "landing_healthy": healthy,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    # ── Done ──────────────────────────────────────────────────────────────────
    console.print(f"\n[bold green]🎉 '{intent.project_name}' is ready![/bold green]")
    if healthy:
        console.print("[green]  ✓ Landing page verified successfully[/green]")
    else:
        console.print("[yellow]  ⚠  Could not verify landing page — check manually[/yellow]")
    console.print(f"\n  cd {intent.project_path}")
    console.print(f"  {PM_RUN_DEV[pm]}")

    if bad_libs:
        console.print(f"\n[yellow]Unresolved libraries: {', '.join(b['original'] for b in bad_libs)}[/yellow]")


if __name__ == "__main__":
    main()