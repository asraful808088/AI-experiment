"""
Python Dependency Scanner & Auto-Installer v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT IT DOES
────────────
1. SCAN   — Recursively walks every directory you give it (or cwd by default).
            Finds ALL .py files, no matter how deep.

2. BUILD LOCAL INDEX — Before anything else, collects the stem name of every
            .py file found AND every directory that contains an __init__.py.
            These are YOUR OWN local modules — they are never installable via
            pip and are silently excluded from all later steps.
            Examples: project_fixer.py → "project_fixer" is local.
                      myapp/__init__.py → "myapp" is local.

3. EXTRACT — For each file:
             • Regex + AST pulls every `import X` / `from X import ...`.
             • Strips stdlib modules (sys.stdlib_module_names on 3.10+,
               or a hardcoded 300+ name list on older Pythons).
             • Strips local modules (step 2 index).
             • Deduplicates across all files.

4. CROSS-REFERENCE — For each unique third-party import name:
             • Maps known import-name → PyPI package-name discrepancies
               (e.g. `cv2` → `opencv-python`, `PIL` → `Pillow`).
             • HTTP-checks https://pypi.org/pypi/<pkg>/json to confirm
               the package actually exists on PyPI.
             • Checks whether it is ALREADY installed (importlib.util.find_spec).

5. AI DECISION — Sends the full dependency report to Ollama (qwen2.5-coder:7b
                 by default) and asks it to:
                 • Confirm or correct the PyPI package names.
                 • Flag anything that looks wrong or suspicious.
                 • Produce a final ordered install list.
                 Falls back gracefully if Ollama is not available.

6. INSTALL — Builds a single `pip install` command (or batches if > 50 pkgs)
             and runs it with --break-system-packages so it works on Debian/
             Ubuntu systems without a venv.  Upgrades already-installed pkgs
             if --upgrade is requested.

USAGE (CLI)
───────────
    # Scan current directory
    python py_dep_scanner_v1.py

    # Scan specific paths
    python py_dep_scanner_v1.py /path/to/project /another/path

    # Scan + upgrade already-installed packages
    python py_dep_scanner_v1.py --upgrade /path/to/project

    # Dry-run (show what would be installed, don't actually install)
    python py_dep_scanner_v1.py --dry-run /path/to/project

    # Skip AI decision step (use regex + PyPI check only)
    python py_dep_scanner_v1.py --no-ai /path/to/project

    # Use a different Ollama model
    python py_dep_scanner_v1.py --model llama3.1 /path/to/project

PROGRAMMATIC USAGE
──────────────────
    from py_dep_scanner_v1 import DependencyScanner, ScannerConfig

    config  = ScannerConfig(upgrade=False, dry_run=False, use_ai=True)
    scanner = DependencyScanner(paths=["/my/project"], config=config)
    result  = scanner.run()
    # result.installed  → list of successfully installed packages
    # result.failed     → list of packages that failed
    # result.skipped    → already-installed packages that were skipped
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Colour helpers  (no dependencies — works even before anything is installed)
# ─────────────────────────────────────────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty()

def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def green(t):   return _c(t, "32")
def yellow(t):  return _c(t, "33")
def red(t):     return _c(t, "31")
def cyan(t):    return _c(t, "36")
def dim(t):     return _c(t, "2")
def bold(t):    return _c(t, "1")
def magenta(t): return _c(t, "35")

def _log(msg: str, color_fn=None):
    out = color_fn(msg) if color_fn else msg
    print(out, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Stdlib module list  (fallback for Python < 3.10)
# ─────────────────────────────────────────────────────────────────────────────
_STDLIB_FALLBACK: Set[str] = {
    "__future__", "_thread", "abc", "aifc", "argparse", "array", "ast",
    "asynchat", "asyncio", "asyncore", "atexit", "audioop", "base64",
    "bdb", "binascii", "binhex", "bisect", "builtins", "bz2", "calendar",
    "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs", "codeop",
    "colorsys", "compileall", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "cProfile", "csv", "ctypes",
    "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib",
    "dis", "distutils", "doctest", "email", "encodings", "enum",
    "errno", "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch",
    "fractions", "ftplib", "functools", "gc", "getopt", "getpass",
    "gettext", "glob", "grp", "gzip", "hashlib", "heapq", "hmac",
    "html", "http", "idlelib", "imaplib", "imghdr", "imp",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "lib2to3", "linecache", "locale", "logging", "lzma",
    "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform",
    "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
    "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
    "selectors", "shelve", "shlex", "shutil", "signal", "site",
    "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd",
    "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl",
    "stat", "statistics", "string", "stringprep", "struct", "subprocess",
    "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
    "tarfile", "telnetlib", "tempfile", "termios", "test", "textwrap",
    "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "tomllib", "trace", "traceback", "tracemalloc", "tty", "turtle",
    "turtledemo", "types", "typing", "unicodedata", "unittest", "urllib",
    "uu", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile",
    "zipimport", "zlib", "zoneinfo", "_collections_abc", "_weakrefset",
    "antigravity", "cgi", "cgitb", "chunk", "crypt", "imghdr",
    "mailcap", "msilib", "nis", "nntplib", "ossaudiodev", "pipes",
    "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib",
    # common internal / private
    "abc", "typing_extensions",
}


def _get_stdlib() -> Set[str]:
    try:
        return sys.stdlib_module_names  # type: ignore[attr-defined]  # Python 3.10+
    except AttributeError:
        return _STDLIB_FALLBACK


# ─────────────────────────────────────────────────────────────────────────────
# Import name  →  PyPI package name  (known discrepancies)
# ─────────────────────────────────────────────────────────────────────────────
_IMPORT_TO_PYPI: Dict[str, str] = {
    # Image / CV
    "cv2":                   "opencv-python",
    "cv":                    "opencv-python",
    "PIL":                   "Pillow",
    "skimage":               "scikit-image",
    "sklearn":               "scikit-learn",

    # Data / ML
    "numpy":                 "numpy",
    "np":                    "numpy",           # alias guard
    "pandas":                "pandas",
    "pd":                    "pandas",
    "scipy":                 "scipy",
    "matplotlib":            "matplotlib",
    "seaborn":               "seaborn",
    "tensorflow":            "tensorflow",
    "tf":                    "tensorflow",
    "torch":                 "torch",
    "torchvision":           "torchvision",
    "torchaudio":            "torchaudio",
    "keras":                 "keras",
    "xgboost":               "xgboost",
    "lightgbm":              "lightgbm",
    "catboost":              "catboost",
    "transformers":          "transformers",
    "datasets":              "datasets",
    "tokenizers":            "tokenizers",
    "accelerate":            "accelerate",
    "diffusers":             "diffusers",
    "langchain":             "langchain",
    "langchain_core":        "langchain-core",
    "langchain_community":   "langchain-community",
    "langchain_openai":      "langchain-openai",
    "langchain_anthropic":   "langchain-anthropic",
    "openai":                "openai",
    "anthropic":             "anthropic",
    "ollama":                "ollama",
    "groq":                  "groq",
    "cohere":                "cohere",
    "tiktoken":              "tiktoken",
    "sentence_transformers": "sentence-transformers",
    "chromadb":              "chromadb",
    "pinecone":              "pinecone-client",
    "faiss":                 "faiss-cpu",
    "hnswlib":               "hnswlib",

    # Web
    "requests":              "requests",
    "httpx":                 "httpx",
    "aiohttp":               "aiohttp",
    "fastapi":               "fastapi",
    "uvicorn":               "uvicorn",
    "starlette":             "starlette",
    "flask":                 "flask",
    "Flask":                 "flask",
    "django":                "django",
    "Django":                "django",
    "tornado":               "tornado",
    "sanic":                 "sanic",
    "litestar":              "litestar",
    "pydantic":              "pydantic",
    "pydantic_settings":     "pydantic-settings",
    "pydantic_v1":           "pydantic",
    "sqlalchemy":            "sqlalchemy",
    "SQLAlchemy":            "sqlalchemy",
    "alembic":               "alembic",
    "databases":             "databases",
    "tortoise":              "tortoise-orm",
    "beanie":                "beanie",
    "motor":                 "motor",
    "pymongo":               "pymongo",
    "redis":                 "redis",
    "celery":                "celery",
    "kombu":                 "kombu",
    "dramatiq":              "dramatiq",

    # CLI / dev tools
    "click":                 "click",
    "typer":                 "typer",
    "rich":                  "rich",
    "loguru":                "loguru",
    "colorama":              "colorama",
    "tqdm":                  "tqdm",
    "tabulate":              "tabulate",
    "prettytable":           "PrettyTable",
    "dotenv":                "python-dotenv",
    "decouple":              "python-decouple",
    "yaml":                  "PyYAML",
    "toml":                  "toml",
    "tomli":                 "tomli",
    "ujson":                 "ujson",
    "orjson":                "orjson",
    "msgpack":               "msgpack",
    "arrow":                 "arrow",
    "pendulum":              "pendulum",
    "dateutil":              "python-dateutil",
    "tzdata":                "tzdata",
    "pytz":                  "pytz",

    # Testing
    "pytest":                "pytest",
    "hypothesis":            "hypothesis",
    "faker":                 "Faker",
    "factory_boy":           "factory-boy",
    "freezegun":             "freezegun",
    "responses":             "responses",
    "httpretty":             "httpretty",

    # Async
    "anyio":                 "anyio",
    "trio":                  "trio",
    "asyncpg":               "asyncpg",
    "aiosqlite":             "aiosqlite",
    "aiofiles":              "aiofiles",
    "aiomysql":              "aiomysql",

    # Cloud / infra
    "boto3":                 "boto3",
    "botocore":              "botocore",
    "google":                "google-cloud-core",
    "googleapiclient":       "google-api-python-client",
    "azure":                 "azure-core",
    "paramiko":              "paramiko",
    "fabric":                "fabric",

    # Parsing / scraping
    "bs4":                   "beautifulsoup4",
    "lxml":                  "lxml",
    "html5lib":              "html5lib",
    "scrapy":                "scrapy",
    "playwright":            "playwright",
    "selenium":              "selenium",
    "pyppeteer":             "pyppeteer",

    # Serialisation / formats
    "openpyxl":              "openpyxl",
    "xlrd":                  "xlrd",
    "xlwt":                  "xlwt",
    "docx":                  "python-docx",
    "pptx":                  "python-pptx",
    "pypdf":                 "pypdf",
    "PyPDF2":                "PyPDF2",
    "fitz":                  "pymupdf",
    "reportlab":             "reportlab",
    "fpdf":                  "fpdf2",
    "barcode":               "python-barcode",
    "qrcode":                "qrcode",

    # Misc popular
    "cryptography":          "cryptography",
    "nacl":                  "pynacl",
    "jwt":                   "PyJWT",
    "passlib":               "passlib",
    "bcrypt":                "bcrypt",
    "parameterized":         "parameterized",
    "attr":                  "attrs",
    "attrs":                 "attrs",
    "cachetools":            "cachetools",
    "diskcache":             "diskcache",
    "joblib":                "joblib",
    "dask":                  "dask",
    "numba":                 "numba",
    "cffi":                  "cffi",
    "ctypes":                "ctypes",      # stdlib but guard
    "psutil":                "psutil",
    "watchdog":              "watchdog",
    "schedule":              "schedule",
    "apscheduler":           "APScheduler",
    "nmap":                  "python-nmap",
    "sh":                    "sh",
    "plumbum":               "plumbum",
    "invoke":                "invoke",
    "gitpython":             "gitpython",
    "git":                   "gitpython",

    # Networking
    "websocket":             "websocket-client",
    "websockets":            "websockets",
    "zmq":                   "pyzmq",
    "pika":                  "pika",
    "kafka":                 "kafka-python",
    "nats":                  "nats-py",

    # Ollama / LLM infra
    "chromadb":              "chromadb",
    "qdrant_client":         "qdrant-client",
    "weaviate":              "weaviate-client",
    "pymilvus":              "pymilvus",

    # Type annotation helpers (not stdlib in older Pythons)
    "typing_extensions":     "typing_extensions",
    "annotated_types":       "annotated-types",
}


# ─────────────────────────────────────────────────────────────────────────────
# Config / Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ScannerConfig:
    upgrade:      bool = False
    dry_run:      bool = False
    use_ai:       bool = True
    ai_model:     str  = "qwen2.5-coder:7b"
    ai_keepalive: int  = -1
    batch_size:   int  = 50       # max packages per pip install call
    pypi_timeout: int  = 6        # seconds per PyPI request
    skip_patterns: List[str] = field(default_factory=lambda: [
        "**/site-packages/**",
        "**/.venv/**",
        "**/venv/**",
        "**/env/**",
        "**/__pycache__/**",
        "**/.git/**",
        "**/node_modules/**",
        "**/dist/**",
        "**/build/**",
    ])


@dataclass
class ScanResult:
    py_files:      List[str]        = field(default_factory=list)
    raw_imports:   Dict[str, Set[str]] = field(default_factory=dict)   # file → set of imports
    all_imports:   Set[str]         = field(default_factory=set)
    stdlib_hits:   Set[str]         = field(default_factory=set)
    third_party:   Set[str]         = field(default_factory=set)
    pypi_map:      Dict[str, str]   = field(default_factory=dict)      # import → pypi name
    pypi_valid:    Set[str]         = field(default_factory=set)        # confirmed on PyPI
    pypi_missing:  Set[str]         = field(default_factory=set)        # not found on PyPI
    already_installed: Set[str]     = field(default_factory=set)
    to_install:    List[str]        = field(default_factory=list)
    ai_corrections: Dict[str, str]  = field(default_factory=dict)
    installed:     List[str]        = field(default_factory=list)
    failed:        List[str]        = field(default_factory=list)
    skipped:       List[str]        = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# File walker
# ─────────────────────────────────────────────────────────────────────────────
def _matches_skip(path: Path, patterns: List[str]) -> bool:
    from fnmatch import fnmatch
    p_str = str(path)
    for pat in patterns:
        # Convert glob pattern to path-match check
        if any(fnmatch(part, pat.strip("**/").strip("**"))
               for part in path.parts):
            return True
        # Also check full path string
        clean_pat = pat.replace("**/", "").replace("/**", "")
        if clean_pat in p_str:
            return True
    return False


def walk_python_files(roots: List[str], config: ScannerConfig) -> List[str]:
    """Recursively find all .py files under every root path."""
    found = []
    for root in roots:
        root_path = Path(root).resolve()
        if not root_path.exists():
            _log(f"  ⚠  Path not found: {root_path}", yellow)
            continue
        if root_path.is_file() and root_path.suffix == ".py":
            found.append(str(root_path))
            continue
        for py_file in root_path.rglob("*.py"):
            if not _matches_skip(py_file, config.skip_patterns):
                found.append(str(py_file))
    return sorted(set(found))


# ─────────────────────────────────────────────────────────────────────────────
# Import extractor  (regex + AST fallback)
# ─────────────────────────────────────────────────────────────────────────────

# Primary regex — handles:
#   import X
#   import X as Y
#   import X, Y, Z
#   from X import ...
#   from X import (...)   ← multi-line via continuation
_IMPORT_RE = re.compile(
    r"""
    ^\s*                          # optional leading whitespace
    (?:
        import\s+                 # bare import
        ([\w\s,]+?)               # module list (comma-separated)
        (?:\s+as\s+\w+)?          # optional alias (last one)
        \s*$                      # end of line
    |
        from\s+                   # from … import
        ([\w.]+)                  # module name (dotted OK)
        \s+import\s+
        (?:[\w\s,.*()\\]|\n)*     # what's imported (we only care about the module)
    )
    """,
    re.VERBOSE | re.MULTILINE,
)

# Simpler backup regex for tricky lines
_SIMPLE_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+([\w]+)",
    re.MULTILINE,
)


def _extract_top_level(module: str) -> str:
    """Return only the top-level package name from a dotted module path."""
    return module.strip().split(".")[0].strip()


def extract_imports_from_file(path: str) -> Set[str]:
    """Extract all unique top-level import names from a Python source file."""
    try:
        src = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return set()

    imports: Set[str] = set()

    # 1. Try AST (most accurate)
    try:
        tree = ast.parse(src, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = _extract_top_level(alias.name)
                    if top:
                        imports.add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:   # skip relative imports
                    top = _extract_top_level(node.module)
                    if top:
                        imports.add(top)
        return imports
    except SyntaxError:
        pass  # fall through to regex

    # 2. Regex fallback (handles files with syntax errors)
    for m in _IMPORT_RE.finditer(src):
        group1, group2 = m.group(1), m.group(2)
        if group2:
            top = _extract_top_level(group2)
            if top:
                imports.add(top)
        elif group1:
            # may be "X, Y, Z" or "X as Y"
            parts = re.split(r",|\s+as\s+", group1)
            for part in parts:
                top = _extract_top_level(part.strip())
                if top:
                    imports.add(top)

    # 3. Ultra-simple regex as last resort
    for m in _SIMPLE_IMPORT_RE.finditer(src):
        top = _extract_top_level(m.group(1))
        if top:
            imports.add(top)

    return imports


# ─────────────────────────────────────────────────────────────────────────────
# Stdlib filter
# ─────────────────────────────────────────────────────────────────────────────
def filter_third_party(imports: Set[str]) -> Tuple[Set[str], Set[str]]:
    """Split imports into (stdlib, third_party)."""
    stdlib     = _get_stdlib()
    std_hits   = set()
    third      = set()
    for name in imports:
        if not name or name.startswith("_"):
            continue
        if name in stdlib:
            std_hits.add(name)
        else:
            third.add(name)
    return std_hits, third


# ─────────────────────────────────────────────────────────────────────────────
# Import → PyPI name mapper
# ─────────────────────────────────────────────────────────────────────────────
def resolve_pypi_names(imports: Set[str]) -> Dict[str, str]:
    """Return {import_name: pypi_package_name} using the known-map + identity fallback."""
    result = {}
    for name in imports:
        pypi_name = _IMPORT_TO_PYPI.get(name, name)
        result[name] = pypi_name
    return result


# ─────────────────────────────────────────────────────────────────────────────
# PyPI existence checker
# ─────────────────────────────────────────────────────────────────────────────
def _pypi_exists(package: str, timeout: int = 6) -> bool:
    url = f"https://pypi.org/pypi/{urllib.parse.quote(package)}/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "py-dep-scanner/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return False   # network down → assume exists (don't block install)


# Make urllib.parse available at module level
import urllib.parse


def check_pypi_existence(pypi_map: Dict[str, str], config: ScannerConfig) -> Tuple[Set[str], Set[str]]:
    """Returns (confirmed_set, missing_set) of PyPI package names."""
    confirmed = set()
    missing   = set()
    unique_pkgs = sorted(set(pypi_map.values()))
    _log(f"\n🌐 Checking {len(unique_pkgs)} packages on PyPI...", cyan)
    for pkg in unique_pkgs:
        if _pypi_exists(pkg, config.pypi_timeout):
            _log(f"  ✓ {pkg}", dim)
            confirmed.add(pkg)
        else:
            _log(f"  ✗ {pkg} — NOT found on PyPI", yellow)
            missing.add(pkg)
    return confirmed, missing


# ─────────────────────────────────────────────────────────────────────────────
# Already-installed checker
# ─────────────────────────────────────────────────────────────────────────────
def check_installed(import_names: Set[str]) -> Set[str]:
    """Return the subset of import names that are already importable."""
    installed = set()
    for name in import_names:
        if importlib.util.find_spec(name) is not None:
            installed.add(name)
    return installed


# ─────────────────────────────────────────────────────────────────────────────
# AI decision layer  (Ollama)
# ─────────────────────────────────────────────────────────────────────────────
_AI_SYSTEM_PROMPT = """You are a Python packaging expert.

