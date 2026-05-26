import os
import subprocess
import re
import json
import sys
import requests
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
  "libraries": ["lib1", "lib2"]
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
- Only include libraries the user explicitly names.
- Do NOT include express, nodemon, or any server-side framework unless the user asks.
- Preserve the library name exactly as the user typed it (even if misspelled).

EXAMPLES:

User: "create app called shop with typescript tailwind eslint app router turbopack"
{
  "project_name": "shop",
  "flags": ["--typescript", "--tailwind", "--eslint", "--app", "--turbopack", "--yes"],
  "libraries": []
}

User: "project name blog, javascript, no tailwind, install axios and zod"
{
  "project_name": "blog",
  "flags": ["--javascript", "--no-tailwind", "--yes"],
  "libraries": ["axios", "zod"]
}

User: "make dashboard with src-dir no git no import alias"
{
  "project_name": "dashboard",
  "flags": ["--src-dir", "--no-git", "--no-import-alias", "--yes"],
  "libraries": []
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

EXAMPLES:

Input: user typed "aksio", google says "axios HTTP client", npm shows "axios"
Output: {"resolved_name": "axios", "confidence": "high", "exists": true, "reason": "axios is a popular HTTP client, user likely misspelled it"}

Input: user typed "zuztand", google says "zustand state management react", npm shows "zustand"
Output: {"resolved_name": "zustand", "confidence": "high", "exists": true, "reason": "zustand is a React state manager, clear misspelling fixed"}

Input: user typed "xyzfakelib123", google returns nothing relevant, npm shows nothing
Output: {"resolved_name": "", "confidence": "low", "exists": false, "reason": "no matching npm package found"}
"""


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def parse_ai_response(raw: str) -> dict:
    """Strip markdown fences and parse JSON."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def sanitize_project_name(name: str) -> str:
    """Convert to valid kebab-case."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "my-next-app"


# ─────────────────────────────────────────────
# Library validation pipeline
# ─────────────────────────────────────────────

def npm_exact_check(lib_name: str) -> bool:
    """Return True if exact package name exists on npm registry."""
    try:
        url = f"https://registry.npmjs.org/{lib_name}/latest"
        resp = requests.get(url, timeout=6)
        return resp.status_code == 200
    except Exception:
        return False


def npm_search_snippet(lib_name: str) -> str:
    """Return top npm search results as a short text snippet."""
    try:
        url = f"https://registry.npmjs.org/-/v1/search?text={lib_name}&size=3"
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            objects = resp.json().get("objects", [])
            lines = []
            for obj in objects:
                pkg = obj.get("package", {})
                lines.append(f"{pkg.get('name','')} — {pkg.get('description','')[:80]}")
            return "\n".join(lines) if lines else "no results"
    except Exception:
        pass
    return "no results"


def google_search_snippet(lib_name: str) -> str:
    """
    Use DuckDuckGo instant-answer API (no key needed) as a lightweight
    'Google-style' search to get a blurb about the library name.
    Falls back gracefully if blocked.
    """
    try:
        url = f"https://api.duckduckgo.com/?q={requests.utils.quote(lib_name + ' npm library')}&format=json&no_redirect=1&no_html=1"
        resp = requests.get(url, timeout=6, headers={"User-Agent": "nextjs-creator-bot/1.0"})
        if resp.status_code == 200:
            data = resp.json()
            abstract = data.get("AbstractText", "").strip()
            related = [r.get("Text", "") for r in data.get("RelatedTopics", [])[:2] if r.get("Text")]
            parts = ([abstract] if abstract else []) + related
            snippet = " | ".join(parts)[:300]
            return snippet if snippet else "no results"
    except Exception:
        pass
    return "no results"


def resolve_library_with_ai(raw_name: str) -> dict:
    """
    Full pipeline:
    1. Exact npm check  → if passes, done (high confidence, exists=True)
    2. Google snippet + npm search snippet → ask AI to decide
    Returns dict: {resolved_name, confidence, exists, reason}
    """
    # Fast path: exact match
    if npm_exact_check(raw_name):
        return {
            "resolved_name": raw_name,
            "confidence": "high",
            "exists": True,
            "reason": "exact npm package found",
        }

    console.print(f"    [dim]🔍 '{raw_name}' not found exactly — searching Google + npm...[/dim]")

    google_info = google_search_snippet(raw_name)
    npm_info    = npm_search_snippet(raw_name)

    user_content = f"""User typed: "{raw_name}"

Google search snippet:
{google_info}

npm search results:
{npm_info}
"""

    try:
        response = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": LIBRARY_RESOLVER_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            keep_alive=KEEP_ALIVE,
        )
        raw = response["message"]["content"]
        result = parse_ai_response(raw)

        # Double-check the resolved name actually exists on npm
        if result.get("exists") and result.get("resolved_name"):
            if not npm_exact_check(result["resolved_name"]):
                result["exists"] = False
                result["confidence"] = "low"
                result["reason"] += " (npm verification failed)"

        return result

    except Exception as e:
        return {
            "resolved_name": "",
            "confidence": "low",
            "exists": False,
            "reason": f"AI resolver error: {e}",
        }


def validate_libraries(libraries: list[str]) -> tuple[list[str], list[dict]]:
    """
    Validate every library in the list.

    Returns:
        installable  — list of resolved package names to actually install
        failed       — list of {original, reason} dicts for the final error report
    """
    if not libraries:
        return [], []

    console.print(f"\n[cyan]🔎 Validating {len(libraries)} librar{'y' if len(libraries)==1 else 'ies'}...[/cyan]")

    installable = []
    failed      = []

    for raw in libraries:
        console.print(f"  [dim]checking {raw}...[/dim]", end=" ")
        result = resolve_library_with_ai(raw)

        if result["exists"]:
            resolved = result["resolved_name"]
            confidence_tag = f"[dim]({result['confidence']} confidence)[/dim]"
            if resolved != raw:
                console.print(f"[green]✓ '{raw}' → '{resolved}'[/green] {confidence_tag}")
            else:
                console.print(f"[green]✓ {resolved}[/green] {confidence_tag}")
            installable.append(resolved)
        else:
            console.print(f"[yellow]⚠ could not resolve[/yellow]")
            failed.append({
                "original": raw,
                "reason": result["reason"],
            })

    return installable, failed


def get_library_version(lib_name: str) -> str:
    """Return latest version tag from npm, fallback to 'latest'."""
    try:
        url = f"https://registry.npmjs.org/{lib_name}/latest"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("version", "latest")
    except Exception:
        pass
    return "latest"


def install_libraries(project_path: str, libraries: list[str]):
    """Install validated npm libraries inside the project directory."""
    if not libraries:
        return

    console.print(f"\n[cyan]📦 Installing {len(libraries)} librar{'y' if len(libraries)==1 else 'ies'}...[/cyan]")

    for lib in libraries:
        version = get_library_version(lib)
        console.print(f"  [dim]→ {lib}@{version}[/dim]")
        result = subprocess.run(
            f"npm install {lib}",
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print(f"  [green]✓ {lib} installed[/green]")
        else:
            console.print(f"  [yellow]⚠ {lib} install failed: {result.stderr.strip()[:120]}[/yellow]")


def create_nextjs_project(project_name: str, flags: list[str]) -> bool:
    """Run create-next-app with the exact flags extracted from the user."""
    cmd_parts = ["npx", "create-next-app@latest", project_name] + flags
    cmd = " ".join(cmd_parts)

    console.print(f"\n[cyan]🚀 Running:[/cyan] [bold]{cmd}[/bold]")

    result = subprocess.run(cmd, shell=True, capture_output=False, text=True)
    if result.returncode != 0:
        console.print("[red]❌ create-next-app failed.[/red]")
        return False

    console.print("[green]✅ Next.js project created successfully![/green]")
    return True


def print_failed_libraries_report(failed: list[dict]):
    """Print red error summary for unresolved libraries — shown at the very end."""
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
# Main
# ─────────────────────────────────────────────

console.print(Panel.fit("⚡ Next.js Agentic Project Creator", style="bold cyan"))

user_request = input("\n💡 Describe your project: ").strip()

if not user_request:
    console.print("[red]No input provided. Exiting.[/red]")
    sys.exit(1)

# ── Step 1: Ask AI to extract config ──────────
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

# ── Step 2: Validate / sanitize ───────────────
project_name = sanitize_project_name(config.get("project_name", "my-next-app"))
flags: list[str]     = config.get("flags", ["--yes"])
raw_libraries: list[str] = config.get("libraries", [])

if "--yes" not in flags:
    flags.append("--yes")

# ── Step 3: Validate libraries BEFORE showing summary ─────────────────────────
installable_libs, failed_libs = validate_libraries(raw_libraries)

# Show what we understood
console.print(f"\n[bold green]📋 Understood:[/bold green]")
console.print(f"  Project  : [cyan]{project_name}[/cyan]")
console.print(f"  Flags    : [cyan]{' '.join(flags)}[/cyan]")
if installable_libs:
    console.print(f"  Libraries: [cyan]{', '.join(installable_libs)}[/cyan]")
else:
    console.print(f"  Libraries: [dim]none[/dim]")
if failed_libs:
    skipped = ", ".join(f["original"] for f in failed_libs)
    console.print(f"  Skipped  : [yellow]{skipped}[/yellow] [dim](unresolved — see report at end)[/dim]")

# ── Step 4: Create project ────────────────────
success = create_nextjs_project(project_name, flags)

if not success:
    sys.exit(1)

# ── Step 5: Install validated libraries ───────
project_path = os.path.join(os.getcwd(), project_name)
install_libraries(project_path, installable_libs)

# ── Step 6: Done ──────────────────────────────
console.print(f"\n[bold green]🎉 All done![/bold green]")
console.print(f"\n[cyan]Next steps:[/cyan]")
console.print(f"  cd {project_name}")
console.print(f"  npm run dev")

# ── Step 7: Show failed library report LAST ───
print_failed_libraries_report(failed_libs)





