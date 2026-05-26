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
import ollama

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

console = Console()

MODEL = "qwen2.5-coder:7b"
KEEP_ALIVE = -1

NEXTJS_DOCS_ROOT = "https://nextjs.org/docs"
NEXTJS_DOCS_ALLOWED_DOMAIN = "nextjs.org"
MAX_CRAWL_PAGES = 6
MAX_PAGE_CHARS = 6000
CRAWL_DELAY = 0.8

# ─────────────────────────────────────────────
# PACKAGE MANAGER SUPPORT
# ─────────────────────────────────────────────

# Maps user-typed aliases → canonical manager name
PM_ALIASES = {
    "npm":  "npm",
    "pnpm": "pnpm",
    "yarn": "yarn",
    "bun":  "bun",
}

# create-next-app bootstrap command per manager
PM_CREATE_CMD = {
    "npm":  "npx create-next-app@latest",
    "pnpm": "pnpm dlx create-next-app@latest",
    "yarn": "yarn dlx create-next-app@latest",
    "bun":  "bunx create-next-app@latest",
}

# `install <pkg>` command per manager
PM_INSTALL_CMD = {
    "npm":  "npm install",
    "pnpm": "pnpm add",
    "yarn": "yarn add",
    "bun":  "bun add",
}

# `install <pkg> --global` command per manager
PM_INSTALL_GLOBAL_CMD = {
    "npm":  "npm install --global",
    "pnpm": "pnpm add --global",
    "yarn": "yarn global add",
    "bun":  "bun add --global",
}

# `install` (no args — install all deps from lockfile) per manager
PM_INSTALL_ALL_CMD = {
    "npm":  "npm install",
    "pnpm": "pnpm install",
    "yarn": "yarn install",
    "bun":  "bun install",
}

# upgrade next+react+react-dom per manager
PM_UPGRADE_NEXT_CMD = {
    "npm":  "npm install next@latest react@latest react-dom@latest",
    "pnpm": "pnpm add next@latest react@latest react-dom@latest",
    "yarn": "yarn add next@latest react@latest react-dom@latest",
    "bun":  "bun add next@latest react@latest react-dom@latest",
}

DEFAULT_PACKAGE_MANAGER = "npm"


def detect_package_manager(user_request: str) -> str:
    """
    Scan the user's raw request for a mentioned package manager.
    Returns the canonical name or DEFAULT_PACKAGE_MANAGER.
    """
    lowered = user_request.lower()
    for alias, name in PM_ALIASES.items():
        # word-boundary match so "yarn" doesn't match "yarning"
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return name
    return DEFAULT_PACKAGE_MANAGER


