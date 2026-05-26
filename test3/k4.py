import os
import subprocess
import re
import json
import sys
import time
import requests
from urllib.parse import urljoin, urlparse
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

# ── Doc crawl settings ─────────────────────────────────────────────────────
NEXTJS_DOCS_ROOT            = "https://nextjs.org/docs"
NEXTJS_INSTALL_PAGE         = "https://nextjs.org/docs/getting-started/installation"
NEXTJS_DOCS_ALLOWED_DOMAIN  = "nextjs.org"
MAX_CRAWL_PAGES             = 6
MAX_PAGE_CHARS              = 8000   # bigger window → more context for AI
CRAWL_DELAY                 = 0.8

MAX_SELF_HEAL_ATTEMPTS = 4


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

PM_CREATE_FALLBACKS = {
    "yarn": ["npx create-next-app@latest"],
    "pnpm": ["npx create-next-app@latest"],
    "bun":  ["npx create-next-app@latest"],
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
    install_cmds = {
        "pnpm": "npm install --global pnpm",
        "yarn": "npm install --global yarn",
        "bun":  "npm install --global bun",
    }
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
# PROJECT PATH DETECTION
# ─────────────────────────────────────────────

# Patterns like: "in C:/projects", "inside /home/user/code", "at D:\work", "into ~/projects"
_PATH_PATTERNS = [
    r"\b(?:in|inside|at|into|under|within)\s+([A-Za-z]:[\\\/][^\s,;\"']+)",  # Windows abs
    r"\b(?:in|inside|at|into|under|within)\s+(\/[^\s,;\"']+)",               # Unix abs
    r"\b(?:in|inside|at|into|under|within)\s+(~[^\s,;\"']*)",                # ~ home
    r"\b(?:in|inside|at|into|under|within)\s+([\"'])([^\1]+)\1",             # quoted path
    r"(?:path|dir(?:ectory)?|folder)\s*[=:]\s*([^\s,;\"']+)",               # path=/foo
]


def extract_install_path(user_request: str) -> str | None:
    """Try to find an explicit installation directory in the user's prompt."""
    for pat in _PATH_PATTERNS:
        m = re.search(pat, user_request, re.IGNORECASE)
        if m:
            # group(2) covers the quoted-path pattern, group(1) for others
            path = m.group(2) if m.lastindex and m.lastindex >= 2 and m.group(2) else m.group(1)
            path = path.strip().rstrip("/\\")
            if path:
                return path
    return None


def resolve_install_dir(user_request: str) -> str:
    """
    Return the absolute directory where the project folder will be created.
    1. Try to extract from prompt.
    2. If not found, force-ask the user until a valid path is given.
    """
    path = extract_install_path(user_request)

    if path:
        abs_path = os.path.expanduser(path)
        abs_path = os.path.abspath(abs_path)
        console.print(f"\n[dim]📁 Install directory found in prompt: [bold]{abs_path}[/bold][/dim]")
        if not os.path.exists(abs_path):
            console.print(f"  [yellow]⚠  Directory does not exist. Creating it...[/yellow]")
            try:
                os.makedirs(abs_path, exist_ok=True)
                console.print(f"  [green]✓ Created: {abs_path}[/green]")
            except Exception as e:
                console.print(f"  [red]✗ Could not create directory: {e}[/red]")
                abs_path = _force_ask_path()
        return abs_path

    # Not found in prompt — force ask
    console.print("\n[yellow]⚠  No install directory found in your request.[/yellow]")
    return _force_ask_path()


def _force_ask_path() -> str:
    """Keep asking until the user gives a valid (or creatable) directory."""
    while True:
        raw = input(
            "\n📂 Where should the project be created?\n"
            "   Enter full path (e.g. C:\\projects  or  /home/user/code): "
        ).strip()

        if not raw:
            console.print("[red]  Path cannot be empty. Please try again.[/red]")
            continue

        abs_path = os.path.abspath(os.path.expanduser(raw))

        if os.path.isfile(abs_path):
            console.print(f"[red]  '{abs_path}' is a file, not a directory. Try again.[/red]")
            continue

        if not os.path.exists(abs_path):
            try:
                os.makedirs(abs_path, exist_ok=True)
                console.print(f"[green]  ✓ Created: {abs_path}[/green]")
            except Exception as e:
                console.print(f"[red]  Could not create '{abs_path}': {e}. Try again.[/red]")
                continue

        console.print(f"[green]  ✓ Install directory: {abs_path}[/green]")
        return abs_path


# ─────────────────────────────────────────────
# ERROR CLASSIFIER
# ─────────────────────────────────────────────

class ErrorKind:
    UNKNOWN_SUBCOMMAND = "unknown_subcommand"
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
# REQUESTS-ONLY HTML → TEXT EXTRACTOR
# (no BeautifulSoup — pure requests + regex)
# ─────────────────────────────────────────────

def _strip_tags(html: str) -> str:
    """Remove every HTML tag and decode common entities."""
    # Remove script / style blocks entirely (content too)
    html = re.sub(r"<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert block-level tags to newlines so text stays readable
    html = re.sub(r"<(?:br|p|div|li|h[1-6]|pre|blockquote)[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Strip all remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    entities = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
                "&apos;": "'", "&nbsp;": " ", "&#39;": "'", "&#x27;": "'"}
    for ent, char in entities.items():
        html = html.replace(ent, char)
    # Collapse whitespace runs but keep paragraph breaks
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def _extract_text_from_html(html: str) -> str:
    """
    Pull the main content region from raw HTML without BeautifulSoup.
    Strategy:
      1. Try to isolate <article>, <main>, or id="main-content" / id="docs-content"
      2. Fall back to full body if none found
    Then strip tags and trim to MAX_PAGE_CHARS.
    """
    # Try to find a main content block
    for pattern in [
        r'<article[^>]*>(.*?)</article>',
        r'<main[^>]*>(.*?)</main>',
        r'id=["\'](?:main-content|docs-content|content)["\'][^>]*>(.*?)</(?:div|section|article)>',
    ]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            html = m.group(1)
            break

    text = _strip_tags(html)
    return text[:MAX_PAGE_CHARS]


def _extract_links_from_html(html: str, base_url: str, allowed_domain: str, visited: set) -> list[str]:
    """Extract all href links from raw HTML that belong to allowed_domain/docs."""
    hrefs = re.findall(r'href=["\']([^"\'#][^"\']*)["\']', html, re.IGNORECASE)
    links = []
    for href in hrefs:
        full  = urljoin(base_url, href)
        parsed = urlparse(full)
        clean = parsed._replace(fragment="").geturl().rstrip("/")
        if (parsed.netloc == allowed_domain
                and parsed.path.startswith("/docs")
                and clean not in visited):
            links.append(clean)
    # deduplicate while preserving order
    seen: set[str] = set()
    unique = []
    for l in links:
        if l not in seen:
            seen.add(l)
            unique.append(l)
    return unique


def _page_title_from_html(html: str, fallback: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return _strip_tags(m.group(1)).strip()
    return fallback


# ─────────────────────────────────────────────
# DOC CRAWLER  (requests-only, no BeautifulSoup)
# ─────────────────────────────────────────────

class DocsCrawler:
    """
    Crawls Next.js docs using only `requests` + regex.

    Crawl order:
      1. Always start from NEXTJS_INSTALL_PAGE  (installation guide)
      2. Then follow topic-relevant links found in the pages
    """

    def __init__(self):
        self.visited: set[str] = set()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 NextJS-Creator-Bot/4.0 (educational)"
        })

    def _fetch(self, url: str) -> str | None:
        try:
            resp = self.session.get(url, timeout=12)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            console.print(f"    [dim red]Fetch error {url}: {e}[/dim red]")
        return None

    def _score(self, url: str, topic: str) -> int:
        topic_words = set(re.findall(r"\w+", topic.lower()))
        url_words   = set(re.findall(r"\w+", url.lower()))
        return len(topic_words & url_words)

    def crawl(self, topic: str, extra_start_url: str | None = None) -> list[dict]:
        """
        Always fetch the installation page first, then follow relevant links.
        `extra_start_url` is an optional second seed if the topic maps to a
        specific doc section (e.g. Tailwind, TypeScript, etc.).
        """
        # Build the seed queue: installation page is ALWAYS first
        seeds = [NEXTJS_INSTALL_PAGE]
        if extra_start_url and extra_start_url != NEXTJS_INSTALL_PAGE:
            seeds.append(extra_start_url)

        queue: list[str] = [s.rstrip("/") for s in seeds]
        pages_data: list[dict] = []

        console.print(f"    [dim]🌐 Crawl seeds: {seeds}  |  topic: \"{topic}\"[/dim]")

        while queue and len(self.visited) < MAX_CRAWL_PAGES:
            url = queue.pop(0)
            if url in self.visited:
                continue
            self.visited.add(url)

            console.print(f"    [dim cyan]→ {url}[/dim cyan]")
            html = self._fetch(url)
            if not html:
                continue

            text  = _extract_text_from_html(html)
            title = _page_title_from_html(html, url)
            pages_data.append({"url": url, "title": title, "text": text})

            # Find and rank child links
            child_links = _extract_links_from_html(html, url, NEXTJS_DOCS_ALLOWED_DOMAIN, self.visited)
            ranked      = sorted(child_links, key=lambda u: self._score(u, topic), reverse=True)
            # Prepend top-ranked links to queue (depth-first toward relevant pages)
            queue = ranked[:MAX_CRAWL_PAGES] + queue

            time.sleep(CRAWL_DELAY)

        console.print(f"    [dim green]✓ Crawled {len(pages_data)} page(s)[/dim green]")
        return pages_data


# ─────────────────────────────────────────────
# TOPIC → DOC URL MAP
# ─────────────────────────────────────────────

_TOPIC_SLUG_MAP = {
    "install":    "/docs/getting-started/installation",
    "setup":      "/docs/getting-started/installation",
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


def find_topic_url(topic: str) -> str | None:
    for key, path in _TOPIC_SLUG_MAP.items():
        if key in topic.lower():
            return f"https://nextjs.org{path}"
    return None


def crawl_docs_and_fix(failed_command: str, error_output: str, topic: str, working_dir: str) -> dict | None:
    topic_url  = find_topic_url(topic)           # may be None
    crawler    = DocsCrawler()
    pages_data = crawler.crawl(topic=topic, extra_start_url=topic_url)

    if not pages_data:
        console.print("    [dim red]No pages crawled.[/dim red]")
        return None

    doc_context = "\n\n".join(
        f"--- PAGE: {p['title']} ---\nURL: {p['url']}\n\n{p['text']}"
        for p in pages_data
    )
    user_content = (
        f"FAILED COMMAND:\n{failed_command}\n\n"
        f"ERROR OUTPUT:\n{error_output.strip()[:1000]}\n\n"
        f"DOCUMENTATION CONTENT ({len(pages_data)} pages crawled — "
        f"installation page always included):\n{doc_context}"
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
# SYSTEM PROMPTS
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
- Only include libraries the user explicitly names (local install).
- Do NOT include express, nodemon, or server frameworks unless asked.
- Preserve the library name exactly as the user typed it.

GLOBAL TOOLS RULES:
- If the user says "globally" or "as a global tool", add it to global_tools.
- Leave global_tools empty otherwise.

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
# SELF-HEALING ENGINE
# ─────────────────────────────────────────────

class HealAttempt:
    def __init__(self, attempt_no: int, strategy: str, command: str, success, note: str = ""):
        self.attempt_no = attempt_no
        self.strategy   = strategy
        self.command    = command
        self.success    = success
        self.note       = note


class SelfHealingRunner:
    QUICK_FIXES = {
        ErrorKind.UNKNOWN_SUBCOMMAND: [
            {
                "strategy": "Fallback to npx",
                "transform": lambda cmd: re.sub(
                    r"(yarn dlx|pnpm dlx|bunx)\s+create-next-app@latest",
                    "npx create-next-app@latest",
                    cmd,
                ),
            },
            {
                "strategy": "Fallback to npm run",
                "transform": lambda cmd: re.sub(r"^(yarn|pnpm|bun)\s+", "npm run ", cmd),
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
                "transform": lambda cmd: cmd,
                "pre_hook":  lambda: time.sleep(5),
            },
        ],
    }

    def __init__(self, pm: str, working_dir: str):
        self.pm          = pm
        self.working_dir = working_dir
        self.history: list[HealAttempt] = []

    def run(self, original_cmd: str, *, label: str = "command", doc_topic: str = "",
            max_attempts: int = MAX_SELF_HEAL_ATTEMPTS) -> tuple[bool, str]:
        attempt  = 0
        cmd      = original_cmd
        last_err = ""

        while attempt < max_attempts:
            attempt += 1
            console.print(f"\n  [dim cyan]▶ Attempt {attempt}/{max_attempts}: {cmd}[/dim cyan]")

            result = subprocess.run(cmd, shell=True, cwd=self.working_dir,
                                    capture_output=True, text=True)

            if result.returncode == 0:
                self.history.append(HealAttempt(attempt, "direct run", cmd, True))
                console.print(f"  [green]✓ Success on attempt {attempt}[/green]")
                return True, result.stdout

            last_err   = result.stderr + result.stdout
            error_kind = classify_error(result.stderr, result.stdout)

            console.print(f"  [red]✗ Failed[/red] [dim](error type: {error_kind})[/dim]")
            console.print(f"  [dim red]{last_err.strip()[:300]}[/dim red]")
            self.history.append(HealAttempt(attempt, "direct run", cmd, False, error_kind))

            if attempt >= max_attempts:
                break

            healed_cmd = self._try_quick_fix(cmd, error_kind, attempt)
            if healed_cmd and healed_cmd != cmd:
                console.print(f"  [yellow]🔧 Quick-fix: {healed_cmd}[/yellow]")
                cmd = healed_cmd
                continue

            if attempt <= max_attempts - 1:
                console.print(f"  [cyan]📚 Crawling Next.js docs (starting from installation page)...[/cyan]")
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

            if "create-next-app" in cmd and "npx" not in cmd:
                fallback = re.sub(
                    r"(yarn dlx|pnpm dlx|bunx|yarn|pnpm|bun)\s+create-next-app@latest",
                    "npx create-next-app@latest", cmd,
                )
                if fallback != cmd:
                    console.print(f"  [yellow]🔧 Last-resort fallback to npx[/yellow]")
                    cmd = fallback
                    continue

            break

        self._print_failure_report(label, original_cmd, last_err)
        return False, last_err

    def _try_quick_fix(self, cmd: str, error_kind: str, attempt: int):
        fixes     = self.QUICK_FIXES.get(error_kind, [])
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

    def _apply_doc_fix_to_cmd(self, fix: dict | None, original_cmd: str, error_output: str):
        if not fix:
            return None
        if fix.get("fix_type") == "impossible":
            return None
        if fix.get("fix_type") == "command":
            commands = fix.get("commands", [])
            if commands:
                return commands[0]
        if fix.get("fix_type") == "config":
            console.print(f"  [yellow]ℹ  Doc fix requires manual config: {fix.get('explanation','')}[/yellow]")
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
        console.print("   • Try running manually: npx create-next-app@latest")
        console.print("   • Visit: https://nextjs.org/docs/getting-started/installation")


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
            runner = SelfHealingRunner(pm, project_path)
            runner.run(PM_UPGRADE_NEXT_CMD[pm], label="upgrade Next.js", doc_topic="upgrade next.js to latest version")
    else:
        console.print(f"  [green]✓ Already on latest ({latest})[/green]")


# ─────────────────────────────────────────────
# LIBRARY VALIDATION
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
            return "\n".join(
                f"{o['package']['name']} — {o['package'].get('description','')[:80]}" for o in objects
            ) or "no results"
    except Exception:
        pass
    return "no results"


def google_search_snippet(lib_name: str) -> str:
    try:
        url  = f"https://api.duckduckgo.com/?q={requests.utils.quote(lib_name + ' npm library')}&format=json&no_redirect=1&no_html=1"
        resp = requests.get(url, timeout=6, headers={"User-Agent": "nextjs-creator-bot/4.0"})
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
            result["exists"]     = False
            result["confidence"] = "low"
            result["reason"]    += " (npm verification failed)"
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
            console.print(
                f"[green]✓ '{raw}' → '{resolved}'[/green] {tag}" if resolved != raw
                else f"[green]✓ {resolved}[/green] {tag}"
            )
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
        success, _ = runner.run(f"{install_base} {lib}", label=f"install {lib}", doc_topic=f"install {lib} in next.js")
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
# PROJECT CREATION
# ─────────────────────────────────────────────

def create_nextjs_project(project_name: str, flags: list[str], pm: str, install_dir: str) -> bool:
    create_base = PM_CREATE_CMD[pm]
    cmd         = f"{create_base} {project_name} {' '.join(flags)}"
    console.print(f"\n[cyan]🚀 Creating project:[/cyan] [bold]{cmd}[/bold]")
    console.print(f"[dim]   in directory: {install_dir}[/dim]")

    # Run the create command from inside install_dir
    runner = SelfHealingRunner(pm, install_dir)
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

console.print(Panel.fit(
    "⚡ Next.js Agentic Project Creator v5.0  —  Self-Healing + Smart Crawl Edition",
    style="bold cyan",
))

user_request = input("\n💡 Describe your project: ").strip()
if not user_request:
    console.print("[red]No input provided. Exiting.[/red]")
    sys.exit(1)

# ── Step 1: Detect package manager ────────────────────────────────────────
pm = detect_package_manager(user_request)
console.print(f"\n[dim]📦 Package manager detected: [bold]{pm}[/bold][/dim]")
if not ensure_pm_installed(pm):
    console.print(f"[red]❌ '{pm}' unavailable. Falling back to npm.[/red]")
    pm = "npm"
console.print(f"[green]✓ Using: [bold]{pm}[/bold][/green]")

# ── Step 2: Resolve install directory ─────────────────────────────────────
install_dir = resolve_install_dir(user_request)

# ── Step 3: Parse request with AI ─────────────────────────────────────────
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

# ── Step 4: Sanitize ──────────────────────────────────────────────────────
project_name      = sanitize_project_name(config.get("project_name", "my-next-app"))
flags: list[str]  = config.get("flags", ["--yes"])
raw_libraries     = config.get("libraries", [])
raw_global_tools  = config.get("global_tools", [])
if "--yes" not in flags:
    flags.append("--yes")

# ── Step 5: Validate libraries ────────────────────────────────────────────
installable_libs, failed_libs = validate_libraries(raw_libraries)

# ── Summary ───────────────────────────────────────────────────────────────
project_path = os.path.join(install_dir, project_name)
console.print(f"\n[bold green]📋 Plan:[/bold green]")
console.print(f"  Package Mgr  : [cyan]{pm}[/cyan]")
console.print(f"  Install into : [cyan]{install_dir}[/cyan]")
console.print(f"  Project name : [cyan]{project_name}[/cyan]")
console.print(f"  Full path    : [cyan]{project_path}[/cyan]")
console.print(f"  Flags        : [cyan]{' '.join(flags)}[/cyan]")
console.print(f"  Libraries    : [cyan]{', '.join(installable_libs) or 'none'}[/cyan]")
console.print(f"  Global tools : [cyan]{', '.join(raw_global_tools) or 'none'}[/cyan]")
if failed_libs:
    console.print(f"  Skipped      : [yellow]{', '.join(f['original'] for f in failed_libs)}[/yellow] [dim](unresolved)[/dim]")

# ── Step 6: Create project ────────────────────────────────────────────────
success = create_nextjs_project(project_name, flags, pm, install_dir)
if not success:
    sys.exit(1)

# ── Step 7: Next.js version check ─────────────────────────────────────────
check_and_update_nextjs(project_path, pm)

# ── Step 8: Install libraries ─────────────────────────────────────────────
install_libraries(project_path, installable_libs, pm)

# ── Step 9: Install global tools ──────────────────────────────────────────
install_global_tools(raw_global_tools, pm)

# ── Step 10: Done ─────────────────────────────────────────────────────────
console.print(f"\n[bold green]🎉 All done![/bold green]")
console.print(f"\n[cyan]Next steps:[/cyan]")
console.print(f"  cd {project_path}")
console.print(f"  {PM_RUN_DEV[pm]}")

# ── Step 11: Failed library report ────────────────────────────────────────
print_failed_libraries_report(failed_libs)