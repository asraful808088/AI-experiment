import os
import re
import sys
import json
import time
import shutil
import signal
import socket
import subprocess
import threading
import urllib.request
import urllib.error
import webbrowser
from dataclasses import dataclass, field
from typing import Callable, Optional


user_csh = {

        "<unique_numver_number_task_id>":{}

}


PrintCallback = Callable[[str, str], None]

def _noop_cb(text: str, color: str = "white") -> None:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
try:
    from experiment.test3.project_fixer import ProjectFixer, detect_errors, FE, fix_project
    HAS_FIXER = True
except ImportError:
    HAS_FIXER = False

try:
    import requests as _requests
    def _http_get(url, timeout=10):
        r = _requests.get(url, timeout=timeout)
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

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


MODEL          = "qwen2.5-coder:7b"
KEEP_ALIVE     = -1
IS_WIN         = sys.platform == "win32"
CACHE_PATH     = os.path.join(_HERE, "nextjs_creator_cache_v12.json")
DEV_PORT       = 3000
LAUNCH_TIMEOUT = 120
HEALTH_POLL    = 2
MAX_FIX_ROUNDS = 3


@dataclass
class Intent:
    project_name: str
    pm:           str
    flags:        list
    libraries:    list
    global_tools: list
    install_dir:  str

    @property
    def project_path(self) -> str:
        return os.path.join(self.install_dir, self.project_name)

    def flag_str(self) -> str:
        return " ".join(self.flags)


def _machine_key() -> str:
    node_ver = "unknown"
    try:
        r = subprocess.run("node --version", shell=True, capture_output=True, text=True)
        node_ver = r.stdout.strip().lstrip("v").split(".")[0]
    except Exception:
        pass
    return f"{sys.platform}__node{node_ver}"

MK = _machine_key()


def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"_v": 12, "pm_create_cmd": {}, "bad_strategies": {},
            "yarn_version": {}, "project_journal": []}

def _save_cache(c: dict):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def cache_get_create(c, pm):       return c.get("pm_create_cmd", {}).get(MK, {}).get(pm)
def cache_set_create(c, pm, cmd):
    c.setdefault("pm_create_cmd", {}).setdefault(MK, {})[pm] = cmd; _save_cache(c)
def cache_clear_create(c, pm, cb):
    mk = c.get("pm_create_cmd", {}).get(MK, {})
    if pm in mk: del mk[pm]; _save_cache(c)
    cb(f"🗑️  Cleared broken cached create-cmd for {pm}", "yellow")
def cache_is_bad(c, key):          return key in c.get("bad_strategies", {}).get(MK, [])
def cache_mark_bad(c, key):
    bads = c.setdefault("bad_strategies", {}).setdefault(MK, [])
    if key not in bads: bads.append(key); _save_cache(c)
def cache_reset_strategies(c, pm, cb):
    mk = c.get("bad_strategies", {}).get(MK, [])
    c["bad_strategies"][MK] = [k for k in mk if pm not in k]; _save_cache(c)
    cb(f"🗑️  Reset bad strategies for {pm}", "yellow")
def cache_full_reset(c, cb):
    for key in ["pm_create_cmd", "bad_strategies"]:
        if MK in c.get(key, {}): del c[key][MK]
    _save_cache(c)
    cb("♻️  Full cache reset for this machine", "yellow")
def cache_add_journal(c, entry):
    c.setdefault("project_journal", []).append(entry); _save_cache(c)



def _run_silent(cmd: str, cwd=None) -> tuple:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()

def _which(name): return shutil.which(name)

