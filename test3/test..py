

import os
import sys
import json

# ── make sure vue_nuxt_creator is importable from the same directory ─────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    from  experiment.test3.k10 import  creator
except ImportError:
    print("ERROR: vue_nuxt_creator.py not found next to this script.")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Rich terminal output (falls back to plain print if rich not installed)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel  import Panel
    from rich.table  import Table
    from rich        import box
    _rc = Console()

    def _print(text, color="white"):
        _rc.print(f"[{color}]{text}[/{color}]")

    def _panel(title, body, color="cyan"):
        _rc.print(Panel(body, title=title, border_style=color))

except ImportError:
    def _print(text, color="white"):
        print(text)
    def _panel(title, body, color="cyan"):
        print(f"\n{'─'*60}")
        print(f"  {title}")
        print(f"{'─'*60}")
        print(body)
        print(f"{'─'*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# State captured from the upgrader callback
# ─────────────────────────────────────────────────────────────────────────────
_captured_upgrade_event = {}   # filled when msg_type == "upgrader"
_upgrade_done           = False


def _demo_cb(text: str, color: str = "white",
             msg_type: str = "normal", data: dict = None):
    """
    Callback given to vue_nuxt_creator.
    Mirrors what a WebSocket server would do with each event.
    """
    global _captured_upgrade_event, _upgrade_done
    data = data or {}

    # ── upgrader events ───────────────────────────────────────────────────────
    if msg_type == "upgrader":
        _print("", "white")

        if data.get("needs_upgrade"):
            # ── first upgrader event: permission request ──────────────────────
            if not _upgrade_done:
                _captured_upgrade_event = data
                _panel(
                    "⬆  UPGRADE AVAILABLE",
                    f"  Framework  : [bold]{data.get('framework','?').upper()}[/bold]\n"
                    f"  Project    : [bold]{data.get('project_name','?')}[/bold]\n"
                    f"  Path       : {data.get('project_path','?')}\n"
                    f"  Installed  : [yellow]{data.get('installed','?')}[/yellow]\n"
                    f"  Latest     : [green]{data.get('latest','?')}[/green]\n"
                    f"  PM         : {data.get('pm','?')}\n"
                    f"  task_id    : [dim]{data.get('task_id','')}[/dim]",
                    color="yellow",
                )
            else:
                # ── second upgrader event: result of the upgrade ──────────────
                success = data.get("success", False)
                _panel(
                    "✅ UPGRADE COMPLETE" if success else "❌ UPGRADE FAILED",
                    f"  Project path : {data.get('project_path','?')}\n"
                    f"  Success      : {'yes' if success else 'no'}",
                    color="green" if success else "red",
                )
        else:
            # Up to date
            _panel(
                "✅ ALREADY UP TO DATE",
                f"  {data.get('framework','?').upper()} {data.get('installed','?')} "
                f"is the latest version.",
                color="green",
            )
        return

    # ── normal log lines ──────────────────────────────────────────────────────
    if msg_type == "warning":
        _print(f"  ⚠  {text}", "yellow")
    elif msg_type == "error":
        _print(f"  ✗  {text}", "red")
    elif color == "green":
        _print(f"  ✓  {text}", "green")
    elif color == "cyan":
        _print(f"  ℹ  {text}", "cyan")
    elif color == "dim":
        _print(f"     {text}", "dim")
    else:
        _print(f"     {text}", "white")


# ─────────────────────────────────────────────────────────────────────────────
# Mock project setup
# ─────────────────────────────────────────────────────────────────────────────
def _setup_mock_project(project_path: str, framework: str, old_version: str):
    """
    Create a minimal fake node_modules/<fw>/package.json so
    get_installed_version() returns old_version instead of 'unknown'.
    """
    pkg = "vue" if framework == "vue" else "nuxt"
    pkg_dir = os.path.join(project_path, "node_modules", pkg)
    os.makedirs(pkg_dir, exist_ok=True)
    pkg_json = os.path.join(pkg_dir, "package.json")
    with open(pkg_json, "w") as f:
        json.dump({"name": pkg, "version": old_version}, f)
    _print(f"  Mock installed version: {old_version}", "dim")


# ─────────────────────────────────────────────────────────────────────────────
# Demo entry point
# ─────────────────────────────────────────────────────────────────────────────
def run_demo():
    _print("", "white")
    _panel(
        "🎬  UPGRADER PERMISSION DEMO  —  vue_nuxt_creator v1.1",
        "This demo simulates the full socket-mode upgrade flow.\n"
        "A mock project is created with an [yellow]old version[/yellow] installed,\n"
        "then the upgrader fires and asks your permission before running.",
        color="cyan",
    )

    # ── 1. Choose framework ───────────────────────────────────────────────────
    _print("\nWhich framework do you want to demo?", "cyan")
    _print("  1) Vue", "white")
    _print("  2) Nuxt", "white")
    try:
        choice = input("  Choice [1/2, default=2]: ").strip() or "2"
    except (EOFError, KeyboardInterrupt):
        _print("\nAborted.", "yellow")
        return

    framework  = "vue" if choice == "1" else "nuxt"
    old_ver    = "3.4.0" if framework == "vue" else "3.12.0"
    proj_name  = f"demo-{framework}-upgrade"

    # ── 2. Choose install dir ─────────────────────────────────────────────────
    _print(f"\nWhere should the mock project live?", "cyan")
    default_dir = os.path.join(os.path.expanduser("~"), "vue_nuxt_demo")
    try:
        raw_dir = input(f"  Install dir [{default_dir}]: ").strip() or default_dir
    except (EOFError, KeyboardInterrupt):
        _print("\nAborted.", "yellow")
        return

    install_dir  = os.path.abspath(raw_dir)
    project_path = os.path.join(install_dir, proj_name)
    os.makedirs(project_path, exist_ok=True)

    # ── 3. Inject fake old version ────────────────────────────────────────────
    _print("", "white")
    _print("📁 Setting up mock project...", "cyan")
    _print(f"  Path: {project_path}", "dim")
    _setup_mock_project(project_path, framework, old_ver)

    # ── 4. Build a real Intent ────────────────────────────────────────────────
    intent = creator.Intent(
        framework    = framework,
        project_name = proj_name,
        pm           = "npm",
        flags        = [],
        libraries    = [],
        global_tools = [],
        install_dir  = install_dir,
    )

    # ── 5. Generate a socket-style task_id ────────────────────────────────────
    task_id = creator._gen_task_id()
    _print(f"\n🔑 task_id = {task_id}", "dim")

    # ── 6. Load knowledge and call check_and_upgrade ──────────────────────────
    _print("", "white")
    _print("🔄 Running check_and_upgrade()  (socket mode — will NOT prompt inline)...", "cyan")
    k = creator._load_knowledge()
    creator.check_and_upgrade(intent, k, _demo_cb, task_id=task_id)

    # ── 7. Inspect the captured event ────────────────────────────────────────
    if not _captured_upgrade_event:
        _print("\n✅ Project is already up to date — nothing to upgrade.", "green")
        _cleanup_mock(project_path)
        return

    upgrade_data = _captured_upgrade_event

    # ── 8. Ask the USER for permission ───────────────────────────────────────
    _print("", "white")
    _print("═" * 60, "yellow")
    _print("  The socket server received the upgrader event above.", "yellow")
    _print("  Now it asks YOU for permission before proceeding.", "yellow")
    _print("═" * 60, "yellow")
    _print("", "white")

    try:
        answer = input(
            f"  ⬆  Upgrade {framework.upper()} "
            f"{upgrade_data.get('installed')} → {upgrade_data.get('latest')}? "
            f"(y/n): "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer != "y":
        _print("\n  Upgrade declined. No changes made.", "yellow")
        _cleanup_mock(project_path)
        return

    # ── 9. Trigger upgrade by calling start() with upgrade_permission ─────────
    _print("", "white")
    _print("▶  Sending upgrade_permission to start()...", "cyan")
    _print(f"   message = {{\"type\": \"upgrade_permission\", \"data\": {{...}}}}", "dim")
    _print("", "white")

    global _upgrade_done
    _upgrade_done = True   # tells callback the next upgrader event is the result

    creator.start(
        user_request = "",
        cb           = _demo_cb,
        message      = {
            "type": "upgrade_permission",
            "data": upgrade_data,   # exact payload from the event
        },
    )

    # ── 10. Show final installed version ─────────────────────────────────────
    _print("", "white")
    new_ver = creator.get_installed_version(project_path, "vue" if framework == "vue" else "nuxt")
    _print(f"  Version in node_modules after upgrade: {new_ver}", "cyan")

    _cleanup_mock(project_path)


def _cleanup_mock(project_path: str):
    """Optionally remove the mock project directory."""
    _print("", "white")
    try:
        answer = input("  🗑  Remove mock project folder? (y/n, default=n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer == "y":
        import shutil
        shutil.rmtree(project_path, ignore_errors=True)
        _print(f"  Removed: {project_path}", "dim")
    else:
        _print(f"  Left in place: {project_path}", "dim")
    _print("", "white")
    _print("Demo complete.", "green")


if __name__ == "__main__":
    run_demo()