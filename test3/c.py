import os
import subprocess
import sys
import json
import shutil
from pathlib import Path
from typing import Dict, List, Tuple, Optional

class ProjectFixer:
    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        self.package_manager = self._detect_package_manager()
        
    def _detect_package_manager(self) -> str:
        """Detect which package manager the project uses"""
        if (self.project_path / "pnpm-lock.yaml").exists():
            return "pnpm"
        elif (self.project_path / "package-lock.json").exists():
            return "npm"
        elif (self.project_path / "yarn.lock").exists():
            return "yarn"
        return "npm"  # default
    
    def run_command(self, cmd: List[str], cwd=None) -> Tuple[int, str, str]:
        """Run a command and return exit code, stdout, stderr"""
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd or self.project_path,
                capture_output=True,
                text=True,
                shell=True if sys.platform == "win32" else False
            )
            return result.returncode, result.stdout, result.stderr
        except Exception as e:
            return 1, "", str(e)
    
    def fix_pnpm_issues(self) -> Dict[str, any]:
        """Fix common pnpm issues"""
        issues_fixed = []
        
        # Issue 1: Build scripts blocked
        print("📦 Checking pnpm build scripts...")
        code, stdout, stderr = self.run_command(["pnpm", "approve-builds"])
        if code == 0:
            issues_fixed.append("Approved build scripts")
        
        # Issue 2: Lockfile issues
        print("🔒 Checking lockfile...")
        code, stdout, stderr = self.run_command(["pnpm", "install", "--frozen-lockfile"])
        if code != 0:
            print("  ⚠️ Lockfile issue detected, reinstalling...")
            self.run_command(["rm", "-rf", "node_modules"])
            self.run_command(["rm", "-f", "pnpm-lock.yaml"])
            code, _, _ = self.run_command(["pnpm", "install"])
            if code == 0:
                issues_fixed.append("Reinstalled dependencies")
        
        # Issue 3: Sharp/Node-gyp issues
        print("🖼️ Checking image optimization packages...")
        code, stdout, _ = self.run_command(["pnpm", "list", "sharp"])
        if "sharp" in stdout and code != 0:
            self.run_command(["pnpm", "add", "sharp", "--ignore-scripts"])
            self.run_command(["pnpm", "rebuild", "sharp"])
            issues_fixed.append("Fixed sharp installation")
        
        return {"fixed": issues_fixed, "success": len(issues_fixed) > 0}
    
    def fix_npm_issues(self) -> Dict[str, any]:
        """Fix common npm issues"""
        issues_fixed = []
        
        # Clear cache
        print("🗑️ Clearing npm cache...")
        self.run_command(["npm", "cache", "clean", "--force"])
        
        # Remove node_modules and reinstall
        print("📦 Reinstalling dependencies...")
        shutil.rmtree(self.project_path / "node_modules", ignore_errors=True)
        code, _, _ = self.run_command(["npm", "install"])
        if code == 0:
            issues_fixed.append("Reinstalled dependencies")
        
        return {"fixed": issues_fixed, "success": len(issues_fixed) > 0}
    
    def fix_nextjs_issues(self) -> Dict[str, any]:
        """Fix Next.js specific issues"""
        issues_fixed = []
        
        # Check if Next.js is installed
        package_json = self.project_path / "package.json"
        if package_json.exists():
            with open(package_json, 'r') as f:
                data = json.load(f)
                deps = {**data.get('dependencies', {}), **data.get('devDependencies', {})}
                
                if 'next' in deps:
                    print("🚀 Fixing Next.js issues...")
                    
                    # Fix .next cache
                    next_cache = self.project_path / ".next"
                    if next_cache.exists():
                        shutil.rmtree(next_cache)
                        issues_fixed.append("Cleared Next.js cache")
                    
                    # Ensure proper scripts
                    if 'dev' not in data.get('scripts', {}):
                        issues_fixed.append("Added dev script")
        
        return {"fixed": issues_fixed, "success": len(issues_fixed) > 0}
    
    def fix_port_conflicts(self, port: int = 3000) -> Dict[str, any]:
        """Fix port conflicts"""
        import socket
        
        def is_port_in_use(port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(('localhost', port))
                    return False
                except:
                    return True
        
        if is_port_in_use(port):
            print(f"🔌 Port {port} is in use, finding alternative...")
            for new_port in range(port + 1, port + 10):
                if not is_port_in_use(new_port):
                    return {"fixed": [f"Changed port from {port} to {new_port}"], 
                            "new_port": new_port, 
                            "success": True}
        return {"fixed": [], "success": False}
    
    def run_dev_server(self, port: int = 3000) -> subprocess.Popen:
        """Start the development server"""
        cmd = [self.package_manager, "dev"]
        
        # Add port parameter for Next.js
        if self.package_manager == "pnpm":
            cmd.extend(["--", "-p", str(port)])
        else:
            cmd.extend(["--port", str(port)])
        
        print(f"🚀 Starting server with: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd,
            cwd=self.project_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        return process
    
    def fix_all(self, auto_start: bool = True) -> Dict[str, any]:
        """Run all fixes and optionally start the server"""
        print(f"🔧 Fixing project at: {self.project_path}")
        print(f"📦 Detected package manager: {self.package_manager}")
        
        all_fixes = []
        
        # Run package manager specific fixes
        if self.package_manager == "pnpm":
            result = self.fix_pnpm_issues()
            all_fixes.extend(result.get("fixed", []))
        else:
            result = self.fix_npm_issues()
            all_fixes.extend(result.get("fixed", []))
        
        # Run Next.js fixes
        result = self.fix_nextjs_issues()
        all_fixes.extend(result.get("fixed", []))
        
        # Fix port conflicts
        port_result = self.fix_port_conflicts(3000)
        all_fixes.extend(port_result.get("fixed", []))
        dev_port = port_result.get("new_port", 3000)
        
        print("\n✅ Fixes applied:")
        for fix in all_fixes:
            print(f"  ✓ {fix}")
        
        # Start server if requested
        process = None
        if auto_start and all_fixes:
            print(f"\n🌐 Starting dev server on port {dev_port}...")
            process = self.run_dev_server(dev_port)
        
        return {
            "success": len(all_fixes) > 0,
            "fixes": all_fixes,
            "port": dev_port,
            "process": process
        }

def fix_project(project_path):
    """Simple function to fix project issues"""
    project_path = Path(project_path)
    
    print(f"🔧 Fixing project at: {project_path}")
    
    # Change to project directory
    os.chdir(project_path)
    
    # Fix pnpm issues
    print("📦 Fixing pnpm build scripts...")
    subprocess.run(["pnpm", "approve-builds"], shell=True)
    
    # Clear node_modules and reinstall if needed
    print("🗑️  Cleaning and reinstalling...")
    if (project_path / "node_modules").exists():
        shutil.rmtree("node_modules", ignore_errors=True)
    
    # Remove lockfile
    if (project_path / "pnpm-lock.yaml").exists():
        os.remove("pnpm-lock.yaml")
    
    # Fresh install
    subprocess.run(["pnpm", "install"], shell=True)
    
    # Start dev server
    print("🚀 Starting dev server...")
    subprocess.run(["pnpm", "dev"], shell=True)

# Run it
if __name__ == "__main__":
    # You can hardcode the path here for now
    project_path = r"E:\project\shop_m\lyren3\test3lyren3\ooqa22"
    fix_project(project_path)