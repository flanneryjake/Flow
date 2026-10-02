"""The free lane for Jarvis training data: add files without asking Jake, logged and undoable.

  python training_intake.py add <file-or-folder> [...] --topic fc-curriculum --source "where it came from" [--by claude]
  python training_intake.py list [--hours 24] [--json]
  python training_intake.py revert <batch-id>

Files are copied into <training dir>\\<topic>\\. Anything overwritten is snapshotted first, and every batch is
written to <training dir>\\_log\\intake.jsonl, which the 7 AM report reads. A batch that contains secrets or
patient identifiers is refused whole (see GUARDRAILS.md, hard stops).

Training dir: JARVIS_TRAINING_DIR, default C:\\Jarvis\\training. Standard library only.
Exit code 0 = done, 1 = refused (hard stop), 2 = bad input.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys

ROOT = os.environ.get("JARVIS_TRAINING_DIR", r"C:\Jarvis\training")
LOG_DIR = os.path.join(ROOT, "_log")
LOG = os.path.join(LOG_DIR, "intake.jsonl")
SNAPSHOTS = os.path.join(LOG_DIR, "snapshots")

TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".html", ".htm", ".xml", ".modelfile"}
OTHER_EXT = {".pdf", ".docx"}  # accepted, but can't be scanned; logged as scanned=false
MAX_BYTES = 50 * 1024 * 1024

SECRETS = [
    (r"\bntn_[A-Za-z0-9]{30,}", "Notion token"),
    (r"\bsecret_[A-Za-z0-9]{30,}", "Notion token"),
    (r"\bAIza[0-9A-Za-z_\-]{30,}", "Google API key"),
    (r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}", "API key"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}", "GitHub token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"(?i)\bpassword\s*[:=]\s*\S{6,}", "password"),
]
PATIENT_IDS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "SSN-shaped number"),
    (r"(?i)\b(?:DOB|date of birth)\s*[:#]?\s*\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}", "date of birth"),
    (r"(?i)\b(?:MRN|medical record (?:number|no\.?|#))\s*[:#]?\s*[A-Z0-9\-]{5,}", "medical record number"),
    (r"(?i)\bpatient name\s*[:=]\s*[A-Z][a-z]+", "patient name"),
]


def now():
    return datetime.datetime.now().astimezone()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(path):
    """Return a list of hard-stop problems in a text file (empty = clean)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    found = []
    for pattern, label in SECRETS + PATIENT_IDS:
        if re.search(pattern, text):
            found.append(label)
    return found