def _ver(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        return (r.stdout + r.stderr).strip().split("\n")[0]
    except Exception:
        return "?"

def _add_to_path(new_dir: str):
    path_var = os.environ.get("PATH", "")
    if new_dir not in path_var.split(os.pathsep):
        os.environ["PATH"] = new_dir + os.pathsep + path_var

def fix_path(new_dir: str):
    if not new_dir or not os.path.isdir(new_dir): return
    _add_to_path(new_dir)
    if IS_WIN:
        try:
            import winreg, ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0,
                                  winreg.KEY_READ | winreg.KEY_WRITE)
            try: current, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError: current = ""
            if new_dir.lower() not in current.lower():
                new_val = current.rstrip(";") + ";" + new_dir if current else new_dir
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_val)
                ctypes.windll.user32.SendMessageTimeoutW(
                    0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
        except Exception: pass
    else:
        for rc in ["~/.bashrc", "~/.zshrc", "~/.profile"]:
            rc_path = os.path.expanduser(rc)
            if not os.path.exists(rc_path): continue
            try:
                content = open(rc_path).read()
                if new_dir not in content:
                    with open(rc_path, "a") as f:
                        f.write(f'\nexport PATH="{new_dir}:$PATH"\n')
            except Exception: pass

def fix_pnpm_path():
    ok, out, _ = _run_silent("pnpm bin -g")
    if ok and out and out not in os.environ.get("PATH", ""): fix_path(out.strip())

def ensure_node(cb: PrintCallback) -> bool:
    if _which("node"): return True
    cb("⚠  Node.js not found — attempting install...", "yellow")
    if IS_WIN:
        ok, _, _ = _run_silent(
            "winget install OpenJS.NodeJS.LTS --silent "
            "--accept-source-agreements --accept-package-agreements"
        )
        if ok: return True
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
    if _which(pm): return True
    cmds = {"pnpm": "npm install --global pnpm",
            "yarn": "npm install --global yarn",
            "bun":  "npm install --global bun"}
    cmd = cmds.get(pm)
    if not cmd: return False
    ok, _, _ = _run_silent(cmd)
    if ok and pm == "pnpm": fix_pnpm_path()
    return ok

def ensure_pip_packages(packages: list, cb: PrintCallback):
    if not packages: return
    for pkg in packages:
        ok, _, _ = _run_silent(f"python -c \"import {pkg.replace('-','_')}\"")
        if ok: continue
        ok2, _, _ = _run_silent(f"pip install {pkg} --break-system-packages")
        if not ok2: _run_silent(f"pip install {pkg}")

def bootstrap_environment(pm: str, cb: PrintCallback):
    if not ensure_node(cb):
        cb("❌ Cannot continue without Node.js.", "red")
        sys.exit(1)
    if _which("pnpm"): fix_pnpm_path()
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
    cb("🖥  Environment Health", "cyan")
    for name, cmd in checks:
        found = bool(_which(name))
        mark  = "✓" if found else "✗"
        color = "green" if found else "red"
        ver   = _ver(cmd) if found else "not found"
        cb(f"  {mark} {name:<10s} {ver}", color)



_PNPM_SOFT = [r"ERR_PNPM_IGNORED_BUILD_SCRIPTS", r"ERR_PNPM_NO_GLOBAL_BIN_DIR"]

def _is_pnpm_soft(text: str) -> bool:
    return any(re.search(p, text) for p in _PNPM_SOFT)

def _verify_installed(project_path: str, lib: str) -> bool:
    return os.path.exists(
        os.path.join(project_path, "node_modules", *lib.split("/"), "package.json")
    )



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
    if _is_pnpm_soft(stderr + stdout):
        if not lib or _verify_installed(project_path, lib):
            return EK.SOFT_SUCCESS
    c = (stderr + stdout).lower()
    if any(k in c for k in ['command "dlx" not found', "unknown command",
                              "unknown subcommand", "is not a yarn command"]):
        return EK.UNKNOWN_SUBCOMMAND
    if any(k in c for k in ["enotfound","etimedout","fetch failed","econnrefused",
                              "network error","socket hang up"]):
        return EK.NETWORK
    if any(k in c for k in ["eacces","permission denied","access denied"]):
        return EK.PERMISSION
    if any(k in c for k in ["peer dep","incompatible","conflict"]):
        return EK.VERSION_CONFLICT
    if any(k in c for k in ["404","not found","no matching version","e404"]):
        return EK.NOT_FOUND
    return EK.GENERIC


def detect_yarn_version(cache: dict) -> str:
    cached = cache.get("yarn_version", {}).get(MK)
    if cached: return cached
    try:
        r     = subprocess.run("yarn --version", shell=True, capture_output=True, text=True)
        major = int(r.stdout.strip().split(".")[0]) if r.stdout.strip()[0].isdigit() else 1
        ver   = "v4" if major >= 2 else "v1"
        cache.setdefault("yarn_version", {})[MK] = ver; _save_cache(cache)
        return ver
    except Exception:
        return "v1"



PM_ALIASES   = {"npm":"npm","pnpm":"pnpm","yarn":"yarn","bun":"bun"}
DEFAULT_PM   = "npm"
_PM_CREATE   = {"npm":"npx create-next-app@latest","pnpm":"pnpm dlx create-next-app@latest",
                "yarn":"yarn create next-app","bun":"bunx create-next-app@latest"}
_YARN4_CREATE = "yarn dlx create-next-app@latest"
PM_INSTALL   = {"npm":"npm install","pnpm":"pnpm add","yarn":"yarn add","bun":"bun add"}
PM_INSTALL_G = {"npm":"npm install --global","pnpm":"pnpm add -g",
                "yarn":"yarn global add","bun":"bun add --global"}
PM_RUN_DEV   = {"npm":"npm run dev","pnpm":"pnpm dev","yarn":"yarn dev","bun":"bun dev"}

def detect_pm(req: str) -> str:
    lowered = req.lower()
    for alias, name in PM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return name
    return DEFAULT_PM

def get_create_base(pm: str, cache: dict) -> str:
    cached = cache_get_create(cache, pm)
    if cached: return cached
    if pm == "yarn":
        ver = detect_yarn_version(cache)
        return _YARN4_CREATE if ver == "v4" else _PM_CREATE["yarn"]
    return _PM_CREATE.get(pm, _PM_CREATE["npm"])

def build_create_cmd(base: str, intent: Intent) -> str:
    return f"{base} {intent.project_name} {intent.flag_str()}".strip()


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
            return p
    return _ask_path()

def _ask_path() -> str:
    while True:
        raw      = input("\n📂 Where to create the project?\n   Full path: ").strip().strip('"\'')
        abs_path = os.path.abspath(os.path.expanduser(raw))
        if os.path.isfile(abs_path):
            print("  That is a file, not a directory."); continue
        try:
            os.makedirs(abs_path, exist_ok=True); return abs_path
        except Exception as e:
            print(f"  Cannot create: {e}")


def _strategies(intent: Intent, cache: dict) -> list:
    result = []
    pm     = intent.pm
    def add(name, base):
        if not cache_is_bad(cache, f"{MK}__{name}"):
            result.append((name, build_create_cmd(base, intent)))

    cached = cache_get_create(cache, pm)
    if cached: add(f"cached_{pm}", cached)

    if pm == "yarn":
        ver     = detect_yarn_version(cache)
        default = _YARN4_CREATE if ver == "v4" else _PM_CREATE["yarn"]
        alt     = _PM_CREATE["yarn"] if ver == "v4" else _YARN4_CREATE
        if default != cached: add(f"default_{pm}", default)
        add("yarn_alt", alt)
    else:
        db = _PM_CREATE.get(pm, _PM_CREATE["npm"])
        if db != cached: add(f"default_{pm}", db)

    add("npx_fallback", "npx create-next-app@latest")
    add("npx_nolast",   "npx create-next-app")
    return result



def _run(cmd: str, cwd: str) -> tuple:
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, "", str(e)


class Runner:
    def __init__(self, intent: Intent, cache: dict, cb: PrintCallback):
        self.intent  = intent
        self.cache   = cache
        self.log     = []
        self._cb     = cb
        self._fixer: Optional[ProjectFixer] = None

   

    def _get_fixer(self) -> Optional[ProjectFixer]:
        if not HAS_FIXER: return None
        if self._fixer is None and os.path.isdir(self.intent.project_path):
            self._fixer = ProjectFixer(self.intent.project_path, cb=self._cb)
        return self._fixer

    def _run_fixer(self, error_output: str = "") -> bool:
        fixer = self._get_fixer()
        if fixer is None: return False
        self._cb("", "white")
        self._cb("🔧 Running auto-fixer...", "yellow")
        result = fixer.fix_all(error_output=error_output)
        return result.get("success", False)



    def create_project(self) -> bool:
        for fix_round in range(MAX_FIX_ROUNDS + 1):
            strats = _strategies(self.intent, self.cache)
            if not strats:
                self._cb("No strategies left.", "red"); break

            for name, cmd in strats:
                self._cb(f"  ▶ [{fix_round}] Strategy '{name}': {cmd}", "cyan")
                ok, out, err = _run(cmd, self.intent.install_dir)
                ek           = classify_error(err, out, self.intent.project_path)

                if ok or ek == EK.SOFT_SUCCESS:
                    if ek == EK.SOFT_SUCCESS:
                        self._cb("  ⚠  Non-zero exit but project created", "yellow")
                    self._cb(f"  ✓ Created with '{name}'", "green")
                    if "fallback" not in name and "nolast" not in name:
                        idx = cmd.find(self.intent.project_name)
                        if idx > 0:
                            cache_set_create(self.cache, self.intent.pm, cmd[:idx].strip())
                    self.log.append({"strategy": name, "cmd": cmd, "ok": True})
                    return True

                self._cb(f"  ✗ Failed ({ek})", "red")
                self._cb(f"  {(err+out).strip()[:250]}", "red")

                if name.startswith("cached_") and ek in (EK.UNKNOWN_SUBCOMMAND, EK.GENERIC):
                    cache_clear_create(self.cache, self.intent.pm, self._cb)

                cache_mark_bad(self.cache, f"{MK}__{name}")
                self.log.append({"strategy": name, "cmd": cmd, "ok": False, "ek": ek})

                if ek == EK.NETWORK:
                    self._cb("  ⏳ Network error — retrying in 5s...", "yellow")
                    time.sleep(5)
                    ok2, out2, err2 = _run(cmd, self.intent.install_dir)
                    if ok2: return True

           
            if fix_round == 0:
                self._cb("⚡ Round 0 failed — resetting strategies", "yellow")
                cache_reset_strategies(self.cache, self.intent.pm, self._cb)
            elif fix_round == 1:
                self._cb("⚡ Round 1 failed — clearing cached create-cmd", "yellow")
                cache_clear_create(self.cache, self.intent.pm, self._cb)
            elif fix_round >= 2:
                self._cb(f"⚡ Round {fix_round} failed — full cache reset", "yellow")
                cache_full_reset(self.cache, self._cb)

        self._cb("❌ All strategies exhausted.", "red")
        self._cb(
            f"  Manual: npx create-next-app@latest "
            f"{self.intent.project_name} {self.intent.flag_str()}",
            "yellow",
        )
        return False

    

    def run_cmd(self, cmd: str, *, label="command", cwd=None, lib="") -> tuple:
        wd = cwd or self.intent.project_path

        for attempt in range(1, MAX_FIX_ROUNDS + 2):
            self._cb(f"  ▶ {label} (attempt {attempt}): {cmd}", "cyan")
            ok, out, err = _run(cmd, wd)
            ek           = classify_error(err, out, wd, lib)

            if ok or ek == EK.SOFT_SUCCESS:
                if ek == EK.SOFT_SUCCESS:
                    self._cb("  ⚠  Non-fatal warning (checking node_modules...)", "yellow")
                    if lib and not _verify_installed(wd, lib):
                        self._cb(f"  ✗ {lib} missing from node_modules", "red")
                        # fall through to fixer
                    else:
                        self._cb(f"  ✓ {label} done", "green")
                        return True, out
                else:
                    self._cb(f"  ✓ {label} done", "green")
                    return True, out

            if attempt > MAX_FIX_ROUNDS:
                break

            self._cb(f"  ✗ {label} failed ({ek}) — running fixer...", "red")
            fixed = self._run_fixer(error_output=err + out)
            if not fixed:
                self._cb("  Fixer had nothing to apply", "yellow")
                if ek == EK.NETWORK:
                    time.sleep(5 * attempt); continue
                break

        if lib and _verify_installed(wd, lib):
            self._cb(f"  ⚠  {lib} found in node_modules despite errors", "yellow")
            return True, ""

        self._cb(f"  ✗ {label} permanently failed", "red")
        self._cb(f"  {(err+out).strip()[:250]}", "red")
        return False, err + out



def get_latest_nextjs() -> str:
    try:
        status, text = _http_get("https://registry.npmjs.org/next/latest", timeout=8)
        if status == 200: return json.loads(text).get("version", "unknown")
    except Exception: pass
    return "unknown"

def get_installed_nextjs(project_path: str) -> str:
    try:
        p = os.path.join(project_path, "node_modules", "next", "package.json")
        if os.path.exists(p):
            with open(p) as f: return json.load(f).get("version", "unknown")
    except Exception: pass
    return "unknown"

def check_and_upgrade(intent: Intent, cache: dict, cb: PrintCallback):
    cb("", "white")
    cb("🔄 Checking Next.js version...", "cyan")
    installed = get_installed_nextjs(intent.project_path)
    latest    = get_latest_nextjs()
    cb(f"  Installed: {installed}  Latest: {latest}", "white")
    if installed not in ("unknown", latest):
        if input(f"\n  Upgrade to {latest}? (y/n): ").strip().lower() == "y":
            runner = Runner(intent, cache, cb)
            runner.run_cmd(
                {"npm":"npm install next@latest react@latest react-dom@latest",
                 "pnpm":"pnpm add next@latest react@latest react-dom@latest",
                 "yarn":"yarn add next@latest react@latest react-dom@latest",
                 "bun": "bun add next@latest react@latest react-dom@latest"}[intent.pm],
                label="upgrade next.js",
            )



def _npm_exists(name: str) -> bool:
    try:
        status, _ = _http_get(f"https://registry.npmjs.org/{name}/latest", timeout=6)
        return status == 200
    except Exception:
        return False

def validate_libraries(raw_libs: list, cb: PrintCallback) -> tuple:
    if not raw_libs: return [], []
    cb(f"", "white")
    cb(f"🔎 Validating {len(raw_libs)} libraries...", "cyan")
    ok_libs, failed = [], []
    for lib in raw_libs:
        if _npm_exists(lib):
            cb(f"  ✓ {lib}", "green"); ok_libs.append(lib)
        else:
            cb(f"  ⚠  {lib} not found on npm", "yellow")
            failed.append({"original": lib, "reason": "not on npm registry"})
    return ok_libs, failed

def install_libraries(intent: Intent, libs: list, cache: dict, cb: PrintCallback):
    if not libs: return
    base   = PM_INSTALL[intent.pm]
    runner = Runner(intent, cache, cb)
    cb("", "white")
    cb(f"📦 Installing {len(libs)} librar{'y' if len(libs)==1 else 'ies'}...", "cyan")
    for lib in libs:
        ok, _ = runner.run_cmd(f"{base} {lib}", label=f"install {lib}", lib=lib)
        if not ok:
            cb(f"  ✗ {lib} could not be installed", "red")

def install_global_tools(intent: Intent, tools: list, cache: dict, cb: PrintCallback):
    if not tools: return
    runner = Runner(intent, cache, cb)
    for tool in tools:
        runner.run_cmd(f"{PM_INSTALL_G[intent.pm]} {tool}",
                       label=f"global {tool}", cwd=os.getcwd())
        fix_pnpm_path()



def _find_free_port(preferred=3000) -> int:
    for port in range(preferred, preferred + 20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port)); return port
        except OSError: continue
    return preferred

