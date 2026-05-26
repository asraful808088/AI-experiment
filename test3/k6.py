"""
Next.js Agentic Creator v10.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KEY DESIGN PRINCIPLES:
  1. USER INTENT is frozen into an Intent object at parse time and NEVER mutated during healing
  2. Every heal attempt rebuilds the full command FROM the frozen intent — never from a broken cmd
  3. Cache is DEEP: stores (pm, os_platform, node_version) tuples so machine-specific knowledge survives
  4. "bad command" cache keys on full strategy name, not fragile 3-word prefix
  5. Doc learning: fetches real Next.js markdown, parses all pm blocks, stores per-pm
  6. Per-project journal: every project gets a log entry with what worked/failed
  7. AI is only used for: (a) parsing user intent, (b) resolving unknown library names
     Everything else is deterministic or doc-driven
"""

import os
import subprocess
import re
import json
import sys
import time
import platform
import requests
from dataclasses import dataclass, field, asdict
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import ollama

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

console = Console()

MODEL      = "qwen2.5-coder:7b"
KEEP_ALIVE = -1

# ══════════════════════════════════════════════════════════════════
# INTENT — frozen user demand, travels unchanged through all healing
# ══════════════════════════════════════════════════════════════════

@dataclass
class Intent:
    """Immutable user demand. Built once from user input. Never changed during healing."""
    project_name: str          # exact sanitized name user asked for
    pm: str                    # npm | yarn | pnpm | bun
    flags: list[str]           # --typescript, --tailwind, etc
    libraries: list[str]       # validated npm packages
    global_tools: list[str]    # tools to install globally
    install_dir: str           # absolute path to create project in

    @property
    def project_path(self) -> str:
        return os.path.join(self.install_dir, self.project_name)

    def flag_str(self) -> str:
        return " ".join(self.flags)

    def describe(self) -> str:
        return (
            f"project='{self.project_name}' pm={self.pm} "
            f"flags={self.flags} libs={self.libraries}"
        )


# ══════════════════════════════════════════════════════════════════
# CACHE — deep, machine-aware, per-pm, per-strategy
# ══════════════════════════════════════════════════════════════════

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nextjs_creator_cache.json")

def _machine_key() -> str:
    """Unique key for this machine's environment — so cache is machine-specific."""
    node_ver = "unknown"
    try:
        r = subprocess.run("node --version", shell=True, capture_output=True, text=True)
        node_ver = r.stdout.strip().lstrip("v").split(".")[0]  # major only: "20"
    except Exception:
        pass
    return f"{sys.platform}__node{node_ver}"


MK = _machine_key()  # e.g. "win32__node20"

DEFAULT_CACHE: dict = {
    "_version": 3,
    "_machine": MK,
    # strategies[machine_key][pm] = {"create": "cmd", "install": "cmd"} — what WORKED
    "strategies": {},
    # bad_strategies[machine_key] = ["strategy_name", ...] — what FAILED permanently
    "bad_strategies": {},
    # yarn_version[machine_key] = "v1" | "v4"
    "yarn_version": {},
    # doc_cache[url] = {"text": ..., "fetched_at": ...}
    "doc_cache": {},
    # pm_create_cmd[machine_key][pm] = exact command that worked
    "pm_create_cmd": {},
    # project_journal: list of {project, pm, flags, worked, failed_strategies, ts}
    "project_journal": [],
}


def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("_version", 0) < 3:
                console.print("[yellow]⚠  Old cache format — migrating to v3[/yellow]")
                data = json.loads(json.dumps(DEFAULT_CACHE))
            console.print(f"[dim]📂 Cache loaded ({MK}): {CACHE_PATH}[/dim]")
            return data
        except Exception as e:
            console.print(f"[yellow]⚠  Cache corrupt ({e}) — starting fresh[/yellow]")
    else:
        console.print(f"[dim]📂 No cache yet — will create: {CACHE_PATH}[/dim]")
    return json.loads(json.dumps(DEFAULT_CACHE))


def _save_cache(cache: dict):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        console.print(f"[yellow]⚠  Cache save failed: {e}[/yellow]")


# ── cache accessors ────────────────────────────────────────────────────────

def cache_get_create_cmd(cache: dict, pm: str) -> Optional[str]:
    return cache.get("pm_create_cmd", {}).get(MK, {}).get(pm)


def cache_set_create_cmd(cache: dict, pm: str, cmd: str):
    cache.setdefault("pm_create_cmd", {}).setdefault(MK, {})[pm] = cmd
    console.print(f"[dim green]💾 Learned create cmd for [{pm}] on {MK}: '{cmd}'[/dim green]")
    _save_cache(cache)


