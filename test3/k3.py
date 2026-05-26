import os
import subprocess
import re
import json
import sys
import time
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import ollama

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

console = Console()

MODEL      = "qwen2.5-coder:7b"
KEEP_ALIVE = -1

NEXTJS_DOCS_ROOT          = "https://nextjs.org/docs"
NEXTJS_DOCS_ALLOWED_DOMAIN = "nextjs.org"
MAX_CRAWL_PAGES = 6
MAX_PAGE_CHARS  = 6000
CRAWL_DELAY     = 0.8

MAX_SELF_HEAL_ATTEMPTS = 4   # how many times we try to fix before giving up


# ─────────────────────────────────────────────
# PACKAGE MANAGER SUPPORT
# ─────────────────────────────────────────────

PM_ALIASES = {"npm": "npm", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun"}

PM_CREATE_CMD = {
    "npm":  "npx create-next-app@latest",
    "pnpm": "pnpm dlx create-next-app@latest",
    "yarn": "yarn dlx create-next-app@latest",
    "bun":  "bunx create-next-app@latest",
}

# Fallback create commands when the primary fails
PM_CREATE_FALLBACKS = {
    "yarn": ["npx create-next-app@latest"],          # yarn dlx → npx
    "pnpm": ["npx create-next-app@latest"],          # pnpm dlx → npx
    "bun":  ["npx create-next-app@latest"],          # bunx     → npx
    "npm":  [],
}

PM_INSTALL_CMD        = {"npm": "npm install",          "pnpm": "pnpm add",          "yarn": "yarn add",          "bun": "bun add"}
PM_INSTALL_GLOBAL_CMD = {"npm": "npm install --global", "pnpm": "pnpm add --global", "yarn": "yarn global add",   "bun": "bun add --global"}
PM_INSTALL_ALL_CMD    = {"npm": "npm install",          "pnpm": "pnpm install",      "yarn": "yarn install",      "bun": "bun install"}
PM_UPGRADE_NEXT_CMD   = {
    "npm":  "npm install next@latest react@latest react-dom@latest",
    "pnpm": "pnpm add next@latest react@latest react-dom@latest",
    "yarn": "yarn add next@latest react@latest react-dom@latest",
    "bun":  "bun add next@latest react@latest react-dom@latest",
}
PM_RUN_DEV = {"npm": "npm run dev", "pnpm": "pnpm dev", "yarn": "yarn dev", "bun": "bun dev"}

DEFAULT_PACKAGE_MANAGER = "npm"


def detect_package_manager(user_request: str) -> str:
    lowered = user_request.lower()
    for alias, name in PM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return name
    return DEFAULT_PACKAGE_MANAGER


def is_pm_installed(pm: str) -> bool:
    try:
        return subprocess.run(f"{pm} --version", shell=True, capture_output=True).returncode == 0
    except Exception:
        return False


def ensure_pm_installed(pm: str) -> bool:
    if is_pm_installed(pm):
        return True
    console.print(f"  [yellow]⚠  '{pm}' not found — attempting global install via npm...[/yellow]")
    install_cmds = {"pnpm": "npm install --global pnpm", "yarn": "npm install --global yarn", "bun": "npm install --global bun"}
    cmd = install_cmds.get(pm)
    if not cmd:
        return False
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        console.print(f"  [green]✓ '{pm}' installed globally.[/green]")
        return True
    console.print(f"  [red]✗ Failed to install '{pm}'.[/red]")
    return False


# ─────────────────────────────────────────────
# ERROR CLASSIFIER
# ─────────────────────────────────────────────

class ErrorKind:
    UNKNOWN_SUBCOMMAND = "unknown_subcommand"   # "command X not found" inside a pm
    NETWORK_ERROR      = "network_error"
    PERMISSION_ERROR   = "permission_error"
    VERSION_CONFLICT   = "version_conflict"
    PACKAGE_NOT_FOUND  = "package_not_found"
    GENERIC            = "generic"


def classify_error(stderr: str, stdout: str = "") -> str:
    combined = (stderr + stdout).lower()
    if any(k in combined for k in ['command "dlx" not found', "command not found", "unknown command", "is not a function"]):
        return ErrorKind.UNKNOWN_SUBCOMMAND
    if any(k in combined for k in ["enotfound", "etimedout", "network", "fetch failed", "connect econnrefused"]):
        return ErrorKind.NETWORK_ERROR
    if any(k in combined for k in ["eacces", "permission denied", "access denied"]):
        return ErrorKind.PERMISSION_ERROR
    if any(k in combined for k in ["peer dep", "incompatible", "conflict", "version"]):
        return ErrorKind.VERSION_CONFLICT
    if any(k in combined for k in ["404", "not found", "no matching version"]):
        return ErrorKind.PACKAGE_NOT_FOUND
    return ErrorKind.GENERIC


# ─────────────────────────────────────────────
# SELF-HEALING ENGINE
# ─────────────────────────────────────────────

class HealAttempt:
    """Records one healing attempt so we can print a full report at the end."""
    def __init__(self, attempt_no: int, strategy: str, command: str, success: bool, note: str = ""):
        self.attempt_no = attempt_no
        self.strategy   = strategy
        self.command    = command
        self.success    = success
        self.note       = note


class SelfHealingRunner:
    """
    Wraps command execution with a multi-strategy retry loop.

    Healing priority order:
      1. Known quick-fixes (no AI, no crawl) — e.g. swap yarn dlx → npx
      2. Doc crawl + AI fix
      3. Alternative package manager fallback
      4. Give up gracefully — print full diagnostic report
    """

    def __init__(self, pm: str, working_dir: str):
        self.pm          = pm
        self.working_dir = working_dir
        self.history: list[HealAttempt] = []

    # ── quick-fix lookup table (error_kind → list of fix strategies) ──
    QUICK_FIXES = {
        ErrorKind.UNKNOWN_SUBCOMMAND: [
            # When yarn/pnpm dlx fails, fallback to npx
            {
                "strategy": "Fallback to npx",
                "transform": lambda cmd: re.sub(
                    r"(yarn dlx|pnpm dlx|bunx)\s+create-next-app@latest",
                    "npx create-next-app@latest",
                    cmd,
                ),
            },
            # If the pm itself is the problem, try npm directly
            {
                "strategy": "Fallback to npm run",
                "transform": lambda cmd: re.sub(
                    r"^(yarn|pnpm|bun)\s+", "npm run ", cmd
                ),
            },
        ],
        ErrorKind.PERMISSION_ERROR: [
            {
                "strategy": "Retry with sudo (unix only)",
                "transform": lambda cmd: f"sudo {cmd}" if sys.platform != "win32" else cmd,
            },
        ],
        ErrorKind.NETWORK_ERROR: [
            {
                "strategy": "Retry after 5s (transient network)",
                "transform": lambda cmd: cmd,   # same command, just retry after wait
                "pre_hook":  lambda: time.sleep(5),
            },
        ],
    }

    def run(
        self,
        original_cmd: str,
        *,
        label: str = "command",
        doc_topic: str = "",
        max_attempts: int = MAX_SELF_HEAL_ATTEMPTS,
    ) -> tuple[bool, str]:
        """
        Execute `original_cmd`, retrying with self-healing on failure.
        Returns (success: bool, final_stdout_or_error: str).
        """
        attempt  = 0
        cmd      = original_cmd
        last_err = ""

        while attempt < max_attempts:
            attempt += 1
            console.print(f"\n  [dim cyan]▶ Attempt {attempt}/{max_attempts}: {cmd}[/dim cyan]")

            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                self.history.append(HealAttempt(attempt, "direct run", cmd, True))
                console.print(f"  [green]✓ Success on attempt {attempt}[/green]")
                return True, result.stdout

            # ── failed ──────────────────────────────────────────────
            last_err   = result.stderr + result.stdout
            error_kind = classify_error(result.stderr, result.stdout)

            console.print(f"  [red]✗ Failed[/red] [dim](error type: {error_kind})[/dim]")
            console.print(f"  [dim red]{last_err.strip()[:300]}[/dim red]")

            self.history.append(HealAttempt(attempt, "direct run", cmd, False, error_kind))

            if attempt >= max_attempts:
                break

            # ── healing strategy selection ───────────────────────────
            healed_cmd = self._try_quick_fix(cmd, error_kind, attempt)
            if healed_cmd and healed_cmd != cmd:
                console.print(f"  [yellow]🔧 Quick-fix: {healed_cmd}[/yellow]")
                cmd = healed_cmd
                continue

            # ── doc crawl + AI ───────────────────────────────────────
            if attempt <= max_attempts - 1:
                console.print(f"  [cyan]📚 Crawling Next.js docs to find a fix...[/cyan]")
                fix = crawl_docs_and_fix(
                    failed_command=cmd,
                    error_output=last_err,
                    topic=doc_topic or label,
                    working_dir=self.working_dir,
                )
                healed_cmd = self._apply_doc_fix_to_cmd(fix, cmd, last_err)
                if healed_cmd and healed_cmd != cmd:
                    self.history.append(HealAttempt(attempt, "doc-crawl fix", healed_cmd, None, "pending"))
                    console.print(f"  [yellow]🔧 Doc fix suggests: {healed_cmd}[/yellow]")
                    cmd = healed_cmd
                    continue
                else:
                    console.print(f"  [dim]Doc crawl did not produce a new command. Trying next strategy...[/dim]")

            # ── if we're near the end, try npx as last resort ────────
            if "create-next-app" in cmd and "npx" not in cmd:
                fallback = re.sub(
                    r"(yarn dlx|pnpm dlx|bunx|yarn|pnpm|bun)\s+create-next-app@latest",
                    "npx create-next-app@latest",
                    cmd,
                )
                if fallback != cmd:
                    console.print(f"  [yellow]🔧 Last-resort fallback to npx[/yellow]")
                    cmd = fallback
                    continue

            # nothing more to try
            break

        self._print_failure_report(label, original_cmd, last_err)
        return False, last_err

    def _try_quick_fix(self, cmd: str, error_kind: str, attempt: int) -> str | None:
        fixes = self.QUICK_FIXES.get(error_kind, [])
        # Use fix indexed by attempt (so we don't repeat the same fix)
        fix_index = attempt - 1
        if fix_index < len(fixes):
            fix = fixes[fix_index]
            if "pre_hook" in fix:
                fix["pre_hook"]()
            new_cmd = fix["transform"](cmd)
            if new_cmd != cmd:
                self.history.append(HealAttempt(attempt, fix["strategy"], new_cmd, None, "quick-fix applied"))
                return new_cmd
        return None

    def _apply_doc_fix_to_cmd(self, fix: dict | None, original_cmd: str, error_output: str) -> str | None:
        """Extract a runnable command from a doc fix dict, or None if impossible."""
        if not fix:
            return None
        fix_type = fix.get("fix_type", "impossible")
        if fix_type == "impossible":
            return None
        if fix_type == "command":
            commands = fix.get("commands", [])
            if commands:
                # Return first command as the new retry command
                return commands[0]
        if fix_type == "config":
            console.print(f"  [yellow]ℹ  Doc fix requires manual config change: {fix.get('explanation','')}[/yellow]")
        return None

    def _print_failure_report(self, label: str, original_cmd: str, last_err: str):
        table = Table(title=f"[red bold]❌ Self-Healing Failed: {label}[/red bold]", show_lines=True)
        table.add_column("Attempt", style="dim", width=8)
        table.add_column("Strategy", style="cyan")
        table.add_column("Command")
        table.add_column("Result", width=10)

        for h in self.history:
            result_str = "[green]✓[/green]" if h.success is True else "[red]✗[/red]" if h.success is False else "[yellow]→[/yellow]"
            table.add_row(str(h.attempt_no), h.strategy, h.command[:60], result_str)

        console.print(table)
        console.print(f"\n[red bold]  Original command:[/red bold] {original_cmd}")
        console.print(f"[red]  Last error:[/red]\n[dim]{last_err.strip()[:500]}[/dim]")
        console.print("\n[yellow]  💡 Suggestions:[/yellow]")
        console.print("   • Check your internet connection")
        console.print("   • Try: npm install -g create-next-app")
        console.print(f"   • Try running manually: npx create-next-app@latest")
        console.print("   • Visit: https://nextjs.org/docs/getting-started/installation")


# ─────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """You are a Next.js CLI assistant.
Your ONLY job is to read the user's request and return a JSON object.

Return ONLY valid JSON — no markdown, no explanation, nothing else.

JSON schema:
{
  "project_name": "kebab-case-name",
  "flags": ["--flag1", "--flag2", ...],
  "libraries": ["lib1", "lib2"],
  "global_tools": ["tool1", "tool2"]
}

FLAG RULES — only include a flag if the user explicitly mentions it:
- Language:   --typescript  OR  --javascript
- Styling:    --tailwind    OR  --no-tailwind
- Linter:     --eslint      OR  --no-eslint
- Router:     --app         OR  --no-app
- Bundler:    --turbopack   OR  --no-turbopack
- Src dir:    --src-dir     OR  --no-src-dir
- Import alias: --import-alias "alias" OR --no-import-alias
- Git:        --no-git      (only if user says "no git" / "skip git")
- Skip install: --skip-install (only if user says so)

Do NOT add flags the user did not mention.
Always add --yes so create-next-app runs non-interactively.

LIBRARY RULES:
- Only include libraries the user explicitly names for the project (local install).
- Do NOT include express, nodemon, or any server-side framework unless the user asks.
- Preserve the library name exactly as the user typed it (even if misspelled).

GLOBAL TOOLS RULES:
- If the user says "globally" or "as a global tool", add it to global_tools.
- Leave global_tools empty if the user doesn't mention anything global.

EXAMPLES:

User: "create app called shop with typescript tailwind eslint app router turbopack"
{"project_name":"shop","flags":["--typescript","--tailwind","--eslint","--app","--turbopack","--yes"],"libraries":[],"global_tools":[]}

User: "project name blog, javascript, no tailwind, install axios and zod, install vercel cli globally"
{"project_name":"blog","flags":["--javascript","--no-tailwind","--yes"],"libraries":["axios","zod"],"global_tools":["vercel"]}
"""

LIBRARY_RESOLVER_PROMPT = """You are an npm package name expert.
Return ONLY a JSON object. No markdown, no explanation.
Schema: {"resolved_name":"exact-npm-package-name","confidence":"high"|"medium"|"low","exists":true|false,"reason":"one line"}
Rules: fix spelling if needed; set exists=false if unconfirmed.
"""

DOC_FIX_PROMPT = """You are a Next.js expert who has just read official Next.js documentation.
Return ONLY a JSON object with the fix. No markdown, no explanation.
Schema: {"fix_type":"command"|"config"|"impossible","commands":["cmd1"],"explanation":"one sentence","source_url":"url"}
fix_type: "command" → shell commands to run; "config" → manual config edit needed; "impossible" → docs have no fix.
commands: ordered list of shell commands. Empty if not "command".
"""


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def parse_ai_response(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def sanitize_project_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "my-next-app"


# ─────────────────────────────────────────────
# WEB CRAWLER
# ─────────────────────────────────────────────

class DocsCrawler:
    def __init__(self):
        self.visited: set[str] = set()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 NextJS-Creator-Bot/3.0 (educational)"})

    def _clean_url(self, url: str) -> str:
        return urlparse(url)._replace(fragment="").geturl().rstrip("/")

    def _is_allowed(self, url: str) -> bool:
        p = urlparse(url)
        return p.netloc == NEXTJS_DOCS_ALLOWED_DOMAIN and p.path.startswith("/docs")

    def _fetch_page(self, url: str) -> BeautifulSoup | None:
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            console.print(f"    [dim red]Fetch error {url}: {e}[/dim red]")
        return None

    def _extract_text(self, soup: BeautifulSoup) -> str:
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.find(id="main-content")
        target = main if main else soup
        lines = []
        for elem in target.find_all(["h1", "h2", "h3", "h4", "p", "li", "code", "pre"]):
            text = elem.get_text(separator=" ", strip=True)
            if text and len(text) > 5:
                lines.append(f"\n## {text}" if elem.name in ("h1","h2","h3","h4") else f"`{text}`" if elem.name in ("code","pre") else text)
        return "\n".join(lines)[:MAX_PAGE_CHARS]

    def _extract_doc_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        links = []
        for a in soup.find_all("a", href=True):
            full  = urljoin(base_url, a["href"])
            clean = self._clean_url(full)
            if self._is_allowed(clean) and clean not in self.visited:
                links.append(clean)
        return list(dict.fromkeys(links))

    def _score(self, url: str, topic: str) -> int:
        return len(set(re.findall(r"\w+", topic.lower())) & set(re.findall(r"\w+", url.lower())))

    def crawl(self, start_url: str, topic: str) -> list[dict]:
        pages_data: list[dict] = []
        queue = [self._clean_url(start_url)]
        console.print(f"    [dim]🌐 Crawling: {start_url}  |  topic: \"{topic}\"[/dim]")
        while queue and len(self.visited) < MAX_CRAWL_PAGES:
            url = queue.pop(0)
            if url in self.visited:
                continue
            self.visited.add(url)
            console.print(f"    [dim cyan]→ {url}[/dim cyan]")
            soup = self._fetch_page(url)
            if not soup:
                continue
            text  = self._extract_text(soup)
            title = soup.title.string.strip() if soup.title else url
            pages_data.append({"url": url, "title": title, "text": text})
            ranked = sorted(self._extract_doc_links(soup, url), key=lambda u: self._score(u, topic), reverse=True)
            queue  = ranked[:MAX_CRAWL_PAGES] + queue
            time.sleep(CRAWL_DELAY)
        console.print(f"    [dim green]✓ Crawled {len(pages_data)} page(s)[/dim green]")
        return pages_data


def find_best_doc_entry_url(topic: str) -> str:
    slug_map = {
        "install":    "/docs/getting-started/installation",
        "upgrade":    "/docs/app/building-your-application/upgrading",
        "update":     "/docs/app/building-your-application/upgrading",
        "turbopack":  "/docs/app/api-reference/turbopack",
        "typescript": "/docs/app/building-your-application/configuring/typescript",
        "tailwind":   "/docs/app/building-your-application/styling/tailwind-css",
        "eslint":     "/docs/app/building-your-application/configuring/eslint",
        "routing":    "/docs/app/building-your-application/routing",
        "deploy":     "/docs/app/building-your-application/deploying",
        "environment":"/docs/app/building-your-application/configuring/environment-variables",
    }
    for key, path in slug_map.items():
        if key in topic.lower():
            return f"https://nextjs.org{path}"
    return NEXTJS_DOCS_ROOT


def crawl_docs_and_fix(failed_command: str, error_output: str, topic: str, working_dir: str) -> dict | None:
    entry_url  = find_best_doc_entry_url(topic)
    crawler    = DocsCrawler()
    pages_data = crawler.crawl(start_url=entry_url, topic=topic)
    if not pages_data:
        return None

    doc_context = "\n\n".join(
        f"--- PAGE: {p['title']} ---\nURL: {p['url']}\n\n{p['text']}" for p in pages_data
    )
    user_content = (
        f"FAILED COMMAND:\n{failed_command}\n\n"
        f"ERROR OUTPUT:\n{error_output.strip()[:1000]}\n\n"
        f"DOCUMENTATION CONTENT ({len(pages_data)} pages):\n{doc_context}"
    )
    console.print("    [dim]🤖 AI analyzing docs...[/dim]")
    try:
        response = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": DOC_FIX_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            keep_alive=KEEP_ALIVE,
        )
        return parse_ai_response(response["message"]["content"])
    except Exception as e:
        console.print(f"    [red]AI fix error: {e}[/red]")
        return None


# ─────────────────────────────────────────────
# AUTO-UPDATE: check and update Next.js
# ─────────────────────────────────────────────

def get_latest_nextjs_version() -> str:
    try:
        resp = requests.get("https://registry.npmjs.org/next/latest", timeout=8)
        if resp.status_code == 200:
            return resp.json().get("version", "unknown")
    except Exception:
        pass
    return "unknown"


def get_installed_nextjs_version(project_path: str) -> str:
    try:
        pkg_path = os.path.join(project_path, "node_modules", "next", "package.json")
        if os.path.exists(pkg_path):
            with open(pkg_path) as f:
                return json.load(f).get("version", "unknown")
    except Exception:
        pass
    return "unknown"


def check_and_update_nextjs(project_path: str, pm: str):
    console.print("\n[cyan]🔄 Checking Next.js version...[/cyan]")
    installed = get_installed_nextjs_version(project_path)
    latest    = get_latest_nextjs_version()
    console.print(f"  Installed: [yellow]{installed}[/yellow]  Latest: [green]{latest}[/green]")
    if installed == "unknown" or latest == "unknown":
        return
    if installed != latest:
        console.print(f"  [yellow]⚠  Newer version {latest} available (you have {installed})[/yellow]")
        if input("  Upgrade to latest? (y/n): ").strip().lower() == "y":
            upgrade_cmd = PM_UPGRADE_NEXT_CMD[pm]
            runner = SelfHealingRunner(pm, project_path)
            runner.run(upgrade_cmd, label="upgrade Next.js", doc_topic="upgrade next.js to latest version")
    else:
        console.print(f"  [green]✓ Already on latest ({latest})[/green]")


# ─────────────────────────────────────────────
# Library validation pipeline
# ─────────────────────────────────────────────

def npm_exact_check(lib_name: str) -> bool:
    try:
        return requests.get(f"https://registry.npmjs.org/{lib_name}/latest", timeout=6).status_code == 200
    except Exception:
        return False


def npm_search_snippet(lib_name: str) -> str:
    try:
        resp = requests.get(f"https://registry.npmjs.org/-/v1/search?text={lib_name}&size=3", timeout=6)
        if resp.status_code == 200:
            objects = resp.json().get("objects", [])
            return "\n".join(f"{o['package']['name']} — {o['package'].get('description','')[:80]}" for o in objects) or "no results"
    except Exception:
        pass
    return "no results"


def google_search_snippet(lib_name: str) -> str:
    try:
        url  = f"https://api.duckduckgo.com/?q={requests.utils.quote(lib_name + ' npm library')}&format=json&no_redirect=1&no_html=1"
        resp = requests.get(url, timeout=6, headers={"User-Agent": "nextjs-creator-bot/2.0"})
        if resp.status_code == 200:
            data    = resp.json()
            parts   = [data.get("AbstractText", "")] + [r.get("Text","") for r in data.get("RelatedTopics",[])[:2]]
            snippet = " | ".join(p for p in parts if p)[:300]
            return snippet or "no results"
    except Exception:
        pass
    return "no results"


def resolve_library_with_ai(raw_name: str) -> dict:
    if npm_exact_check(raw_name):
        return {"resolved_name": raw_name, "confidence": "high", "exists": True, "reason": "exact npm package found"}
    console.print(f"    [dim]🔍 '{raw_name}' not found exactly — searching...[/dim]")
    google_info  = google_search_snippet(raw_name)
    npm_info     = npm_search_snippet(raw_name)
    user_content = f'User typed: "{raw_name}"\n\nGoogle snippet:\n{google_info}\n\nnpm results:\n{npm_info}'
    try:
        response = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": LIBRARY_RESOLVER_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            keep_alive=KEEP_ALIVE,
        )
        result = parse_ai_response(response["message"]["content"])
        if result.get("exists") and result.get("resolved_name") and not npm_exact_check(result["resolved_name"]):
            result["exists"] = False
            result["confidence"] = "low"
            result["reason"] += " (npm verification failed)"
        return result
    except Exception as e:
        return {"resolved_name": "", "confidence": "low", "exists": False, "reason": f"AI resolver error: {e}"}


def validate_libraries(libraries: list[str]) -> tuple[list[str], list[dict]]:
    if not libraries:
        return [], []
    console.print(f"\n[cyan]🔎 Validating {len(libraries)} librar{'y' if len(libraries)==1 else 'ies'}...[/cyan]")
    installable, failed = [], []
    for raw in libraries:
        console.print(f"  [dim]checking {raw}...[/dim]", end=" ")
        result = resolve_library_with_ai(raw)
        if result["exists"]:
            resolved = result["resolved_name"]
            tag = f"[dim]({result['confidence']} confidence)[/dim]"
            console.print(f"[green]✓ '{raw}' → '{resolved}'[/green] {tag}" if resolved != raw else f"[green]✓ {resolved}[/green] {tag}")
            installable.append(resolved)
        else:
            console.print(f"[yellow]⚠ could not resolve[/yellow]")
            failed.append({"original": raw, "reason": result["reason"]})
    return installable, failed


def get_library_version(lib_name: str) -> str:
    try:
        resp = requests.get(f"https://registry.npmjs.org/{lib_name}/latest", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("version", "latest")
    except Exception:
        pass
    return "latest"


def install_libraries(project_path: str, libraries: list[str], pm: str):
    if not libraries:
        return
    install_base = PM_INSTALL_CMD[pm]
    console.print(f"\n[cyan]📦 Installing {len(libraries)} librar{'y' if len(libraries)==1 else 'ies'} via {pm}...[/cyan]")
    runner = SelfHealingRunner(pm, project_path)
    for lib in libraries:
        version = get_library_version(lib)
        console.print(f"  [dim]→ {lib}@{version}[/dim]")
        cmd = f"{install_base} {lib}"
        success, _ = runner.run(cmd, label=f"install {lib}", doc_topic=f"install {lib} in next.js")
        if not success:
            console.print(f"  [red]✗ Could not install {lib} after all retry attempts.[/red]")


def install_global_tools(tools: list[str], pm: str):
    if not tools:
        return
    install_base = PM_INSTALL_GLOBAL_CMD[pm]
    console.print(f"\n[cyan]🌐 Installing {len(tools)} global tool(s) via {pm}...[/cyan]")
    runner = SelfHealingRunner(pm, os.getcwd())
    for tool in tools:
        console.print(f"  [dim]→ {tool} (global)[/dim]")
        runner.run(f"{install_base} {tool}", label=f"install global {tool}")


# ─────────────────────────────────────────────
# PROJECT CREATION (self-healing)
# ─────────────────────────────────────────────

def create_nextjs_project(project_name: str, flags: list[str], pm: str) -> bool:
    create_base = PM_CREATE_CMD[pm]
    cmd         = f"{create_base} {project_name} {' '.join(flags)}"

    console.print(f"\n[cyan]🚀 Creating project:[/cyan] [bold]{cmd}[/bold]")

    runner  = SelfHealingRunner(pm, os.getcwd())
    success, output = runner.run(
        cmd,
        label="create-next-app",
        doc_topic="create-next-app installation setup",
    )

    if success:
        console.print("[green]✅ Next.js project created successfully![/green]")
    else:
        console.print("[red bold]\n❌ Could not create project after all self-healing attempts.[/red bold]")
        console.print("[yellow]  The error report above shows every strategy that was tried.[/yellow]")

    return success


def print_failed_libraries_report(failed: list[dict]):
    if not failed:
        return
    console.print()
    console.print(Panel(
        "\n".join(f"[red]✗[/red] [bold]{f['original']}[/bold] — {f['reason']}" for f in failed),
        title="[red bold]⚠ Unresolved Libraries[/red bold]",
        subtitle="[dim]Not installed. Check spelling or try the npm registry.[/dim]",
        border_style="red",
    ))


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

console.print(Panel.fit("⚡ Next.js Agentic Project Creator v4.0  —  Self-Healing Edition", style="bold cyan"))

user_request = input("\n💡 Describe your project: ").strip()
if not user_request:
    console.print("[red]No input provided. Exiting.[/red]")
    sys.exit(1)

# ── Step 1: Detect package manager ────────────
pm = detect_package_manager(user_request)
console.print(f"\n[dim]📦 Package manager detected: [bold]{pm}[/bold][/dim]")
if not ensure_pm_installed(pm):
    console.print(f"[red]❌ '{pm}' unavailable. Falling back to npm.[/red]")
    pm = "npm"
console.print(f"[green]✓ Using: [bold]{pm}[/bold][/green]")

# ── Step 2: Parse request with AI ─────────────
console.print("\n[dim]🤖 Parsing your request...[/dim]")
response = ollama.chat(
    model=MODEL,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_request},
    ],
    keep_alive=KEEP_ALIVE,
)
raw_output = response["message"]["content"]

try:
    config = parse_ai_response(raw_output)
except json.JSONDecodeError as e:
    console.print(f"[red]❌ Could not parse AI response: {e}[/red]")
    sys.exit(1)

# ── Step 3: Sanitize ──────────────────────────
project_name     = sanitize_project_name(config.get("project_name", "my-next-app"))
flags: list[str] = config.get("flags", ["--yes"])
raw_libraries    = config.get("libraries", [])
raw_global_tools = config.get("global_tools", [])
if "--yes" not in flags:
    flags.append("--yes")

# ── Step 4: Validate libraries ────────────────
installable_libs, failed_libs = validate_libraries(raw_libraries)

# ── Summary ───────────────────────────────────
console.print(f"\n[bold green]📋 Plan:[/bold green]")
console.print(f"  Package Mgr : [cyan]{pm}[/cyan]")
console.print(f"  Project     : [cyan]{project_name}[/cyan]")
console.print(f"  Flags       : [cyan]{' '.join(flags)}[/cyan]")
console.print(f"  Libraries   : [cyan]{', '.join(installable_libs) or 'none'}[/cyan]")
console.print(f"  Global tools: [cyan]{', '.join(raw_global_tools) or 'none'}[/cyan]")
if failed_libs:
    console.print(f"  Skipped     : [yellow]{', '.join(f['original'] for f in failed_libs)}[/yellow] [dim](unresolved)[/dim]")

# ── Step 5: Create project ────────────────────
success = create_nextjs_project(project_name, flags, pm)
if not success:
    sys.exit(1)

# ── Step 6: Next.js version check ─────────────
project_path = os.path.join(os.getcwd(), project_name)
check_and_update_nextjs(project_path, pm)

# ── Step 7: Install libraries ─────────────────
install_libraries(project_path, installable_libs, pm)

# ── Step 8: Install global tools ──────────────
install_global_tools(raw_global_tools, pm)

# ── Step 9: Done ──────────────────────────────
console.print(f"\n[bold green]🎉 All done![/bold green]")
console.print(f"\n[cyan]Next steps:[/cyan]")
console.print(f"  cd {project_name}")
console.print(f"  {PM_RUN_DEV[pm]}")

# ── Step 10: Failed library report ────────────
print_failed_libraries_report(failed_libs)