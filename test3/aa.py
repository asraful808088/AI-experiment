import ollama
import re

MODEL = "qwen2.5-coder:7b"
KEEP_ALIVE = -1

AI_PROMPT = """Extract ONLY the Windows file path from the text.
Rules:
- Output ONLY the path, nothing else
- Fix typos (proect -> project)
- Convert natural language to path (e.g. "proect in c drive" -> C:\\project)
- Format: drive:\\folder\\subfolder
- If only a drive is mentioned, return just that (e.g. C:\\)
- Never add explanation or extra text"""

# ──────────────────────────────────────────────
# Regex strategies (handles actual path strings)
# ──────────────────────────────────────────────
REGEX_PATTERNS = [
    # Full path: C:\folder\sub\file.txt
    r'[A-Za-z]:\\(?:[^\\\s<>|:\"]+\\)*[^\\\s<>|:\"]*',

    # Drive + path until whitespace: C:\something
    r'[A-Za-z]:\\[^\s]+',

    # Path after keywords like "in", "at", "inside"
    r'(?:in|at|inside)\s+([A-Za-z]:\\[^\s]+)',
]

def fix_typos(path: str) -> str:
    replacements = {
        'proect': 'project',
        'Proect': 'Project',
        'foldr':  'folder',
        'deskt0p': 'desktop',
    }
    for wrong, right in replacements.items():
        path = path.replace(wrong, right)
    return path

def clean_path(path: str) -> str:
    path = path.strip()
    path = re.sub(r'[>|]+$', '', path)   # remove trailing > or |
    path = path.strip('"').strip("'")     # remove surrounding quotes
    path = fix_typos(path)
    return path

def try_regex(text: str):
    for pattern in REGEX_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            path = matches[0]
            # findall returns tuples when there are groups
            if isinstance(path, tuple):
                path = next((p for p in path if p), "")
            path = clean_path(path)
            if re.match(r'[A-Za-z]:\\', path):
                return path
    return None

# ──────────────────────────────────────────────
# AI fallback (handles natural language inputs)
# ──────────────────────────────────────────────
def try_ai(text: str):
    print("⚠️  Regex found nothing — falling back to AI...")
    response = ollama.chat(
        model=MODEL,
        keep_alive=KEEP_ALIVE,
        messages=[
            {"role": "system", "content": AI_PROMPT},
            {"role": "user",   "content": text},
        ],
    )
    raw = response["message"]["content"].strip()
    path = clean_path(raw)
    return path if re.match(r'[A-Za-z]:\\', path) else None

# ──────────────────────────────────────────────
# Main extractor
# ──────────────────────────────────────────────
def extract_path(text: str) -> tuple[str | None, str]:
    path = try_regex(text)
    if path:
        return path, "regex"

    path = try_ai(text)
    if path:
        return path, "ai"

    return None, "failed"

# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────
tests = [
    "proect in c drive",                              
    "my files are at E:\\proect\\shop_m\\lyren3",    
    "PS C:\\Users\\dev> cd project",                  
    "open the folder C:\\Users\\Desktop\\notes.txt",  
    "project is inside D drive in shop folder in mama in asdasd",          
]

for text in tests:
    path, method = extract_path(text)
    print(f"Input  : {text!r}")
    print(f"Path   : {path}")
    print(f"Method : {method}")
    print("-" * 50)