def is_pm_installed(pm: str) -> bool:
    """Check whether a package manager binary is available on PATH."""
    try:
        result = subprocess.run(
            f"{pm} --version",
            shell=True,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def ensure_pm_installed(pm: str) -> bool:
    """
    If the requested package manager isn't installed, attempt to install it
    globally via npm (the most universally available fallback).
    Returns True if pm is (now) available.
    """
    if is_pm_installed(pm):
        return True

    console.print(f"  [yellow]⚠  '{pm}' not found on PATH — attempting global install via npm...[/yellow]")

    install_cmds = {
        "pnpm": "npm install --global pnpm",
        "yarn": "npm install --global yarn",
        "bun":  "npm install --global bun",   # bun has its own installer but npm works too
    }

    cmd = install_cmds.get(pm)
    if not cmd:
        console.print(f"  [red]✗ Don't know how to install '{pm}' automatically.[/red]")
        return False

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        console.print(f"  [green]✓ '{pm}' installed globally.[/green]")
        return True
    else:
        console.print(f"  [red]✗ Failed to install '{pm}':[/red] {result.stderr.strip()[:200]}")
        return False


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
- If the user says they want something installed "globally" or "as a global tool", add it to global_tools.
- Examples: "install vercel cli globally", "add prisma globally"
- Leave global_tools empty if the user doesn't mention anything global.

EXAMPLES:

User: "create app called shop with typescript tailwind eslint app router turbopack"
{
  "project_name": "shop",
  "flags": ["--typescript", "--tailwind", "--eslint", "--app", "--turbopack", "--yes"],
  "libraries": [],
  "global_tools": []
}

User: "project name blog, javascript, no tailwind, install axios and zod, install vercel cli globally"
{
  "project_name": "blog",
  "flags": ["--javascript", "--no-tailwind", "--yes"],
  "libraries": ["axios", "zod"],
  "global_tools": ["vercel"]
}
"""

# ─────────────────────────────────────────────
# LIBRARY RESOLVER PROMPT
# ─────────────────────────────────────────────
LIBRARY_RESOLVER_PROMPT = """You are an npm package name expert.

The user typed a library name that may be misspelled or informal.
You will be given:
1. The raw name the user typed
2. Google search snippet results about it
3. npm search results

Your job: return ONLY a JSON object. No markdown, no explanation.

Schema:
{
  "resolved_name": "exact-npm-package-name",
  "confidence": "high" | "medium" | "low",
  "exists": true | false,
  "reason": "one line explanation"
}

Rules:
- "resolved_name" must be the exact npm package name (e.g. "react-query" not "ReactQuery")
- If the package clearly exists but was misspelled, fix the spelling and set exists=true
- If no matching npm package can be confirmed, set exists=false and resolved_name=""
- confidence=high   → you are certain from npm evidence
- confidence=medium → strong indication but not 100% sure
- confidence=low    → guessing from partial match
"""

# ─────────────────────────────────────────────
# DOC CRAWLER PROMPT
# ─────────────────────────────────────────────
DOC_FIX_PROMPT = """You are a Next.js expert who has just read official Next.js documentation.

You will be given:
1. The command that FAILED and its error output
2. The documentation content you crawled from nextjs.org

Your job: return ONLY a JSON object with the fix. No markdown, no explanation.

Schema:
{
  "fix_type": "command" | "config" | "impossible",
  "commands": ["cmd1", "cmd2"],
  "explanation": "one sentence explaining the fix",
  "source_url": "the doc page where you found this"
}

fix_type rules:
- "command"    → there are shell commands the user should run
- "config"     → the fix requires editing a config file (explain in explanation)
- "impossible" → docs don't contain a fix for this error

commands: list of shell commands to run IN ORDER. Empty array if fix_type != "command".
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

    console.print(f"  Installed : [yellow]{installed}[/yellow]")
    console.print(f"  Latest    : [green]{latest}[/green]")

    if installed == "unknown" or latest == "unknown":
        console.print("  [dim]Could not compare versions. Skipping update check.[/dim]")
        return

    if installed != latest:
        console.print(f"\n  [yellow]⚠  Next.js {installed} is installed but {latest} is available.[/yellow]")
        answer = input("  Upgrade to latest? (y/n): ").strip().lower()
        if answer == "y":
            upgrade_cmd = PM_UPGRADE_NEXT_CMD[pm]
            console.print(f"  [cyan]⬆  Upgrading next → {latest} via {pm}...[/cyan]")
            result = subprocess.run(
                upgrade_cmd,
                shell=True,
                cwd=project_path,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console.print(f"  [green]✓ Upgraded to Next.js {latest}[/green]")
            else:
                console.print(f"  [red]✗ Upgrade failed:[/red] {result.stderr.strip()[:200]}")
                console.print("  [cyan]📚 Trying to find fix in Next.js docs...[/cyan]")
                fix = crawl_docs_and_fix(
                    failed_command=upgrade_cmd,
                    error_output=result.stderr,
                    topic="upgrade next.js to latest version",
                    working_dir=project_path,
                )
                apply_doc_fix(fix, project_path)
    else:
        console.print(f"  [green]✓ Already on latest ({latest})[/green]")


# ─────────────────────────────────────────────
# WEB CRAWLER
# ─────────────────────────────────────────────

class DocsCrawler:
    def __init__(self):
        self.visited: set[str] = set()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 NextJS-Creator-Bot/2.0 (educational use)"
        })

    def _clean_url(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed._replace(fragment="").geturl().rstrip("/")

    def _is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.netloc == NEXTJS_DOCS_ALLOWED_DOMAIN
            and parsed.path.startswith("/docs")
        )

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
                tag = elem.name
                if tag in ("h1", "h2", "h3", "h4"):
                    lines.append(f"\n## {text}")
                elif tag in ("code", "pre"):
                    lines.append(f"`{text}`")
                else:
                    lines.append(text)
        return "\n".join(lines)[:MAX_PAGE_CHARS]

    def _extract_doc_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = urljoin(base_url, href)
            clean = self._clean_url(full)
            if self._is_allowed(clean) and clean not in self.visited:
                links.append(clean)
        return list(dict.fromkeys(links))

    def _score_link_relevance(self, url: str, topic: str) -> int:
        topic_words = set(re.findall(r"\w+", topic.lower()))
        url_words   = set(re.findall(r"\w+", url.lower()))
        return len(topic_words & url_words)

    def crawl(self, start_url: str, topic: str) -> list[dict]:
        pages_data: list[dict] = []
        queue: list[str] = [self._clean_url(start_url)]

        console.print(f"    [dim]🌐 Starting crawl at: {start_url}[/dim]")
        console.print(f"    [dim]📌 Topic: \"{topic}\"[/dim]")

        while queue and len(self.visited) < MAX_CRAWL_PAGES:
            url = queue.pop(0)
            if url in self.visited:
                continue
            self.visited.add(url)

            console.print(f"    [dim cyan]→ Visiting: {url}[/dim cyan]")
            soup = self._fetch_page(url)
            if not soup:
                continue

            text  = self._extract_text(soup)
            title = soup.title.string.strip() if soup.title else url

            pages_data.append({"url": url, "title": title, "text": text})

            child_links = self._extract_doc_links(soup, url)
            ranked = sorted(
                child_links,
                key=lambda u: self._score_link_relevance(u, topic),
                reverse=True,
            )
            queue = ranked[:MAX_CRAWL_PAGES] + queue
            time.sleep(CRAWL_DELAY)

        console.print(f"    [dim green]✓ Crawled {len(pages_data)} page(s)[/dim green]")
        return pages_data


