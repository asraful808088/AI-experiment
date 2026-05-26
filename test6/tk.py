"""
╔══════════════════════════════════════════════════════════════╗
║              Agentic Bot v2  —  Self-Improving Agent         ║
║                                                              ║
║  • Task Library  task_library/<name>/task.json               ║
║    — 15 pre-built system tasks (temp, prefetch, cache, …)    ║
║    — Saves every NEW successful task automatically           ║
║    — Reads cached working code on repeat runs                ║
║                                                              ║
║  • Silent output  — only stage labels shown, never raw code  ║
║    or LLM tokens                                             ║
║                                                              ║
║  • No duplicate writes — final code saved exactly ONCE       ║
║                                                              ║
║  • Cross-platform (auto-detects Windows / Linux / macOS)     ║
╚══════════════════════════════════════════════════════════════╝
"""

import ollama
import subprocess, os, sys, json, hashlib, re, platform, shutil
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Callable, Optional

# ──────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────
MAX_RETRIES    = 5
TASKS_ROOT     = Path("agent_tasks")
TASK_LIB_ROOT  = Path("task_library")
META_FILE      = TASKS_ROOT / "_metadata" / "index.json"
SESSIONS_ROOT  = Path("sessions")
OS_NAME        = platform.system().lower()   # 'windows' | 'linux' | 'darwin'
SESSION_INSTALLED: set[str] = set()

# ──────────────────────────────────────────────────────────────
# EVENTS
# ──────────────────────────────────────────────────────────────
class EV:
    TASK_START       = "task_start"
    STAGE            = "stage"
    PLAN             = "plan"
    CODE_GEN         = "code_gen"
    RUN              = "run"
    RETRY            = "retry"
    SUMMARY          = "summary"
    CACHE_HIT        = "cache_hit"
    CACHE_SAVE       = "cache_save"
    LIB_HIT          = "lib_hit"
    LIB_SAVE         = "lib_save"
    SESSION_SAVE     = "session_save"
    SUCCESS          = "success"
    FAILURE          = "failure"
    PKG_INSTALL      = "pkg_install"
    PKG_REINSTALL    = "pkg_reinstall"
    PKG_REMOVE       = "pkg_remove"
    PKG_ERROR        = "pkg_error"
    LLM_STREAM       = "llm_stream"
    LLM_STREAM_START = "llm_stream_start"
    LLM_STREAM_END   = "llm_stream_end"
    LLM_DONE         = "llm_done"
    LLM_ERROR        = "llm_error"
    WARN             = "warn"
    INFO             = "info"

_EV_COLORS: dict[str, str] = {
    EV.TASK_START:    "cyan",    EV.STAGE:        "blue",
    EV.PLAN:          "blue",    EV.CODE_GEN:     "blue",
    EV.RUN:           "cyan",    EV.RETRY:        "orange",
    EV.SUMMARY:       "cyan",    EV.CACHE_HIT:    "magenta",
    EV.CACHE_SAVE:    "magenta", EV.LIB_HIT:      "magenta",
    EV.LIB_SAVE:      "magenta", EV.SESSION_SAVE: "magenta",
    EV.SUCCESS:       "green",   EV.FAILURE:      "red",
    EV.PKG_INSTALL:   "yellow",  EV.PKG_REINSTALL:"yellow",
    EV.PKG_REMOVE:    "gray",    EV.PKG_ERROR:    "red",
    EV.LLM_DONE:      "gray",    EV.LLM_ERROR:    "red",
    EV.WARN:          "yellow",  EV.INFO:         "gray",
}

ANSI = {
    "green":   "\033[92m", "red":     "\033[91m", "yellow": "\033[93m",
    "cyan":    "\033[96m", "blue":    "\033[94m", "magenta":"\033[95m",
    "white":   "\033[97m", "gray":    "\033[90m", "orange": "\033[33m",
}
RESET = "\033[0m"


@dataclass
class AgentEvent:
    event:     str
    text:      str
    color:     str
    data:      dict
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


def make_event(event: str, text: str, **data) -> AgentEvent:
    return AgentEvent(
        event=event, text=text,
        color=_EV_COLORS.get(event, "white"),
        data=data, timestamp=datetime.now().isoformat()
    )


# ──────────────────────────────────────────────────────────────
# QUIET DEFAULT CALLBACK
# Only shows: task header, stage labels, pkg install, success/fail
# Hides:      raw LLM tokens, code blocks
# ──────────────────────────────────────────────────────────────
_SILENT_EVENTS = {
    EV.LLM_STREAM, EV.LLM_STREAM_START, EV.LLM_STREAM_END, EV.LLM_DONE,
}

def default_callback(ev: AgentEvent):
    if ev.event in _SILENT_EVENTS:
        return
    color = ANSI.get(ev.color, "")

    if ev.event == EV.TASK_START:
        task = ev.data.get("task", "")
        print(f"\n{color}{'═'*62}")
        print(f"  ▶  {task}")
        print(f"{'═'*62}{RESET}")

    elif ev.event == EV.SUCCESS:
        print(f"\n{color}  ✅  {ev.text}{RESET}")

    elif ev.event == EV.FAILURE:
        print(f"\n{color}  ❌  {ev.text}{RESET}")

    elif ev.event == EV.SUMMARY:
        summary = ev.data.get("summary", "")
        print(f"\n{color}{'─'*62}")
        print("[SUMMARY]")
        print(summary)
        print(f"{'─'*62}{RESET}")

    elif ev.event == EV.RETRY:
        attempt = ev.data.get("attempt", "?")
        mx      = ev.data.get("max_retries", MAX_RETRIES)
        print(f"{color}  🔁  Retry {attempt}/{mx}...{RESET}")

    elif ev.event in (EV.LLM_ERROR, EV.PKG_ERROR, EV.WARN):
        print(f"{color}  ⚠  {ev.text}{RESET}")

    else:
        print(f"{color}{ev.text}{RESET}")


