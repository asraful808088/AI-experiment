#!/usr/bin/env python3
"""
VS Code Project Directory Detector
Finds the correct project directory even when VS Code doesn't expose it in command line
"""

import os
import sys
import subprocess
import json
from pathlib import Path

def get_vscode_project_dir():
    """
    Returns the VS Code project directory (workspace root)
    Uses multiple methods to find the correct path
    """
    
    # Method 1: Get from current file location (most reliable for your setup)
    current_file = os.path.abspath(__file__)
    current_dir = os.path.dirname(current_file)
    
    # If script is in a 'test' folder, project root is parent
    if os.path.basename(current_dir).lower() in ['test', 'tests', 'testing']:
        return os.path.dirname(current_dir)
    
    # Method 2: Look for .vscode folder by traversing up
    test_dir = current_dir
    while test_dir != os.path.dirname(test_dir):
        if os.path.exists(os.path.join(test_dir, '.vscode')):
            return test_dir
        # Check for common project files
        if os.path.exists(os.path.join(test_dir, 'requirements.txt')) or \
           os.path.exists(os.path.join(test_dir, 'package.json')) or \
           os.path.exists(os.path.join(test_dir, '.git')):
            return test_dir
        test_dir = os.path.dirname(test_dir)
    
    # Method 3: Search through VS Code extension processes (Windows only)
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ['wmic', 'process', 'where', "name='Code.exe'", 'get', 'commandline'],
                capture_output=True,
                text=True,
                shell=True
            )
            
            # Look for your project path patterns
            for line in result.stdout.split('\n'):
                # Look for drive paths like e:\project
                if ':\\project\\' in line or ':\\Project\\' in line:
                    import re
                    matches = re.findall(r'([A-Za-z]:\\[^\s"]+?)(?:\\|$)', line)
                    for match in matches:
                        # Get the root project directory (up to lyren3 or similar)
                        if 'shop_m' in match or 'lyren3' in match:
                            # Extract up to the project root
                            parts = match.split('\\')
                            if 'shop_m' in parts:
                                idx = parts.index('shop_m') + 1
                                if idx < len(parts):
                                    return '\\'.join(parts[:idx+1])
                            elif 'lyren3' in parts:
                                idx = parts.index('lyren3')
                                return '\\'.join(parts[:idx+1])
        except:
            pass
    
    # Method 4: Check environment variables
    if 'VSCODE_CWD' in os.environ:
        vscode_cwd = os.environ['VSCODE_CWD']
        if 'Microsoft VS Code' not in vscode_cwd:
            return vscode_cwd
    
    # Method 5: Default to current directory
    return current_dir

def get_project_info():
    """
    Returns detailed information about the project
    """
    project_root = get_vscode_project_dir()
    
    info = {
        'project_root': project_root,
        'project_name': os.path.basename(project_root),
        'script_location': os.path.abspath(__file__),
        'current_working_dir': os.getcwd(),
    }
    
    # Check if script is inside project
    try:
        rel_path = os.path.relpath(info['script_location'], project_root)
        info['script_relative_path'] = rel_path
    except:
        info['script_relative_path'] = None
    
    # Detect project type
    project_types = []
    if os.path.exists(os.path.join(project_root, 'requirements.txt')):
        project_types.append('Python')
    if os.path.exists(os.path.join(project_root, 'package.json')):
        project_types.append('Node.js')
    if os.path.exists(os.path.join(project_root, '.git')):
        project_types.append('Git Repository')
    
    info['project_types'] = project_types if project_types else ['Unknown']
    
    # Common folders
    common_folders = ['src', 'test', 'tests', 'docs', 'dist', 'build', 'node_modules']
    info['has_folders'] = [f for f in common_folders if os.path.exists(os.path.join(project_root, f))]
    
    return info

def main():
    """Main function to display project information"""
    print("=" * 70)
    print("VS CODE PROJECT DETECTOR")
    print("=" * 70)
    print()
    
    info = get_project_info()
    
    print(f"📁 Project Root: {info['project_root']}")
    print(f"📂 Project Name: {info['project_name']}")
    print(f"📍 Script Location: {info['script_location']}")
    
    if info['script_relative_path']:
        print(f"📄 Script in Project: {info['script_relative_path']}")
    
    print(f"💻 Current Working Dir: {info['current_working_dir']}")
    print(f"🏗️  Project Type(s): {', '.join(info['project_types'])}")
    
    if info['has_folders']:
        print(f"📁 Found Folders: {', '.join(info['has_folders'])}")
    
    print()
    print("=" * 70)
    print("USAGE EXAMPLES:")
    print("=" * 70)
    print()
    print(f"from vscode_project import PROJECT_ROOT")
    print(f"config_file = os.path.join(PROJECT_ROOT, 'config.json')")
    print(f"data_dir = os.path.join(PROJECT_ROOT, 'data')")
    print()
    
    return info['project_root']

# For direct use in other scripts
PROJECT_ROOT = get_vscode_project_dir()

if __name__ == "__main__":
    PROJECT_ROOT = main()
else:
    # When imported as module
    print(f"✓ VS Code project detector loaded")
    print(f"  Project Root: {PROJECT_ROOT}")