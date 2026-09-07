r"""
Reads the extracted course documents and pulls out every deadline.

    python -m pip install anthropic

Put your API key in a file called api_key.txt next to this script.
It is gitignored, so it never gets committed or shown in a terminal.

    python find_dates.py              compare two models on the syllabi
    python find_dates.py --all        read every document with one model

Run from inside the scraper folder.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic

HERE = Path(__file__).parent
EXTRACTED = HERE / "extracted"
COLLECTED = HERE / "collected.json"
OUT = HERE / "found_dates.json"
KEY_FILE = HERE / "api_key.txt"

MODELS = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "time": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["assignment", "midterm", "final_exam", "quiz",
                                 "lab", "reading", "presentation", "other"],
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "source_excerpt": {"type": "string"},
                },
                "required": ["title", "date", "time", "kind", "confidence", "source_excerpt"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["dates"],
    "additionalProperties": False,
}

PROMPT = """You are reading a university course document to find every date a student must act on.

Course: {course}
Document: {doc}
Today is {today}. This is the Fall 2026 term at the University of Ottawa \
(classes early September to early December, final exams mid-December).

Find EVERY deadline, exam, test, quiz, lab, assignment due date, presentation, \
and milestone mentioned anywhere in the text below.

Rules:
- Favour finding too many over missing one. A student can dismiss a wrong date in \
one click; a missed midterm costs them a grade. When unsure, include it.
- Dates are often buried mid-paragraph, in a schedule table, or in a weekly \
breakdown. Read all of it, not just headings.
- For a relative date like "the Friday after reading week" or "week 7", resolve it \
to a real calendar date if you reasonably can, and mark confidence "low". Put the \
original wording in source_excerpt.
- Use YYYY-MM-DD for date. If the year is not stated, infer it from the term.
- Use 24-hour HH:MM for time, or "" if no time is given.
- source_excerpt must be the actual sentence or table row the date came from, \
copied verbatim, so the student can check it.
- Weekly recurring lectures, tutorials, and DGD sessions with nothing to hand in are NOT deadlines. Skip those. A lab only counts if there is a report or a submission.
- NEVER invent a date. If the document says an assignment is due weekly but gives no actual dates, do not generate a series of dates by counting forward. Report only dates the document states.
- Every date you return must appear in, or be directly stated by, the text. The source_excerpt must contain the evidence. If you cannot quote evidence, leave it out.
- If the document contains no deadlines at all, return an empty list. An empty list is a correct and useful answer.