def cache_is_strategy_bad(cache: dict, strategy_key: str) -> bool:
    return strategy_key in cache.get("bad_strategies", {}).get(MK, [])


def cache_mark_strategy_bad(cache: dict, strategy_key: str):
    bads = cache.setdefault("bad_strategies", {}).setdefault(MK, [])
    if strategy_key not in bads:
        bads.append(strategy_key)
        console.print(f"[dim red]💾 Strategy marked bad on {MK}: '{strategy_key}'[/dim red]")
        _save_cache(cache)


def cache_get_yarn_version(cache: dict) -> Optional[str]:
    return cache.get("yarn_version", {}).get(MK)


def cache_set_yarn_version(cache: dict, ver: str):
    cache.setdefault("yarn_version", {})[MK] = ver
    _save_cache(cache)


def cache_get_doc(cache: dict, url: str) -> Optional[str]:
    entry = cache.get("doc_cache", {}).get(url)
    if not entry:
        return None
    age_h = (time.time() - entry.get("fetched_at", 0)) / 3600
    if age_h > 24:
        return None
    console.print(f"[dim green]📄 Doc cache hit ({age_h:.1f}h old): {url}[/dim green]")
    return entry["text"]


def cache_set_doc(cache: dict, url: str, text: str):
    cache.setdefault("doc_cache", {})[url] = {"text": text, "fetched_at": time.time()}
    _save_cache(cache)


def cache_add_journal(cache: dict, entry: dict):
    cache.setdefault("project_journal", []).append(entry)
    _save_cache(cache)


def cache_get_past_project(cache: dict, pm: str) -> Optional[dict]:
    """Return the last successful project entry for this pm on this machine."""
    for entry in reversed(cache.get("project_journal", [])):
        if entry.get("pm") == pm and entry.get("machine") == MK and entry.get("worked"):
            return entry
    return None


# ══════════════════════════════════════════════════════════════════
# YARN VERSION
# ══════════════════════════════════════════════════════════════════

def detect_yarn_version(cache: dict) -> str:
    cached = cache_get_yarn_version(cache)
    if cached:
        console.print(f"[dim]🧶 Yarn version (cached on {MK}): {cached}[/dim]")
        return cached
    try:
        r = subprocess.run("yarn --version", shell=True, capture_output=True, text=True)
        raw = r.stdout.strip()
        major = int(raw.split(".")[0]) if raw and raw[0].isdigit() else 1
        ver = "v4" if major >= 2 else "v1"
        console.print(f"[dim]🧶 Yarn detected: {raw} → {ver}[/dim]")
        cache_set_yarn_version(cache, ver)
        return ver
    except Exception:
        cache_set_yarn_version(cache, "v1")
        return "v1"


# ══════════════════════════════════════════════════════════════════
# PACKAGE MANAGER
# ══════════════════════════════════════════════════════════════════

