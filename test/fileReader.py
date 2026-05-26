#!/usr/bin/env python3
"""
Directory Reader - Skip traversal for ignored directories but still show them
"""
import sys

import os
from datetime import datetime







def read_file(config):
    file_path = config['path']
    try:
        if not os.path.exists(file_path):
            return {"result": f"Error: File '{file_path}' does not exist."}
        if not os.path.isfile(file_path):
            return {"result": f"Error: '{file_path}' is not a file."}
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        return {"result": content}  
    except PermissionError:
        return {"result": "Error: Permission denied."}
    except UnicodeDecodeError:
        return {"result": "Error: Binary file."}
    except Exception as e:
        return {"result": f"Error: {e}"}


def write_file(config):
    file_path = config['path']
    data = config['data']
    try:
        directory = os.path.dirname(file_path)
        if directory and not os.path.exists(directory):
            return {"result": False}
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(data)
        return {"result": True}   
    except Exception as e:
        return {"result": False}




def get_project_root():
    current_file = os.path.abspath(__file__)
    current_dir = os.path.dirname(current_file)
    
    test_dir = current_dir
    while test_dir != os.path.dirname(test_dir):
        if os.path.exists(os.path.join(test_dir, '.vscode')):
            return test_dir
        if os.path.exists(os.path.join(test_dir, 'requirements.txt')) or \
           os.path.exists(os.path.join(test_dir, 'package.json')) or \
           os.path.exists(os.path.join(test_dir, '.git')):
            return test_dir
        test_dir = os.path.dirname(test_dir)
    
    if os.path.basename(current_dir).lower() in ['test', 'tests', 'testing']:
        return os.path.dirname(current_dir)
    
    return current_dir

def format_size(size_bytes):
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

def get_folder_size(folder_path, skip_dirs=None):
    
    if skip_dirs is None:
        skip_dirs = []
    
    total = 0
    try:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                try:
                    total += os.path.getsize(os.path.join(root, file))
                except:
                    pass
    except:
        pass
    return total

def scan_directory(path=None, skip_traversal=None):
    
    
    if path is None:
        path = get_project_root()
    
    if skip_traversal is None:
        skip_traversal = ['node_modules', '.nuxt', '.git', '__pycache__', 'dist', 'build', '.vscode']
    
    path = os.path.abspath(path)
    
    if not os.path.exists(path):
        return {
            "success": False,
            "error": f"Directory not found: {path}",
            "path": path
        }
    
    if not os.path.isdir(path):
        return {
            "success": False,
            "error": f"Not a directory: {path}",
            "path": path
        }
    
    result = {
        "success": True,
        "path": path,
        "name": os.path.basename(path),
        "total_files": 0,
        "total_folders": 0,
        "total_size_bytes": 0,
        "total_size_readable": "0 B",
        "skip_traversal": skip_traversal,
        "items": []
    }
    
    try:
        for root, dirs, files in os.walk(path):
            rel_path = os.path.relpath(root, path)
            if rel_path == '.':
                rel_path = ''
            
            current_dir_name = os.path.basename(root)
            if current_dir_name in skip_traversal and rel_path != '':
                continue
            
            for dir_name in dirs:
                dir_full_path = os.path.join(root, dir_name)
                will_skip = dir_name in skip_traversal
                
                if will_skip:
                    dir_size = 0
                    try:
                        for file in os.listdir(dir_full_path):
                            file_path = os.path.join(dir_full_path, file)
                            if os.path.isfile(file_path):
                                dir_size += os.path.getsize(file_path)
                    except:
                        pass
                else:
                    dir_size = get_folder_size(dir_full_path, skip_traversal)
                
                dir_info = {
                    "type": "directory",
                    "name": dir_name,
                    "path": dir_full_path,
                    "relative_path": os.path.join(rel_path, dir_name) if rel_path else dir_name,
                    "size_bytes": dir_size,
                    "size_readable": format_size(dir_size),
                    "skipped": will_skip
                }
                
                result["items"].append(dir_info)
                result["total_folders"] += 1
                result["total_size_bytes"] += dir_size
            
            for file_name in files:
                file_full_path = os.path.join(root, file_name)
                file_size = os.path.getsize(file_full_path) if os.path.exists(file_full_path) else 0
                
                file_info = {
                    "type": "file",
                    "name": file_name,
                    "path": file_full_path,
                    "relative_path": os.path.join(rel_path, file_name) if rel_path else file_name,
                    "extension": os.path.splitext(file_name)[1].lower(),
                    "size_bytes": file_size,
                    "size_readable": format_size(file_size),
                    "modified": datetime.fromtimestamp(os.path.getmtime(file_full_path)).strftime("%Y-%m-%d %H:%M:%S") if os.path.exists(file_full_path) else "Unknown"
                }
                result["items"].append(file_info)
                result["total_files"] += 1
                result["total_size_bytes"] += file_size
            
            dirs[:] = [d for d in dirs if d not in skip_traversal]
    
    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
    
    result["total_size_readable"] = format_size(result["total_size_bytes"])
    
    return result

def show_directory_structure(path=None, skip_traversal=None):
   
    result = scan_directory(path, skip_traversal)
    
    if not result.get("success"):
        return f"❌ Error: {result.get('error')}"
    
    output_lines = []
    output_lines.append("=" * 80)
    output_lines.append(f"📁 PROJECT: {result['name']}")
    output_lines.append("=" * 80)
    output_lines.append(f"📍 Location: {result['path']}")
    output_lines.append(f"📊 Summary: {result['total_files']} files, {result['total_folders']} folders")
    output_lines.append(f"💾 Total Size: {result['total_size_readable']}")
    output_lines.append(f"🚫 Skip Traversal: {', '.join(result['skip_traversal'])}")
    output_lines.append("=" * 80)
    output_lines.append("\n📋 CONTENTS (with adjacent path):")
    output_lines.append("-" * 80)
    
    # Show directories first
    folders = [i for i in result['items'] if i['type'] == 'directory']
    for folder in folders:
        skipped_flag = " [TRAVERSAL SKIPPED]" if folder.get('skipped') else ""
        line = f"📂 {folder['relative_path']}/".ljust(60) + f"{folder['size_readable']}{skipped_flag}"
        output_lines.append(line)
    
    
    files = [i for i in result['items'] if i['type'] == 'file']
    for file in files:
        line = f"📄 {file['relative_path']}".ljust(60) + f"{file['size_readable']}"
        output_lines.append(line)
    
    output_lines.append("=" * 80)
    
    return "\n".join(output_lines)


def gotFileWithInfo(config):
    structure = show_directory_structure(
        path=config['path'],
        skip_traversal=config['ignore'],
    )
    def analyze_project():
        result = show_directory_structure()
        return result
    project_info = analyze_project()
    return f"{structure}\n{project_info[:500]}"