# ──────────────────────────────────────────────────────────────
# PRE-DEFINED TASK TEMPLATES
# ──────────────────────────────────────────────────────────────
PREDEFINED_TASKS: list[dict] = [

    # ── Windows: Temp cleanup ──────────────────────────────
    {
        "name": "clean_temp",
        "description": "Delete all files in %TEMP% and C:\\Windows\\Temp",
        "tags": ["system", "cleanup", "temp", "windows"],
        "aliases": ["clean temp", "clear temp", "delete temp", "clean temp files",
                    "empty temp", "remove temp"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script that safely deletes all files and sub-folders inside:\n"
            "  1. os.environ.get('TEMP') or os.environ.get('TMP')\n"
            "  2. C:\\Windows\\Temp\n"
            "Skip locked / in-use files (catch PermissionError silently).\n"
            "Track total files deleted and total MB freed.\n"
            "Print a summary report: files deleted, folders deleted, MB freed."
        ),
    },

    # ── Windows: Prefetch cleanup ──────────────────────────
    {
        "name": "clean_prefetch",
        "description": "Delete Windows Prefetch (.pf) files",
        "tags": ["system", "cleanup", "prefetch", "windows"],
        "aliases": ["clean prefetch", "clear prefetch", "delete prefetch",
                    "prefetch files", "prefetch"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script to delete all *.pf files from C:\\Windows\\Prefetch.\n"
            "First check admin rights: import ctypes; is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0\n"
            "If not admin, print a warning but continue (some files may be skippable).\n"
            "Skip locked files gracefully. Print count deleted and MB freed."
        ),
    },

    # ── Windows: Recycle Bin ───────────────────────────────
    {
        "name": "clean_recycle_bin",
        "description": "Empty the Windows Recycle Bin",
        "tags": ["system", "cleanup", "recycle", "windows"],
        "aliases": ["empty recycle bin", "clear recycle bin", "empty trash",
                    "recycle bin", "trash"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script to empty the Windows Recycle Bin using ctypes.\n"
            "Use: ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x0007)\n"
            "  flags: SHERB_NOCONFIRMATION=1 | SHERB_NOPROGRESSUI=2 | SHERB_NOSOUND=4 → 0x0007\n"
            "Print success or error. If return code is 0 → success, else show error code."
        ),
    },

    # ── Windows: DNS flush ─────────────────────────────────
    {
        "name": "flush_dns",
        "description": "Flush Windows DNS resolver cache",
        "tags": ["network", "dns", "cache", "windows"],
        "aliases": ["flush dns", "clear dns", "dns cache", "reset dns",
                    "dns flush", "clear dns cache"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script that flushes the Windows DNS cache by running:\n"
            "  subprocess.run(['ipconfig', '/flushdns'], capture_output=True, text=True)\n"
            "Print the command output. Also reset the NetBIOS name cache:\n"
            "  subprocess.run(['nbtstat', '-R'], capture_output=True, text=True)\n"
            "Print both results clearly."
        ),
    },

    # ── All OS: Network info ───────────────────────────────
    {
        "name": "network_info",
        "description": "Show full network info: IPs, gateway, DNS, public IP, hostname",
        "tags": ["network", "info", "ip", "system"],
        "aliases": ["network info", "ip info", "network details", "show network",
                    "network status", "ip address", "my ip"],
        "os": ["windows", "linux", "darwin"],
        "task_prompt": (
            "Write a Python script to display complete network information using psutil and socket:\n"
            "  1. Hostname and local IP addresses for each interface (skip loopback)\n"
            "  2. Default gateway (parse from psutil.net_if_stats or use subprocess: "
            "     'route print' on Windows, 'ip route' on Linux)\n"
            "  3. DNS servers (parse ipconfig /all on Windows or /etc/resolv.conf on Linux)\n"
            "  4. Public/external IP via: requests.get('https://api.ipify.org').text\n"
            "  5. MAC address per interface\n"
            "Print as a neatly formatted report."
        ),
    },

    # ── All OS: System info ────────────────────────────────
    {
        "name": "system_info",
        "description": "Show CPU, RAM, disk, OS, uptime and hardware info",
        "tags": ["system", "info", "hardware", "cpu", "ram", "disk", "specs"],
        "aliases": ["system info", "pc info", "hardware info", "show system",
                    "specs", "system specs", "my pc"],
        "os": ["windows", "linux", "darwin"],
        "task_prompt": (
            "Write a Python script using platform, psutil, os to display:\n"
            "  1. OS: name, version, architecture, build\n"
            "  2. CPU: model (from platform.processor()), physical cores, logical cores, "
            "     current MHz, current usage %\n"
            "  3. RAM: total GB, used GB, free GB, usage %\n"
            "  4. Disks: for each partition — mount, total GB, used GB, free GB, usage %\n"
            "  5. Current user, hostname\n"
            "  6. System uptime (hours:minutes)\n"
            "  7. Python version\n"
            "Format as a clean ASCII report with section headers."
        ),
    },

    # ── Windows: Startup programs ──────────────────────────
    {
        "name": "list_startup_programs",
        "description": "List all programs set to run at Windows startup",
        "tags": ["system", "startup", "windows", "registry", "autorun"],
        "aliases": ["list startup", "startup programs", "show startup",
                    "startup list", "autostart", "boot programs"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script that lists ALL Windows startup programs from:\n"
            "  1. HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\n"
            "  2. HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\n"
            "  3. HKLM\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Run\n"
            "  4. %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup (folder)\n"
            "  5. C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\StartUp (folder)\n"
            "Use winreg for registry keys. For each entry print: source, name, command/path."
        ),
    },

    # ── All OS: Disk usage ─────────────────────────────────
    {
        "name": "disk_usage",
        "description": "Show disk space usage for all drives with bar chart",
        "tags": ["system", "disk", "storage", "space", "drives"],
        "aliases": ["disk usage", "disk space", "storage info", "drive space",
                    "free space", "hard drive space"],
        "os": ["windows", "linux", "darwin"],
        "task_prompt": (
            "Write a Python script to show disk usage for every mounted partition.\n"
            "Use psutil.disk_partitions() and psutil.disk_usage().\n"
            "For each drive print:\n"
            "  Drive | Total GB | Used GB | Free GB | Usage% | [ASCII bar 20 chars]\n"
            "Bar: filled with '█', empty with '░'. Example: ████████░░░░░░░░░░░░ 43%\n"
            "Skip drives that raise PermissionError. Print grand total at the end."
        ),
    },

    # ── All OS: High CPU/RAM processes ────────────────────
    {
        "name": "top_processes",
        "description": "Show top 10 processes by CPU and RAM usage",
        "tags": ["system", "process", "cpu", "ram", "performance", "memory"],
        "aliases": ["high cpu", "top processes", "cpu usage", "heavy processes",
                    "ram usage", "memory usage", "process list"],
        "os": ["windows", "linux", "darwin"],
        "task_prompt": (
            "Write a Python script using psutil to:\n"
            "  1. List TOP 10 processes by CPU% (name, pid, cpu%, ram MB, status)\n"
            "  2. List TOP 10 processes by RAM (name, pid, ram MB, cpu%, status)\n"
            "Wait 0.5s after calling cpu_percent(interval=None) to get accurate readings.\n"
            "Skip AccessDenied processes. Print as two neat ASCII tables."
        ),
    },

    # ── Windows: Browser cache cleanup ────────────────────
    {
        "name": "clean_browser_cache",
        "description": "Clear Chrome, Edge, and Firefox cache files",
        "tags": ["browser", "cache", "cleanup", "chrome", "edge", "firefox"],
        "aliases": ["browser cache", "clear browser cache", "chrome cache",
                    "edge cache", "clean cache", "clear cache"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script to clear browser caches on Windows using shutil.rmtree:\n"
            "  Chrome:  %LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Cache\n"
            "           %LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Code Cache\n"
            "  Edge:    %LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Cache\n"
            "           %LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Code Cache\n"
            "  Firefox: find profile folder in %APPDATA%\\Mozilla\\Firefox\\Profiles\\ "
            "           and clear cache2\\entries inside it\n"
            "For each: measure size before, delete, report MB freed. "
            "Skip paths that do not exist. Print total MB freed."
        ),
    },

    # ── Windows: Installed software ───────────────────────
    {
        "name": "list_installed_software",
        "description": "List all installed software from Windows registry",
        "tags": ["system", "software", "installed", "windows", "programs"],
        "aliases": ["installed software", "installed programs", "list software",
                    "what is installed", "programs list"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script that reads the Windows registry to list all installed software.\n"
            "Check all three uninstall keys:\n"
            "  HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\n"
            "  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\n"
            "  HKLM\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\n"
            "Use winreg. For each subkey read: DisplayName, DisplayVersion, Publisher, InstallDate.\n"
            "Skip entries without a DisplayName. Sort alphabetically. Print as a table.\n"
            "Show total count at the end."
        ),
    },

    # ── All OS: Open ports ────────────────────────────────
    {
        "name": "open_ports",
        "description": "Show all listening ports and which process owns them",
        "tags": ["network", "ports", "security", "connections", "firewall"],
        "aliases": ["open ports", "listening ports", "network ports",
                    "show ports", "check ports"],
        "os": ["windows", "linux", "darwin"],
        "task_prompt": (
            "Write a Python script using psutil.net_connections(kind='inet') to list "
            "all LISTEN-state ports.\n"
            "For each connection get the owning process name via psutil.Process(pid).\n"
            "Columns: Proto | Local Address | Port | PID | Process Name\n"
            "Sort by port number. Handle AccessDenied for system processes.\n"
            "Print as an ASCII table."
        ),
    },

    # ── Windows: WiFi passwords ───────────────────────────
    {
        "name": "wifi_passwords",
        "description": "Show all saved WiFi network names and passwords",
        "tags": ["network", "wifi", "password", "windows", "wireless"],
        "aliases": ["wifi passwords", "saved wifi", "wifi profiles",
                    "show wifi passwords", "wireless passwords"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script to list all saved WiFi passwords on Windows.\n"
            "Step 1: run subprocess: netsh wlan show profiles\n"
            "        parse all 'All User Profile' lines to get profile names.\n"
            "Step 2: for each profile run: "
            "        netsh wlan show profile name='<name>' key=clear\n"
            "        parse 'Key Content' line for the password.\n"
            "Print: Network Name | Password (or '<No Password>'). "
            "Handle encoding issues with errors='ignore'."
        ),
    },

    # ── All OS: Environment variables ─────────────────────
    {
        "name": "environment_variables",
        "description": "List all system environment variables",
        "tags": ["system", "environment", "env", "variables", "config"],
        "aliases": ["env variables", "environment variables", "show env",
                    "list env", "environment", "env vars"],
        "os": ["windows", "linux", "darwin"],
        "task_prompt": (
            "Write a Python script to display all environment variables from os.environ.\n"
            "Group them into sections:\n"
            "  [SYSTEM PATHS]  — PATH, PATHEXT, SystemRoot, WINDIR, etc.\n"
            "  [USER]          — USERNAME, USERPROFILE, HOME, APPDATA, LOCALAPPDATA\n"
            "  [TEMP]          — TEMP, TMP\n"
            "  [OTHER]         — everything else, sorted alphabetically\n"
            "For PATH: split by ; or : and print each entry on its own line, indented.\n"
            "Print total variable count at the end."
        ),
    },

    # ── Windows: FULL deep clean ──────────────────────────
    {
        "name": "deep_clean_system",
        "description": "Full Windows cleanup: temp, prefetch, recycle bin, caches, DNS",
        "tags": ["system", "cleanup", "all", "full", "windows", "deep", "cache"],
        "aliases": ["full cleanup", "clean all", "system cleanup", "deep clean",
                    "full clean", "deep system clean", "clean everything",
                    "clear all cache", "complete cleanup"],
        "os": ["windows"],
        "task_prompt": (
            "Write a Python script to perform a FULL Windows system cleanup. "
            "Print a section header before each step and MB freed after each.\n\n"
            "Step 1 — TEMP FILES\n"
            "  Delete all files/folders in os.environ['TEMP'] and C:\\Windows\\Temp.\n\n"
            "Step 2 — PREFETCH\n"
            "  Check ctypes.windll.shell32.IsUserAnAdmin(). "
            "  If admin: delete *.pf in C:\\Windows\\Prefetch. Else skip with warning.\n\n"
            "Step 3 — RECYCLE BIN\n"
            "  ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x0007)\n\n"
            "Step 4 — BROWSER CACHES\n"
            "  Chrome: %LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Cache\n"
            "  Edge:   %LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Cache\n"
            "  Use shutil.rmtree on each; skip if path not found.\n\n"
            "Step 5 — DNS CACHE\n"
            "  subprocess.run(['ipconfig', '/flushdns'])\n\n"
            "Step 6 — WINDOWS UPDATE CACHE\n"
            "  Delete contents of C:\\Windows\\SoftwareDistribution\\Download\\ "
            "  (requires admin; skip gracefully if not).\n\n"
            "Step 7 — THUMBNAIL CACHE\n"
            "  Delete files in "
            "%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\ matching thumbcache_*.db\n\n"
            "At the end print a grand total: 'TOTAL FREED: X.XX MB' and a per-step table.\n"
            "Always skip locked files with PermissionError — never crash."
        ),
    },
]


