import re
import os

# ==============================
# CONFIG
# ==============================
VALID_EXTENSIONS = ("vue", "ts", "js", "css", "json", "jsx", "tsx", "html")


# ==============================
# FILENAME DETECTOR
# ==============================
def is_filename(line: str):
    line = line.strip()

    # 1. Numbered file: "1. file.vue" OR "1. Name - path/file.vue"
    match = re.match(
        r'^\d+\.\s+(?:.*?-\s*)?(.+\.(?:' + "|".join(VALID_EXTENSIONS) + r'))$',
        line
    )
    if match:
        return match.group(1).strip()

    # 2. Plain file: "pages/index.vue"
    if re.match(r'^[\w\-/\.]+\.(?:' + "|".join(VALID_EXTENSIONS) + r')$', line):
        return line

    # 3. File with comment: "app.vue (root)"
    match = re.match(
        r'^([\w\-/\.]+\.(?:' + "|".join(VALID_EXTENSIONS) + r'))\s*\(',
        line
    )
    if match:
        return match.group(1)

    return None






def extract_files_smart(text: str):
    blocks = {}
    lines = text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Try detect filename
        filename = is_filename(line)

        if not filename:
            i += 1
            continue

        i += 1

        # Skip language identifiers
        while i < len(lines) and lines[i].strip().lower() in [
            "vue", "js", "ts", "json", "css", "html",
            "javascript", "typescript"
        ]:
            i += 1

        # Skip opening ```
        if i < len(lines) and lines[i].strip().startswith("```"):
            i += 1

        code_lines = []

        while i < len(lines):
            current_line = lines[i]
            stripped = current_line.strip()

            # 🚨 Stop ONLY if a NEW file starts
            next_file = is_filename(stripped)
            if next_file:
                break

            # Skip closing ```
            if stripped.startswith("```"):
                i += 1
                continue

            code_lines.append(current_line)
            i += 1

        # Clean code
        code = "\n".join(code_lines).strip()

        if code:
            blocks[filename] = code

    return blocks


def save_files(blocks, output_dir="extracted_code"):
    os.makedirs(output_dir, exist_ok=True)

    print("\n" + "=" * 60)
    print(f"Found {len(blocks)} files")
    print("=" * 60)

    for filename in sorted(blocks.keys()):
        code = blocks[filename]

        filepath = os.path.join(output_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)

        print(f"✓ {filepath} ({len(code.splitlines())} lines)")

    print("\n✅ Extraction completed successfully!")



def vueBlockBuilder(config):
  blocks = extract_files_smart(config['text'])
  save_files(blocks)



prompt = '''











'''
vueBlockBuilder(prompt)