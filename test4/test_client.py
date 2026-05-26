"""
Quick WebSocket test client.
Usage: python test_client.py "create a next.js app with tailwind"
"""
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8000/ws/chat"

COLORS = {
    "routing": "\033[96m",   
    "token":   "\033[97m",   
    "done":    "\033[92m",   
    "error":   "\033[91m",   
    "reset":   "\033[0m",
}

async def chat(prompt: str, model: str = "k10"):
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"prompt": prompt, "model": model}))
        print(f"\n{'─'*60}")
        print(f"Prompt: {prompt} | Model: {model}")
        print(f"{'─'*60}\n")

        async for raw in ws:
            msg  = json.loads(raw)
            kind = msg["type"]
            data = msg["data"]

            if kind == "routing":
                print(f"{COLORS['routing']}[ROUTER]{COLORS['reset']}")
                print(f"  Domain    : {data.get('domain')}")
                print(f"  Subdomain : {data.get('subdomain')}")
                print(f"  Agent     : {data.get('agent')}")
                print(f"  Reason    : {data.get('domain_reason')}")
                print(f"\n{COLORS['token']}[AGENT RESPONSE]{COLORS['reset']}")

            elif kind == "token":
                print(data, end="", flush=True)

            elif kind == "done":
                print(f"\n\n{COLORS['done']}[DONE]{COLORS['reset']}")
                break

            elif kind == "error":
                print(f"\n{COLORS['error']}[ERROR] {data}{COLORS['reset']}")
                break

if __name__ == "__main__":
    prompt = "create a next js project"
    model = "k10"
    if len(sys.argv) > 1:
        prompt = sys.argv[1]
    if len(sys.argv) > 2:
        model = sys.argv[2]
    asyncio.run(chat(prompt, model))