# ──────────────────────────────────────────────────────────────
# TASK LIBRARY MANAGER
# ──────────────────────────────────────────────────────────────

class TaskLibrary:
    """
    Manages task_library/<name>/task.json files.

    task.json schema
    ─────────────────
    name              str       unique slug
    description       str       one-liner
    tags              [str]     search keywords
    aliases           [str]     natural-language phrases that match this task
    os                [str]     'windows' | 'linux' | 'darwin'  (empty = all)
    task_prompt       str       the exact prompt fed to the coder LLM
    last_working_code str|null  Python code from the most recent successful run
    success_count     int
    fail_count        int
    last_run          str|null  ISO timestamp
    created_at        str       ISO timestamp
    """

    def __init__(self, root: Path = TASK_LIB_ROOT):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_predefined()

    # ── internal helpers ───────────────────────────────────
    def _task_dir(self, name: str) -> Path:
        return self.root / name

    def _json_path(self, name: str) -> Path:
        return self._task_dir(name) / "task.json"

    def _ensure_predefined(self):
        """Create task.json for every predefined task (skip if already exists)."""
        for defn in PREDEFINED_TASKS:
            p = self._json_path(defn["name"])
            if p.exists():
                continue
            self._task_dir(defn["name"]).mkdir(parents=True, exist_ok=True)
            entry = {
                **defn,
                "last_working_code": None,
                "success_count":     0,
                "fail_count":        0,
                "last_run":          None,
                "created_at":        datetime.now().isoformat(),
            }
            p.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── public API ─────────────────────────────────────────
    def get(self, name: str) -> Optional[dict]:
        p = self._json_path(name)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save(self, name: str, entry: dict):
        d = self._task_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "task.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def list_tasks(self) -> list[dict]:
        tasks = []
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            jp = d / "task.json"
            if jp.exists():
                try:
                    tasks.append(json.loads(jp.read_text(encoding="utf-8")))
                except Exception:
                    pass
        return tasks

    def find(self, command: str) -> Optional[dict]:
        """Return the best matching task for a natural-language command."""
        cmd_lower = command.lower()
        cmd_words = set(re.findall(r'\b\w{3,}\b', cmd_lower))
        best, best_score = None, 0

        for task in self.list_tasks():
            # Skip tasks for other operating systems
            os_filter = task.get("os", [])
            if os_filter and OS_NAME not in os_filter:
                continue

            score = 0
            # Alias exact substring match → highest priority
            for alias in task.get("aliases", []):
                if alias.lower() in cmd_lower:
                    score += 20
                    break

            name_words = set(re.findall(r'\b\w{3,}\b',
                             task["name"].replace("_", " ")))
            tag_words  = set(w.lower() for w in task.get("tags", []))
            desc_words = set(re.findall(r'\b\w{3,}\b',
                             task.get("description", "").lower()))

            score += len(cmd_words & name_words) * 5
            score += len(cmd_words & tag_words)  * 3
            score += len(cmd_words & desc_words) * 1

            if score > best_score:
                best_score, best = score, task

        return best if best_score >= 3 else None

    def update_after_run(self, name: str, code: str, success: bool):
        """Persist run result back into the task JSON."""
        entry = self.get(name) or {}
        entry["last_run"] = datetime.now().isoformat()
        if success:
            entry["last_working_code"] = code
            entry["success_count"] = entry.get("success_count", 0) + 1
        else:
            entry["fail_count"] = entry.get("fail_count", 0) + 1
        self.save(name, entry)

    def create_from_command(self, command: str, code: str,
                            summary: str, success: bool) -> str:
        """Create a brand-new task entry for a previously unseen command."""
        slug = re.sub(r'[^a-z0-9]+', '_',
                      command.strip().lower())[:45].strip('_')
        name = slug or f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Avoid collisions
        candidate = name
        counter   = 2
        while self._json_path(candidate).exists():
            candidate = f"{name}_{counter}"
            counter  += 1
        name = candidate

        entry = {
            "name":              name,
            "description":       command[:120],
            "tags":              [],
            "aliases":           [command.lower()],
            "os":                [OS_NAME],
            "task_prompt":       command,
            "last_working_code": code if success else None,
            "success_count":     1 if success else 0,
            "fail_count":        0 if success else 1,
            "summary":           summary,
            "last_run":          datetime.now().isoformat(),
            "created_at":        datetime.now().isoformat(),
        }
        self.save(name, entry)
        return name


