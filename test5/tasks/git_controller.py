"""
git_controller.py
═════════════════════════════════════════════════════════════════════════════
Helper-level Git Controller Agent

Capabilities:
  ┌─ Git Operations ──────────────────────────────────────────────────────┐
  │  init, clone, commit, push, pull, branch, merge, rebase, tag,        │
  │  stash, cherry-pick, reset, revert, diff, log, status, remote        │
  ├─ Version Control Intelligence ────────────────────────────────────────┤
  │  smart commit messages, changelog generation, semver bump,            │
  │  release notes, .gitignore generation, branch strategy advice         │
  ├─ Project Intelligence (from repo) ────────────────────────────────────┤
  │  auto-detect language/framework, parse README.md, package.json,       │
  │  requirements.txt, pubspec.yaml, composer.json, pom.xml, build.gradle │
  ├─ Web Research ────────────────────────────────────────────────────────┤
  │  crawl GitHub repo pages, fetch npm/PyPI/pub.dev/packagist docs,      │
  │  search Google for setup guides, read linked .md docs                 │
  └─ Project Bootstrap ───────────────────────────────────────────────────┘
     after cloning: install deps, run build, generate .env.example,
     scaffold missing config files, suggest next steps

Pipeline (start):
  1. IntentAgent      — classify what the user wants to do
  2. RepoAnalyser     — detect language, framework, deps from local or remote
  3. WebResearcher    — crawl README / docs / npm / Google as needed
  4. ExecutorAgent    — run the right git + shell commands
  5. VersionAgent     — handle semver, changelogs, tags, releases
  6. SummaryAgent     — explain what happened + next steps

FIXES applied (v2):
  - Bug 1: Removed invalid --depth=0 flag from git clone command
  - Bug 2: Pre-clone analyse_repo no longer runs on wrong parent directory
  - Bug 3: git_clone retry loop returns immediately on success
  - Bonus: repo_dir resolution now correctly waits for clone before analysis
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
OLLAMA_URL    = os.getenv("OLLAMA_URL",    "http://localhost:11434")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen2.5-coder:7b")
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
GOOGLE_API_KEY   = os.getenv("GOOGLE_API_KEY",   "")
GOOGLE_SEARCH_CX = os.getenv("GOOGLE_SEARCH_CX", "")

# Language/framework detection markers
LANG_MARKERS: Dict[str, List[str]] = {
    "python":     ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "*.py"],
    "javascript": ["package.json", "*.js", "*.mjs"],
    "typescript": ["tsconfig.json", "*.ts", "*.tsx"],
    "flutter":    ["pubspec.yaml", "*.dart"],
    "java":       ["pom.xml", "build.gradle", "*.java"],
    "php":        ["composer.json", "*.php"],
    "go":         ["go.mod", "go.sum", "*.go"],
    "rust":       ["Cargo.toml", "*.rs"],
    "ruby":       ["Gemfile", "*.rb"],
    "csharp":     ["*.csproj", "*.sln", "*.cs"],
    "kotlin":     ["*.kt", "*.kts"],
    "swift":      ["Package.swift", "*.swift", "*.xcodeproj"],
}

FRAMEWORK_MARKERS: Dict[str, List[str]] = {
    "nextjs":   ["next.config.js", "next.config.ts", "next.config.mjs"],
    "nuxt":     ["nuxt.config.js", "nuxt.config.ts"],
    "vue":      ["vue.config.js", "vite.config.*"],
    "angular":  ["angular.json"],
    "react":    ["src/App.jsx", "src/App.tsx"],
    "vite":     ["vite.config.js", "vite.config.ts"],
    "django":   ["manage.py", "django_project"],
    "fastapi":  ["main.py"],
    "flask":    ["app.py"],
    "laravel":  ["artisan", "app/Http"],
    "spring":   ["src/main/java"],
    "flutter":  ["pubspec.yaml", "lib/main.dart"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Terminal callback
# ─────────────────────────────────────────────────────────────────────────────
def _terminal_cb(
    text: str = "",
    color: str = "white",
    msg_type: str = "normal",
    data: Optional[Dict] = None,
) -> None:
    print(f"[{msg_type.upper()}] {text}")


# ─────────────────────────────────────────────────────────────────────────────
# LLM helpers
# ─────────────────────────────────────────────────────────────────────────────
def _llm(messages: List[Dict], model: str = DEFAULT_MODEL, timeout: int = 90) -> str:
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model":    model,
                "messages": messages,
                "stream":   False,
                "options":  {"temperature": 0.2},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()
    except Exception as exc:
        return f"LLM error: {exc}"


def _llm_json(messages: List[Dict]) -> Dict:
    raw = _llm(messages)
    raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Shell helper
# ─────────────────────────────────────────────────────────────────────────────
def _run(
    cmd: str,
    cwd: Optional[str] = None,
    cb: Callable = _terminal_cb,
    capture: bool = False,
    env: Optional[Dict] = None,
) -> Tuple[bool, str]:
    cb(f"$ {cmd}", color="cyan", msg_type="command")
    run_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=run_env,
        )
        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            if not capture and line:
                cb(line, msg_type="output")
        proc.wait()
        output = "\n".join(lines)
        success = proc.returncode == 0
        if not success:
            cb(f"Exit code {proc.returncode}", color="red", msg_type="error")
        return success, output
    except Exception as exc:
        cb(str(exc), color="red", msg_type="error")
        return False, str(exc)


def _run_git(cmd: str, cwd: str, cb: Callable, capture: bool = False) -> Tuple[bool, str]:
    """Convenience wrapper for git commands."""
    env = {}
    if GITHUB_TOKEN:
        env["GIT_ASKPASS"] = "echo"
    return _run(f"git {cmd}", cwd=cwd, cb=cb, capture=capture, env=env)


# ─────────────────────────────────────────────────────────────────────────────
# Web helpers
# ─────────────────────────────────────────────────────────────────────────────
def _fetch(url: str, cb: Callable, timeout: int = 20) -> str:
    cb(f"🌐 Fetching: {url}", color="blue", msg_type="web")
    try:
        headers = {"User-Agent": "GitController/1.0 (project-helper-agent)"}
        if "github.com" in url and GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"\s{3,}", "\n\n", text)
        return text[:8000]
    except Exception as exc:
        cb(f"Fetch failed: {exc}", color="yellow", msg_type="warning")
        return ""


def _fetch_raw(url: str, cb: Callable, timeout: int = 20) -> str:
    cb(f"📄 Raw fetch: {url}", color="blue", msg_type="web")
    try:
        headers = {"User-Agent": "GitController/1.0"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text[:12000]
    except Exception as exc:
        cb(f"Raw fetch failed: {exc}", color="yellow", msg_type="warning")
        return ""


def _github_api(endpoint: str, cb: Callable) -> Dict:
    url = f"https://api.github.com/{endpoint.lstrip('/')}"
    headers = {
        "User-Agent":  "GitController/1.0",
        "Accept":      "application/vnd.github.v3+json",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    try:
        resp = httpx.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        cb(f"GitHub API error: {exc}", color="yellow", msg_type="warning")
        return {}


def _google_search(query: str, cb: Callable, num: int = 5) -> List[Dict]:
    cb(f"🔍 Searching: {query}", color="blue", msg_type="web")
    if GOOGLE_API_KEY and GOOGLE_SEARCH_CX:
        try:
            url = "https://www.googleapis.com/customsearch/v1"
            params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_SEARCH_CX, "q": query, "num": num}
            resp = httpx.get(url, params=params, timeout=15)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [{"title": i.get("title",""), "url": i.get("link",""), "snippet": i.get("snippet","")} for i in items]
        except Exception:
            pass
    try:
        q = urllib.parse.quote_plus(query)
        resp = httpx.get(
            f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1",
            timeout=15,
        )
        data = resp.json()
        results = []
        for topic in data.get("RelatedTopics", [])[:num]:
            if isinstance(topic, dict) and "FirstURL" in topic:
                results.append({
                    "title":   topic.get("Text", "")[:80],
                    "url":     topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", "")[:200],
                })
        return results
    except Exception as exc:
        cb(f"Search failed: {exc}", color="yellow", msg_type="warning")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Utility: parse GitHub URL
# ─────────────────────────────────────────────────────────────────────────────
def _parse_github_url(url: str) -> Optional[Dict]:
    patterns = [
        r"github\.com[:/]([^/]+)/([^/\s\.]+?)(?:\.git)?(?:/tree/([^/\s]+))?(?:/(.*))?$",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return {
                "owner":  m.group(1),
                "repo":   m.group(2),
                "branch": m.group(3) or "main",
                "path":   m.group(4) or "",
            }
    return None


def _raw_github_url(owner: str, repo: str, branch: str, filepath: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filepath}"


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1 — IntentAgent
# ─────────────────────────────────────────────────────────────────────────────
def detect_intent(prompt: str, cb: Callable) -> Dict:
    cb("🧠 Detecting intent…", color="yellow", msg_type="thinking")
    system = """You classify what a user wants to do with Git / a project repo.