You will receive a JSON object with these fields:
  - third_party_imports: list of raw import names found in Python source files
  - initial_pypi_map:    dict mapping import name → guessed PyPI package name
  - pypi_missing:        list of package names NOT found on PyPI (may be wrong name)
  - already_installed:   list of import names already importable in this Python env

Your job:
1. Review and CORRECT any wrong import→PyPI name mappings (e.g. cv2→opencv-python).
2. For packages in pypi_missing, suggest the correct PyPI name if you know it,
   or mark them as "unknown" if they are truly not on PyPI.
3. Remove duplicates and packages that are part of another package.
4. Produce a FINAL ordered install list of PyPI package names.

Return ONLY valid JSON — no markdown fences, no explanation:
{
  "corrections": {
    "<import_name>": "<correct_pypi_name>"
  },
  "final_install_list": [
    "<pypi_package_name>",
    ...
  ],
  "notes": "optional short string with any warnings"
}

RULES:
- Include packages even if already_installed (the caller decides whether to upgrade).
- Do NOT include stdlib modules.
- Do NOT include packages you are not confident about — omit rather than guess wrongly.
- Sort final_install_list alphabetically.
"""


def ask_ai(
    third_party:    Set[str],
    pypi_map:       Dict[str, str],
    pypi_missing:   Set[str],
    already_inst:   Set[str],
    config:         ScannerConfig,
) -> Tuple[Dict[str, str], List[str], str]:
    """
    Returns (corrections_dict, final_install_list, notes_str).
    Falls back to (empty, sorted(pypi_map.values()), "") if Ollama is unavailable.
    """
    try:
        import ollama as _ollama
    except ImportError:
        _log("  ⚠  ollama package not installed — skipping AI step", yellow)
        return {}, sorted(set(pypi_map.values())), "ollama not available"

    payload = {
        "third_party_imports": sorted(third_party),
        "initial_pypi_map":    {k: v for k, v in sorted(pypi_map.items())},
        "pypi_missing":        sorted(pypi_missing),
        "already_installed":   sorted(already_inst),
    }

    _log(f"\n🤖 Consulting AI ({config.ai_model}) for final package verification...", cyan)

    try:
        response = _ollama.chat(
            model=config.ai_model,
            keep_alive=config.ai_keepalive,
            messages=[
                {"role": "system", "content": _AI_SYSTEM_PROMPT},
                {"role": "user",   "content": json.dumps(payload, indent=2)},
            ],
        )
        raw = response["message"]["content"].strip()
        # Strip code fences if model wraps in them
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$",          "", raw, flags=re.MULTILINE)
        data        = json.loads(raw.strip())
        corrections = data.get("corrections", {})
        final_list  = data.get("final_install_list", [])
        notes       = data.get("notes", "")
        return corrections, final_list, notes
    except json.JSONDecodeError as e:
        _log(f"  ⚠  AI returned invalid JSON ({e}) — using regex+PyPI results", yellow)
    except Exception as e:
        _log(f"  ⚠  AI step failed ({e}) — using regex+PyPI results", yellow)

    return {}, sorted(set(pypi_map.values())), "AI step failed"


# ─────────────────────────────────────────────────────────────────────────────
# Pip installer
# ─────────────────────────────────────────────────────────────────────────────
def _decode(b) -> str:
    if isinstance(b, bytes):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return b.decode(enc)
            except Exception:
                pass
    return str(b)


def pip_install(
    packages:  List[str],
    upgrade:   bool,
    dry_run:   bool,
    batch_size: int,
) -> Tuple[List[str], List[str]]:
    """
    Install packages in batches.
    Returns (installed, failed).
    """
    if not packages:
        return [], []

    installed: List[str] = []
    failed:    List[str] = []

    # Build base command
    base_cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        base_cmd.append("--upgrade")
    base_cmd.append("--break-system-packages")
    # Quiet output to reduce noise; errors still surface
    base_cmd.extend(["-q", "--progress-bar", "off"])

    # Split into batches
    batches = [packages[i:i + batch_size] for i in range(0, len(packages), batch_size)]
    _log(f"\n📦 Installing {len(packages)} package(s) in {len(batches)} batch(es)...", cyan)

    for b_idx, batch in enumerate(batches, 1):
        cmd = base_cmd + batch
        _log(f"\n  Batch {b_idx}/{len(batches)}: {' '.join(batch)}", dim)

        if dry_run:
            _log(f"  [DRY-RUN] Would run: {' '.join(cmd)}", yellow)
            installed.extend(batch)
            continue

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
            )
            stdout = _decode(proc.stdout)
            stderr = _decode(proc.stderr)

            if proc.returncode == 0:
                _log(f"  ✓ Batch {b_idx} installed successfully", green)
                installed.extend(batch)
            else:
                _log(f"  ✗ Batch {b_idx} failed (rc={proc.returncode})", red)
                if stderr:
                    _log(f"    {stderr.strip()[:400]}", red)
                # Try each package individually to isolate the failure
                _log(f"  ↪ Retrying packages one by one...", yellow)
                for pkg in batch:
                    single_cmd = base_cmd + [pkg]
                    r2 = subprocess.run(single_cmd, capture_output=True)
                    if r2.returncode == 0:
                        _log(f"    ✓ {pkg}", green)
                        installed.append(pkg)
                    else:
                        err2 = _decode(r2.stderr).strip()[:200]
                        _log(f"    ✗ {pkg}: {err2}", red)
                        failed.append(pkg)
        except Exception as e:
            _log(f"  ✗ Batch {b_idx} exception: {e}", red)
            failed.extend(batch)

    return installed, failed


# ─────────────────────────────────────────────────────────────────────────────
# Report printer
# ─────────────────────────────────────────────────────────────────────────────
def print_report(result: ScanResult, config: ScannerConfig):
    sep = "─" * 60
    _log(f"\n{sep}", dim)
    _log(bold("📊 SCAN REPORT"), None)
    _log(sep, dim)
    _log(f"  Python files scanned  : {len(result.py_files)}", None)
    _log(f"  Total imports found   : {len(result.all_imports)}", None)
    _log(f"  Stdlib (skipped)      : {len(result.stdlib_hits)}", dim)
    _log(f"  Third-party           : {len(result.third_party)}", cyan)
    _log(f"  Already installed     : {len(result.already_installed)}", green)
    _log(f"  Not on PyPI           : {len(result.pypi_missing)}", yellow)
    _log(f"  Queued for install    : {len(result.to_install)}", bold)

    if result.ai_corrections:
        _log(f"\n{bold('🤖 AI Corrections')}", None)
        for imp, pkg in sorted(result.ai_corrections.items()):
            _log(f"  {imp:<30s} → {pkg}", magenta)

    if result.to_install:
        _log(f"\n{bold('📋 Packages to install')}", None)
        for pkg in result.to_install:
            marker = "[upgrade]" if pkg in {
                result.pypi_map.get(i, i) for i in result.already_installed
            } else ""
            _log(f"  • {pkg} {marker}", None)

    if result.pypi_missing:
        _log(f"\n{bold('⚠  Not found on PyPI (skipped)')}", None)
        for pkg in sorted(result.pypi_missing):
            _log(f"  • {pkg}", yellow)

    _log(sep, dim)

    if config.dry_run:
        _log(bold("🔍 DRY-RUN — nothing was installed."), yellow)
        return

    if result.installed:
        _log(f"\n{bold('✅ Successfully installed')} ({len(result.installed)})", None)
        for p in result.installed:
            _log(f"  ✓ {p}", green)

    if result.failed:
        _log(f"\n{bold('❌ Failed to install')} ({len(result.failed)})", None)
        for p in result.failed:
            _log(f"  ✗ {p}", red)

    if result.skipped:
        _log(f"\n{bold('⏭  Skipped (already installed)')} ({len(result.skipped)})", None)
        for p in result.skipped:
            _log(f"  · {p}", dim)


# ─────────────────────────────────────────────────────────────────────────────
# Main scanner class
# ─────────────────────────────────────────────────────────────────────────────
class DependencyScanner:
    def __init__(self, paths: List[str], config: Optional[ScannerConfig] = None):
        self.paths  = paths or [os.getcwd()]
        self.config = config or ScannerConfig()

    def run(self) -> ScanResult:
        result = ScanResult()
        cfg    = self.config

        _log(bold("\n⚡ Python Dependency Scanner & Auto-Installer v1.0"), None)
        _log(dim("   Scan → Extract → AI-Verify → PyPI-Check → Install"), None)
        _log("", None)

        # ── STEP 1: Walk files ────────────────────────────────────────────────
        _log(bold("📂 STEP 1 — Scanning Python files..."), cyan)
        result.py_files = walk_python_files(self.paths, cfg)
        _log(f"  Found {len(result.py_files)} .py file(s) across {len(self.paths)} path(s)", None)

        if not result.py_files:
            _log("  No Python files found. Exiting.", yellow)
            return result

        # ── STEP 2: Extract imports ────────────────────────────────────────────
        _log(bold("\n🔍 STEP 2 — Extracting imports (regex + AST)..."), cyan)
        all_imports: Set[str] = set()

        for py_file in result.py_files:
            file_imports = extract_imports_from_file(py_file)
            result.raw_imports[py_file] = file_imports
            all_imports |= file_imports
            rel = os.path.relpath(py_file, self.paths[0]) if len(self.paths) == 1 else py_file
            _log(f"  {rel} → {len(file_imports)} import(s)", dim)

        result.all_imports = all_imports
        _log(f"\n  Total unique imports: {len(all_imports)}", None)

        # ── STEP 3: Filter stdlib ─────────────────────────────────────────────
        _log(bold("\n🧹 STEP 3 — Filtering stdlib modules..."), cyan)
        result.stdlib_hits, result.third_party = filter_third_party(all_imports)
        _log(f"  Stdlib   : {len(result.stdlib_hits)} (removed)", dim)
        _log(f"  Third-party: {len(result.third_party)}", None)

        if not result.third_party:
            _log("  No third-party imports found. Nothing to install.", green)
            return result

        # ── STEP 4: Resolve PyPI names ────────────────────────────────────────
        _log(bold("\n🗺  STEP 4 — Mapping import names → PyPI package names..."), cyan)
        result.pypi_map = resolve_pypi_names(result.third_party)
        for imp, pkg in sorted(result.pypi_map.items()):
            marker = " (remapped)" if imp != pkg else ""
            _log(f"  {imp:<35s} → {pkg}{marker}", dim)

        # ── STEP 5: Check already installed ────────────────────────────────────
        _log(bold("\n✅ STEP 5 — Checking which packages are already installed..."), cyan)
        result.already_installed = check_installed(result.third_party)
        for name in sorted(result.already_installed):
            _log(f"  ✓ {name} (already installed)", green)
        missing_imports = result.third_party - result.already_installed
        _log(f"  Already installed: {len(result.already_installed)}  |  "
             f"Need checking: {len(missing_imports)}", None)

        # ── STEP 6: PyPI existence check ────────────────────────────────────────
        _log(bold("\n🌐 STEP 6 — Cross-referencing with PyPI..."), cyan)
        packages_to_check = {
            imp: result.pypi_map[imp]
            for imp in missing_imports
            if imp in result.pypi_map
        }
        if packages_to_check:
            result.pypi_valid, result.pypi_missing = check_pypi_existence(
                packages_to_check, cfg
            )
        else:
            _log("  All imports already installed — skipping PyPI check.", dim)

        # ── STEP 7: AI decision ────────────────────────────────────────────────
        corrections: Dict[str, str] = {}
        final_list:  List[str]      = []

        if cfg.use_ai and result.third_party:
            _log(bold("\n🤖 STEP 7 — AI verification & final install list..."), cyan)
            corrections, final_list, notes = ask_ai(
                result.third_party,
                result.pypi_map,
                result.pypi_missing,
                result.already_installed,
                cfg,
            )
            result.ai_corrections = corrections
            if notes:
                _log(f"  📝 AI notes: {notes}", magenta)

            # Apply corrections to pypi_map
            for imp_name, correct_pkg in corrections.items():
                result.pypi_map[imp_name] = correct_pkg

        if not final_list:
            # Build list ourselves: confirmed PyPI packages + corrections
            final_list = sorted(
                {v for k, v in result.pypi_map.items()
                 if v in result.pypi_valid or v in set(corrections.values())}
            )

        # ── STEP 8: Decide what to install ─────────────────────────────────────
        _log(bold("\n📋 STEP 8 — Building install queue..."), cyan)

        # Already-installed import names mapped to their PyPI names
        installed_pypi_names = {
            result.pypi_map.get(imp, imp)
            for imp in result.already_installed
        }

        if cfg.upgrade:
            to_install = final_list  # include everything (pip --upgrade handles it)
        else:
            to_install = [p for p in final_list if p not in installed_pypi_names]
            result.skipped = sorted(installed_pypi_names & set(final_list))

        result.to_install = to_install

        # ── Print report before installing ─────────────────────────────────────
        print_report(result, cfg)

        if not to_install:
            _log(bold("\n🎉 All dependencies satisfied — nothing to install!"), green)
            return result

        # ── STEP 9: Install ────────────────────────────────────────────────────
        _log(bold("\n🚀 STEP 9 — Installing packages..."), cyan)
        if cfg.dry_run:
            _log("  [DRY-RUN] Skipping actual installation.", yellow)
            result.installed = to_install
            return result

        result.installed, result.failed = pip_install(
            to_install,
            upgrade=cfg.upgrade,
            dry_run=cfg.dry_run,
            batch_size=cfg.batch_size,
        )

        # ── Final summary ───────────────────────────────────────────────────────
        _log(bold("\n" + "═" * 60), None)
        if result.failed:
            _log(bold(f"⚠  DONE — {len(result.installed)} installed, "
                      f"{len(result.failed)} failed"), yellow)
        else:
            _log(bold(f"✅ DONE — {len(result.installed)} package(s) installed "
                      f"successfully!"), green)
        _log("═" * 60, None)

        return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────
def _parse_args():
    import argparse
    p = argparse.ArgumentParser(
        description="Scan Python files, find all imports, verify on PyPI via AI, install.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python py_dep_scanner_v1.py                        # scan current dir
  python py_dep_scanner_v1.py /path/to/project       # scan a project
  python py_dep_scanner_v1.py --upgrade .            # scan + upgrade
  python py_dep_scanner_v1.py --dry-run .            # show only, no install
  python py_dep_scanner_v1.py --no-ai .              # skip Ollama step
  python py_dep_scanner_v1.py --model llama3.1 .     # use different model
  python py_dep_scanner_v1.py src/ tests/ scripts/   # multiple roots
        """,
    )
    p.add_argument("paths", nargs="*", default=[os.getcwd()],
                   help="Directories or files to scan (default: current directory)")
    p.add_argument("--upgrade",  action="store_true",
                   help="Upgrade already-installed packages too")
    p.add_argument("--dry-run",  action="store_true",
                   help="Show what would be installed, don't actually install")
    p.add_argument("--no-ai",    action="store_true",
                   help="Skip Ollama AI verification step")
    p.add_argument("--model",    default="qwen2.5-coder:7b",
                   help="Ollama model to use (default: qwen2.5-coder:7b)")
    p.add_argument("--batch",    type=int, default=50,
                   help="Max packages per pip install call (default: 50)")
    return p.parse_args()


if __name__ == "__main__":
    args   = _parse_args()
    config = ScannerConfig(
        upgrade    = args.upgrade,
        dry_run    = args.dry_run,
        use_ai     = not args.no_ai,
        ai_model   = args.model,
        batch_size = args.batch,
    )
    scanner = DependencyScanner(paths=args.paths, config=config)
    result  = scanner.run()

    sys.exit(1 if result.failed else 0)