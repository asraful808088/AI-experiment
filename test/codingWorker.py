import ollama
import json
import re
import os
import sys
import shutil
from experiment.test.workers import Workers

MODEL = 'llama3:latest'

IGNORE_DIRS     = {"node_modules", ".git", ".nuxt", "dist", "__pycache__", ".vscode", "build", ".output"}
CODE_EXTENSIONS = {".vue", ".ts", ".js", ".jsx", ".tsx", ".css", ".json", ".html", ".py"}

# Content signatures to detect wrong file extensions
# key = string found in file content (lowercase), value = correct extension
CONTENT_SIGNATURES = [
    ("<template",      ".vue"),
    ("<!doctype html", ".html"),
    ("<html",          ".html"),
    ("def ",           ".py"),
    ("print(",         ".py"),
    ("class ",         ".py"),
    ("import vue",     ".js"),
    ("const ",         ".js"),
    ("export default", ".js"),
]

# ==============================
# PROMPTS
# ==============================

# Model only picks from a list Python already verified exists on disk
FOLDER_PICKER_PROMPT = """The user made a request. Below is a list of real folders found on disk.
Pick the ONE folder the user wants to work on.
Return ONLY JSON: {"folder": "/exact/path/from/the/list"}
If none match well, return the most likely one.
NEVER invent or modify paths. Only return paths from the provided list."""

ANALYZE_PROMPT = """Analyze this file and return ONLY a JSON object:
{"has_issues": true or false, "issues": ["short description of each issue"]}
No markdown. No explanation. Only the JSON."""

FIX_PROMPT = """Fix the file below. Return ONLY the corrected file content.
RULES (each violation breaks the output):
- NO backticks, NO markdown fences, NO triple-backticks of any kind
- NO explanation text before or after
- NO comments about your changes  
- .vue files: first line must be <template> or <script>
- .py files: first line must be an import or code statement
- .html files: first line must be <!DOCTYPE or <html
- Output goes byte-for-byte to disk
Fix all issues. If correct already, return as-is."""


# ==============================
# HELPERS
# ==============================

def call_model(messages, stream=False, callback=None):
    if stream:
        result = []
        for chunk in ollama.chat(model=MODEL, messages=messages, stream=True, keep_alive=-1):
            token = chunk['message']['content']
            result.append(token)
            if callback:
                callback(token)
        if callback:
            callback('\n')
        return ''.join(result)
    else:
        resp = ollama.chat(model=MODEL, messages=messages, stream=False, keep_alive=-1)
        return resp['message']['content']


def parse_json(text):
    text = re.sub(r'```(?:json)?', '', text).strip().rstrip('`').strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {}


def strip_fences(text):
    """Strip all markdown fences the model adds despite instructions."""
    text = text.strip()
    for _ in range(5):
        lines = text.splitlines()
        changed = False
        if lines and lines[0].startswith('`'):
            lines = lines[1:]
            changed = True
        if lines and lines[-1].strip().startswith('`'):
            lines = lines[:-1]
            changed = True
        text = '\n'.join(lines).strip()
        if not changed:
            break
    return text


def detect_correct_ext(content, current_ext):
    """Return the extension the file should have based on its content."""
    first = content.strip()[:500].lower()
    for signature, correct_ext in CONTENT_SIGNATURES:
        if signature in first:
            return correct_ext
    return current_ext


def find_all_real_dirs(prompt):
    """
    Pull every path-like substring from the prompt.
    Return only the ones that are real directories on disk, longest first.
    """
    # Windows: C:\foo\bar  or  C:/foo/bar
    win  = re.findall(r'[A-Za-z]:[\\\/][^\s"\'<>\n]+', prompt)
    # Unix: /foo/bar
    unix = re.findall(r'\/[^\s"\'<>\n]{3,}', prompt)

    candidates = set()
    for raw in win + unix:
        raw = raw.strip()
        # try the full match and progressively shorter prefixes
        # (handles trailing punctuation / extra words glued to path)
        for end in range(len(raw), 2, -1):
            candidates.add(raw[:end].rstrip('/\\.,;: '))

    real = []
    seen = set()
    for c in sorted(candidates, key=len, reverse=True):
        try:
            if os.path.isdir(c) and c not in seen:
                real.append(c)
                seen.add(c)
        except Exception:
            pass
    return real


def pick_folder(prompt, real_dirs, callback=None):
    """Ask the model to pick the best folder from the verified list."""
    def emitln(t):
        if callback:
            callback(t + '\n')

    if not real_dirs:
        return None
    if len(real_dirs) == 1:
        return real_dirs[0]

    emitln(f"🗂  Picking best folder from {len(real_dirs)} candidates...")
    raw = call_model([
        {"role": "system", "content": FOLDER_PICKER_PROMPT},
        {"role": "user",   "content": f"Request: {prompt}\n\nFolders on disk:\n" + "\n".join(real_dirs)}
    ])
    result  = parse_json(raw)
    picked  = result.get("folder", "").strip()

    # validate it's from our real list
    if picked and os.path.isdir(picked) and picked in real_dirs:
        return picked

    return real_dirs[0]   # fallback: most specific path


def walk_all_files(folder):
    """Return every file under folder, skipping ignored dirs."""
    files = []
    for root, dirs, filenames in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for name in filenames:
            files.append(os.path.join(root, name))
    return files


# ==============================
# PER-FILE PIPELINE
# ==============================