def _wait_for_server(url: str, timeout: int, cb: PrintCallback) -> bool:
    deadline = time.time() + timeout
    attempt  = 0
    while time.time() < deadline:
        try:
            status, _ = _http_get(url, timeout=3)
            if status == 200:
                cb(f"  ✓ Server responded 200 at {url}", "green"); return True
        except Exception: pass
        attempt += 1
        time.sleep(HEALTH_POLL)
    return False

def launch_and_verify(intent: Intent, cb: PrintCallback) -> bool:
    port    = _find_free_port(DEV_PORT)
    dev_cmd = PM_RUN_DEV[intent.pm]
    url     = f"http://localhost:{port}"
    cb("", "white")
    cb(f"🚀 Launching dev server on port {port}...", "cyan")
    env         = os.environ.copy()
    env["PORT"] = str(port)
    try:
        proc = subprocess.Popen(
            dev_cmd, shell=True, cwd=intent.project_path, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except Exception as e:
        cb(f"  ✗ Could not start dev server: {e}", "red"); return False

    server_output = []
    def _capture(stream):
        for line in stream: server_output.append(line.rstrip())
    threading.Thread(target=_capture, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=_capture, args=(proc.stderr,), daemon=True).start()

    healthy = _wait_for_server(url, LAUNCH_TIMEOUT, cb)
    try:
        if IS_WIN: subprocess.run(f"taskkill /PID {proc.pid} /T /F",
                                   shell=True, capture_output=True)
        else: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try: proc.terminate()
        except Exception: pass
    proc.wait(timeout=10)

    if healthy:
        cb(f"✅ Landing page verified at {url}", "green")
    else:
        cb(f"❌ Dev server did not respond in {LAUNCH_TIMEOUT}s", "red")
        if HAS_FIXER and os.path.isdir(intent.project_path):
            err_text = "\n".join(server_output[-40:])
            cb("🔧 Running fixer on startup failure...", "yellow")
            fix_project(intent.project_path, error_output=err_text, cb=cb)
        for line in server_output[-20:]:
            cb(f"  {line}", "red")
    return healthy



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
        name = re.sub(r"[^a-z0-9-]", "-", req.lower().split()[0])[:30] or "my-next-app"
        return {"project_name": name, "flags": ["--yes"],
                "libraries": [], "global_tools": [], "python_packages": []}
    r   = _ollama.chat(model=MODEL,
                        messages=[{"role":"system","content":_PARSE_SYSTEM},
                                   {"role":"user","content":req}],
                        keep_alive=KEEP_ALIVE)
    raw = r["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$",          "", raw, flags=re.MULTILINE)
    return json.loads(raw.strip())

def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "my-next-app"




def start(user_request: str, cb: PrintCallback = _noop_cb) -> bool:
    """
    Full pipeline.  Every line of output goes through  cb(text, color).

    Usage
    -----
    def my_callback(text: str, color: str) -> None:
        # color: "green" | "yellow" | "red" | "cyan" | "dim" | "white"
        print(f"[{color.upper():6s}] {text}")

    from nextjs_creator import start
    start("create my-blog with pnpm typescript tailwind in /projects", cb=my_callback)
    """
    cache = _load_cache()

    cb("", "white")
    cb("⚡ Next.js Agentic Creator v12.1", "cyan")
    cb("   Auto-Fixer · Smart Cache Recovery · Retry Loops", "cyan")

    pm = detect_pm(user_request)

    bootstrap_environment(pm, cb)
    print_env_health(cb)

    install_dir = resolve_install_dir(user_request)

    cb("", "white")
    cb("🤖 Parsing request...", "dim")
    try:
        config = parse_user_request(user_request)
    except Exception as e:
        cb(f"⚠  AI parse failed ({e}) — using defaults", "yellow")
        config = {"project_name": "my-next-app", "flags": ["--yes"],
                  "libraries": [], "global_tools": [], "python_packages": []}

    project_name    = _sanitize_name(config.get("project_name", "my-next-app"))
    flags           = config.get("flags", [])
    raw_libs        = config.get("libraries", [])
    raw_globals     = config.get("global_tools", [])
    python_packages = config.get("python_packages", [])
    if "--yes" not in flags:
        flags.append("--yes")

    good_libs, bad_libs = validate_libraries(raw_libs, cb)

    intent = Intent(
        project_name=project_name, pm=pm, flags=flags,
        libraries=good_libs, global_tools=raw_globals,
        install_dir=install_dir,
    )

    cb("", "white")
    cb("📋 Plan", "green")
    cb(f"  PM          : {intent.pm}",          "cyan")
    cb(f"  Project     : {intent.project_name}", "cyan")
    cb(f"  Flags       : {intent.flag_str()}",   "cyan")
    cb(f"  Install dir : {intent.install_dir}",  "cyan")
    cb(f"  Libraries   : {', '.join(good_libs) or 'none'}", "cyan")
    cb(f"  Fixer       : {'enabled' if HAS_FIXER else 'disabled (project_fixer.py missing)'}", "cyan")
    if bad_libs:
        cb(f"  Skipped     : {', '.join(b['original'] for b in bad_libs)}", "yellow")


    runner = Runner(intent, cache, cb)
    ok     = runner.create_project()
    if not ok:
        cache_add_journal(cache, {"machine":MK,"pm":pm,"project":project_name,
                                   "worked":False,"ts":time.strftime("%Y-%m-%d %H:%M:%S")})
        return False


    check_and_upgrade(intent, cache, cb)


    install_libraries(intent, good_libs, cache, cb)


    install_global_tools(intent, raw_globals, cache, cb)


    ensure_pip_packages(python_packages, cb)

    
    cb("", "white")
    cb("🔬 Verifying project health...", "cyan")
    healthy = launch_and_verify(intent, cb)

    cache_add_journal(cache, {
        "machine":MK,"pm":pm,"project":project_name,"flags":flags,
        "worked":True,"landing_healthy":healthy,
        "ts":time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    cb("", "white")
    cb(f"🎉 '{intent.project_name}' is ready!", "green")
    cb(f"  cd {intent.project_path}",           "white")
    cb(f"  {PM_RUN_DEV[pm]}",                   "white")
    if bad_libs:
        cb(f"Unresolved libraries: {', '.join(b['original'] for b in bad_libs)}", "yellow")

    return healthy


try:
    from rich.console import Console as _RichConsole
    _rc = _RichConsole()
    _COLOR_MAP = {
        "green": "green", "yellow": "yellow", "red": "red",
        "cyan": "cyan",   "dim": "dim",        "white": "white",
    }
    def _terminal_cb(text: str, color: str = "white") -> None:
        tag = _COLOR_MAP.get(color, "white")
        _rc.print(f"[{tag}]{text}[/{tag}]")
except ImportError:
    def _terminal_cb(text: str, color: str = "white") -> None:
        print(text)


if __name__ == "__main__":
    user_request = input("\n💡 Describe your project: ").strip()
    if not user_request:
        print("No input."); sys.exit(1)
    start(user_request, cb=_terminal_cb)