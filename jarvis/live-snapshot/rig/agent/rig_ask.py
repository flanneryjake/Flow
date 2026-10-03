"""Ask the rig's local model (card #437: drafting goes to the rig, not Claude). Stdlib only.

    python rig_ask.py "Write a one-page guide to X" -f notes.md -f STATUS.md -o draft.md

Uses OLLAMA_URL if set, else the rig's tailnet address, else 127.0.0.1. Exit 0 ok, 3 rig unreachable/failed.
"""
import argparse
import json
import os
import sys
import urllib.request

HOSTS = [os.environ.get("OLLAMA_URL", ""), "http://100.96.134.64:11434", "http://127.0.0.1:11434"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("prompt")
    p.add_argument("-f", "--file", action="append", default=[])
    p.add_argument("-o", "--out", required=True)
    p.add_argument("-m", "--model", default=os.environ.get("JARVIS_RIG_MODEL", "qwen3.6:35b"))
    a = p.parse_args()
    prompt = a.prompt
    for f in a.file:
        with open(f, encoding="utf-8", errors="replace") as h:
            prompt += f"\n\n--- {os.path.basename(f)} ---\n" + h.read()[:20000]
    body = json.dumps({"model": a.model, "prompt": prompt, "stream": False, "think": False,
                       "options": {"num_ctx": 32768}}).encode()
    for host in filter(None, HOSTS):
        try:
            req = urllib.request.Request(host.rstrip("/") + "/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as r:
                text = json.loads(r.read())["response"]
            with open(a.out, "w", encoding="utf-8") as h:
                h.write(text + f"\n\n_drafted by Jarvis (rig), not verified_\n")
            print(f"ok: {a.out} ({len(text)} chars, {host})")
            return 0
        except Exception as e:
            print(f"{host}: {e}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