--- DOCUMENT TEXT ---
{text}
--- END ---"""

DATEISH = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|due|deadline|midterm|exam|quiz|submit|week\s*\d+"
    r"|\d{1,2}[/-]\d{1,2}|\d{4}-\d{2}-\d{2})",
    re.I,
)


def looks_dated(text):
    """Cheap filter -- skip documents with no date-like language at all."""
    return len(DATEISH.findall(text)) >= 2


def ask(client, model, course, doc, text):
    """One document, one model. Returns (dates, input_tokens, output_tokens)."""
    text = text[:150_000]
    try:
        r = client.messages.create(
            model=model,
            max_tokens=8000,
            messages=[{
                "role": "user",
                "content": PROMPT.format(
                    course=course, doc=doc,
                    today=datetime.now().strftime("%Y-%m-%d"), text=text,
                ),
            }],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except Exception as e:
        print(f"    ! {model} failed: {e}")
        return [], 0, 0

    body = next((b.text for b in r.content if b.type == "text"), "{}")
    try:
        dates = json.loads(body).get("dates", [])
    except json.JSONDecodeError:
        dates = []

    # A day or month of 00 cannot have been read from a document -- it is the
    # shape a guessed date takes when only the month was known.
    kept = []
    for d in dates:
        parts = (d.get("date") or "").split("-")
        if len(parts) == 3 and "00" in parts[1:]:
            print(f"    dropped invented date {d.get('date')} ({d.get('title','')[:40]})")
            continue
        kept.append(d)
    return kept, r.usage.input_tokens, r.usage.output_tokens


def cost(model_key, tin, tout):
    rates = {"haiku": (1.0, 5.0), "sonnet": (2.0, 10.0), "opus": (5.0, 25.0)}
    cin, cout = rates[model_key]
    return (tin / 1_000_000) * cin + (tout / 1_000_000) * cout


def load_documents(all_docs):
    """(course, document name, text) for everything worth reading."""
    docs = []
    for folder in sorted(EXTRACTED.iterdir()) if EXTRACTED.exists() else []:
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.txt")):
            is_syllabus = "syllab" in f.stem.lower()
            if not all_docs and not is_syllabus:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if not is_syllabus and not looks_dated(text):
                continue
            docs.append((folder.name, f.stem, text))

    if all_docs and COLLECTED.exists():
        for course in json.loads(COLLECTED.read_text(encoding="utf-8")):
            for a in course.get("announcements", []):
                body = (a.get("Body") or {}).get("Text", "") or ""
                if body.strip():
                    docs.append((course["name"][:40],
                                 f"announcement: {a.get('Title','')}"[:60], body))
    return docs


def show(dates, indent="    "):
    for d in sorted(dates, key=lambda x: x.get("date") or "9999"):
        flag = {"high": " ", "medium": "?", "low": "??"}.get(d.get("confidence"), " ")
        when = d.get("date", "?")
        if d.get("time"):
            when += " " + d["time"]
        print(f"{indent}{flag:<3}{when:<17}{d.get('kind',''):<13}{d.get('title','')[:44]}")


def api_key():
    """From api_key.txt if present, else the environment.

    A key typed at a command prompt ends up in the window's history and in
    anything pasted from it. A gitignored file avoids both, and stripping
    whitespace here guards against a key that picked up a line break on its
    way out of the browser.
    """
    if KEY_FILE.exists():
        key = "".join(KEY_FILE.read_text(encoding="utf-8", errors="replace").split())
        if key:
            return key
        # An empty file means an editor was opened and nothing was saved.
        # Falling through to the environment silently would then use a stale
        # key and report it as invalid, which hides the real problem.
        sys.exit(
            f"{KEY_FILE} exists but is empty.\n\n"
            f"Open it, paste the key, and press Ctrl+S to save before closing."
        )
    key = "".join((os.environ.get("ANTHROPIC_API_KEY") or "").split())
    if key:
        return key
    sys.exit(
        f"No API key found.\n\n"
        f"Get one at https://console.anthropic.com, then paste it into:\n"
        f"    {KEY_FILE}\n\n"
        f"Nothing else -- just the key on one line. That file is gitignored."
    )


def main():
    key = api_key()
    if not key.startswith("sk-ant-"):
        sys.exit("That does not look like an Anthropic key -- they start with sk-ant-")

    all_docs = "--all" in sys.argv
    chosen = next((a.split("=")[1] for a in sys.argv if a.startswith("--model=")), None)
    compare = not all_docs and not chosen
    keys = ["haiku", "sonnet"] if compare else [chosen or "haiku"]

    docs = load_documents(all_docs)
    if not docs:
        sys.exit("Nothing to read. Run download.py first.")

    print(f"{len(docs)} documents to read using: {', '.join(keys)}")
    if compare:
        print("(comparing two models on your syllabi -- add --all for everything)\n")
    else:
        print()

    client = anthropic.Anthropic(api_key=key)
    totals = {k: [0, 0, 0] for k in keys}      # dates, input tokens, output tokens
    results = []

    for course, doc, text in docs:
        print(f"{course}\n  {doc}")
        for key in keys:
            dates, tin, tout = ask(client, MODELS[key], course, doc, text)
            totals[key][0] += len(dates)
            totals[key][1] += tin
            totals[key][2] += tout
            label = f"  {key}: {len(dates)} found" if compare else f"  {len(dates)} found"
            print(label)
            show(dates)
            results.append({"course": course, "document": doc, "model": key, "dates": dates})
        print()

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("=" * 66)
    for key in keys:
        found, tin, tout = totals[key]
        print(f"{key:<8} {found:>3} dates   ${cost(key, tin, tout):.4f} for this run")
    if compare:
        print("\nIf both found the same dates, use haiku -- it is a fifth of the price.")
        print("If sonnet found real ones haiku missed, the extra cost is worth it.")
    print(f"\n?  = medium confidence, ??  = low (check these)\nSaved to: {OUT}")


if __name__ == "__main__":
    main()