def collect(paths):
    """(source file, path relative to its input) for every file under the inputs."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            base = os.path.abspath(p)
            for dirpath, _, names in os.walk(p):
                for n in sorted(names):
                    full = os.path.join(dirpath, n)
                    out.append((full, os.path.relpath(full, base)))
        elif os.path.isfile(p):
            out.append((p, os.path.basename(p)))
        else:
            print(f"Not found: {p}")
            sys.exit(2)
    return out


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._\-]+", "-", s).strip("-") or "misc"


def read_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_log(rec):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def cmd_add(a):
    topic = safe_name(a.topic)
    files = collect(a.paths)
    if not files:
        print("Nothing to add.")
        return 2

    problems, skipped, plan = [], [], []
    for src, rel in files:
        ext = os.path.splitext(src)[1].lower()
        if ext not in TEXT_EXT | OTHER_EXT:
            skipped.append(f"{rel} (type {ext or 'none'} not accepted)")
            continue
        if os.path.getsize(src) > MAX_BYTES:
            skipped.append(f"{rel} (over 50 MB)")
            continue
        scanned = ext in TEXT_EXT
        if scanned:
            for label in scan(src):
                problems.append(f"{rel}: {label}")
        plan.append((src, rel, scanned))

    if problems:
        print("REFUSED: this batch breaks a hard stop (GUARDRAILS.md). Nothing was added.")
        for p in problems:
            print("  " + p)
        append_log({"event": "refused", "at": now().isoformat(timespec="seconds"), "topic": topic,
                    "source": a.source, "by": a.by, "problems": problems})
        return 1
    if not plan:
        print("Nothing accepted. Skipped: " + "; ".join(skipped))
        return 2

    taken = {r.get("batch") for r in read_log()}
    stamp = now().strftime("%Y%m%d-%H%M%S")
    batch, n = f"{stamp}-{topic}", 2
    while batch in taken:
        batch, n = f"{stamp}-{topic}-{n}", n + 1
    dest_root = os.path.join(ROOT, topic)
    entries = []
    for src, rel, scanned in plan:
        dest = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        replaced = os.path.exists(dest)
        if replaced:
            snap = os.path.join(SNAPSHOTS, batch, topic, rel)
            os.makedirs(os.path.dirname(snap), exist_ok=True)
            shutil.copy2(dest, snap)
        shutil.copy2(src, dest)
        entries.append({"path": os.path.relpath(dest, ROOT), "sha256": sha256(dest),
                        "bytes": os.path.getsize(dest), "replaced": replaced, "scanned": scanned})

    append_log({"event": "add", "batch": batch, "at": now().isoformat(timespec="seconds"), "topic": topic,
                "source": a.source, "by": a.by, "files": entries, "skipped": skipped})
    print(f"Added batch {batch}: {len(entries)} file(s) to {dest_root}")
    for s in skipped:
        print("  skipped " + s)
    return 0


def cmd_list(a):
    since = now() - datetime.timedelta(hours=a.hours)
    recs = [r for r in read_log() if datetime.datetime.fromisoformat(r["at"]) >= since]
    reverted = {r["batch"] for r in read_log() if r.get("event") == "revert"}
    if a.json:
        print(json.dumps(recs, indent=2, ensure_ascii=False))
        return 0
    if not recs:
        print(f"No training intake in the last {a.hours} h.")
    for r in recs:
        if r["event"] == "add":
            state = " (reverted)" if r["batch"] in reverted else ""
            print(f"{r['at']}  {r['batch']}{state}: {len(r['files'])} file(s), {r['source']} [by {r['by']}]")
        elif r["event"] == "refused":
            print(f"{r['at']}  REFUSED {r['topic']}: {'; '.join(r['problems'])}")
        elif r["event"] == "revert":
            print(f"{r['at']}  reverted {r['batch']}")
    return 0


def cmd_revert(a):
    rec = next((r for r in read_log() if r.get("event") == "add" and r["batch"] == a.batch), None)
    if not rec:
        print(f"No batch {a.batch} in {LOG}")
        return 2
    kept = []
    for e in rec["files"]:
        dest = os.path.join(ROOT, e["path"])
        snap = os.path.join(SNAPSHOTS, a.batch, e["path"])
        if os.path.exists(dest) and sha256(dest) != e["sha256"]:
            kept.append(e["path"] + " (changed since, left alone)")
            continue
        if e["replaced"] and os.path.exists(snap):
            shutil.copy2(snap, dest)
        elif os.path.exists(dest):
            os.remove(dest)
    append_log({"event": "revert", "batch": a.batch, "at": now().isoformat(timespec="seconds"), "kept": kept})
    print(f"Reverted {a.batch}.")
    for k in kept:
        print("  kept " + k)
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("paths", nargs="+")
    add.add_argument("--topic", required=True)
    add.add_argument("--source", required=True, help="where the material came from")
    add.add_argument("--by", default="claude", help="who added it: claude, worker, rig, gemini, jake")
    ls = sub.add_parser("list")
    ls.add_argument("--hours", type=float, default=24)
    ls.add_argument("--json", action="store_true")
    rv = sub.add_parser("revert")
    rv.add_argument("batch")
    a = p.parse_args()
    return {"add": cmd_add, "list": cmd_list, "revert": cmd_revert}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