# ─────────────────────────────────────────────
# DOC-BASED FIX PIPELINE
# ─────────────────────────────────────────────

def find_best_doc_entry_url(topic: str) -> str:
    slug_map = {
        "install":          "/docs/getting-started/installation",
        "upgrade":          "/docs/app/building-your-application/upgrading",
        "update":           "/docs/app/building-your-application/upgrading",
        "turbopack":        "/docs/app/api-reference/turbopack",
        "typescript":       "/docs/app/building-your-application/configuring/typescript",
        "tailwind":         "/docs/app/building-your-application/styling/tailwind-css",
        "eslint":           "/docs/app/building-your-application/configuring/eslint",
        "app router":       "/docs/app/building-your-application/routing",
        "routing":          "/docs/app/building-your-application/routing",
        "deploy":           "/docs/app/building-your-application/deploying",
        "environment":      "/docs/app/building-your-application/configuring/environment-variables",
    }
    topic_lower = topic.lower()
    for key, path in slug_map.items():
        if key in topic_lower:
            return f"https://nextjs.org{path}"
    return NEXTJS_DOCS_ROOT


def crawl_docs_and_fix(
    failed_command: str,
    error_output: str,
    topic: str,
    working_dir: str,
) -> dict | None:
    entry_url = find_best_doc_entry_url(topic)
    crawler    = DocsCrawler()
    pages_data = crawler.crawl(start_url=entry_url, topic=topic)

    if not pages_data:
        console.print("    [red]No documentation pages could be fetched.[/red]")
        return None

    doc_context_parts = []
    for page in pages_data:
        doc_context_parts.append(
            f"--- PAGE: {page['title']} ---\nURL: {page['url']}\n\n{page['text']}"
        )
    doc_context = "\n\n".join(doc_context_parts)

    user_content = f"""FAILED COMMAND:
{failed_command}

ERROR OUTPUT:
{error_output.strip()[:1000]}

DOCUMENTATION CONTENT (from {len(pages_data)} pages on nextjs.org/docs):
{doc_context}
"""
    console.print("    [dim]🤖 AI analyzing docs to find fix...[/dim]")

    try:
        response = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": DOC_FIX_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            keep_alive=KEEP_ALIVE,
        )
        raw = response["message"]["content"]
        return parse_ai_response(raw)
    except Exception as e:
        console.print(f"    [red]AI fix error: {e}[/red]")
        return None