Return ONLY valid JSON:
{
  "intent": "<one of the intents below>",
  "confidence": 0.95,
  "target_url": "<github/gitlab URL if mentioned, else ''>",
  "target_dir": "<local path if mentioned, else ''>",
  "branch": "<branch name if mentioned, else ''>",
  "tag": "<version tag if mentioned, else ''>",
  "commit_message": "<if user provided one, else ''>",
  "version_bump": "<major|minor|patch|''>",
  "extra": {}
}

Intent values:
  git_init          — initialise a new git repo
  git_clone         — clone a remote repo
  git_clone_setup   — clone AND fully set up the project (install deps, configure)
  git_status        — show working tree status
  git_add_commit    — stage and commit changes
  git_push          — push to remote
  git_pull          — pull / fetch from remote
  git_branch        — create / switch / list / delete branches
  git_merge         — merge branches
  git_rebase        — rebase
  git_tag           — create / list tags
  git_stash         — stash / pop stash
  git_reset         — reset / revert commits
  git_diff          — show diffs
  git_log           — show commit history
  git_remote        — manage remotes (add / remove / rename)
  git_cherry_pick   — cherry-pick commits
  version_bump      — bump semver version, update changelog, tag release
  changelog         — generate or update CHANGELOG.md
  gitignore         — generate .gitignore for a language/framework
  release           — full release flow (bump + changelog + tag + push)
  repo_analyse      — analyse a repo and collect all info from README / docs / web
  project_setup     — fully set up a cloned project (install, configure, build)
  git_help          — general git help / explain a concept
  unknown           — doesn't fit above