# Global library singleton
_library = TaskLibrary()


# ──────────────────────────────────────────────────────────────
# SESSION
# ──────────────────────────────────────────────────────────────

def _task_id(task: str) -> str:
    return hashlib.md5(task.strip().lower().encode()).hexdigest()[:8]


def create_session(task: str) -> tuple[Path, str]:
    sid = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + _task_id(task)
    d   = SESSIONS_ROOT / sid
    d.mkdir(parents=True, exist_ok=True)
    return d, sid


def save_session(session_dir: Path, session_id: str, task: str,
                 events: list[AgentEvent], final_code: str,
                 final_report: str, success: bool, summary: str) -> Path:
    out = session_dir / "history.json"
    out.write_text(json.dumps({
        "session_id":   session_id,
        "task":         task,
        "started_at":   events[0].timestamp if events else datetime.now().isoformat(),
        "finished_at":  datetime.now().isoformat(),
        "success":      success,
        "summary":      summary,
        "final_code":   final_code,
        "final_report": final_report,
        "events":       [e.to_dict() for e in events],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ──────────────────────────────────────────────────────────────
# TASK RESULT CACHE  (agent_tasks/)
# ──────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9 ]', '', text).strip().lower()
    return (re.sub(r'\s+', '_', s)[:50]) or "unnamed_task"


def get_task_dir(task: str) -> Path:
    folder = TASKS_ROOT / f"{_slug(task)}__{_task_id(task)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_metadata() -> dict:
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        if META_FILE.exists():
            return json.loads(META_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_metadata(meta: dict):
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def save_task_result(task: str, attempt: int, code: str,
                     report: str, success: bool, summary: str):
    """
    Save final task result EXACTLY ONCE.
    On success: remove any previous step files, write a single clean file.
    On failure: write only if no file exists yet.
    """
    task_dir = get_task_dir(task)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")

    existing = sorted(task_dir.glob("step_*.py"))

    if success:
        # Remove previous attempts — keep only the winning code
        for old in existing:
            try:
                old.unlink()
            except Exception:
                pass
        step_file = task_dir / f"step_{attempt:02d}_{ts}.py"
        step_file.write_text(code, encoding="utf-8")
    elif not existing:
        # Nothing saved yet — save this failed attempt
        step_file = task_dir / f"step_{attempt:02d}_{ts}.py"
        step_file.write_text(code, encoding="utf-8")
    else:
        # Already have a file, don't write duplicates on failure
        step_file = existing[-1]

    result = {
        "task":      task,
        "timestamp": ts,
        "attempts":  attempt,
        "success":   success,
        "report":    report,
        "summary":   summary,
        "best_code": str(step_file),
    }
    (task_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    meta = load_metadata()
    meta[_task_id(task)] = {
        "task":      task,
        "task_dir":  str(task_dir),
        "timestamp": ts,
        "success":   success,
        "summary":   summary,
    }
    save_metadata(meta)


# ──────────────────────────────────────────────────────────────
# PACKAGE MANAGER
# ──────────────────────────────────────────────────────────────

IMPORT_TO_PIP: dict[str, str] = {
    "bs4":          "beautifulsoup4",
    "cv2":          "opencv-python",
    "sklearn":      "scikit-learn",
    "PIL":          "Pillow",
    "dotenv":       "python-dotenv",
    "yaml":         "PyYAML",
    "serial":       "pyserial",
    "dateutil":     "python-dateutil",
    "attr":         "attrs",
    "toml":         "toml",
    "nacl":         "PyNaCl",
    "cryptography": "cryptography",
    "boto3":        "boto3",
    "usb":          "pyusb",
    "psutil":       "psutil",
    "requests":     "requests",
}

STDLIB = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
    "os", "sys", "re", "json", "math", "time", "datetime", "pathlib",
    "shutil", "subprocess", "tempfile", "hashlib", "random", "string",
    "io", "csv", "collections", "itertools", "functools", "typing",
    "abc", "copy", "inspect", "logging", "argparse", "threading",
    "multiprocessing", "socket", "http", "urllib", "email", "html",
    "xml", "sqlite3", "struct", "ctypes", "unittest", "traceback",
    "warnings", "enum", "dataclasses", "contextlib", "textwrap",
    "pprint", "ast", "base64", "glob", "stat", "zipfile", "tarfile",
    "gzip", "pickle", "calendar", "locale", "platform", "signal",
    "builtins", "types", "weakref", "uuid", "queue", "heapq",
    "winreg",   # Windows registry — stdlib on Windows
}


def _pip_list() -> set[str]:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=freeze"],
            capture_output=True, text=True)
        return {
            line.split("==")[0].strip().lower()
            for line in r.stdout.splitlines() if "==" in line
        }
    except Exception:
        return set()


def _pip_install(name: str, force: bool = False) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", name,
           "--quiet", "--disable-pip-version-check"]
    if force:
        cmd.insert(4, "--force-reinstall")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


def _pip_uninstall(name: str) -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", name, "-y",
             "--quiet", "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def extract_imports(code: str) -> list[str]:
    names = []
    for line in code.splitlines():
        line = line.strip()
        m = (re.match(r'^import\s+([\w]+)', line) or
             re.match(r'^from\s+([\w]+)', line))
        if m:
            names.append(m.group(1))
    return list(set(names))


def auto_install_imports(code: str, cb: Callable):
    installed = _pip_list()
    for imp in extract_imports(code):
        if imp in STDLIB:
            continue
        pip_name = IMPORT_TO_PIP.get(imp, imp)
        if pip_name.lower() in installed:
            continue
        cb(make_event(EV.PKG_INSTALL, f"  📦 Installing '{pip_name}'...", pkg=pip_name))
        if _pip_install(pip_name):
            cb(make_event(EV.PKG_INSTALL, f"  ✅ Installed: {pip_name}", pkg=pip_name))
            SESSION_INSTALLED.add(pip_name)
        else:
            cb(make_event(EV.PKG_ERROR, f"  ❌ Could not install: {pip_name}", pkg=pip_name))


def auto_fix_runtime_import(report: str, cb: Callable) -> bool:
    m = (re.search(r"ModuleNotFoundError: No module named '([^']+)'", report) or
         re.search(r"ImportError:.*'([^']+)'", report))
    if not m:
        return False
    imp      = m.group(1).split(".")[0]
    pip_name = IMPORT_TO_PIP.get(imp, imp)
    cb(make_event(EV.PKG_REINSTALL, f"  🔄 Reinstalling '{pip_name}'...", pkg=pip_name))
    if _pip_install(pip_name, force=True):
        cb(make_event(EV.PKG_REINSTALL, f"  ✅ Reinstalled: {pip_name}", pkg=pip_name))
        SESSION_INSTALLED.add(pip_name)
        return True
    cb(make_event(EV.PKG_ERROR, f"  ❌ Reinstall failed: {pip_name}", pkg=pip_name))
    return False


def cleanup_session_packages(cb: Callable):
    if not SESSION_INSTALLED:
        return
    cb(make_event(EV.PKG_REMOVE,
                  f"  🗑️  Removing {len(SESSION_INSTALLED)} session package(s)..."))
    for pkg in list(SESSION_INSTALLED):
        ok = _pip_uninstall(pkg)
        cb(make_event(
            EV.PKG_REMOVE if ok else EV.PKG_ERROR,
            f"  {'Removed' if ok else 'Could not remove'}: {pkg}",
            pkg=pkg
        ))
    SESSION_INSTALLED.clear()


# ──────────────────────────────────────────────────────────────
# LLM WRAPPER
# ──────────────────────────────────────────────────────────────

def llm_call(prompt: str, system_p: str, cb: Callable,
             history: list = []) -> str:
    messages = [{"role": "system", "content": system_p}] + history
    messages.append({"role": "user", "content": prompt})
    try:
        stream = ollama.chat(
            model="qwen2.5-coder:7b",
            messages=messages,
            stream=True,
            keep_alive=-1,
        )
        full = ""
        cb(make_event(EV.LLM_STREAM_START, ""))
        for chunk in stream:
            c = chunk["message"]["content"]
            cb(make_event(EV.LLM_STREAM, c))
            full += c
        cb(make_event(EV.LLM_STREAM_END, full))
        cb(make_event(EV.LLM_DONE, ""))
        return full
    except Exception as e:
        cb(make_event(EV.LLM_ERROR, f"  [LLM ERROR] {e}", error=str(e)))
        return ""


def extract_code(response: str) -> str:
    if "```python" in response:
        s = response.index("```python") + 9
        e = response.index("```", s)
        return response[s:e].strip()
    if "```" in response:
        s = response.index("```") + 3
        e = response.index("```", s)
        return response[s:e].strip()
    return response.strip()


# ──────────────────────────────────────────────────────────────
# CODE RUNNER
# ──────────────────────────────────────────────────────────────

def run_code(code: str, cb: Callable) -> tuple[str, bool]:
    tmp_path = None
    auto_install_imports(code, cb)
    try:
        tmp_dir  = TASKS_ROOT / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        tmp_path = tmp_dir / f"run_{ts}.py"
        tmp_path.write_text(code, encoding="utf-8")

        cb(make_event(EV.RUN, "  ⚙️  Executing..."))

        def _run(p: Path):
            return subprocess.run(
                [sys.executable, str(p)],
                capture_output=True, text=True, timeout=120,
            )

        result  = _run(tmp_path)
        stdout  = result.stdout.strip()
        stderr  = result.stderr.strip()

        report  = ""
        if stdout: report += f"=== OUTPUT ===\n{stdout}\n"
        if stderr: report += f"=== ERRORS ===\n{stderr}\n"
        if not report: report = "(no output)"

        success = result.returncode == 0 and "Traceback" not in stderr

        if not success and ("ModuleNotFoundError" in report or
                            "ImportError" in report):
            if auto_fix_runtime_import(report, cb):
                cb(make_event(EV.PKG_REINSTALL, "  🔄 Retrying after package fix..."))
                result2 = _run(tmp_path)
                s2, e2  = result2.stdout.strip(), result2.stderr.strip()
                report  = ""
                if s2: report += f"=== OUTPUT ===\n{s2}\n"
                if e2: report += f"=== ERRORS ===\n{e2}\n"
                if not report: report = "(no output)"
                success = result2.returncode == 0 and "Traceback" not in e2

    except subprocess.TimeoutExpired:
        report, success = "ERROR: Script timed out (120 s).", False
    except Exception as e:
        report, success = f"ERROR launching script: {e}", False
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    return report, success


# ──────────────────────────────────────────────────────────────
# SYSTEM PROMPTS
# ──────────────────────────────────────────────────────────────

CODER_SYSTEM = f"""You are an expert Python developer. OS: {platform.system()} {platform.release()}.
RULES:
- Reply with ONLY a complete runnable Python script inside ```python ... ```
- Zero text outside the code block
- The script must print its results clearly to stdout
- Handle ALL exceptions internally — script must never crash unhandled
- You may use any third-party package (it will be auto-installed)
- For system tasks use: os, subprocess, pathlib, shutil, ctypes, winreg, psutil
- Keep stdout output concise — results only, no commentary
"""

FIX_SYSTEM = f"""You are an expert Python debugger. OS: {platform.system()}.
Fix the given bug completely.
Return ONLY the corrected Python script in ```python ... ```.
No explanation outside the code block.
"""

REFLECT_SYSTEM = """Write a concise plain-English summary (3-5 sentences):
- What the script did
- Whether it succeeded
- Key numbers / findings
- Any remaining issues
Be brief and factual. No code. No markdown.
"""

PLAN_SYSTEM = f"""You are a senior software architect. OS: {platform.system()}.
Given a task, output ONLY a numbered list of concrete sub-steps a Python script should follow.
No code. No explanation outside the numbered list.
"""


# ──────────────────────────────────────────────────────────────
# MAIN AGENT
# ──────────────────────────────────────────────────────────────

def agent(
    task:          str,
    keep_packages: bool = False,
    callback:      Optional[Callable[[AgentEvent], None]] = None,
) -> str:
    """
    Run a task through the agent pipeline.

    Parameters
    ----------
    task           : Natural-language task description
    keep_packages  : If False (default), uninstall packages installed this session
    callback       : Receives every AgentEvent; defaults to quiet terminal printer

    Returns
    -------
    str  — Plain-English summary of what was done
    """
    cb = callback or default_callback

    # ── Session setup ──────────────────────────────────────
    session_dir, session_id = create_session(task)
    all_events: list[AgentEvent] = []

    def emit(ev: AgentEvent):
        # Never store raw streaming tokens — too large, not useful in history
        if ev.event != EV.LLM_STREAM:
            all_events.append(ev)
        cb(ev)

    emit(make_event(EV.TASK_START, f"Task: {task}",
                    task=task, session_id=session_id))

    # ── Task Library lookup ────────────────────────────────
    lib_match  = _library.find(task)
    lib_name   = None
    lib_code   = None
    task_prompt = task          # may be overridden by library prompt

    if lib_match:
        lib_name   = lib_match["name"]
        task_prompt = lib_match.get("task_prompt") or task
        lib_code    = lib_match.get("last_working_code")
        emit(make_event(EV.LIB_HIT,
            f"  📚 Library match: '{lib_name}' — {lib_match.get('description','')}",
            lib_name=lib_name))
        if lib_code:
            emit(make_event(EV.LIB_HIT,
                f"  💾 Cached working code found — will use as baseline"))
    else:
        emit(make_event(EV.INFO, "  🔍 No library match — generating from scratch"))

    # ── Plan ───────────────────────────────────────────────
    emit(make_event(EV.STAGE, "  📋 [1/4] Planning..."))
    plan = llm_call(task_prompt, PLAN_SYSTEM, emit)

    # ── Generate code ──────────────────────────────────────
    emit(make_event(EV.STAGE, "  ✍️  [2/4] Generating code..."))
    code_prompt = f"Task:\n{task_prompt}\n\nPlan:\n{plan}"
    if lib_code:
        code_prompt += f"\n\nAdapt this known-working code:\n```python\n{lib_code}\n```"
    code = extract_code(llm_call(code_prompt, CODER_SYSTEM, emit))

    # ── Run ────────────────────────────────────────────────
    emit(make_event(EV.STAGE, "  🚀 [3/4] Running..."))
    attempt         = 1
    report, success = run_code(code, emit)

    if success:
        emit(make_event(EV.SUCCESS, f"Completed on attempt {attempt}.",
                        attempt=attempt))
    else:
        emit(make_event(EV.FAILURE, f"Failed — attempt {attempt}.",
                        attempt=attempt, report=report))

    # ── Retry loop ─────────────────────────────────────────
    fix_history: list[dict] = []

    while not success and attempt < MAX_RETRIES:
        attempt += 1
        emit(make_event(EV.RETRY, f"  🔁 Retry {attempt}/{MAX_RETRIES}...",
                        attempt=attempt, max_retries=MAX_RETRIES))

        fix_prompt = (
            f"Task:\n{task_prompt}\n\n"
            f"Previous code:\n```python\n{code}\n```\n\n"
            f"Error output:\n{report}\n\n"
            f"Fix it completely."
        )
        fix_history.append({"role": "user",      "content": fix_prompt})
        raw = llm_call(fix_prompt, FIX_SYSTEM, emit, history=fix_history[:-1])
        fix_history.append({"role": "assistant",  "content": raw})

        code            = extract_code(raw)
        report, success = run_code(code, emit)

        if success:
            emit(make_event(EV.SUCCESS, f"Completed on attempt {attempt}.",
                            attempt=attempt))
        else:
            emit(make_event(EV.FAILURE, f"Failed — attempt {attempt}.",
                            attempt=attempt, report=report))

    emit(make_event(
        EV.SUCCESS if success else EV.FAILURE,
        f"{'✅ Solved' if success else '❌ Could not solve'} "
        f"after {attempt} attempt(s).",
        attempts=attempt, success=success,
    ))

    # ── Summarise ──────────────────────────────────────────
    emit(make_event(EV.STAGE, "  📝 [4/4] Summarising..."))
    summary = llm_call(
        f"Task:\n{task}\n\nFinal code:\n```python\n{code}\n```\n\nOutput:\n{report}",
        REFLECT_SYSTEM, emit,
    )
    emit(make_event(EV.SUMMARY, f"[SUMMARY]\n{summary}", summary=summary))

    # ── Save task result (ONCE, no duplicates) ─────────────
    save_task_result(task, attempt, code, report, success, summary)
    emit(make_event(EV.CACHE_SAVE,
        f"  💾 Result saved → {get_task_dir(task)}"))

    # ── Update Task Library ────────────────────────────────
    if lib_name:
        _library.update_after_run(lib_name, code, success)
        emit(make_event(EV.LIB_SAVE,
            f"  📚 Library updated: '{lib_name}' "
            f"({'✅' if success else '❌'})"))
    else:
        new_name = _library.create_from_command(task, code, summary, success)
        emit(make_event(EV.LIB_SAVE,
            f"  📚 New task saved to library: '{new_name}'"))

    # ── Session history ────────────────────────────────────
    sf = save_session(session_dir, session_id, task, all_events,
                      code, report, success, summary)
    emit(make_event(EV.SESSION_SAVE, f"  📁 Session log → {sf}",
                    session_id=session_id))

    # ── Package cleanup ────────────────────────────────────
    if not keep_packages:
        cleanup_session_packages(emit)
    elif SESSION_INSTALLED:
        emit(make_event(EV.PKG_INSTALL,
            f"  📦 Keeping installed: {SESSION_INSTALLED}"))

    return summary


# ──────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ──────────────────────────────────────────────────────────────

def _print_library():
    tasks = sorted(_library.list_tasks(), key=lambda t: t["name"])
    print(f"\n{'─'*64}")
    print(f"  📚  Task Library  ({len(tasks)} tasks)")
    print(f"{'─'*64}")
    for t in tasks:
        os_tag = ", ".join(t.get("os", ["all"])) or "all"
        cached = "💾" if t.get("last_working_code") else "  "
        runs   = t.get("success_count", 0)
        print(f"  {cached} {t['name']:<35} [{os_tag}]  ✅{runs}")
        print(f"       {t.get('description','')[:60]}")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        prog="agent",
        description="Agentic Bot v2 — runs tasks, learns, reuses code",
    )
    parser.add_argument("task", nargs="?",
                        help="Task to run (quote if multi-word)")
    parser.add_argument("--list",  "-l", action="store_true",
                        help="Show all tasks in the library")
    parser.add_argument("--keep-packages", "-k", action="store_true",
                        help="Don't uninstall packages after run")
    args = parser.parse_args()

    if args.list:
        _print_library()
        sys.exit(0)

    if args.task:
        agent(args.task, keep_packages=args.keep_packages)
    else:
        # ── Interactive mode ───────────────────────────────
        print(f"\n{ANSI['cyan']}🤖  Agentic Bot v2  —  Interactive{RESET}")
        print("  Commands: <task description>  |  list  |  quit\n")
        while True:
            try:
                cmd = input(f"{ANSI['blue']}▶ {RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Bye.")
                break
            if not cmd:
                continue
            if cmd.lower() in ("quit", "exit", "q"):
                break
            if cmd.lower() in ("list", "ls", "tasks"):
                _print_library()
                continue
            agent(cmd, keep_packages=args.keep_packages)