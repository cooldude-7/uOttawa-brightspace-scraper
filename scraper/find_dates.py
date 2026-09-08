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

import paths
import store
from download import safe_name

HERE = Path(__file__).parent
EXTRACTED = paths.EXTRACTED
COLLECTED = paths.COLLECTED
OUT = paths.FOUND
KEY_FILE = paths.API_KEY

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
                                 "lab", "session", "reading", "presentation", "other"],
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
{term_note}

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
- NEVER invent a date. If the document says assignments are due weekly but gives no \
actual dates, do NOT generate a series by counting forward. Report only dates the \
document actually states.
- Every date must appear in, or be directly stated by, the text, and source_excerpt \
must contain that evidence. No evidence, no date.

Two things that are easy to wrongly discard, and are both wanted:

- A deliverable that is NAMED BUT NOT YET SCHEDULED ("two midterms, dates TBA", \
"4 economics assignments", "weekly problem sets"). Return it with date set to "" and \
a title naming the thing. These get tracked as awaiting a date. Do not skip them, and \
do not guess a date for them.
- A SCHEDULED EVENT the student attends on a specific stated date -- a lab session, \
tutorial, DGD, review session, client meeting -- with kind "session". The student \
plans their week around these alongside the deadlines.

But only where the document gives a specific date. Do NOT expand "labs run every \
Wednesday" into a list of Wednesdays, and do not report ordinary weekly lectures.

Picking kind: "midterm" for a test during term, "final_exam" only for the end-of-term \
final, "quiz" for short in-class tests, "lab" for a lab report or lab submission, \
"session" for something attended rather than handed in, "assignment" for anything \
else submitted.

- If the document genuinely contains nothing, return an empty list. That is a correct \
and useful answer.

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


def term_window(term):
    """Plausible date range for a uOttawa term code like 20269.

    Last digit is the term: 1 winter, 5 summer, 9 fall. Content folders are
    routinely reused between offerings, so a course page can still carry last
    term's schedule -- dates outside this window are from a previous run of
    the course, not this one.
    """
    if not term or len(term) != 5 or not term.isdigit():
        return None, None
    year, code = int(term[:4]), term[4]
    if code == "1":
        return f"{year}-01-01", f"{year}-05-15"
    if code == "5":
        return f"{year}-04-15", f"{year}-09-15"
    if code == "9":
        return f"{year}-08-15", f"{year + 1}-01-31"
    return None, None


def looks_dated(text):
    """Cheap filter -- skip documents with no date-like language at all."""
    return len(DATEISH.findall(text)) >= 2


