import asyncio
import argparse
import json
import sys

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

SERVER = "ws://localhost:8000/ws"
REST   = "http://localhost:8000/chat"

COLORS = {
    "thinking": "\033[36m",   # cyan
    "routing":  "\033[33m",   # yellow
    "stream":   "\033[0m",    # white
    "done":     "\033[32m",   # green
    "error":    "\033[31m",   # red
    "reset":    "\033[0m",
}

def color(text, event):
    c = COLORS.get(event, COLORS["reset"])
    return f"{c}{text}{COLORS['reset']}"


async def run_ws(prompt: str):
    print(f"\n🔌 Connecting to {SERVER} …")
    async with websockets.connect(SERVER) as ws:
        await ws.send(json.dumps({"prompt": prompt}))
        print(f"📤 Sent: {prompt!r}\n")
        print("─" * 60)

        async for raw in ws:
            msg = json.loads(raw)
            event = msg.get("event", "")
            data  = msg.get("data", {})
            step  = msg.get("step", "")

            if event == "thinking":
                print(color(f"\n⚙  [{step}] {data.get('message', '')}", "thinking"))

            elif event == "routing":
                lvl  = data.get("level", step)
                dec  = data.get("decision", "?")
                why  = data.get("reason", "")
                conf = data.get("confidence", 0)
                print(color(
                    f"🔀 [{lvl}] → {dec}  (conf={conf:.2f})  reason: {why}",
                    "routing"
                ))

            elif event == "stream":
                # Print chunks inline without newlines until done
                print(data.get("chunk", ""), end="", flush=True)

            elif event == "done":
                print()
                print(color(
                    f"\n✅ Done — agent={data.get('agent')}  "
                    f"category={data.get('category')}  "
                    f"chars={data.get('length', 0)}",
                    "done"
                ))
                break

            elif event == "error":
                print(color(f"\n❌ Error: {data.get('message', '')}", "error"))
                break

    print("─" * 60)


async def run_rest(prompt: str):
    if not HAS_HTTPX:
        print("pip install httpx  (needed for REST mode)")
        sys.exit(1)
    print(f"\n📡 POST {REST}\n   prompt: {prompt!r}\n")
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(REST, json={"prompt": prompt})
        data = r.json()
    print("─" * 60)
    for route in data.get("routing", []):
        d = route.get("data", {})
        print(f"🔀 [{d.get('level','?')}] → {d.get('decision','?')}  reason: {d.get('reason','')}")
    print()
    print(data.get("response", ""))
    print("─" * 60)
    print(f"Meta: {data.get('meta', {})}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="")
    parser.add_argument("--rest",   action="store_true")
    args = parser.parse_args()

    if not args.prompt:
        print("Example prompts to try:")
        prompts = [
            "create a next.js project with typescript and tailwind",
            "generate 10 interesting sentences about artificial intelligence",
            "what is the difference between REST and GraphQL?",
            "create a nuxt 3 app with pinia and tailwind",
            "write a Python FastAPI server with user auth",
            "analyse this CSV and find outliers",
        ]
        for i, p in enumerate(prompts, 1):
            print(f"  {i}. {p}")
        args.prompt = input("\nPaste or type your prompt: ").strip()
        if not args.prompt:
            sys.exit(0)

    if args.rest:
        asyncio.run(run_rest(args.prompt))
    else:
        asyncio.run(run_ws(args.prompt))