def process_file(file_path, prompt, worker, callback=None):
    def emit(t):
        if callback:
            callback(t)
        else:
            print(t, end='', flush=True)
    def emitln(t=''):
        emit(t + '\n')

    current_ext = os.path.splitext(file_path)[1].lower()

    # 1. READ
    result_obj = worker.allFunctonalTools("READ_FILE", {"path": file_path})
    content = str(result_obj.get("result", "")) if result_obj else ""

    if not content or content.startswith("Error:"):
        emitln(f"   ⚠️  Unreadable")
        return "unreadable"

    # 2. EXTENSION CHECK — fix wrong extension before anything else
    correct_ext = detect_correct_ext(content, current_ext)
    if correct_ext != current_ext:
        new_path = os.path.splitext(file_path)[0] + correct_ext
        emitln(f"   🏷  Wrong extension  {current_ext} → {correct_ext}")
        try:
            shutil.move(file_path, new_path)
            emitln(f"   📁 Renamed → {os.path.basename(new_path)}")
            file_path    = new_path
            current_ext  = correct_ext
        except Exception as e:
            emitln(f"   ❌ Rename failed: {e}")
            return "write_error"

    # 3. SKIP non-code files
    if current_ext not in CODE_EXTENSIONS:
        emitln(f"   ⏭  Skipped (not a code file)")
        return "clean"

    # 4. ANALYZE
    analysis_raw = call_model([
        {"role": "system", "content": ANALYZE_PROMPT},
        {"role": "user",   "content": f"File: {file_path}\nType: {current_ext}\n\n{content}"}
    ])
    analysis = parse_json(analysis_raw)

    if not analysis.get("has_issues", False):
        emitln(f"   ✅ Clean")
        return "clean"

    issues = analysis.get("issues", [])
    emitln(f"   🐛 {', '.join(issues)}")

    # 5. FIX
    emitln(f"   🔧 Fixing...")
    fixed_raw = call_model(
        messages=[
            {"role": "system", "content": FIX_PROMPT},
            {"role": "user", "content": (
                f"File: {file_path}\n"
                f"Type: {current_ext}\n"
                f"Issues: {', '.join(issues)}\n\n"
                f"--- CONTENT ---\n{content}\n--- END ---\n\n"
                f"Fixed file content:"
            )}
        ],
        stream=True,
        callback=callback
    )
    fixed = strip_fences(fixed_raw)

    # 6. WRITE
    write_result = worker.allFunctonalTools("WRITE_FILE", {"path": file_path, "data": fixed})
    if write_result and write_result.get("result") is not False:
        emitln(f"   💾 Saved")
        return "fixed"
    else:
        emitln(f"   ❌ Write failed")
        return "write_error"


# ==============================
# CORE AGENT
# ==============================

def run_agent(prompt, callback=None):
    def emit(t):
        if callback:
            callback(t)
        else:
            print(t, end='', flush=True)
    def emitln(t=''):
        emit(t + '\n')

    worker = Workers()

    emitln(f"\n{'='*60}")
    emitln(f"🤖 Agent started")
    emitln(f"🎯 {prompt}")
    emitln(f"{'='*60}")

    # Step 1 — find real dirs in prompt, let model pick the right one
    real_dirs = find_all_real_dirs(prompt)

    if not real_dirs:
        emitln("❌ No valid folder found in your prompt.")
        emitln("   Just include the path anywhere, e.g:  fix E:\\project\\myapp\\src")
        return

    folder = pick_folder(prompt, real_dirs, callback)
    if not folder:
        emitln("❌ Could not determine target folder.")
        return

    emitln(f"\n📁 Target: {folder}")

    # Step 2 — collect every file
    files = walk_all_files(folder)
    if not files:
        emitln(f"⚠️  No files found in {folder}")
        return

    emitln(f"📂 {len(files)} files\n")
    for f in files:
        emitln(f"   • {os.path.relpath(f, folder)}")
    emitln()

    # Step 3 — process each file
    stats = {"clean": 0, "fixed": 0, "renamed": 0, "unreadable": 0, "write_error": 0}

    for i, file_path in enumerate(files, 1):
        rel = os.path.relpath(file_path, folder)
        emitln(f"\n[{i}/{len(files)}] {rel}")
        status = process_file(file_path, prompt, worker, callback)
        stats[status] = stats.get(status, 0) + 1

    # Step 4 — summary
    emitln(f"\n{'='*60}")
    emitln(f"✅ Done — {len(files)} files processed")
    emitln(f"   Clean      : {stats['clean']}")
    emitln(f"   Fixed      : {stats['fixed']}")
    emitln(f"   Renamed    : {stats['renamed']}")
    emitln(f"   Unreadable : {stats['unreadable']}")
    emitln(f"   Write error: {stats['write_error']}")
    emitln(f"{'='*60}")
    return stats


# ==============================
# PUBLIC API
# ==============================

def codingWorker(prompt, callback=None):
    """
    prompt   : natural language — just include the folder path somewhere in it.
               Examples:
                 "PS E:\\project\\shop_m\\lyren3\\demo  fix test3 dir all files"
                 "fix all files in E:\\project\\myapp\\src"
    callback : called with every text chunk (str). stdout if None.
    """
    return run_agent(prompt, callback)


def startbot():
    
    codingWorker(' PS E:\project\shop_m\lyren3\demo> this is  my path you check all  files and if find error then  fix it  ')


if __name__ == "__main__":
    startbot()