def apply_doc_fix(fix: dict | None, working_dir: str) -> bool:
    if not fix:
        console.print("    [red]No fix available.[/red]")
        return False

    fix_type    = fix.get("fix_type", "impossible")
    explanation = fix.get("explanation", "")
    source_url  = fix.get("source_url", "")

    console.print(f"\n  [bold cyan]📖 Doc Fix Found:[/bold cyan]")
    console.print(f"  Type       : [yellow]{fix_type}[/yellow]")
    console.print(f"  Explanation: {explanation}")
    if source_url:
        console.print(f"  Source     : [dim]{source_url}[/dim]")

    if fix_type == "impossible":
        console.print("  [red]Docs don't contain a fix for this error.[/red]")
        return False

    if fix_type == "config":
        console.print("  [yellow]Manual config change needed — see explanation above.[/yellow]")
        return False

    if fix_type == "command":
        commands = fix.get("commands", [])
        if not commands:
            console.print("  [red]No commands returned.[/red]")
            return False

        console.print(f"  [cyan]Running {len(commands)} fix command(s):[/cyan]")
        for cmd in commands:
            console.print(f"    [dim]$ {cmd}[/dim]")
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=working_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console.print(f"    [green]✓ success[/green]")
            else:
                console.print(f"    [red]✗ failed:[/red] {result.stderr.strip()[:200]}")
                return False
        return True

    return False


# ─────────────────────────────────────────────
# Library validation pipeline
# ─────────────────────────────────────────────

def npm_exact_check(lib_name: str) -> bool:
    try:
        url  = f"https://registry.npmjs.org/{lib_name}/latest"
        resp = requests.get(url, timeout=6)
        return resp.status_code == 200
    except Exception:
        return False


def npm_search_snippet(lib_name: str) -> str:
    try:
        url  = f"https://registry.npmjs.org/-/v1/search?text={lib_name}&size=3"
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            objects = resp.json().get("objects", [])
            lines   = []
            for obj in objects:
                pkg = obj.get("package", {})
                lines.append(f"{pkg.get('name','')} — {pkg.get('description','')[:80]}")
            return "\n".join(lines) if lines else "no results"
    except Exception:
        pass
    return "no results"


def google_search_snippet(lib_name: str) -> str:
    try:
        url  = f"https://api.duckduckgo.com/?q={requests.utils.quote(lib_name + ' npm library')}&format=json&no_redirect=1&no_html=1"
        resp = requests.get(url, timeout=6, headers={"User-Agent": "nextjs-creator-bot/1.0"})
        if resp.status_code == 200:
            data     = resp.json()
            abstract = data.get("AbstractText", "").strip()
            related  = [r.get("Text", "") for r in data.get("RelatedTopics", [])[:2] if r.get("Text")]
            parts    = ([abstract] if abstract else []) + related
            snippet  = " | ".join(parts)[:300]
            return snippet if snippet else "no results"
    except Exception:
        pass
    return "no results"


