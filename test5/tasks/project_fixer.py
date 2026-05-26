"""
Project Fixer v3.1
━━━━━━━━━━━━━━━━━━
All output is routed through a single PrintCallback.
No direct print() or console.print() anywhere — every message
goes through  cb(text, color)  so callers control display.

color values: "green" | "yellow" | "red" | "cyan" | "dim" | "white"
"""

import os
import re
import sys
import json
import time
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any

IS_WIN = sys.platform == "win32"

# ── Callback type ──────────────────────────────────────────────────────────────
#   cb(text: str, color: str = "white")
PrintCallback = Callable[[str, str], None]

def _noop_cb(text: str, color: str = "white") -> None:
    """Default no-op callback (silent)."""
    pass


# ══════════════════════════════════════════════════════════════════════════════
# ERROR CODES
# ══════════════════════════════════════════════════════════════════════════════

class FE:
    PNPM_IGNORED_BUILD   = "pnpm_ignored_build_scripts"
    PNPM_LOCKFILE        = "pnpm_lockfile_mismatch"
    PNPM_PEER_DEPS       = "pnpm_peer_deps"
    PNPM_FROZEN          = "pnpm_frozen_lockfile"
    PNPM_GLOBAL_BIN      = "pnpm_no_global_bin"
    NPM_PEER_DEPS        = "npm_peer_deps"
    NPM_CACHE            = "npm_cache_corrupt"
    YARN_WORKSPACE       = "yarn_workspace"
    NODE_MODULES_CORRUPT = "node_modules_corrupt"
    SHARP_GYNODE         = "sharp_or_node_gyp"
    PORT_IN_USE          = "port_in_use"
    NEXT_CACHE           = "next_cache_corrupt"
    NEXT_BUILD_ERROR     = "next_build_error"
    MISSING_DEP          = "missing_dependency"
    TS_ERROR             = "typescript_error"
    ESLINT_ERROR         = "eslint_error"
    ENV_MISSING          = "env_file_missing"
    PERMISSION           = "permission_denied"
    NETWORK              = "network_error"
    UNKNOWN              = "unknown"


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _run(
    cmd: "List[str] | str",
    cwd: Optional[Path] = None,
    shell: bool = False,
    timeout: int = 120,
) -> Tuple[int, str, str]:
    try:
        if isinstance(cmd, str) and not shell:
            cmd = cmd.split()
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            shell=shell or IS_WIN, timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("localhost", port))
            return True
        except OSError:
            return False


def _find_free_port(start: int = 3000) -> int:
    for p in range(start, start + 20):
        if _is_port_free(p):
            return p
    return start


def _pkg_installed(project_path: Path, pkg: str) -> bool:
    check = project_path / "node_modules" / pkg.replace("/", os.sep) / "package.json"
    return check.exists()


def _read_package_json(project_path: Path) -> dict:
    pj = project_path / "package.json"
    if pj.exists():
        try:
            return json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# ERROR DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

def detect_errors(stdout: str, stderr: str) -> List[str]:
    combined = (stdout + "\n" + stderr).lower()
    errors: List[str] = []

    patterns = [
        (FE.PNPM_IGNORED_BUILD,   r"err_pnpm_ignored_build_scripts"),
        (FE.PNPM_LOCKFILE,        r"err_pnpm_lockfile|lockfile is not up-to-date|outdated lockfile"),
        (FE.PNPM_FROZEN,          r"err_pnpm_frozen_lockfile|cannot install with frozen"),
        (FE.PNPM_PEER_DEPS,       r"err_pnpm_peer|missing peer|peer dep"),
        (FE.PNPM_GLOBAL_BIN,      r"err_pnpm_no_global_bin_dir"),
        (FE.NPM_PEER_DEPS,        r"npm warn peer|peer dep conflict|eresolve"),
        (FE.NPM_CACHE,            r"npm err! code eintegrity|cache verify failed|integrity check"),
        (FE.NODE_MODULES_CORRUPT, r"cannot find module|module not found|failed to resolve"),
        (FE.SHARP_GYNODE,         r"sharp|node-gyp|gyp err|node_pre_gyp|prebuild-install"),
        (FE.NEXT_CACHE,           r"\.next.*corrupt|failed to compile|invalid cache"),
        (FE.NEXT_BUILD_ERROR,     r"build failed|error during.*build|failed to build"),
        (FE.MISSING_DEP,          r"cannot find module|module not found|unresolved import"),
        (FE.TS_ERROR,             r"ts\d{4}|type error|typescript.*error|\.ts.*error"),
        (FE.ESLINT_ERROR,         r"eslint.*error|parsing error.*eslint"),
        (FE.PERMISSION,           r"eacces|permission denied|access denied"),
        (FE.NETWORK,              r"enotfound|etimedout|fetch failed|econnrefused|network error|socket hang up"),
        (FE.ENV_MISSING,          r"\.env.*not found|missing env|environment variable"),
    ]

    for code, pat in patterns:
        if re.search(pat, combined):
            errors.append(code)

    return errors or [FE.UNKNOWN]