def ask(client, model, course, doc, text, window=(None, None)):
    """One document, one model. Returns (dates, input_tokens, output_tokens)."""
    text = text[:150_000]
    start, end = window
    term_note = (
        f"\nThis term runs from {start} to {end}. Course pages are often reused "
        f"between terms, so any schedule outside that range belongs to a previous "
        f"offering -- do not report those dates.\n" if start else ""
    )
    try:
        r = client.messages.create(
            model=model,
            max_tokens=8000,
            messages=[{
                "role": "user",
                "content": PROMPT.format(
                    course=course, doc=doc,
                    today=datetime.now().strftime("%Y-%m-%d"),
                    term_note=term_note, text=text,
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

    kept = []
    for d in dates:
        when = (d.get("date") or "").strip()

        # No date at all is not a failure -- it is a deliverable that has been
        # named but not yet scheduled. Those are worth tracking separately.
        if not when:
            d["pending"] = True
            kept.append(d)
            continue

        # A day or month of 00 cannot have been read from a document -- it is
        # the shape a guessed date takes when only the month was known.
        parts = when.split("-")
        if len(parts) == 3 and "00" in parts[1:]:
            print(f"    dropped invented date {when} ({d.get('title','')[:40]})")
            continue

        # Content folders get reused between offerings, so a stale schedule
        # from a previous term is a real and dangerous failure mode.
        if start and not (start <= when <= end):
            print(f"    dropped stale date {when} ({d.get('title','')[:40]}) "
                  f"-- outside {start}..{end}")
            continue

        d["pending"] = False
        kept.append(d)
    return kept, r.usage.input_tokens, r.usage.output_tokens


def cost(model_key, tin, tout):
    rates = {"haiku": (1.0, 5.0), "sonnet": (2.0, 10.0), "opus": (5.0, 25.0)}
    cin, cout = rates[model_key]
    return (tin / 1_000_000) * cin + (tout / 1_000_000) * cout


def current_term():
    """Newest term stamp across the collected courses."""
    if not COLLECTED.exists():
        return None
    terms = [c.get("term") for c in json.loads(COLLECTED.read_text(encoding="utf-8"))
             if c.get("term")]
    return max(terms) if terms else None


def load_documents(all_docs):
    """Every document worth reading, tagged with the course it belongs to."""
    courses = json.loads(COLLECTED.read_text(encoding="utf-8")) if COLLECTED.exists() else []
    by_folder = {safe_name(c["name"], 40): c for c in courses}
    docs = []

    def add(course, kind, title, text):
        if (text or "").strip():
            docs.append({"course": course, "kind": kind, "title": title, "text": text})

    for folder in sorted(EXTRACTED.iterdir()) if EXTRACTED.exists() else []:
        if not folder.is_dir():
            continue
        course = by_folder.get(folder.name)
        if course is None:
            continue
        for f in sorted(folder.glob("*.txt")):
            if f.name == "_links.txt":
                continue
            is_syllabus = "syllab" in f.stem.lower()
            if not all_docs and not is_syllabus:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if not is_syllabus and not looks_dated(text):
                continue
            add(course, "file", f.stem, text)

    if all_docs:
        for course in courses:
            name = course["name"][:40]

            for a in course.get("announcements", []):
                body = (a.get("Body") or {}).get("Text", "") or ""
                if body.strip():
                    add(course, "announcement", (a.get("Title") or "")[:80], body)

            # Quizzes and assignments carry their own due dates as real fields,
            # but the text inside them often mentions further dates -- late
            # penalties, what a test covers, when solutions get posted.
            for q in course.get("quizzes", []):
                body = "\n".join(filter(None, [
                    deep_text(q.get("Description")),
                    deep_text(q.get("Instructions")),
                ]))
                if body.strip():
                    add(course, "quiz", (q.get("Name") or "")[:80],
                        with_known(q.get("Name"),
                                   q.get("DueDate") or q.get("EndDate"), body))

            for a in course.get("assignments", []):
                body = deep_text(a.get("CustomInstructions"))
                if body.strip():
                    add(course, "assignment", (a.get("Name") or "")[:80],
                        with_known(a.get("Name"), a.get("DueDate"), body))

            for m in course.get("modules", []):
                if (m.get("description") or "").strip():
                    add(course, "folder", (m.get("title") or "")[:80], m["description"])

            # Discussions: a professor answering "when is this due" in a
            # thread is often the only place that date is written down.
            for forum in course.get("discussions", []):
                for topic in forum.get("topics", []):
                    chunks = [deep_text(topic.get("description"))]
                    for post in topic.get("posts", []):
                        chunks.append(f"{post.get('subject') or ''}\n"
                                      f"{strip_html(post.get('body') or '')}")
                    body = "\n\n".join(c for c in chunks if c.strip())
                    if body.strip():
                        add(course, "discussion",
                            f"{forum.get('title','')} / {topic.get('title','')}"[:80], body)

            for cl in course.get("checklists", []):
                lines = [deep_text(cl.get("description"))]
                for item in cl.get("items", []):
                    lines.append(f"{item.get('name','')} "
                                 f"{'(due ' + item['due'] + ')' if item.get('due') else ''}\n"
                                 f"{deep_text(item.get('description'))}")
                body = "\n".join(l for l in lines if l.strip())
                if body.strip():
                    add(course, "checklist", (cl.get("name") or "")[:80], body)

            for sv in course.get("surveys", []):
                body = "\n".join(filter(None, [
                    deep_text(sv.get("Description")), deep_text(sv.get("Instructions"))]))
                if body.strip():
                    add(course, "survey", (sv.get("Name") or "")[:80], body)

            overview = deep_text(course.get("overview", {}).get("Description"))
            if overview.strip():
                add(course, "overview", "course overview", overview)

            for g in course.get("grades", []):
                body = deep_text(g.get("Description"))
                if body.strip():
                    add(course, "grade", (g.get("Name") or "")[:80], body)
    return docs


def strip_html(text):
    """Discussion posts come back as HTML; the tags are noise to the reader."""
    text = re.sub(r"<br\s*/?>|</p>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    return re.sub(r"[ \t]{2,}", " ", text)


def deep_text(field):
    """Brightspace nests description text inconsistently -- dig it out."""
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        inner = field.get("Text")
        if isinstance(inner, str):
            return inner
        if isinstance(inner, dict):
            return inner.get("Text", "") or ""
    return ""


def with_known(title, due, body):
    """Tell the model the date Brightspace already knows, so it does not
    re-report it as a discovery and can spot dates that differ from it."""
    if not due:
        return body
    return (f"[Brightspace already lists '{title}' as due {due}. Do not report that "
            f"date again -- report only ADDITIONAL dates mentioned below.]\n\n{body}")


def show(dates, indent="    "):
    for d in sorted(dates, key=lambda x: x.get("date") or "9999"):
        flag = {"high": " ", "medium": "?", "low": "??"}.get(d.get("confidence"), " ")
        if d.get("pending"):
            print(f"{indent}{'--':<3}{'no date yet':<17}"
                  f"{d.get('kind',''):<13}{d.get('title','')[:44]}")
            continue
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    key = api_key()
    if not key.startswith("sk-ant-"):
        sys.exit("That does not look like an Anthropic key -- they start with sk-ant-")

    all_docs = "--all" in argv
    chosen = next((a.split("=")[1] for a in argv if a.startswith("--model=")), None)
    compare = not all_docs and not chosen
    keys = ["haiku", "sonnet"] if compare else [chosen or "haiku"]

    docs = load_documents(all_docs)
    if not docs:
        sys.exit("Nothing to read. Run download.py first.")

    window = term_window(current_term())
    if window[0]:
        print(f"Only accepting dates between {window[0]} and {window[1]}")

    db = store.connect()
    run_id = store.start_run(db, "compare" if compare else keys[0])
    client = anthropic.Anthropic(api_key=key)

    totals = {k: [0, 0, 0] for k in keys}      # dates, input tokens, output tokens
    results = []
    seen = read = new_cards = 0

    # Comparing two models means asking twice about the same text, so the
    # already-read shortcut has to stay out of the way.
    reread = compare or "--force" in argv

    print(f"{len(docs)} documents found; skipping any already read\n")

    for entry in docs:
        course = entry["course"]
        course_id = store.upsert_course(db, course["id"], course["name"], course.get("term"))
        doc_id, needs = store.see_document(
            db, course_id, entry["kind"], entry["title"], entry["text"])
        seen += 1
        if not needs and not reread:
            continue

        read += 1
        print(f"{course['name'][:40]}\n  [{entry['kind']}] {entry['title'][:56]}")
        for key in keys:
            dates, tin, tout = ask(client, MODELS[key], course["name"],
                                   entry["title"], entry["text"], window)
            totals[key][0] += len(dates)
            totals[key][1] += tin
            totals[key][2] += tout
            print(f"  {key}: {len(dates)} found" if compare else f"  {len(dates)} found")
            show(dates)
            results.append({"course": course["name"], "document": entry["title"],
                            "model": key, "dates": dates})
            # Only one model's findings belong in the database; a comparison
            # run would otherwise store both and double every card.
            if key == keys[0]:
                new_cards += store.save_dates(db, course_id, doc_id, dates)
        store.mark_read(db, doc_id)
        print()

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    spent = sum(cost(k, totals[k][1], totals[k][2]) for k in keys)
    store.finish_run(db, run_id, seen, read, new_cards, spent)
    db.commit()

    print("=" * 66)
    if read == 0:
        print("Nothing new to read -- every document is unchanged since last time.")
        print("Add --force to read them all again anyway.")
    for key in keys:
        found, tin, tout = totals[key]
        print(f"{key:<8} {found:>3} found   ${cost(key, tin, tout):.4f}")
    print(f"\n{read} of {seen} documents needed reading. {new_cards} new deadlines stored.")

    if compare:
        print("\nIf both found the same dates, use haiku -- it is a fifth of the price.")
        print("If sonnet found real ones haiku missed, the extra cost is worth it.")

    totals_db = store.summary(db)
    print(f"\nWaiting for you: {totals_db['new']} dated, "
          f"{totals_db['pending']} with no date yet")
    print(f"Already handled:  {totals_db['accepted']} accepted, "
          f"{totals_db['dismissed']} dismissed")
    print(f"Spent all time:   ${totals_db['spent']:.2f}")
    print("\nSee them with:  python cards.py")
    db.close()


if __name__ == "__main__":
    main()