def resolve_library_with_ai(raw_name: str) -> dict:
    if npm_exact_check(raw_name):
        return {"resolved_name": raw_name, "confidence": "high", "exists": True, "reason": "exact npm package found"}

    console.print(f"    [dim]🔍 '{raw_name}' not found exactly — searching...[/dim]")

    google_info = google_search_snippet(raw_name)
    npm_info    = npm_search_snippet(raw_name)

    user_content = f"""User typed: "{raw_name}"\n\nGoogle snippet:\n{google_info}\n\nnpm results:\n{npm_info}"""

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
        if result.get("exists") and result.get("resolved_name"):
            if not npm_exact_check(result["resolved_name"]):
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

    installable = []
    failed      = []

    for raw in libraries:
        console.print(f"  [dim]checking {raw}...[/dim]", end=" ")
        result = resolve_library_with_ai(raw)

        if result["exists"]:
            resolved       = result["resolved_name"]
            confidence_tag = f"[dim]({result['confidence']} confidence)[/dim]"
            if resolved != raw:
                console.print(f"[green]✓ '{raw}' → '{resolved}'[/green] {confidence_tag}")
            else:
                console.print(f"[green]✓ {resolved}[/green] {confidence_tag}")
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
    """Install project-local libraries using the chosen package manager."""
    if not libraries:
        return

    install_base = PM_INSTALL_CMD[pm]
    console.print(f"\n[cyan]📦 Installing {len(libraries)} librar{'y' if len(libraries)==1 else 'ies'} via {pm}...[/cyan]")

    for lib in libraries:
        version = get_library_version(lib)
        console.print(f"  [dim]→ {lib}@{version}[/dim]")
        cmd = f"{install_base} {lib}"
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print(f"  [green]✓ {lib} installed[/green]")
        else:
            console.print(f"  [yellow]⚠ {lib} install failed.[/yellow]")
            console.print(f"  [cyan]📚 Searching Next.js docs for a fix...[/cyan]")
            fix = crawl_docs_and_fix(
                failed_command=cmd,
                error_output=result.stderr,
                topic=f"install {lib} in next.js",
                working_dir=project_path,
            )
            apply_doc_fix(fix, project_path)


def install_global_tools(tools: list[str], pm: str):
    """Install CLI tools globally using the chosen package manager."""
    if not tools:
        return

    install_base = PM_INSTALL_GLOBAL_CMD[pm]
    console.print(f"\n[cyan]🌐 Installing {len(tools)} global tool(s) via {pm}...[/cyan]")

    for tool in tools:
        console.print(f"  [dim]→ {tool} (global)[/dim]")
        cmd = f"{install_base} {tool}"
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print(f"  [green]✓ {tool} installed globally[/green]")
        else:
            console.print(f"  [red]✗ Failed to install {tool} globally:[/red] {result.stderr.strip()[:200]}")


def create_nextjs_project(project_name: str, flags: list[str], pm: str) -> bool:
    create_base = PM_CREATE_CMD[pm]
    cmd_parts   = create_base.split() + [project_name] + flags
    cmd         = " ".join(cmd_parts)

    console.print(f"\n[cyan]🚀 Running:[/cyan] [bold]{cmd}[/bold]")

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.stdout:
        console.print(result.stdout)

    if result.returncode != 0:
        console.print("[red]❌ create-next-app failed.[/red]")
        if result.stderr:
            console.print(f"[dim red]{result.stderr.strip()[:500]}[/dim red]")

        console.print("\n[cyan]📚 Searching Next.js docs to fix this error...[/cyan]")
        fix = crawl_docs_and_fix(
            failed_command=cmd,
            error_output=result.stderr + result.stdout,
            topic="create-next-app installation setup",
            working_dir=os.getcwd(),
        )
        if apply_doc_fix(fix, os.getcwd()):
            console.print("\n[cyan]🔁 Retrying create-next-app after fix...[/cyan]")
            retry = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if retry.returncode == 0:
                console.print("[green]✅ Next.js project created successfully (after fix)![/green]")
                return True
            else:
                console.print("[red]❌ Still failing after applying doc fix.[/red]")
                return False
        return False

    console.print("[green]✅ Next.js project created successfully![/green]")
    return True