PM_ALIASES = {"npm": "npm", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun"}
DEFAULT_PM  = "npm"

# Canonical base create commands — used ONLY when nothing is cached
_PM_CREATE_DEFAULT = {
    "npm":  "npx create-next-app@latest",
    "pnpm": "pnpm dlx create-next-app@latest",
    "yarn": "yarn create next-app",   # v1 default
    "bun":  "bunx create-next-app@latest",
}
_YARN_V4_CREATE = "yarn dlx create-next-app@latest"

PM_INSTALL_CMD        = {"npm": "npm install",          "pnpm": "pnpm add",     "yarn": "yarn add",        "bun": "bun add"}
PM_INSTALL_GLOBAL_CMD = {"npm": "npm install --global", "pnpm": "pnpm add -g",  "yarn": "yarn global add", "bun": "bun add --global"}
PM_UPGRADE_NEXT_CMD   = {
    "npm":  "npm install next@latest react@latest react-dom@latest",
    "pnpm": "pnpm add next@latest react@latest react-dom@latest",
    "yarn": "yarn add next@latest react@latest react-dom@latest",
    "bun":  "bun add next@latest react@latest react-dom@latest",
}
PM_RUN_DEV = {"npm": "npm run dev", "pnpm": "pnpm dev", "yarn": "yarn dev", "bun": "bun dev"}


def get_create_base(pm: str, cache: dict) -> str:
    """
    Return the best known create command BASE (no project name / flags).
    Priority: (1) machine-specific cache, (2) yarn-version-aware default, (3) npm fallback
    """
    cached = cache_get_create_cmd(cache, pm)
    if cached:
        console.print(f"[dim green]✓ Using cached create base for [{pm}]: '{cached}'[/dim green]")
        return cached
    if pm == "yarn":
        ver = detect_yarn_version(cache)
        base = _YARN_V4_CREATE if ver == "v4" else _PM_CREATE_DEFAULT["yarn"]
    else:
        base = _PM_CREATE_DEFAULT.get(pm, _PM_CREATE_DEFAULT["npm"])
    console.print(f"[dim]Default create base for [{pm}]: '{base}'[/dim]")
    return base


def build_create_cmd(base: str, intent: Intent) -> str:
    """
    Build the full create command from a base + frozen intent.
    Intent is NEVER modified — we always re-derive from it.
    """
    return f"{base} {intent.project_name} {intent.flag_str()}".strip()


def detect_pm(user_request: str) -> str:
    lowered = user_request.lower()
    for alias, name in PM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return name
    return DEFAULT_PM


def is_pm_available(pm: str) -> bool:
    try:
        return subprocess.run(f"{pm} --version", shell=True, capture_output=True).returncode == 0
    except Exception:
        return False


def ensure_pm(pm: str) -> bool:
    if is_pm_available(pm):
        return True
    install_map = {
        "pnpm": "npm install --global pnpm",
        "yarn": "npm install --global yarn",
        "bun":  "npm install --global bun",
    }
    cmd = install_map.get(pm)
    if not cmd:
        return False
    console.print(f"[cyan]Installing {pm} globally...[/cyan]")
    return subprocess.run(cmd, shell=True, capture_output=True).returncode == 0


# ══════════════════════════════════════════════════════════════════
# PATH RESOLUTION
# ══════════════════════════════════════════════════════════════════

_PATH_PATTERNS = [
    r"\b(?:in|inside|at|into|under|within)\s+([A-Za-z]:[\\\/][^\s,;\"']+)",
    r"\b(?:in|inside|at|into|under|within)\s+(\/[^\s,;\"']+)",
    r"\b(?:in|inside|at|into|under|within)\s+(~[^\s,;\"']*)",
    r"(?:path|dir(?:ectory)?|folder)\s*[=:]\s*([^\s,;\"']+)",
]


def extract_path_from_request(req: str) -> Optional[str]:
    for pat in _PATH_PATTERNS:
        m = re.search(pat, req, re.IGNORECASE)
        if m:
            p = m.group(1).strip().rstrip("/\\")
            if p:
                return p
    return None


def resolve_install_dir(user_request: str) -> str:
    path = extract_path_from_request(user_request)
    if path:
        abs_path = os.path.abspath(os.path.expanduser(path))
        console.print(f"[dim]📁 Install dir: [bold]{abs_path}[/bold][/dim]")
        os.makedirs(abs_path, exist_ok=True)
        return abs_path
    console.print("[yellow]⚠  No install path found in request.[/yellow]")
    return _ask_path()


def _ask_path() -> str:
    while True:
        raw = input("\n📂 Where to create the project?\n   Full path: ").strip()
        if not raw:
            continue
        raw = raw.strip('"\'').rstrip(">$#%").strip()
        raw = re.sub(r"\\{2,}", r"\\", raw).rstrip("/\\").strip()
        abs_path = os.path.abspath(os.path.expanduser(raw))
        if os.path.isfile(abs_path):
            console.print("[red]  That is a file, not a directory.[/red]")
            continue
        try:
            os.makedirs(abs_path, exist_ok=True)
            console.print(f"[green]  ✓ {abs_path}[/green]")
            return abs_path
        except Exception as e:
            console.print(f"[red]  Cannot create: {e}[/red]")


# ══════════════════════════════════════════════════════════════════
# DOC FETCHER + PARSER
# Learn the REAL commands from official docs — not hardcoded guesses
# ══════════════════════════════════════════════════════════════════

NEXTJS_INSTALL_MD = "https://nextjs.org/docs/app/getting-started/installation.md"

_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/markdown, text/plain, */*",
}

# Additional doc URLs for sub-topic learning
NEXTJS_MD_URLS = {
    "install":    NEXTJS_INSTALL_MD,
    "upgrade":    "https://nextjs.org/docs/app/building-your-application/upgrading.md",
    "tailwind":   "https://nextjs.org/docs/app/building-your-application/styling/tailwind-css.md",
    "typescript": "https://nextjs.org/docs/app/building-your-application/configuring/typescript.md",
    "eslint":     "https://nextjs.org/docs/app/building-your-application/configuring/eslint.md",
    "routing":    "https://nextjs.org/docs/app/building-your-application/routing.md",
    "deploy":     "https://nextjs.org/docs/app/building-your-application/deploying.md",
}


def fetch_md(url: str, cache: dict) -> Optional[str]:
    """Fetch a markdown doc — cache first, network second."""
    cached = cache_get_doc(cache, url)
    if cached:
        return cached
    try:
        r = requests.get(url, headers=_FETCH_HEADERS, timeout=15)
        if r.status_code != 200:
            console.print(f"    [dim yellow]HTTP {r.status_code}: {url}[/dim yellow]")
            return None
        ct = r.headers.get("Content-Type", "")
        sniff = r.text[:300].lstrip()
        if sniff.startswith("<") and "markdown" not in ct:
            console.print(f"    [dim yellow]Skipping {url} — not markdown[/dim yellow]")
            return None
        text = r.text[:20000]
        console.print(f"    [dim green]✓ Fetched {len(text)} chars from {url}[/dim green]")
        cache_set_doc(cache, url, text)
        return text
    except Exception as e:
        console.print(f"    [dim red]Fetch error ({url}): {e}[/dim red]")
    return None


def parse_create_cmd_from_docs(markdown: str, pm: str) -> Optional[str]:
    """
    Parse the Next.js install.md to find the real create command for a pm.
    Tries multiple patterns so it handles doc reformats gracefully.
    Returns base command only (no project name placeholder).
    """
    # Pattern 1: ```bash package="yarn"  style blocks
    pattern1 = rf'```bash\s+package="{re.escape(pm)}"(.*?)```'
    blocks = re.findall(pattern1, markdown, re.DOTALL | re.IGNORECASE)
    for block in blocks:
        for line in block.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if "create" in line and ("next-app" in line or "create-next-app" in line):
                    # Strip placeholder project name — we'll add from intent
                    cmd = _strip_placeholder_name(line)
                    if cmd:
                        console.print(f"    [dim green]Doc found (block): '{cmd}'[/dim green]")
                        return cmd

    # Pattern 2: inline lines like `yarn create next-app@latest my-app`
    for line in markdown.splitlines():
        line = line.strip().strip("`")
        for prefix in [f"{pm} create", f"{pm} dlx create-next-app", f"{pm}x create-next-app",
                       f"npx create-next-app"]:
            if line.lower().startswith(prefix.lower()):
                cmd = _strip_placeholder_name(line)
                if cmd:
                    console.print(f"    [dim green]Doc found (inline): '{cmd}'[/dim green]")
                    return cmd

    # Pattern 3: any code block containing the pm name near "create-next-app"
    code_blocks = re.findall(r'```[^\n]*\n(.*?)```', markdown, re.DOTALL)
    for block in code_blocks:
        for line in block.splitlines():
            line = line.strip()
            if pm in line and "create-next-app" in line and "create" in line:
                cmd = _strip_placeholder_name(line)
                if cmd:
                    console.print(f"    [dim green]Doc found (any block): '{cmd}'[/dim green]")
                    return cmd

    return None


def _strip_placeholder_name(cmd_line: str) -> Optional[str]:
    """
    Remove the placeholder project name from a doc command line.
    e.g. "yarn create next-app@latest my-app --yes" → "yarn create next-app@latest"
    e.g. "npx create-next-app@latest my-app"         → "npx create-next-app@latest"
    Also strips flags like --yes since we add those from intent.
    """
    # Remove flags
    cmd_line = re.sub(r"\s+--\S+", "", cmd_line).strip()
    # Known placeholders
    cmd_line = re.sub(r"\s+(my-app|my_app|app-name|project-name|<name>|<project>)\b", "", cmd_line, flags=re.IGNORECASE).strip()
    # If still has a bare word at the end that looks like a name, remove it
    # (doesn't start with - and isn't a command keyword)
    command_keywords = {"npx", "yarn", "pnpm", "bun", "bunx", "create-next-app",
                        "create-next-app@latest", "next-app", "next-app@latest",
                        "create", "dlx", "add", "run", "install"}
    parts = cmd_line.split()
    while parts and parts[-1] not in command_keywords and not parts[-1].startswith("-") and "@" not in parts[-1]:
        parts.pop()
    result = " ".join(parts).strip()
    return result if result else None


def learn_create_cmd_from_docs(pm: str, cache: dict) -> Optional[str]:
    """
    Fetch official Next.js docs and learn the right create command for this pm.
    Saves to cache so we never re-fetch unnecessarily.
    """
    console.print(f"  [cyan]📚 Learning [{pm}] create command from Next.js docs...[/cyan]")
    md = fetch_md(NEXTJS_INSTALL_MD, cache)
    if not md:
        console.print("  [dim red]Could not fetch install docs.[/dim red]")
        return None
    cmd = parse_create_cmd_from_docs(md, pm)
    if cmd:
        cache_set_create_cmd(cache, pm, cmd)
        return cmd
    # Fallback: try npx if pm-specific not found
    npx_cmd = parse_create_cmd_from_docs(md, "npm")
    if npx_cmd:
        console.print(f"  [dim]No {pm} block found — using npx as fallback[/dim]")
        return npx_cmd
    return None


# ══════════════════════════════════════════════════════════════════
# HEALING STRATEGY SYSTEM
# Each strategy is named, deterministic, and derived from frozen intent
# ══════════════════════════════════════════════════════════════════

def _strategies_for(intent: Intent, cache: dict) -> list[tuple[str, str]]:
    """
    Return an ordered list of (strategy_name, full_command) for create-next-app.
    All commands are built from the frozen intent — user's name/flags are ALWAYS preserved.
    Strategies already marked bad on this machine are skipped.
    """
    strategies: list[tuple[str, str]] = []
    pm = intent.pm

    def add(name: str, base: str):
        if not cache_is_strategy_bad(cache, f"{MK}__{name}"):
            full = build_create_cmd(base, intent)
            strategies.append((name, full))
        else:
            console.print(f"  [dim yellow]⊘ Skipping bad strategy: {name}[/dim yellow]")

    # S1: Whatever this machine already knows works (from cache)
    cached_base = cache_get_create_cmd(cache, pm)
    if cached_base:
        add(f"cached_{pm}", cached_base)

    # S2: What we learned from the docs
    doc_base = learn_create_cmd_from_docs(pm, cache)
    if doc_base and doc_base != cached_base:
        add(f"docs_{pm}", doc_base)

    # S3: Hardcoded default for this pm
    if pm == "yarn":
        ver = detect_yarn_version(cache)
        default_base = _YARN_V4_CREATE if ver == "v4" else _PM_CREATE_DEFAULT["yarn"]
    else:
        default_base = _PM_CREATE_DEFAULT.get(pm, _PM_CREATE_DEFAULT["npm"])
    if default_base not in (cached_base, doc_base):
        add(f"default_{pm}", default_base)

    # S4: For yarn — try the other version variant
    if pm == "yarn":
        alt_base = _PM_CREATE_DEFAULT["yarn"] if detect_yarn_version(cache) == "v4" else _YARN_V4_CREATE
        if alt_base not in (cached_base, doc_base, default_base):
            add(f"yarn_alt_version", alt_base)

    # S5: npx universal fallback (always works if Node is installed)
    npx_base = "npx create-next-app@latest"
    if pm != "npm" or npx_base not in (cached_base, doc_base, default_base):
        add("npx_fallback", npx_base)

    # S6: Last resort — older npx without @latest
    add("npx_nolast", "npx create-next-app")

    return strategies


# ══════════════════════════════════════════════════════════════════
# ERROR CLASSIFIER
# ══════════════════════════════════════════════════════════════════

class EK:
    UNKNOWN_SUBCOMMAND = "unknown_subcommand"
    NETWORK            = "network_error"
    PERMISSION         = "permission_error"
    VERSION_CONFLICT   = "version_conflict"
    NOT_FOUND          = "not_found"
    GENERIC            = "generic"


def classify_error(stderr: str, stdout: str = "") -> str:
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


# ══════════════════════════════════════════════════════════════════
# SELF-HEALING RUNNER
# Intent-preserving, strategy-based, deeply learning
# ══════════════════════════════════════════════════════════════════

@dataclass
class AttemptLog:
    n: int
    strategy: str
    command: str
    success: bool
    error_kind: str = ""
    note: str = ""


class SelfHealingRunner:

    def __init__(self, intent: Intent, cache: dict):
        self.intent = intent
        self.cache  = cache
        self.log: list[AttemptLog] = []

    # ── public: create project ─────────────────────────────────────────────

    def create_project(self) -> bool:
        """
        Try all strategies in order.
        Every attempt uses the SAME frozen intent — name/flags never change.
        Learns from success: caches the winning strategy for this machine+pm.
        """
        console.print(f"\n[cyan]🚀 Creating '{self.intent.project_name}' with {self.intent.pm}...[/cyan]")
        strategies = _strategies_for(self.intent, self.cache)

        if not strategies:
            console.print("[red]No strategies available — all marked bad on this machine.[/red]")
            console.print("[yellow]💡 Clear the cache or try a different package manager.[/yellow]")
            return False

        for strategy_name, full_cmd in strategies:
            console.print(f"\n  [dim cyan]▶ Strategy '{strategy_name}': {full_cmd}[/dim cyan]")
            ok, output, err = self._run(full_cmd, self.intent.install_dir)

            if ok:
                self.log.append(AttemptLog(len(self.log)+1, strategy_name, full_cmd, True))
                console.print(f"  [green]✓ Success with strategy '{strategy_name}'[/green]")
                # ── LEARN: save the winning base command ──────────────────
                # Extract base (everything before project name)
                idx = full_cmd.find(self.intent.project_name)
                if idx > 0:
                    winning_base = full_cmd[:idx].strip()
                    cache_set_create_cmd(self.cache, self.intent.pm, winning_base)
                return True

            error_kind = classify_error(err, output)
            self.log.append(AttemptLog(len(self.log)+1, strategy_name, full_cmd, False, error_kind))
            console.print(f"  [red]✗ Failed[/red] [dim]({error_kind})[/dim]")
            console.print(f"  [dim red]{(err+output).strip()[:300]}[/dim red]")

            # Mark this strategy as permanently bad on this machine
            cache_mark_strategy_bad(self.cache, f"{MK}__{strategy_name}")

            # Network error: wait and retry same strategy once
            if error_kind == EK.NETWORK:
                console.print("  [yellow]⏳ Network error — waiting 5s then retrying...[/yellow]")
                time.sleep(5)
                ok2, out2, err2 = self._run(full_cmd, self.intent.install_dir)
                if ok2:
                    self.log.append(AttemptLog(len(self.log)+1, strategy_name+"_retry", full_cmd, True))
                    idx = full_cmd.find(self.intent.project_name)
                    if idx > 0:
                        cache_set_create_cmd(self.cache, self.intent.pm, full_cmd[:idx].strip())
                    return True

        self._report_failure()
        return False

    # ── public: generic command (install, upgrade, etc.) ──────────────────

    def run_cmd(self, cmd: str, *, label: str = "command", cwd: Optional[str] = None) -> tuple[bool, str]:
        """Run a non-create command. Retries on network errors. Returns (success, output)."""
        wd = cwd or self.intent.project_path
        console.print(f"\n  [dim cyan]▶ {label}: {cmd}[/dim cyan]")
        ok, out, err = self._run(cmd, wd)
        if ok:
            console.print(f"  [green]✓ {label} succeeded[/green]")
            return True, out

        error_kind = classify_error(err, out)
        console.print(f"  [red]✗ {label} failed[/red] [dim]({error_kind})[/dim]")
        if error_kind == EK.NETWORK:
            console.print("  [yellow]⏳ Retrying after 5s...[/yellow]")
            time.sleep(5)
            ok2, out2, err2 = self._run(cmd, wd)
            if ok2:
                return True, out2
        if error_kind == EK.PERMISSION and sys.platform != "win32":
            ok3, out3, _ = self._run(f"sudo {cmd}", wd)
            if ok3:
                return True, out3
        console.print(f"  [dim red]{(err+out).strip()[:300]}[/dim red]")
        return False, err + out

    # ── internal ────────────────────────────────────────────────────────────

    @staticmethod
    def _run(cmd: str, cwd: str) -> tuple[bool, str, str]:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout, r.stderr

    def _report_failure(self):
        t = Table(title="[red bold]❌ All strategies failed[/red bold]", show_lines=True)
        t.add_column("Attempt", width=6, style="dim")
        t.add_column("Strategy", min_width=20)
        t.add_column("Command")
        t.add_column("Error", width=18)
        for a in self.log:
            icon = "[green]✓[/green]" if a.success else "[red]✗[/red]"
            t.add_row(str(a.n), a.strategy, a.command[:60], f"{icon} {a.error_kind}")
        console.print(t)
        console.print("\n[yellow]💡 Manual fallback:[/yellow]")
        console.print(f"   npx create-next-app@latest {self.intent.project_name} {self.intent.flag_str()}")
        console.print("   https://nextjs.org/docs/app/getting-started/installation")


# ══════════════════════════════════════════════════════════════════
# VERSION CHECK
# ══════════════════════════════════════════════════════════════════

def get_latest_nextjs_version() -> str:
    try:
        r = requests.get("https://registry.npmjs.org/next/latest", timeout=8)
        if r.status_code == 200:
            return r.json().get("version", "unknown")
    except Exception:
        pass
    return "unknown"


def get_installed_nextjs_version(project_path: str) -> str:
    try:
        p = os.path.join(project_path, "node_modules", "next", "package.json")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f).get("version", "unknown")
    except Exception:
        pass
    return "unknown"


def check_and_upgrade(intent: Intent, cache: dict):
    console.print("\n[cyan]🔄 Checking Next.js version...[/cyan]")
    installed = get_installed_nextjs_version(intent.project_path)
    latest    = get_latest_nextjs_version()
    console.print(f"  Installed: [yellow]{installed}[/yellow]  Latest: [green]{latest}[/green]")
    if installed not in ("unknown", latest):
        if input(f"\n  Upgrade to {latest}? (y/n): ").strip().lower() == "y":
            runner = SelfHealingRunner(intent, cache)
            runner.run_cmd(PM_UPGRADE_NEXT_CMD[intent.pm], label="upgrade next.js")
    else:
        console.print("  [green]✓ Already on latest[/green]")


# ══════════════════════════════════════════════════════════════════
# LIBRARY VALIDATOR
# ══════════════════════════════════════════════════════════════════

def _npm_exists(name: str) -> bool:
    try:
        return requests.get(f"https://registry.npmjs.org/{name}/latest", timeout=6).status_code == 200
    except Exception:
        return False


def _npm_search(name: str) -> str:
    try:
        r = requests.get(f"https://registry.npmjs.org/-/v1/search?text={name}&size=3", timeout=6)
        if r.status_code == 200:
            return "\n".join(
                f"{o['package']['name']} — {o['package'].get('description','')[:80]}"
                for o in r.json().get("objects", [])
            ) or "no results"
    except Exception:
        pass
    return "no results"


_LIB_RESOLVER_PROMPT = """You are an npm package expert.
Return ONLY a JSON object. No markdown, no explanation, no backticks.
Schema: {"resolved_name":"exact-npm-name","exists":true|false,"confidence":"high"|"medium"|"low","reason":"one line"}
"""


def _resolve_library(raw: str) -> dict:
    if _npm_exists(raw):
        return {"resolved_name": raw, "exists": True, "confidence": "high", "reason": "exact match"}
    snippet = _npm_search(raw)
    try:
        r = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": _LIB_RESOLVER_PROMPT},
                {"role": "user",   "content": f'User typed: "{raw}"\nnpm search results:\n{snippet}'},
            ],
            keep_alive=KEEP_ALIVE,
        )
        raw_json = r["message"]["content"].strip()
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.MULTILINE)
        raw_json = re.sub(r"\s*```$",          "", raw_json, flags=re.MULTILINE)
        result = json.loads(raw_json.strip())
        # Cross-verify AI claim
        if result.get("exists") and not _npm_exists(result.get("resolved_name", "")):
            result["exists"] = False
            result["confidence"] = "low"
            result["reason"] = "AI suggested name not found in registry"
        return result
    except Exception as e:
        return {"resolved_name": "", "exists": False, "confidence": "low", "reason": str(e)}


def validate_libraries(raw_libs: list[str]) -> tuple[list[str], list[dict]]:
    if not raw_libs:
        return [], []
    console.print(f"\n[cyan]🔎 Validating {len(raw_libs)} librar{'y' if len(raw_libs)==1 else 'ies'}...[/cyan]")
    ok_libs, failed = [], []
    for raw in raw_libs:
        console.print(f"  [dim]{raw}...[/dim]", end=" ")
        result = _resolve_library(raw)
        if result["exists"]:
            console.print(f"[green]✓ {result['resolved_name']}[/green]")
            ok_libs.append(result["resolved_name"])
        else:
            console.print(f"[yellow]⚠  not found — {result['reason']}[/yellow]")
            failed.append({"original": raw, "reason": result["reason"]})
    return ok_libs, failed


def _lib_latest_version(name: str) -> str:
    try:
        r = requests.get(f"https://registry.npmjs.org/{name}/latest", timeout=5)
        if r.status_code == 200:
            return r.json().get("version", "latest")
    except Exception:
        pass
    return "latest"


def install_libraries(intent: Intent, libs: list[str], cache: dict):
    if not libs:
        return
    base = PM_INSTALL_CMD[intent.pm]
    console.print(f"\n[cyan]📦 Installing {len(libs)} librar{'y' if len(libs)==1 else 'ies'}...[/cyan]")
    runner = SelfHealingRunner(intent, cache)
    for lib in libs:
        v = _lib_latest_version(lib)
        console.print(f"  [dim]→ {lib}@{v}[/dim]")
        ok, _ = runner.run_cmd(f"{base} {lib}", label=f"install {lib}")
        if not ok:
            console.print(f"  [red]✗ Could not install {lib}[/red]")


def install_global_tools(intent: Intent, tools: list[str], cache: dict):
    if not tools:
        return
    console.print(f"\n[cyan]🌐 Installing global tools: {tools}[/cyan]")
    runner = SelfHealingRunner(intent, cache)
    for tool in tools:
        runner.run_cmd(
            f"{PM_INSTALL_GLOBAL_CMD[intent.pm]} {tool}",
            label=f"global {tool}",
            cwd=os.getcwd(),
        )


# ══════════════════════════════════════════════════════════════════
# AI REQUEST PARSER — only job is to extract intent from user text
# ══════════════════════════════════════════════════════════════════

_PARSE_SYSTEM = """You are a Next.js CLI assistant.
Return ONLY valid JSON — no markdown, no explanation, no code fences.

Schema:
{
  "project_name": "kebab-case-name",
  "flags": ["--flag1"],
  "libraries": ["lib1"],
  "global_tools": []
}

FLAG RULES — ONLY include flags the user explicitly mentioned:
  --typescript  OR  --javascript
  --tailwind    OR  --no-tailwind
  --eslint      OR  --no-eslint
  --app         OR  --no-app
  --turbopack   OR  --no-turbopack
  --src-dir     OR  --no-src-dir
  --no-git            (only if user says "no git")

ALWAYS add --yes to flags.
Do NOT invent flags the user did not mention.
"""


def parse_user_request(user_request: str) -> dict:
    r = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": _PARSE_SYSTEM},
            {"role": "user",   "content": user_request},
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


# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def main():
    console.print(Panel.fit(
        "⚡ Next.js Agentic Creator v10.0\n"
        "   Intent-Preserving · Doc-Learning · Machine-Aware Cache",
        style="bold cyan",
    ))

    cache = _load_cache()

    user_request = input("\n💡 Describe your project: ").strip()
    if not user_request:
        console.print("[red]No input.[/red]")
        sys.exit(1)

    # ── Step 1: Determine package manager ─────────────────────────────────
    pm = detect_pm(user_request)
    console.print(f"\n[dim]📦 Package manager: [bold]{pm}[/bold][/dim]")
    if not ensure_pm(pm):
        console.print(f"[red]'{pm}' not available — falling back to npm[/red]")
        pm = "npm"
    if pm == "yarn":
        detect_yarn_version(cache)
    console.print(f"[green]✓ Using: {pm}[/green]")

    # ── Step 2: Install directory ──────────────────────────────────────────
    install_dir = resolve_install_dir(user_request)

    # ── Step 3: Parse user intent via AI ──────────────────────────────────
    console.print("\n[dim]🤖 Parsing request...[/dim]")
    try:
        config = parse_user_request(user_request)
    except Exception as e:
        console.print(f"[red]Failed to parse request: {e}[/red]")
        sys.exit(1)

    project_name = _sanitize_name(config.get("project_name", "my-next-app"))
    flags        = config.get("flags", [])
    raw_libs     = config.get("libraries", [])
    raw_globals  = config.get("global_tools", [])
    if "--yes" not in flags:
        flags.append("--yes")

    # ── Step 4: Validate libraries ─────────────────────────────────────────
    good_libs, bad_libs = validate_libraries(raw_libs)

    # ── Step 5: Build the frozen Intent object ─────────────────────────────
    intent = Intent(
        project_name=project_name,
        pm=pm,
        flags=flags,
        libraries=good_libs,
        global_tools=raw_globals,
        install_dir=install_dir,
    )

    # ── Plan summary ───────────────────────────────────────────────────────
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

    # ── Step 6: Create project (self-healing) ─────────────────────────────
    runner = SelfHealingRunner(intent, cache)
    ok     = runner.create_project()

    if not ok:
        # Save failure to journal so we can inspect it
        cache_add_journal(cache, {
            "machine": MK, "pm": pm, "project": project_name,
            "flags": flags, "worked": False,
            "failed_strategies": [a.strategy for a in runner.log],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        sys.exit(1)

    # ── Step 7: Version check & optional upgrade ───────────────────────────
    check_and_upgrade(intent, cache)

    # ── Step 8: Install libraries ──────────────────────────────────────────
    install_libraries(intent, good_libs, cache)

    # ── Step 9: Install global tools ──────────────────────────────────────
    install_global_tools(intent, raw_globals, cache)

    # ── Step 10: Save success to journal ──────────────────────────────────
    cache_add_journal(cache, {
        "machine": MK, "pm": pm, "project": project_name,
        "flags": flags, "worked": True,
        "winning_strategy": runner.log[-1].strategy if runner.log else "unknown",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    # ── Done ───────────────────────────────────────────────────────────────
    console.print(f"\n[bold green]🎉 Done! '{intent.project_name}' is ready.[/bold green]")
    console.print(f"  cd {intent.project_path}")
    console.print(f"  {PM_RUN_DEV[pm]}")

    if bad_libs:
        console.print(Panel(
            "\n".join(f"[red]✗[/red] {b['original']} — {b['reason']}" for b in bad_libs),
            title="[red]Unresolved Libraries[/red]", border_style="red",
        ))


if __name__ == "__main__":
    main()