import ollama
import requests

# ── Config ────────────────────────────────────────────────────────────────────
URL        = "https://nextjs.org/docs/app/getting-started/installation.md"
MODEL      = "qwen2.5-coder:7b"
KEEP_ALIVE = -1
# ─────────────────────────────────────────────────────────────────────────────

try:
    # 1. Fetch the markdown
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/markdown, text/plain, */*'
    }
    print(f"Fetching: {URL}")
    response = requests.get(url=URL, headers=headers, timeout=10)
    response.raise_for_status()

    markdown_content = response.text

    print("=" * 80)
    print(f"Fetched {len(markdown_content)} characters of markdown")
    print("=" * 80)

    # 2. Ask Ollama to extract commands from clean markdown
    print("\n" + "=" * 80)
    print("ASKING OLLAMA TO EXTRACT COMMANDS...")
    print("=" * 80)

    ai_response = ollama.chat(
        model=MODEL,
        keep_alive=KEEP_ALIVE,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a command extractor. "
                    "You read markdown documentation and output ONLY shell commands. "
                    "No explanations. No markdown formatting. No code fences. "
                    "No extra text. Just the commands, one per line, exactly as written."
                )
            },
            {
                "role": "user",
                "content": f"""Read the markdown below and extract every shell command (lines that start with npx, npm, yarn, pnpm, bun, or cd).

Rules:
- Copy each command EXACTLY as it appears in the markdown — do not change anything
- One command per line
- No bullet points, no numbering, no labels, no explanations
- Do not add commands that are not in the markdown
- Do not use your training knowledge

MARKDOWN:
{markdown_content}"""
            },
        ],
    )

    raw_output = ai_response["message"]["content"]

    print("\n" + "=" * 80)
    print("EXTRACTED COMMANDS")
    print("=" * 80)
    print(raw_output)

except requests.exceptions.RequestException as e:
    print(f"Error fetching the page: {e}")
except Exception as e:
    print(f"An error occurred: {e}")