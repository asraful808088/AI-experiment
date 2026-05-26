import platform
import os
import sys
import subprocess
import socket
import json
import struct
from datetime import datetime


def run_cmd(cmd):
   
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, shell=True).decode().strip()
    except:
        return "N/A"


def get_os_info():
    return {
        "system":       platform.system(),
        "release":      platform.release(),
        "version":      platform.version(),
        "machine":      platform.machine(),
        "processor":    platform.processor(),
        "architecture": platform.architecture()[0],
        "hostname":     socket.gethostname(),
        "username":     os.getenv("USER") or os.getenv("USERNAME") or "N/A",
        "home_dir":     os.path.expanduser("~"),
        "cwd":          os.getcwd(),
    }


def get_cpu_info():
    cpu = {
        "python_cpu_count": os.cpu_count(),
    }

    system = platform.system()

    if system == "Linux":
        cpuinfo = run_cmd("cat /proc/cpuinfo")
        for line in cpuinfo.splitlines():
            if "model name" in line:
                cpu["model"] = line.split(":")[1].strip()
                break
        cpu["cores_info"] = run_cmd("lscpu | grep -E 'Core|Thread|Socket|CPU MHz'")

    elif system == "Windows":
        cpu["model"] = run_cmd("wmic cpu get Name /value").replace("Name=", "").strip()
        cpu["cores"]  = run_cmd("wmic cpu get NumberOfCores /value").replace("NumberOfCores=", "").strip()
        cpu["threads"]= run_cmd("wmic cpu get NumberOfLogicalProcessors /value").replace("NumberOfLogicalProcessors=", "").strip()
        cpu["speed_mhz"] = run_cmd("wmic cpu get MaxClockSpeed /value").replace("MaxClockSpeed=", "").strip()

    elif system == "Darwin":
        cpu["model"] = run_cmd("sysctl -n machdep.cpu.brand_string")
        cpu["cores"] = run_cmd("sysctl -n hw.physicalcpu")
        cpu["threads"]= run_cmd("sysctl -n hw.logicalcpu")

    return cpu


def get_ram_info():
    system = platform.system()
    ram = {}

    if system == "Linux":
        meminfo = run_cmd("cat /proc/meminfo")
        mem = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                mem[parts[0].rstrip(":")] = parts[1]
        total_kb  = int(mem.get("MemTotal", 0))
        free_kb   = int(mem.get("MemFree", 0))
        avail_kb  = int(mem.get("MemAvailable", 0))
        swap_total= int(mem.get("SwapTotal", 0))
        swap_free = int(mem.get("SwapFree", 0))
        ram = {
            "total_gb":      round(total_kb / 1e6, 2),
            "available_gb":  round(avail_kb / 1e6, 2),
            "used_gb":       round((total_kb - avail_kb) / 1e6, 2),
            "swap_total_gb": round(swap_total / 1e6, 2),
            "swap_used_gb":  round((swap_total - swap_free) / 1e6, 2),
        }

    elif system == "Windows":
        total = run_cmd("wmic ComputerSystem get TotalPhysicalMemory /value").replace("TotalPhysicalMemory=", "").strip()
        free  = run_cmd("wmic OS get FreePhysicalMemory /value").replace("FreePhysicalMemory=", "").strip()
        try:
            total_gb = round(int(total) / 1e9, 2)
            free_gb  = round(int(free)  / 1e6, 2)
            ram = {
                "total_gb":     total_gb,
                "free_gb":      free_gb,
                "used_gb":      round(total_gb - free_gb, 2),
            }
        except:
            ram = {"info": "unavailable"}

    elif system == "Darwin":
        total = run_cmd("sysctl -n hw.memsize")
        ram["total_gb"] = round(int(total) / 1e9, 2) if total != "N/A" else "N/A"
        ram["vm_stat"] = run_cmd("vm_stat")

    return ram


def get_disk_info():
    system = platform.system()

    if system == "Windows":
        raw = run_cmd("wmic logicaldisk get DeviceID,Size,FreeSpace,FileSystem /format:csv")
        disks = []
        for line in raw.splitlines():
            if not line.strip() or "DeviceID" in line:
                continue
            parts = line.split(",")
            if len(parts) >= 4:
                try:
                    free  = int(parts[1]) if parts[1] else 0
                    total = int(parts[3]) if parts[3] else 0
                    disks.append({
                        "device":      parts[2],
                        "filesystem":  parts[4] if len(parts) > 4 else "N/A",
                        "total_gb":    round(total / 1e9, 2),
                        "free_gb":     round(free  / 1e9, 2),
                        "used_gb":     round((total - free) / 1e9, 2),
                    })
                except:
                    pass
        return disks
    else:
        raw = run_cmd("df -h")
        disks = []
        for line in raw.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 6:
                disks.append({
                    "filesystem": parts[0],
                    "size":       parts[1],
                    "used":       parts[2],
                    "available":  parts[3],
                    "use%":       parts[4],
                    "mountpoint": parts[5],
                })
        return disks


