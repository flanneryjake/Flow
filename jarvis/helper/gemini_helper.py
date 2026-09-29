"""Cheap second opinion for Jarvis: research and proofreading on Gemini's free tier,
so Claude only does the real work and the final fixes.

  python gemini_helper.py proofread draft.md [-o review.md]
  python gemini_helper.py research "best free OCR libraries for Python" [-o notes.md]

Reads the key from the GEMINI_API_KEY environment variable. Standard library only.
Exit code 0 = answer on stdout (or in -o), 2 = no key, 3 = every model failed.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Tried in order. On 2026-09-29 the free key answered on these two; flash-latest
# was often 503 (busy) and Google Search grounding returned 429 (no free quota).
MODELS = ["gemini-3.5-flash", "gemini-flash-lite-latest"]
NO_SEARCH = [False]

PROMPTS = {
    "proofread": (
        "You are proofreading a document an AI assistant wrote. List concrete problems only: "
        "factual claims that look wrong or unsupported, unclear sentences, typos, missing steps, "
        "and inconsistencies. For each, quote the passage and give the fix. End with a one-line "
        "verdict: READY or NEEDS FIXES. Do not rewrite the whole document.\n\n---\n\n{text}"
    ),
    "research": (
        "Research this for a small home automation / side-business project. Give a short answer, "
        "then a list of specific resources (name, link if you know it, why it fits, cost). "
        "Mark anything you are unsure of as unverified.\n\nQuestion: {text}"
    ),
}


def call(model, key, prompt, search):
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if search:
        body["tools"] = [{"google_search": {}}]
    req = urllib.request.Request(
        API.format(model=model),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.load(r)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def ask(key, prompt, search):
    errors = []
    for model in MODELS:
        # Search first when asked; fall back to plain generation if search has no quota.
        for use_search in ([True, False] if search and not NO_SEARCH[0] else [False]):
            for attempt in range(2):
                try:
                    return model, use_search, call(model, key, prompt, use_search)
                except urllib.error.HTTPError as e:
                    errors.append(f"{model} search={use_search}: HTTP {e.code}")
                    if use_search and e.code == 429:
                        NO_SEARCH[0] = True  # no search quota; don't retry it on the next model
                    if e.code == 503 and attempt == 0:
                        time.sleep(5)
                        continue
                    break
                except (urllib.error.URLError, KeyError, IndexError, TimeoutError, OSError) as e:
                    errors.append(f"{model} search={use_search}: {e!r}")
                    break
    raise RuntimeError("; ".join(errors))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=PROMPTS)
    p.add_argument("input", help="file to proofread, or the research question")
    p.add_argument("-o", "--out")
    a = p.parse_args()

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2
    text = a.input
    if a.mode == "proofread":
        with open(a.input, encoding="utf-8") as f:
            text = f.read()
    try:
        model, searched, answer = ask(key, PROMPTS[a.mode].format(text=text), a.mode == "research")
    except RuntimeError as e:
        print(f"Gemini failed: {e}", file=sys.stderr)
        return 3
    footer = f"\n\n_{a.mode} by {model}{' with Google Search' if searched else ''}_\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(answer + footer)
    else:
        sys.stdout.write(answer + footer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