"""
    result = _llm_json([
        {"role": "system", "content": system},
        {"role": "user",   "content": prompt},
    ])
    result.setdefault("intent",         "unknown")
    result.setdefault("confidence",     0.0)
    result.setdefault("target_url",     "")
    result.setdefault("target_dir",     "")
    result.setdefault("branch",         "")
    result.setdefault("tag",            "")
    result.setdefault("commit_message", "")
    result.setdefault("version_bump",   "")
    result.setdefault("extra",          {})
    cb(f"Intent: {result['intent']} (conf={result['confidence']})", color="green", msg_type="info")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2 — RepoAnalyser
# ─────────────────────────────────────────────────────────────────────────────
def analyse_repo(repo_dir: str, cb: Callable) -> Dict:
    cb(f"🔬 Analysing repo: {repo_dir}", color="yellow", msg_type="thinking")
    info: Dict[str, Any] = {
        "languages":  [],
        "frameworks": [],
        "deps":       {},
        "scripts":    {},
        "config_files": [],
        "has_tests":  False,
        "has_docker": False,
        "has_ci":     False,
        "readme":     "",
        "description": "",
    }

    if not os.path.isdir(repo_dir):
        cb(f"Directory not found: {repo_dir}", color="red", msg_type="error")
        return info

    all_files = []
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   ("node_modules", "__pycache__", ".git", "vendor", "dist", "build")]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), repo_dir)
            all_files.append(rel)

    info["config_files"] = all_files[:200]

    for lang, markers in LANG_MARKERS.items():
        for marker in markers:
            if marker.startswith("*"):
                ext = marker[1:]
                if any(f.endswith(ext) for f in all_files):
                    if lang not in info["languages"]:
                        info["languages"].append(lang)
                    break
            else:
                if any(f == marker or f.endswith(f"/{marker}") for f in all_files):
                    if lang not in info["languages"]:
                        info["languages"].append(lang)
                    break

    for fw, markers in FRAMEWORK_MARKERS.items():
        for marker in markers:
            if "*" in marker:
                pattern = marker.replace("*", "")
                if any(pattern in f for f in all_files):
                    if fw not in info["frameworks"]:
                        info["frameworks"].append(fw)
                    break
            else:
                if any(f == marker or f.endswith(f"/{marker}") for f in all_files):
                    if fw not in info["frameworks"]:
                        info["frameworks"].append(fw)
                    break

    _parse_manifests(repo_dir, info, cb)

    info["has_tests"]  = any("test" in f.lower() or "spec" in f.lower() for f in all_files)
    info["has_docker"] = any(f in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml")
                             for f in all_files)
    info["has_ci"]     = any(".github/workflows" in f or ".gitlab-ci" in f
                             or "Jenkinsfile" in f for f in all_files)

    for readme_name in ("README.md", "README.rst", "README.txt", "readme.md"):
        readme_path = os.path.join(repo_dir, readme_name)
        if os.path.exists(readme_path):
            try:
                with open(readme_path, encoding="utf-8", errors="ignore") as f:
                    info["readme"] = f.read()[:6000]
                break
            except Exception:
                pass

    cb(
        f"Detected: langs={info['languages']} fw={info['frameworks']} "
        f"tests={info['has_tests']} docker={info['has_docker']}",
        color="green",
        msg_type="info",
    )
    return info


def _parse_manifests(repo_dir: str, info: Dict, cb: Callable) -> None:
    # package.json
    pj = os.path.join(repo_dir, "package.json")
    if os.path.exists(pj):
        try:
            with open(pj) as f:
                pkg = json.load(f)
            info["description"] = pkg.get("description", "")
            info["deps"]["npm_dependencies"]     = list(pkg.get("dependencies", {}).keys())
            info["deps"]["npm_devDependencies"]  = list(pkg.get("devDependencies", {}).keys())
            info["scripts"]["npm"]               = pkg.get("scripts", {})
            info["version"]                      = pkg.get("version", "")
        except Exception:
            pass

    # requirements.txt
    req = os.path.join(repo_dir, "requirements.txt")
    if os.path.exists(req):
        try:
            with open(req) as f:
                info["deps"]["pip"] = [l.strip().split("==")[0] for l in f if l.strip() and not l.startswith("#")]
        except Exception:
            pass

    # pyproject.toml
    ppt = os.path.join(repo_dir, "pyproject.toml")
    if os.path.exists(ppt):
        try:
            with open(ppt) as f:
                content = f.read()
            info["deps"]["pyproject"] = re.findall(r'^([a-zA-Z0-9_\-]+)\s*[>=<!]', content, re.MULTILINE)
        except Exception:
            pass

    # pubspec.yaml (Flutter/Dart)
    pub = os.path.join(repo_dir, "pubspec.yaml")
    if os.path.exists(pub):
        try:
            with open(pub) as f:
                content = f.read()
            desc_match = re.search(r"^description:\s*(.+)", content, re.MULTILINE)
            info["description"] = desc_match.group(1) if desc_match else ""
            deps_section = re.search(r"^dependencies:(.*?)^[a-z]", content, re.MULTILINE | re.DOTALL)
            if deps_section:
                info["deps"]["flutter"] = re.findall(r"^\s+(\w+):", deps_section.group(1), re.MULTILINE)
        except Exception:
            pass

    # composer.json (PHP)
    comp = os.path.join(repo_dir, "composer.json")
    if os.path.exists(comp):
        try:
            with open(comp) as f:
                pkg = json.load(f)
            info["deps"]["composer_require"]     = list(pkg.get("require", {}).keys())
            info["deps"]["composer_require_dev"] = list(pkg.get("require-dev", {}).keys())
            info["scripts"]["composer"]          = pkg.get("scripts", {})
        except Exception:
            pass

    # pom.xml (Java Maven)
    pom = os.path.join(repo_dir, "pom.xml")
    if os.path.exists(pom):
        try:
            with open(pom) as f:
                content = f.read()
            info["deps"]["maven"] = re.findall(r"<artifactId>([^<]+)</artifactId>", content)
        except Exception:
            pass

    # build.gradle (Java/Kotlin Gradle)
    gradle = os.path.join(repo_dir, "build.gradle")
    if not os.path.exists(gradle):
        gradle = os.path.join(repo_dir, "build.gradle.kts")
    if os.path.exists(gradle):
        try:
            with open(gradle) as f:
                content = f.read()
            info["deps"]["gradle"] = re.findall(r"['\"]([^:]+:[^:]+:[^'\"]+)['\"]", content)
        except Exception:
            pass

    # go.mod
    gomod = os.path.join(repo_dir, "go.mod")
    if os.path.exists(gomod):
        try:
            with open(gomod) as f:
                content = f.read()
            info["deps"]["go"] = re.findall(r"^\s+([^\s]+)\s+v", content, re.MULTILINE)
        except Exception:
            pass

    # Cargo.toml (Rust)
    cargo = os.path.join(repo_dir, "Cargo.toml")
    if os.path.exists(cargo):
        try:
            with open(cargo) as f:
                content = f.read()
            info["deps"]["rust"] = re.findall(r'^(\w[\w-]*)\s*=', content, re.MULTILINE)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3 — WebResearcher
# ─────────────────────────────────────────────────────────────────────────────
def research_repo(
    repo_info: Dict,
    target_url: str,
    cb: Callable,
) -> Dict:
    cb("🔭 Web research starting…", color="yellow", msg_type="thinking")
    research: Dict[str, Any] = {
        "github_meta":   {},
        "readme":        repo_info.get("readme", ""),
        "doc_pages":     [],
        "package_docs":  [],
        "setup_guides":  [],
        "releases":      [],
    }

    gh = _parse_github_url(target_url) if target_url else None

    if gh:
        meta = _github_api(f"repos/{gh['owner']}/{gh['repo']}", cb)
        research["github_meta"] = {
            "description": meta.get("description", ""),
            "stars":       meta.get("stargazers_count", 0),
            "forks":       meta.get("forks_count", 0),
            "language":    meta.get("language", ""),
            "topics":      meta.get("topics", []),
            "homepage":    meta.get("homepage", ""),
            "license":     (meta.get("license") or {}).get("spdx_id", ""),
            "open_issues": meta.get("open_issues_count", 0),
            "default_branch": meta.get("default_branch", "main"),
        }
        cb(
            f"GitHub: ⭐{research['github_meta']['stars']} "
            f"🍴{research['github_meta']['forks']} "
            f"lang={research['github_meta']['language']}",
            color="green",
            msg_type="info",
        )

        # Fetch README if not already loaded from local clone
        if not research["readme"]:
            branch = research["github_meta"].get("default_branch", "main")
            for readme_file in ("README.md", "readme.md", "README.rst"):
                raw_url = _raw_github_url(gh["owner"], gh["repo"], branch, readme_file)
                content = _fetch_raw(raw_url, cb)
                if content:
                    research["readme"] = content
                    break

        # Latest releases
        releases = _github_api(f"repos/{gh['owner']}/{gh['repo']}/releases?per_page=5", cb)
        if isinstance(releases, list):
            research["releases"] = [
                {"tag": r.get("tag_name",""), "name": r.get("name",""), "body": (r.get("body","") or "")[:500]}
                for r in releases[:5]
            ]

    # Parse doc links from README
    readme_text = research["readme"]
    if readme_text:
        doc_links = _extract_doc_links(readme_text, target_url)
        for link in doc_links[:4]:
            page_content = _fetch(link, cb)
            if page_content:
                research["doc_pages"].append({"url": link, "content": page_content[:3000]})

    # Package registry docs
    languages = repo_info.get("languages", [])
    npm_deps  = repo_info.get("deps", {}).get("npm_dependencies", [])
    pip_deps  = repo_info.get("deps", {}).get("pip", [])

    for pkg in npm_deps[:3]:
        content = _fetch_raw(f"https://registry.npmjs.org/{pkg}/latest", cb)
        if content:
            try:
                d = json.loads(content)
                research["package_docs"].append({
                    "pkg":         pkg,
                    "registry":    "npm",
                    "description": d.get("description",""),
                    "homepage":    d.get("homepage",""),
                    "version":     d.get("version",""),
                })
            except Exception:
                pass

    for pkg in pip_deps[:3]:
        content = _fetch_raw(f"https://pypi.org/pypi/{pkg}/json", cb)
        if content:
            try:
                d = json.loads(content)
                info = d.get("info", {})
                research["package_docs"].append({
                    "pkg":         pkg,
                    "registry":    "pypi",
                    "description": info.get("summary",""),
                    "homepage":    info.get("home_page",""),
                    "version":     info.get("version",""),
                })
            except Exception:
                pass

    project_name = ""
    if gh:
        project_name = gh["repo"]
    elif repo_info.get("description"):
        project_name = repo_info["description"][:40]

    if project_name:
        frameworks = repo_info.get("frameworks", [])
        fw_str = " ".join(frameworks[:2]) if frameworks else ""
        query = f"{project_name} {fw_str} setup guide installation".strip()
        results = _google_search(query, cb, num=5)
        for r in results[:3]:
            page = _fetch(r["url"], cb)
            research["setup_guides"].append({
                "title":   r["title"],
                "url":     r["url"],
                "snippet": r["snippet"],
                "content": page[:2000],
            })

    cb(
        f"Research done: {len(research['doc_pages'])} doc pages, "
        f"{len(research['package_docs'])} pkg docs, "
        f"{len(research['setup_guides'])} guides",
        color="green",
        msg_type="info",
    )
    return research


def _extract_doc_links(readme: str, base_url: str) -> List[str]:
    links = re.findall(r'\[.*?\]\((https?://[^\)]+)\)', readme)
    doc_keywords = ["docs", "documentation", "guide", "wiki", "tutorial", "getting-started", "readme"]
    filtered = []
    for link in links:
        if any(kw in link.lower() for kw in doc_keywords):
            filtered.append(link)
    return list(dict.fromkeys(filtered))[:6]


# ─────────────────────────────────────────────────────────────────────────────
# Agent 4 — ExecutorAgent  (all git operations)
# ─────────────────────────────────────────────────────────────────────────────

def git_init(target_dir: str, cb: Callable) -> bool:
    os.makedirs(target_dir, exist_ok=True)
    ok, _ = _run_git("init", target_dir, cb)
    if ok:
        _generate_gitignore_file(target_dir, [], cb)
        cb("✅ Git repo initialised", color="green", msg_type="success")
    return ok


def git_clone(url: str, target_dir: str, branch: str, cb: Callable) -> Optional[str]:
    """
    Clone a repo into target_dir and return the final cloned path.

    Case A: target_dir does NOT exist yet → clone directly into it.
    Case B: target_dir already EXISTS     → clone as a sub-folder named after the repo.

    FIX v2:
      - Removed invalid --depth=0 flag (caused exit 128)
      - Returns immediately inside the loop when clone succeeds
      - Falls back to default branch (no --branch flag) only when branch fails
    """
    gh        = _parse_github_url(url)
    repo_name = gh["repo"] if gh else os.path.splitext(os.path.basename(url))[0]

    if os.path.isdir(target_dir):
        # Case B — target exists, clone inside it
        parent     = target_dir
        clone_name = repo_name
        final_dir  = os.path.join(target_dir, repo_name)
    else:
        # Case A — clone to exactly this path
        parent     = os.path.dirname(target_dir) or os.getcwd()
        clone_name = os.path.basename(target_dir)
        final_dir  = target_dir

    # Guard: already cloned?
    if os.path.isdir(os.path.join(final_dir, ".git")):
        cb(f"📁 Repo already exists at {final_dir} — skipping clone", color="yellow", msg_type="warning")
        return final_dir

    os.makedirs(parent, exist_ok=True)

    # Attempt 1: with the requested branch (if any)
    # Attempt 2: fallback — let git pick the remote default branch
    branch_flags = [f"--branch {branch}" if branch else "", ""]

    for attempt, branch_flag in enumerate(branch_flags):
        # Build the command — NO --depth flag (was invalid with 0)
        parts = ["clone"]
        if branch_flag:
            parts.append(branch_flag)
        parts.append(url)
        parts.append(clone_name)
        cmd = " ".join(parts)

        ok, out = _run_git(cmd, parent, cb)

        if ok:
            # ── FIX: return immediately on success ──────────────────────────
            cb(f"✅ Cloned into {final_dir}", color="green", msg_type="success")
            return final_dir

        # Clone failed
        if attempt == 0 and branch:
            cb(
                f"⚠️  Branch '{branch}' not found, retrying with default branch…",
                color="yellow",
                msg_type="warning",
            )
        else:
            cb(f"❌ Clone failed.\n{out}", color="red", msg_type="error")
            return None

    return None


def git_add_commit(repo_dir: str, message: str, cb: Callable, all_files: bool = True) -> bool:
    add_flag = "-A" if all_files else "-u"
    ok1, _ = _run_git(f"add {add_flag}", repo_dir, cb)
    if not ok1:
        return False
    ok2, _ = _run_git(f'commit -m "{message}"', repo_dir, cb)
    return ok2


def git_push(repo_dir: str, remote: str, branch: str, cb: Callable, force: bool = False) -> bool:
    force_flag = "--force-with-lease" if force else ""
    ok, _ = _run_git(f"push {force_flag} {remote} {branch}", repo_dir, cb)
    return ok


def git_pull(repo_dir: str, remote: str, branch: str, cb: Callable) -> bool:
    ok, _ = _run_git(f"pull {remote} {branch}", repo_dir, cb)
    return ok


def git_status(repo_dir: str, cb: Callable) -> str:
    _, out = _run_git("status --short", repo_dir, cb, capture=True)
    cb(out or "(clean working tree)", msg_type="output")
    return out


def git_log(repo_dir: str, n: int, cb: Callable) -> str:
    _, out = _run_git(
        f'log --oneline --graph --decorate -n {n}', repo_dir, cb, capture=True
    )
    cb(out, msg_type="output")
    return out


def git_diff(repo_dir: str, cb: Callable, staged: bool = False) -> str:
    flag = "--staged" if staged else ""
    _, out = _run_git(f"diff {flag}", repo_dir, cb, capture=True)
    cb(out[:4000] if out else "(no diff)", msg_type="output")
    return out


def git_branch_op(repo_dir: str, action: str, name: str, cb: Callable) -> bool:
    if action == "create":
        ok, _ = _run_git(f"checkout -b {name}", repo_dir, cb)
    elif action == "switch":
        ok, _ = _run_git(f"checkout {name}", repo_dir, cb)
    elif action == "delete":
        ok, _ = _run_git(f"branch -d {name}", repo_dir, cb)
    elif action == "list":
        ok, out = _run_git("branch -a", repo_dir, cb, capture=True)
        cb(out, msg_type="output")
    else:
        ok, out = _run_git("branch -a", repo_dir, cb, capture=True)
        cb(out, msg_type="output")
        ok = True
    return ok


def git_merge(repo_dir: str, branch: str, cb: Callable) -> bool:
    ok, _ = _run_git(f"merge --no-ff {branch}", repo_dir, cb)
    return ok


def git_rebase(repo_dir: str, onto: str, cb: Callable) -> bool:
    ok, _ = _run_git(f"rebase {onto}", repo_dir, cb)
    return ok


def git_tag_op(repo_dir: str, tag: str, message: str, cb: Callable) -> bool:
    if message:
        ok, _ = _run_git(f'tag -a {tag} -m "{message}"', repo_dir, cb)
    else:
        ok, _ = _run_git(f"tag {tag}", repo_dir, cb)
    return ok


def git_stash_op(repo_dir: str, action: str, cb: Callable) -> bool:
    if action == "pop":
        ok, _ = _run_git("stash pop", repo_dir, cb)
    elif action == "list":
        _, out = _run_git("stash list", repo_dir, cb, capture=True)
        cb(out or "(empty stash)", msg_type="output")
        ok = True
    else:
        ok, _ = _run_git("stash", repo_dir, cb)
    return ok


def git_reset_op(repo_dir: str, mode: str, ref: str, cb: Callable) -> bool:
    ok, _ = _run_git(f"reset {mode} {ref}", repo_dir, cb)
    return ok


def git_remote_op(repo_dir: str, action: str, name: str, url: str, cb: Callable) -> bool:
    if action == "add":
        ok, _ = _run_git(f"remote add {name} {url}", repo_dir, cb)
    elif action == "remove":
        ok, _ = _run_git(f"remote remove {name}", repo_dir, cb)
    elif action == "list":
        _, out = _run_git("remote -v", repo_dir, cb, capture=True)
        cb(out or "(no remotes)", msg_type="output")
        ok = True
    else:
        _, out = _run_git("remote -v", repo_dir, cb, capture=True)
        cb(out, msg_type="output")
        ok = True
    return ok


def git_cherry_pick(repo_dir: str, commit: str, cb: Callable) -> bool:
    ok, _ = _run_git(f"cherry-pick {commit}", repo_dir, cb)
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Agent 5 — VersionAgent
# ─────────────────────────────────────────────────────────────────────────────
def bump_version(repo_dir: str, bump_type: str, cb: Callable) -> str:
    cb(f"📦 Bumping version ({bump_type})…", color="yellow", msg_type="thinking")
    current = _read_current_version(repo_dir)
    if not current:
        cb("Could not read current version", color="yellow", msg_type="warning")
        current = "0.1.0"

    parts = current.lstrip("v").split(".")
    if len(parts) < 3:
        parts = ["0", "1", "0"]
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2].split("-")[0])

    if bump_type == "major":
        major += 1; minor = 0; patch = 0
    elif bump_type == "minor":
        minor += 1; patch = 0
    else:
        patch += 1

    new_version = f"{major}.{minor}.{patch}"
    _write_version(repo_dir, new_version, cb)
    cb(f"Version: {current} → {new_version}", color="green", msg_type="success")
    return new_version


def _read_current_version(repo_dir: str) -> str:
    for filepath, pattern in [
        (os.path.join(repo_dir, "package.json"),    r'"version"\s*:\s*"([^"]+)"'),
        (os.path.join(repo_dir, "pyproject.toml"), r'version\s*=\s*"([^"]+)"'),
        (os.path.join(repo_dir, "pubspec.yaml"),   r'^version:\s*(.+)'),
        (os.path.join(repo_dir, "Cargo.toml"),     r'^version\s*=\s*"([^"]+)"'),
    ]:
        if os.path.exists(filepath):
            try:
                with open(filepath) as f:
                    content = f.read()
                m = re.search(pattern, content, re.MULTILINE)
                if m:
                    return m.group(1).strip()
            except Exception:
                pass
    return ""


def _write_version(repo_dir: str, version: str, cb: Callable) -> None:
    # package.json
    pj = os.path.join(repo_dir, "package.json")
    if os.path.exists(pj):
        try:
            with open(pj) as f:
                pkg = json.load(f)
            pkg["version"] = version
            with open(pj, "w") as f:
                json.dump(pkg, f, indent=2)
            cb(f"✏️  Updated package.json → {version}", msg_type="success")
        except Exception as e:
            cb(f"package.json write failed: {e}", color="red", msg_type="error")

    # pyproject.toml
    ppt = os.path.join(repo_dir, "pyproject.toml")
    if os.path.exists(ppt):
        try:
            with open(ppt) as f:
                content = f.read()
            content = re.sub(r'(version\s*=\s*)"[^"]+"', f'\\1"{version}"', content)
            with open(ppt, "w") as f:
                f.write(content)
            cb(f"✏️  Updated pyproject.toml → {version}", msg_type="success")
        except Exception as e:
            cb(f"pyproject.toml write failed: {e}", color="red", msg_type="error")

    # pubspec.yaml
    pub = os.path.join(repo_dir, "pubspec.yaml")
    if os.path.exists(pub):
        try:
            with open(pub) as f:
                content = f.read()
            content = re.sub(r'^(version:\s*)(.+)', f'\\g<1>{version}', content, flags=re.MULTILINE)
            with open(pub, "w") as f:
                f.write(content)
            cb(f"✏️  Updated pubspec.yaml → {version}", msg_type="success")
        except Exception as e:
            cb(f"pubspec.yaml write failed: {e}", color="red", msg_type="error")


def generate_changelog(repo_dir: str, version: str, cb: Callable) -> str:
    cb("📝 Generating changelog…", color="yellow", msg_type="thinking")
    _, log_out = _run_git(
        "log --oneline --no-merges -50", repo_dir, cb, capture=True
    )
    if not log_out.strip():
        log_out = "No commits found."

    system = (
        "You are a changelog writer. Given a list of git commit messages, "
        "produce a well-structured CHANGELOG.md section for the given version. "
        "Group into: Features, Bug Fixes, Improvements, Breaking Changes. "
        "Use Keep a Changelog format. Plain markdown, no extra commentary."
    )
    entry = _llm([
        {"role": "system", "content": system},
        {"role": "user",   "content": f"Version: {version}\n\nCommits:\n{log_out}"},
    ])

    changelog_path = os.path.join(repo_dir, "CHANGELOG.md")
    date_str = time.strftime("%Y-%m-%d")
    new_entry = f"\n## [{version}] - {date_str}\n\n{entry}\n"

    if os.path.exists(changelog_path):
        with open(changelog_path) as f:
            existing = f.read()
        if "## [" in existing:
            idx = existing.index("## [")
            updated = existing[:idx] + new_entry + existing[idx:]
        else:
            updated = existing + new_entry
    else:
        updated = f"# Changelog\n\nAll notable changes to this project will be documented here.\n{new_entry}"

    with open(changelog_path, "w") as f:
        f.write(updated)

    cb("✅ CHANGELOG.md updated", color="green", msg_type="success")
    return new_entry


def smart_commit_message(repo_dir: str, cb: Callable) -> str:
    cb("✨ Generating smart commit message…", color="yellow", msg_type="thinking")
    _, diff = _run_git("diff --staged --stat", repo_dir, cb, capture=True)
    _, diff_detail = _run_git("diff --staged", repo_dir, cb, capture=True)
    combined = (diff + "\n" + diff_detail)[:4000]

    if not combined.strip():
        return "chore: update files"

    system = (
        "You write Conventional Commit messages (feat/fix/chore/docs/refactor/test/style). "
        "Given a git diff, output ONE commit message only. "
        "Format: <type>(<optional scope>): <short description>\n\n<optional body>\n\n<optional footer>. "
        "Keep subject under 72 chars. No markdown, no explanation."
    )
    msg = _llm([
        {"role": "system", "content": system},
        {"role": "user",   "content": combined},
    ])
    first_line = msg.split("\n")[0].strip()
    cb(f"Commit: {first_line}", color="green", msg_type="info")
    return msg


# ─────────────────────────────────────────────────────────────────────────────
# .gitignore generator
# ─────────────────────────────────────────────────────────────────────────────
GITIGNORE_TEMPLATES: Dict[str, str] = {
    "python": "__pycache__/\n*.py[cod]\n*.pyo\n*.pyd\n.Python\nbuild/\ndist/\n*.egg-info/\n.env\n.venv\nvenv/\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n*.sqlite3\n",
    "javascript": "node_modules/\ndist/\nbuild/\n.env\n.env.local\n.env.*.local\nnpm-debug.log*\nyarn-debug.log*\nyarn-error.log*\n.DS_Store\n",
    "typescript": "node_modules/\ndist/\nbuild/\n*.js.map\n.env\n.env.local\n.tsbuildinfo\n",
    "flutter": ".dart_tool/\n.flutter-plugins\n.flutter-plugins-dependencies\nbuild/\n*.g.dart\n*.freezed.dart\npubspec.lock\n",
    "java": "target/\nbuild/\n*.class\n*.jar\n*.war\n.gradle/\n.idea/\n*.iml\n",
    "php": "vendor/\n.env\nstorage/\nbootstrap/cache/\n*.log\n",
    "go": "*.exe\n*.exe~\n*.dll\n*.so\n*.dylib\n*.test\n*.out\nvendor/\n",
    "rust": "/target/\nCargo.lock\n",
    "general": ".DS_Store\nThumbs.db\n*.log\n*.tmp\n*.swp\n*.swo\n.idea/\n.vscode/\n*.env\n",
}


def _generate_gitignore_file(repo_dir: str, languages: List[str], cb: Callable) -> None:
    gitignore_path = os.path.join(repo_dir, ".gitignore")
    if os.path.exists(gitignore_path):
        cb(".gitignore already exists — skipping", color="yellow", msg_type="info")
        return

    content = "# Generated by GitController Agent\n\n"
    added = set()
    for lang in languages:
        template = GITIGNORE_TEMPLATES.get(lang, "")
        if template and lang not in added:
            content += f"# {lang.capitalize()}\n{template}\n"
            added.add(lang)
    content += GITIGNORE_TEMPLATES["general"]

    with open(gitignore_path, "w") as f:
        f.write(content)
    cb("✅ .gitignore created", color="green", msg_type="success")


# ─────────────────────────────────────────────────────────────────────────────
# Project Setup Agent
# ─────────────────────────────────────────────────────────────────────────────
def setup_project(repo_dir: str, repo_info: Dict, research: Dict, cb: Callable) -> None:
    cb("⚙️  Setting up project…", color="yellow", msg_type="thinking")

    languages  = repo_info.get("languages", [])
    frameworks = repo_info.get("frameworks", [])
    scripts    = repo_info.get("scripts", {})

    # npm / yarn / pnpm
    if os.path.exists(os.path.join(repo_dir, "package.json")):
        manager = _detect_node_manager(repo_dir)
        cb(f"📦 Installing Node deps with {manager}…", color="cyan", msg_type="info")
        _run(f"{manager} install", cwd=repo_dir, cb=cb)

    # Python
    if "python" in languages:
        if os.path.exists(os.path.join(repo_dir, "requirements.txt")):
            _run("pip install -r requirements.txt", cwd=repo_dir, cb=cb)
        elif os.path.exists(os.path.join(repo_dir, "pyproject.toml")):
            _run("pip install -e .", cwd=repo_dir, cb=cb)
        elif os.path.exists(os.path.join(repo_dir, "Pipfile")):
            _run("pipenv install", cwd=repo_dir, cb=cb)

    # Flutter / Dart
    if "flutter" in languages:
        _run("flutter pub get", cwd=repo_dir, cb=cb)

    # PHP / Composer
    if "php" in languages and os.path.exists(os.path.join(repo_dir, "composer.json")):
        _run("composer install", cwd=repo_dir, cb=cb)

    # Java Maven
    if os.path.exists(os.path.join(repo_dir, "pom.xml")):
        _run("mvn install -DskipTests", cwd=repo_dir, cb=cb)

    # Java / Kotlin Gradle
    if os.path.exists(os.path.join(repo_dir, "build.gradle")) or \
       os.path.exists(os.path.join(repo_dir, "build.gradle.kts")):
        gradle_cmd = "./gradlew" if os.path.exists(os.path.join(repo_dir, "gradlew")) else "gradle"
        _run(f"{gradle_cmd} build -x test", cwd=repo_dir, cb=cb)

    # Go
    if "go" in languages:
        _run("go mod download", cwd=repo_dir, cb=cb)

    # Rust
    if "rust" in languages:
        _run("cargo build", cwd=repo_dir, cb=cb)

    # .env.example
    _generate_env_example(repo_dir, repo_info, cb)

    # Suggest build scripts
    npm_scripts = scripts.get("npm", {})
    if "build" in npm_scripts:
        cb("💡 Run `npm run build` to build the project", color="cyan", msg_type="tip")
    if "dev" in npm_scripts:
        cb("💡 Run `npm run dev` to start the dev server", color="cyan", msg_type="tip")
    if "start" in npm_scripts:
        cb("💡 Run `npm start` to start the application", color="cyan", msg_type="tip")

    cb("✅ Project setup complete", color="green", msg_type="success")


def _detect_node_manager(repo_dir: str) -> str:
    if os.path.exists(os.path.join(repo_dir, "pnpm-lock.yaml")):
        return "pnpm"
    if os.path.exists(os.path.join(repo_dir, "yarn.lock")):
        return "yarn"
    return "npm"


def _generate_env_example(repo_dir: str, repo_info: Dict, cb: Callable) -> None:
    env_example = os.path.join(repo_dir, ".env.example")
    env_file    = os.path.join(repo_dir, ".env")

    if os.path.exists(env_example):
        cb(".env.example already exists", color="yellow", msg_type="info")
        if not os.path.exists(env_file):
            import shutil
            shutil.copy(env_example, env_file)
            cb("📋 Copied .env.example → .env (fill in your values)", color="cyan", msg_type="tip")
        return

    env_vars: List[str] = []
    scan_extensions = (".js", ".ts", ".py", ".php", ".env", ".yaml", ".yml")
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "vendor", "dist", "build", "__pycache__")]
        for fname in files:
            if any(fname.endswith(ext) for ext in scan_extensions):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    found = re.findall(r'process\.env\.([A-Z_][A-Z0-9_]+)', content)
                    found += re.findall(r'os\.environ(?:\.get)?\([\'"]([A-Z_][A-Z0-9_]+)[\'"]', content)
                    found += re.findall(r'\$\{([A-Z_][A-Z0-9_]+)\}', content)
                    env_vars.extend(found)
                except Exception:
                    pass

    env_vars = list(dict.fromkeys(env_vars))
    if env_vars:
        lines = ["# Auto-generated by GitController Agent\n"]
        for var in env_vars:
            lines.append(f"{var}=\n")
        with open(env_example, "w") as f:
            f.writelines(lines)
        cb(f"✅ .env.example created with {len(env_vars)} variables", color="green", msg_type="success")


# ─────────────────────────────────────────────────────────────────────────────
# Agent 6 — SummaryAgent
# ─────────────────────────────────────────────────────────────────────────────
def summarise(
    intent: Dict,
    repo_info: Dict,
    research: Dict,
    result_notes: List[str],
    cb: Callable,
) -> None:
    cb("📋 Generating summary…", color="yellow", msg_type="thinking")

    context = {
        "intent":       intent.get("intent"),
        "languages":    repo_info.get("languages", []),
        "frameworks":   repo_info.get("frameworks", []),
        "github_meta":  research.get("github_meta", {}),
        "releases":     research.get("releases", []),
        "result_notes": result_notes,
    }
    readme_snippet = (research.get("readme") or "")[:1500]

    system = (
        "You are a Git and project expert assistant. Given the operation context, "
        "write a concise, helpful summary of what was done and clear next steps "
        "(plain text, under 250 words). Include relevant commands the developer should run next."
    )
    summary = _llm([
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Context:\n{json.dumps(context, indent=2)}\n\n"
                f"README snippet:\n{readme_snippet}"
            ),
        },
    ])

    cb("─" * 55, msg_type="divider")
    cb("✅ GitController operation complete!", color="green", msg_type="success")
    for note in result_notes:
        cb(f"   {note}", msg_type="info")
    cb("─" * 55, msg_type="divider")
    cb(summary, msg_type="guide")
    cb("─" * 55, msg_type="divider")


# ─────────────────────────────────────────────────────────────────────────────
# Full Release Flow
# ─────────────────────────────────────────────────────────────────────────────
def full_release(
    repo_dir: str,
    bump_type: str,
    remote: str,
    branch: str,
    cb: Callable,
) -> str:
    cb("🚀 Full release flow starting…", color="magenta", msg_type="start")

    git_status(repo_dir, cb)
    new_version = bump_version(repo_dir, bump_type, cb)
    generate_changelog(repo_dir, new_version, cb)

    msg = f"chore(release): v{new_version}"
    git_add_commit(repo_dir, msg, cb)
    git_tag_op(repo_dir, f"v{new_version}", f"Release v{new_version}", cb)
    git_push(repo_dir, remote, branch, cb)
    _run_git(f"push {remote} v{new_version}", repo_dir, cb)

    cb(f"🎉 Released v{new_version}", color="green", msg_type="success")
    return new_version


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def start(
    prompt: str,
    cb: Callable = _terminal_cb,
    init_data: Optional[Dict] = None,
    base_dir: Optional[str] = None,
) -> None:
    """
    Main entry point — called by the server via asyncio.to_thread.

    Args:
        prompt   : raw user prompt
        cb       : terminal callback(text, color, msg_type, data)
        init_data: optional initial context (e.g. {"cwd": "/path/to/repo"})
        base_dir : working directory override
    """
    base_dir  = base_dir or (init_data or {}).get("cwd") or os.getcwd()
    cb("🐙 GitController Agent starting…", color="magenta", msg_type="start")

    result_notes: List[str] = []
    repo_info:    Dict      = {}
    research:     Dict      = {}

    # ── Step 1: Intent ────────────────────────────────────────────────────────
    intent = detect_intent(prompt, cb)
    action = intent["intent"]

    target_url = intent.get("target_url", "")
    raw_dir    = intent.get("target_dir", "")
    branch     = intent.get("branch", "") or "main"
    tag        = intent.get("tag", "")
    bump_type  = intent.get("version_bump", "") or "patch"

    # ── Resolve target directory ──────────────────────────────────────────────
    gh        = _parse_github_url(target_url) if target_url else None
    repo_name = gh["repo"] if gh else ""

    if raw_dir:
        norm_dir = os.path.normpath(raw_dir)
        if not os.path.isabs(norm_dir):
            norm_dir = os.path.join(base_dir, norm_dir)

        if os.path.isdir(norm_dir):
            if repo_name and action in ("git_clone", "git_clone_setup"):
                # Will clone into norm_dir/repo_name
                repo_dir = os.path.join(norm_dir, repo_name)
            else:
                potential = os.path.join(norm_dir, repo_name) if repo_name else norm_dir
                repo_dir  = potential if os.path.isdir(potential) else norm_dir
        else:
            repo_dir = norm_dir
    elif target_url and repo_name:
        repo_dir = os.path.join(base_dir, repo_name)
    else:
        repo_dir = base_dir

    # ── Step 2: Repo analyse ──────────────────────────────────────────────────
    # FIX v2: Never analyse before a clone operation — the correct directory
    # doesn't exist yet (or is the wrong parent folder).  Analysis runs AFTER
    # the clone succeeds, inside each clone branch below.
    if action not in ("git_clone", "git_clone_setup", "repo_analyse"):
        if os.path.isdir(os.path.join(repo_dir, ".git")):
            repo_info = analyse_repo(repo_dir, cb)
        elif os.path.isdir(repo_dir):
            repo_info = analyse_repo(repo_dir, cb)

    # ── Step 3: Web research ──────────────────────────────────────────────────
    if action in ("git_clone_setup", "repo_analyse", "project_setup") or (target_url and not repo_info):
        research = research_repo(repo_info, target_url, cb)

    # ── Step 4: Execute ───────────────────────────────────────────────────────
    if action == "git_init":
        git_init(repo_dir, cb)
        result_notes.append(f"Initialised repo at {repo_dir}")

    elif action == "git_clone":
        clone_target = os.path.normpath(raw_dir) if raw_dir else repo_dir
        if raw_dir and not os.path.isabs(clone_target):
            clone_target = os.path.join(base_dir, clone_target)
        cloned = git_clone(target_url, clone_target, branch, cb)
        if cloned:
            result_notes.append(f"Cloned {target_url} → {cloned}")
            # Analyse the actual cloned directory (correct project)
            repo_info = analyse_repo(cloned, cb)
            repo_dir  = cloned

    elif action == "git_clone_setup":
        clone_target = os.path.normpath(raw_dir) if raw_dir else repo_dir
        if raw_dir and not os.path.isabs(clone_target):
            clone_target = os.path.join(base_dir, clone_target)
        cloned = git_clone(target_url, clone_target, branch, cb)
        if cloned:
            result_notes.append(f"Cloned {target_url} → {cloned}")
            # Analyse the actual cloned directory (correct project)
            repo_info = analyse_repo(cloned, cb)
            research  = research_repo(repo_info, target_url, cb)
            setup_project(cloned, repo_info, research, cb)
            _generate_gitignore_file(cloned, repo_info.get("languages", []), cb)
            result_notes.append("Dependencies installed and project configured")
            repo_dir = cloned

    elif action == "git_status":
        out = git_status(repo_dir, cb)
        result_notes.append(f"Status: {len(out.splitlines())} changed files")

    elif action == "git_add_commit":
        msg = intent.get("commit_message", "")
        if not msg:
            git_add_commit(repo_dir, "temp", cb)
            msg = smart_commit_message(repo_dir, cb)
            _run_git("reset HEAD", repo_dir, cb)
        git_add_commit(repo_dir, msg, cb)
        result_notes.append(f"Committed: {msg}")

    elif action == "git_push":
        remote = intent.get("extra", {}).get("remote", "origin")
        git_push(repo_dir, remote, branch, cb)
        result_notes.append(f"Pushed to {remote}/{branch}")

    elif action == "git_pull":
        remote = intent.get("extra", {}).get("remote", "origin")
        git_pull(repo_dir, remote, branch, cb)
        result_notes.append(f"Pulled from {remote}/{branch}")

    elif action == "git_branch":
        extra  = intent.get("extra", {})
        op     = extra.get("action", "list")
        name   = extra.get("name", branch)
        git_branch_op(repo_dir, op, name, cb)
        result_notes.append(f"Branch operation: {op} {name}")

    elif action == "git_merge":
        git_merge(repo_dir, branch, cb)
        result_notes.append(f"Merged {branch}")

    elif action == "git_rebase":
        git_rebase(repo_dir, branch, cb)
        result_notes.append(f"Rebased onto {branch}")

    elif action == "git_tag":
        msg = intent.get("commit_message", f"Tag {tag}")
        git_tag_op(repo_dir, tag or "v0.1.0", msg, cb)
        result_notes.append(f"Tagged: {tag}")

    elif action == "git_stash":
        extra = intent.get("extra", {})
        op    = extra.get("action", "save")
        git_stash_op(repo_dir, op, cb)
        result_notes.append(f"Stash: {op}")

    elif action == "git_reset":
        extra = intent.get("extra", {})
        mode  = extra.get("mode", "--soft")
        ref   = extra.get("ref", "HEAD~1")
        git_reset_op(repo_dir, mode, ref, cb)
        result_notes.append(f"Reset {mode} to {ref}")

    elif action == "git_diff":
        staged = intent.get("extra", {}).get("staged", False)
        git_diff(repo_dir, cb, staged=staged)
        result_notes.append("Diff shown")

    elif action == "git_log":
        n = intent.get("extra", {}).get("n", 20)
        git_log(repo_dir, n, cb)
        result_notes.append(f"Log: last {n} commits")

    elif action == "git_remote":
        extra = intent.get("extra", {})
        op    = extra.get("action", "list")
        name  = extra.get("name", "origin")
        url   = extra.get("url", "")
        git_remote_op(repo_dir, op, name, url, cb)
        result_notes.append(f"Remote: {op} {name}")

    elif action == "git_cherry_pick":
        commit = intent.get("extra", {}).get("commit", "")
        if commit:
            git_cherry_pick(repo_dir, commit, cb)
            result_notes.append(f"Cherry-picked {commit}")
        else:
            cb("No commit hash specified for cherry-pick", color="red", msg_type="error")

    elif action == "version_bump":
        new_ver = bump_version(repo_dir, bump_type, cb)
        result_notes.append(f"Version bumped to {new_ver}")

    elif action == "changelog":
        ver = _read_current_version(repo_dir) or "0.1.0"
        entry = generate_changelog(repo_dir, ver, cb)
        result_notes.append(f"Changelog updated for v{ver}")

    elif action == "gitignore":
        languages = repo_info.get("languages", [])
        if not languages:
            cb("Detecting language from prompt…", msg_type="thinking")
            for lang in LANG_MARKERS.keys():
                if lang in prompt.lower():
                    languages.append(lang)
        _generate_gitignore_file(repo_dir, languages, cb)
        result_notes.append(f".gitignore generated for {languages}")

    elif action == "release":
        remote = intent.get("extra", {}).get("remote", "origin")
        new_ver = full_release(repo_dir, bump_type, remote, branch, cb)
        result_notes.append(f"Released v{new_ver}")

    elif action == "repo_analyse":
        if target_url and not os.path.isdir(repo_dir):
            git_clone(target_url, repo_dir, branch, cb)
        repo_info = analyse_repo(repo_dir, cb)
        research  = research_repo(repo_info, target_url, cb)
        result_notes.append(
            f"Analysed repo: {len(repo_info.get('languages',[]))} languages, "
            f"{len(repo_info.get('frameworks',[]))} frameworks"
        )

    elif action == "project_setup":
        if not os.path.isdir(repo_dir) and target_url:
            git_clone(target_url, repo_dir, branch, cb)
            repo_info = analyse_repo(repo_dir, cb)
        setup_project(repo_dir, repo_info, research, cb)
        result_notes.append("Project fully set up")

    elif action == "git_help":
        system = (
            "You are a Git expert. Answer the user's Git question clearly and concisely, "
            "with examples. Use plain text."
        )
        answer = _llm([
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ])
        cb(answer, msg_type="guide")
        result_notes.append("Git help provided")

    else:
        system = (
            "You are a Git and version-control expert assistant. "
            "Help the user with their request. Be specific and practical."
        )
        answer = _llm([
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ])
        cb(answer, msg_type="guide")
        result_notes.append("General git assistance provided")

    
    summarise(intent, repo_info, research, result_notes, cb)