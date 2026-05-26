import os
import shutil
import subprocess
import ctypes

def get_directory_size(directory):
    try:
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(directory):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
        return total_size
    except Exception as e:
        print(f"Error getting directory size: {e}")
        return 0

def delete_directory_contents(directory):
    try:
        for root, dirs, files in os.walk(directory, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                shutil.rmtree(os.path.join(root, name))
        return True
    except PermissionError as e:
        print(f"Permission error deleting {directory}: {e}")
        return False

def main():
    steps = [
        ("TEMP FILES", os.environ['TEMP'], "C:\\Windows\\Temp"),
        ("PREFETCH", "C:\\Windows\\Prefetch", ctypes.windll.shell32.IsUserAnAdmin()),
        ("RECYCLE BIN", None, ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x0007)),
        ("BROWSER CACHES", [os.path.expandvars('%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Cache'),
                            os.path.expandvars('%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Cache')], None),
        ("DNS CACHE", "ipconfig /flushdns", None),
        ("WINDOWS UPDATE CACHE", "C:\\Windows\\SoftwareDistribution\\Download\\", ctypes.windll.shell32.IsUserAnAdmin()),
        ("THUMBNAIL CACHE", os.path.expandvars('%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\thumbcache_*.db'), None)
    ]

    total_freed = 0
    step_results = []

    for i, (header, paths, admin_required) in enumerate(steps):
        print(f"\nStep {i+1} — {header}")
        
        freed = 0
        if isinstance(paths, list):
            for path in paths:
                if os.path.exists(path):
                    initial_size = get_directory_size(path)
                    success = delete_directory_contents(path)
                    if success:
                        final_size = get_directory_size(path)
                        freed += initial_size - final_size
        elif isinstance(paths, str):
            if admin_required and not ctypes.windll.shell32.IsUserAnAdmin():
                print("Warning: Skipping this step as script is not running as an administrator.")
            else:
                initial_size = get_directory_size(paths)
                try:
                    subprocess.run(paths.split(), check=True)
                    final_size = get_directory_size(paths)
                    freed += initial_size - final_size
                except Exception as e:
                    print(f"Error: {e}")
        
        if freed > 0:
            freed_mb = freed / (1024 * 1024)
            print(f"{freed_mb:.2f} MB")
            total_freed += freed_mb
        else:
            print("0.00 MB")

        step_results.append((header, freed))

    grand_total_mb = total_freed
    print(f"\nTOTAL FREED: {grand_total_mb:.2f} MB")
    print("\nPer-Step Results:")
    for header, freed in step_results:
        print(f"{header}: {'{:.2f}'.format(freed if freed > 0 else 0)} MB")

if __name__ == "__main__":
    main()