def print_failed_libraries_report(failed: list[dict]):
    if not failed:
        return
    console.print()
    console.print(Panel(
        "\n".join(
            f"[red]✗[/red] [bold]{f['original']}[/bold] — {f['reason']}"
            for f in failed
        ),
        title="[red bold]⚠ Unresolved Libraries[/red bold]",
        subtitle="[dim]These packages were NOT installed. Check spelling or try the npm registry.[/dim]",
        border_style="red",
    ))


# ─────────────────────────────────────────────
# Run-script helper (differs per pm)
# ─────────────────────────────────────────────

PM_RUN_DEV = {
    "npm":  "npm run dev",
    "pnpm": "pnpm dev",
    "yarn": "yarn dev",
    "bun":  "bun dev",
}

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

console.print(Panel.fit("⚡ Next.js Agentic Project Creator v3.0", style="bold cyan"))

user_request = input("\n💡 Describe your project: ").strip()

if not user_request:
    console.print("[red]No input provided. Exiting.[/red]")
    sys.exit(1)

# ── Step 1: Detect package manager ────────────
pm = detect_package_manager(user_request)
console.print(f"\n[dim]📦 Package manager detected: [bold]{pm}[/bold][/dim]")

# Ensure the pm is actually available; auto-install if needed
if not ensure_pm_installed(pm):
    console.print(f"[red]❌ '{pm}' is not available. Falling back to npm.[/red]")
    pm = "npm"

console.print(f"[green]✓ Using package manager: [bold]{pm}[/bold][/green]")

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
    console.print(f"[dim]Raw: {raw_output}[/dim]")
    sys.exit(1)

# ── Step 3: Sanitize ──────────────────────────
project_name     = sanitize_project_name(config.get("project_name", "my-next-app"))
flags: list[str] = config.get("flags", ["--yes"])
raw_libraries    = config.get("libraries", [])
raw_global_tools = config.get("global_tools", [])

if "--yes" not in flags:
    flags.append("--yes")

# ── Step 4: Validate local libraries ──────────
installable_libs, failed_libs = validate_libraries(raw_libraries)

# ── Show summary ──────────────────────────────
console.print(f"\n[bold green]📋 Understood:[/bold green]")
console.print(f"  Package Mgr: [cyan]{pm}[/cyan]")
console.print(f"  Project    : [cyan]{project_name}[/cyan]")
console.print(f"  Flags      : [cyan]{' '.join(flags)}[/cyan]")
console.print(f"  Libraries  : [cyan]{', '.join(installable_libs) if installable_libs else 'none'}[/cyan]")
console.print(f"  Global tools: [cyan]{', '.join(raw_global_tools) if raw_global_tools else 'none'}[/cyan]")
if failed_libs:
    skipped = ", ".join(f["original"] for f in failed_libs)
    console.print(f"  Skipped    : [yellow]{skipped}[/yellow] [dim](unresolved)[/dim]")

# ── Step 5: Create project ─────────────────────
success = create_nextjs_project(project_name, flags, pm)
if not success:
    sys.exit(1)

# ── Step 6: Check & offer Next.js update ──────
project_path = os.path.join(os.getcwd(), project_name)
check_and_update_nextjs(project_path, pm)

# ── Step 7: Install local libraries ───────────
install_libraries(project_path, installable_libs, pm)

# ── Step 8: Install global tools ──────────────
install_global_tools(raw_global_tools, pm)

# ── Step 9: Done ──────────────────────────────
run_dev_cmd = PM_RUN_DEV[pm]
console.print(f"\n[bold green]🎉 All done![/bold green]")
console.print(f"\n[cyan]Next steps:[/cyan]")
console.print(f"  cd {project_name}")
console.print(f"  {run_dev_cmd}")

# ── Step 10: Failed library report ────────────
print_failed_libraries_report(failed_libs)