# ══════════════════════════════════════════════════════════════════════════════
# MAIN FIXER CLASS
# ══════════════════════════════════════════════════════════════════════════════

class ProjectFixer:
    """
    All output goes through self._cb(text, color).
    Construct with a callback; if omitted, output is silent.
    """

    def __init__(self, project_path: str, cb: PrintCallback = _noop_cb):
        self.project_path   = Path(project_path).resolve()
        self.pm             = self._detect_pm()
        self.fixes_applied: List[str] = []
        self.fixes_failed:  List[str] = []
        self._cb            = cb          # ← the single output gateway

    # ── Internal output helper ─────────────────────────────────────────────────

    def _out(self, text: str, color: str = "white") -> None:
        """Route every message through the caller-supplied callback."""
        self._cb(text, color)

    # ── Detection ─────────────────────────────────────────────────────────────

    def _detect_pm(self) -> str:
        p = self.project_path
        if (p / "pnpm-lock.yaml").exists():   return "pnpm"
        if (p / "yarn.lock").exists():         return "yarn"
        if (p / "package-lock.json").exists(): return "npm"
        if (p / "bun.lockb").exists():         return "bun"
        return "npm"

    # ── Low-level helpers ──────────────────────────────────────────────────────

    def _do(self, cmd, *, label: str, cwd=None, shell=False) -> bool:
        code, out, err = _run(cmd, cwd=cwd or self.project_path, shell=shell)
        ok = code == 0
        if ok:
            self._out(f"  ✓ {label}", "green")
            self.fixes_applied.append(label)
        else:
            snippet = (err or out)[:120]
            self._out(f"  ⚠  {label} — {snippet}", "yellow")
            self.fixes_failed.append(label)
        return ok

    def _rm(self, *paths: str) -> None:
        for p in paths:
            full = self.project_path / p
            if full.is_dir():
                shutil.rmtree(full, ignore_errors=True)
                self._out(f"  Removed dir: {p}", "dim")
            elif full.is_file():
                full.unlink(missing_ok=True)
                self._out(f"  Removed file: {p}", "dim")

    # ══════════════════════════════════════════════════════════════════════════
    # INDIVIDUAL FIXES
    # ══════════════════════════════════════════════════════════════════════════

    def fix_pnpm_build_scripts(self) -> bool:
        self._out("🔧 Approving pnpm build scripts...", "cyan")
        ok = self._do(["pnpm", "approve-builds"], label="pnpm approve-builds")
        if not ok:
            npmrc = self.project_path / ".npmrc"
            try:
                content = npmrc.read_text(encoding="utf-8") if npmrc.exists() else ""
                if "allow-scripts" not in content:
                    npmrc.write_text(content + "\nallow-scripts=true\n", encoding="utf-8")
                    self._out("  ✓ Set allow-scripts=true in .npmrc", "green")
                    self.fixes_applied.append("Set allow-scripts=true in .npmrc")
                    ok = True
            except Exception as e:
                self._out(f"  Could not write .npmrc: {e}", "yellow")
        if ok:
            self._do(["pnpm", "install"], label="pnpm install after approve")
        return ok

    def fix_pnpm_lockfile(self) -> bool:
        self._out("🔒 Fixing pnpm lockfile...", "cyan")
        self._rm("node_modules", "pnpm-lock.yaml")
        return self._do(["pnpm", "install"], label="pnpm fresh install")

    def fix_pnpm_peer_deps(self) -> bool:
        self._out("🔗 Fixing pnpm peer deps...", "cyan")
        ok = self._do(
            ["pnpm", "install", "--resolve-peers-from-workspace-root"],
            label="pnpm resolve peers",
        )
        if not ok:
            ok = self._do(
                ["pnpm", "install", "--no-strict-peer-dependencies"],
                label="pnpm no-strict-peers",
            )
        return ok

    def fix_npm_peer_deps(self) -> bool:
        self._out("🔗 Fixing npm peer deps...", "cyan")
        ok = self._do(["npm", "install", "--legacy-peer-deps"], label="npm legacy-peer-deps")
        if not ok:
            ok = self._do(["npm", "install", "--force"], label="npm install --force")
        return ok

    def fix_npm_cache(self) -> bool:
        self._out("🗑️  Cleaning npm cache...", "cyan")
        self._do(["npm", "cache", "clean", "--force"], label="npm cache clean")
        self._rm("node_modules", "package-lock.json")
        return self._do(["npm", "install"], label="npm fresh install")

    def fix_node_modules(self) -> bool:
        self._out("📦 Reinstalling node_modules...", "cyan")
        self._rm("node_modules")
        install_cmds = {
            "pnpm": ["pnpm", "install"],
            "npm":  ["npm",  "install"],
            "yarn": ["yarn", "install"],
            "bun":  ["bun",  "install"],
        }
        cmd = install_cmds.get(self.pm, ["npm", "install"])
        return self._do(cmd, label=f"{self.pm} install")

    def fix_sharp(self) -> bool:
        self._out("🖼️  Fixing sharp/node-gyp...", "cyan")
        ok = self._do(
            ["pnpm" if self.pm == "pnpm" else "npm",
             "add"  if self.pm == "pnpm" else "install",
             "sharp", "--ignore-scripts"],
            label="install sharp --ignore-scripts",
        )
        if ok:
            self._do([self.pm, "rebuild", "sharp"], label="rebuild sharp")
        if not ok:
            ok = self._do(
                ["npm", "install", "--platform=win32", "--arch=x64", "@img/sharp-win32-x64"]
                if IS_WIN else ["npm", "install", "sharp"],
                label="sharp platform install",
            )
        return ok

    def fix_next_cache(self) -> bool:
        self._out("🧹 Clearing Next.js .next cache...", "cyan")
        self._rm(".next")
        self.fixes_applied.append("Cleared .next cache")
        return True

    def fix_port_conflict(self, port: int = 3000) -> int:
        if _is_port_free(port):
            return port
        new_port = _find_free_port(port + 1)
        self._out(f"  Port {port} in use → using {new_port}", "yellow")
        self.fixes_applied.append(f"Port {port}→{new_port}")
        return new_port

    def fix_typescript(self) -> bool:
        self._out("📝 Fixing TypeScript issues...", "cyan")
        tsconfig = self.project_path / "tsconfig.json"
        if not tsconfig.exists():
            self._out("  No tsconfig.json found — skipping", "dim")
            return False
        for f in self.project_path.rglob("*.tsbuildinfo"):
            f.unlink(missing_ok=True)
        self.fixes_applied.append("Cleared TS build info")
        pj   = _read_package_json(self.project_path)
        deps = {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}
        types_needed = []
        if "react" in deps and "@types/react" not in deps:
            types_needed.append("@types/react")
        if "@types/node" not in deps:
            types_needed.append("@types/node")
        if types_needed:
            cmd = [self.pm,
                   "add" if self.pm in ("pnpm", "bun", "yarn") else "install",
                   "-D"] + types_needed
            self._do(cmd, label=f"install types: {types_needed}")
        return True

    def fix_eslint(self) -> bool:
        self._out("🔍 Fixing ESLint...", "cyan")
        for nc in ["next.config.js", "next.config.ts", "next.config.mjs"]:
            config_file = self.project_path / nc
            if config_file.exists():
                try:
                    content = config_file.read_text(encoding="utf-8")
                    if "eslint" not in content.lower():
                        patched = re.sub(
                            r"(module\.exports\s*=\s*\{)",
                            r"\1\n  eslint: { ignoreDuringBuilds: true },",
                            content,
                        )
                        config_file.write_text(patched, encoding="utf-8")
                        self.fixes_applied.append("Set eslint.ignoreDuringBuilds=true")
                except Exception as e:
                    self._out(f"  Could not patch next.config: {e}", "yellow")
                break
        return True

    def fix_missing_env(self) -> bool:
        self._out("🔑 Checking .env files...", "cyan")
        env_example = self.project_path / ".env.example"
        env_local   = self.project_path / ".env.local"
        env_file    = self.project_path / ".env"
        if not env_local.exists() and not env_file.exists():
            if env_example.exists():
                shutil.copy(env_example, env_local)
                self.fixes_applied.append("Copied .env.example → .env.local")
            else:
                env_local.write_text("# Auto-created by project fixer\n", encoding="utf-8")
                self.fixes_applied.append("Created empty .env.local")
        return True

    def fix_permission(self) -> bool:
        if IS_WIN:
            self._out("  Permission fix not needed on Windows", "dim")
            return False
        self._out("🔐 Fixing permissions...", "cyan")
        nm = self.project_path / "node_modules"
        if nm.exists():
            self._do(["chmod", "-R", "755", str(nm)], label="chmod node_modules")
        return True

    def fix_network_retry(self, original_cmd: List[str], retries: int = 3) -> bool:
        self._out(f"🌐 Retrying on network error ({retries}x)...", "cyan")
        for i in range(retries):
            wait = 5 * (i + 1)
            self._out(f"  Attempt {i+1}/{retries} in {wait}s...", "dim")
            time.sleep(wait)
            code, _, _ = _run(original_cmd, cwd=self.project_path)
            if code == 0:
                self.fixes_applied.append(f"Network retry #{i+1} succeeded")
                return True
        return False

    def fix_missing_scripts(self) -> bool:
        pj_path = self.project_path / "package.json"
        if not pj_path.exists():
            return False
        try:
            data    = json.loads(pj_path.read_text(encoding="utf-8"))
            scripts = data.setdefault("scripts", {})
            changed = False
            defaults = {"dev": "next dev", "build": "next build",
                        "start": "next start", "lint": "next lint"}
            for k, v in defaults.items():
                if k not in scripts:
                    scripts[k] = v
                    changed = True
            if changed:
                pj_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                self.fixes_applied.append("Added missing npm scripts")
        except Exception as e:
            self._out(f"  Could not patch package.json: {e}", "yellow")
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # INSTALL A SINGLE LIBRARY
    # ══════════════════════════════════════════════════════════════════════════

    def install_library(self, lib: str, max_attempts: int = 3) -> bool:
        install_map = {
            "pnpm": ["pnpm", "add"],
            "npm":  ["npm",  "install"],
            "yarn": ["yarn", "add"],
            "bun":  ["bun",  "add"],
        }
        base_cmd = install_map.get(self.pm, ["npm", "install"])

        for attempt in range(1, max_attempts + 1):
            self._out(f"  📦 Installing {lib} (attempt {attempt}/{max_attempts})...", "cyan")
            cmd          = base_cmd + [lib]
            code, out, err = _run(cmd, cwd=self.project_path)

            if code == 0:
                self._out(f"  ✓ {lib} installed", "green")
                return True

            combined = out + err
            if re.search(r"err_pnpm_ignored_build_scripts", combined, re.IGNORECASE):
                if _pkg_installed(self.project_path, lib):
                    self._out(f"  ⚠  {lib} installed (pnpm build-script warning ignored)", "yellow")
                    return True
                self._out("  Fixing build scripts then retrying...", "dim")
                self.fix_pnpm_build_scripts()
                continue

            errors = detect_errors(out, err)
            self._out(f"  Errors detected: {errors}", "red")

            if attempt == max_attempts:
                break

            for e in errors:
                if e in (FE.PNPM_LOCKFILE, FE.PNPM_FROZEN):  self.fix_pnpm_lockfile()
                elif e == FE.PNPM_PEER_DEPS:                  self.fix_pnpm_peer_deps()
                elif e == FE.NPM_PEER_DEPS:                   self.fix_npm_peer_deps()
                elif e == FE.NPM_CACHE:                       self.fix_npm_cache()
                elif e == FE.NODE_MODULES_CORRUPT:            self.fix_node_modules()
                elif e == FE.SHARP_GYNODE:                    self.fix_sharp()
                elif e == FE.PERMISSION:                      self.fix_permission()
                elif e == FE.NETWORK:
                    if self.fix_network_retry(cmd):
                        return True

        if _pkg_installed(self.project_path, lib):
            self._out(f"  ⚠  {lib} found in node_modules despite errors", "yellow")
            return True

        self._out(f"  ✗ Could not install {lib} after {max_attempts} attempts", "red")
        return False

    # ══════════════════════════════════════════════════════════════════════════
    # FIX ALL
    # ══════════════════════════════════════════════════════════════════════════

    def fix_all(self, error_output: str = "") -> Dict[str, Any]:
        self._out("", "white")
        self._out("🔧 ProjectFixer v3.1", "cyan")
        self._out(f"  Path : {self.project_path}", "dim")
        self._out(f"  PM   : {self.pm}", "cyan")

        if error_output:
            errors = detect_errors(error_output, "")
            self._out(f"  ⚡ Targeted fixes for: {errors}", "yellow")
        else:
            errors = [FE.UNKNOWN]

        self.fix_missing_scripts()
        self.fix_next_cache()

        if FE.PNPM_IGNORED_BUILD in errors and self.pm == "pnpm":
            self.fix_pnpm_build_scripts()
        if (FE.PNPM_LOCKFILE in errors or FE.PNPM_FROZEN in errors) and self.pm == "pnpm":
            self.fix_pnpm_lockfile()
        if FE.PNPM_PEER_DEPS in errors and self.pm == "pnpm":
            self.fix_pnpm_peer_deps()
        if FE.NPM_PEER_DEPS in errors and self.pm == "npm":
            self.fix_npm_peer_deps()
        if FE.NPM_CACHE in errors and self.pm == "npm":
            self.fix_npm_cache()
        if FE.NODE_MODULES_CORRUPT in errors or FE.UNKNOWN in errors:
            self.fix_node_modules()
        if FE.SHARP_GYNODE in errors:
            self.fix_sharp()
        if FE.TS_ERROR in errors:
            self.fix_typescript()
        if FE.ESLINT_ERROR in errors:
            self.fix_eslint()
        if FE.ENV_MISSING in errors:
            self.fix_missing_env()
        if FE.PERMISSION in errors and not IS_WIN:
            self.fix_permission()
        if FE.NETWORK in errors:
            self._out("  Network issue detected — check connection and retry", "yellow")

        port = self.fix_port_conflict(3000)

        # ── Summary ────────────────────────────────────────────────────────────
        self._out("", "white")
        self._out("✅ Fix Summary", "green")
        if self.fixes_applied:
            for f in self.fixes_applied:
                self._out(f"  ✓ {f}", "green")
        else:
            self._out("  No fixes were needed / applied", "dim")
        if self.fixes_failed:
            self._out("⚠  Failed:", "yellow")
            for f in self.fixes_failed:
                self._out(f"  ✗ {f}", "yellow")

        return {
            "success":       len(self.fixes_applied) > 0,
            "fixes_applied": self.fixes_applied,
            "fixes_failed":  self.fixes_failed,
            "port":          port,
        }


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def fix_project(
    project_path: str,
    error_output: str = "",
    cb: PrintCallback = _noop_cb,
) -> Dict[str, Any]:
    """
    Entry point.  Pass your own callback to receive all output:

        def my_callback(text: str, color: str) -> None:
            print(f"[{color}] {text}")

        fix_project("/path/to/project", cb=my_callback)
    """
    fixer = ProjectFixer(project_path, cb=cb)
    return fixer.fix_all(error_output=error_output)