def get_network_info():
    interfaces = {}
    try:
        for name, info in socket.if_nameindex() if hasattr(socket, 'if_nameindex') else []:
            interfaces[info] = {}
    except:
        pass

    
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = "N/A"

    system = platform.system()
    if system == "Windows":
        raw_interfaces = run_cmd("ipconfig /all")
    elif system == "Darwin":
        raw_interfaces = run_cmd("ifconfig")
    else:
        raw_interfaces = run_cmd("ip addr show") or run_cmd("ifconfig")

    return {
        "hostname":   hostname,
        "local_ip":   local_ip,
        "interfaces": raw_interfaces,
    }


def get_gpu_info():

    nvidia = run_cmd(
        "nvidia-smi --query-gpu=name,memory.total,memory.used,temperature.gpu,utilization.gpu "
        "--format=csv,noheader,nounits"
    )
    if nvidia != "N/A":
        gpus = []
        for line in nvidia.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                gpus.append({
                    "name":            parts[0],
                    "memory_total_mb": parts[1],
                    "memory_used_mb":  parts[2],
                    "temp_c":          parts[3],
                    "utilization_pct": parts[4],
                })
        return gpus

   
    amd = run_cmd("rocm-smi --showproductname --showmeminfo vram --showtemp --showuse")
    if amd != "N/A":
        return {"amd_gpu_raw": amd}

   
    if platform.system() == "Darwin":
        return {"info": run_cmd("system_profiler SPDisplaysDataType")}

   
    if platform.system() == "Windows":
        return {"info": run_cmd("wmic path win32_VideoController get Name,AdapterRAM /value")}

    return "No GPU info available"


def get_python_info():
    pkgs_raw = run_cmd(f'"{sys.executable}" -m pip list --format=json')
    try:
        packages = json.loads(pkgs_raw)
    except:
        packages = []

    return {
        "version":        sys.version,
        "executable":     sys.executable,
        "platform":       sys.platform,
        "package_count":  len(packages),
        "packages":       packages,
    }


def get_env_info():
    safe_keys = ["PATH", "SHELL", "TERM", "LANG", "HOME",
                 "USERPROFILE", "COMPUTERNAME", "NUMBER_OF_PROCESSORS"]
    return {k: os.environ.get(k, "N/A") for k in safe_keys}



def get_full_config():
    print("🔍 Collecting system info...", flush=True)
    return {
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "os":           get_os_info(),
        "cpu":          get_cpu_info(),
        "ram":          get_ram_info(),
        "disks":        get_disk_info(),
        "network":      get_network_info(),
        "gpu":          get_gpu_info(),
        "python":       get_python_info(),
        "environment":  get_env_info(),
    }

def format_config(cfg):
    lines = []
    W = 55

    
    lines.append("\n" + "═"*W)
    lines.append("        🖥️  FULL SYSTEM CONFIGURATION")
    lines.append(f"        📅 {cfg.get('collected_at', 'N/A')}")
    lines.append("═"*W)

   
    ICONS = {
        "os":          "🗂️  OS",
        "cpu":         "⚙️  CPU",
        "ram":         "🧠 RAM",
        "disks":       "💾 DISKS",
        "network":     "🌐 NETWORK",
        "gpu":         "🎮 GPU",
        "python":      "🐍 PYTHON",
        "environment": "🔧 ENVIRONMENT",
    }

    for section, data in cfg.items():
        if section == "collected_at":
            continue

        label = ICONS.get(section, f"📌 {section.upper()}")
        lines.append(f"\n{'─'*W}")
        lines.append(f"  {label}")
        lines.append("─"*W)

        if isinstance(data, dict):
            for k, v in data.items():
                if k in ("packages", "interfaces"):
                    continue  
                
                v_str = str(v)
                if len(v_str) > 80:
                    v_str = v_str[:77] + "..."
                lines.append(f"  {k:<24} {v_str}")

        elif isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    lines.append(f"  [{i}]")
                    for k, v in item.items():
                        lines.append(f"      {k:<20} {v}")
                else:
                    lines.append(f"  • {item}")

        elif data in (None, "N/A", ""):
            lines.append("  (not available)")

        else:
           
            for line in str(data).splitlines():
                lines.append(f"  {line}")


    net_interfaces = cfg.get("network", {}).get("interfaces", "")
    if net_interfaces:
        lines.append(f"\n{'─'*W}")
        lines.append("  🌐 NETWORK INTERFACES (raw)")
        lines.append("─"*W)
        for line in str(net_interfaces).splitlines()[:30]: 
            lines.append(f"  {line}")

    
    pkgs = cfg.get("python", {}).get("packages", [])
    if pkgs:
        lines.append(f"\n{'─'*W}")
        lines.append(f"  📦 PYTHON PACKAGES  ({len(pkgs)} installed)")
        lines.append("─"*W)
        lines.append(f"  {'Name':<30} Version")
        lines.append(f"  {'─'*29} {'─'*9}")
        for p in pkgs:
            lines.append(f"  {p['name']:<30} {p['version']}")

    
    lines.append("\n" + "═"*W)

    return "\n".join(lines)

cfg = format_config(get_full_config())
