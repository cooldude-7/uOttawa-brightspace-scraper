r"""
Give the undated tasks a place in the term.

    python link_tasks.py            link anything not linked yet
    python link_tasks.py --relink   throw the existing links away and redo them
    python link_tasks.py --dry-run  show what it would do, write nothing

find_dates.py reads one document at a time, which is the right way to find a
date but the wrong way to time a task. Reading "The Basics of Arduino" it has
no way to know Lab 5 is on 11 October, so "install the Arduino IDE" comes back
with no date and sits at the bottom of the list being useless -- while
"evaluate the lab assistants", which belongs in December, sits right next to it
looking equally urgent.

So this is a second pass with the one thing the first pass could not have: the
whole course at once. Every undated task and every dated item in one request,
asked which task must come before which thing.

Two rules it works under, both the same rule really:

  It may only choose from the dated items it was given. An anchor that is not
  in that list is discarded, not trusted -- the same discipline that stopped
  the extractor inventing a semester of assignment dates.

  A task that genuinely has no anchor stays undated. "Join a competitive team
  (optional)" is not due before anything, and guessing a date for it would be
  worse than leaving it alone.

A linked date is inferred, never stated. It is stored with linked_to set so
that every place it is shown can say so.
"""

import json
import sys
from datetime import date

import anthropic

import find_dates
import store

MODEL = "claude-sonnet-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "anchor_title": {"type": "string"},
                    "anchor_date": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["task_id", "anchor_title", "anchor_date", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["links"],
    "additionalProperties": False,
}

PROMPT = """You are timing a student's to-do list for one university course.

Course: {course}
Today is {today}.

Below are TASKS the student must do but which no document gave a date for, and \
DATED ITEMS in the same course that do have dates.

Link a task to the dated item it must be done BEFORE, so it surfaces when it \
matters instead of on day one.

Rules:
- Choose anchors ONLY from the dated items listed. Copy "title" exactly, and put \
that item's "date" field in anchor_date as YYYY-MM-DD with no time. Never invent \
an item or a date.
- Link only where there is a real dependency. "Install the Arduino IDE" belongs \
before the Arduino lab. "Create a Tinkercad account" belongs before the circuits \
lab. "Evaluate the lab assistants" and "return prototypes" belong at the END of \
term, so anchor them to the last relevant session.
- If a task must come before a REPEATING thing, anchor it to the EARLIEST one \
that has not happened yet -- preparation is needed before the first occurrence, \
not the last.
- Some tasks have no anchor. An optional club, a general policy, something with \
no relationship to any dated item: leave it out of your answer entirely. \
Returning fewer links is correct and expected. Do not stretch for a connection.
- "why" is one short phrase a student would understand, naming the connection: \
"needed for the Arduino lab", "end of term paperwork". Not a restatement of the \
task.

TASKS (id, title, and the sentence it came from):
{tasks}

DATED ITEMS -- these are the only anchors you may choose from:
{anchors}
"""


def undated_tasks(db, course_id, relink=False):
    extra = "" if relink else " AND linked_to IS NULL"
    return db.execute(
        f"""SELECT id, title, source_excerpt FROM dates
            WHERE course_id = ? AND status = 'new' AND due_date IS NULL
              AND kind = 'todo'{extra}
            ORDER BY id""",
        (course_id,),
    ).fetchall()


def anchors(db, course_id):
    """Dated things in this course a task could sensibly precede."""
    return db.execute(
        """SELECT title, due_date, due_time, kind FROM dates
           WHERE course_id = ? AND due_date IS NOT NULL AND status != 'dismissed'
           ORDER BY due_date, due_time""",
        (course_id,),
    ).fetchall()


def ask(client, course_name, tasks, anchor_rows, today):
    task_lines = "\n".join(
        f"  {t['id']}. {t['title']}\n       from: "
        f"{' '.join((t['source_excerpt'] or '')[:200].split())}"
        for t in tasks)
    anchor_lines = "\n".join(
        f"  title: {a['title']}  |  date: {a['due_date']}  |  "
        f"time: {a['due_time'] or '-'}  |  kind: {a['kind'] or 'other'}"
        for a in anchor_rows)

    r = client.messages.create(
        model=MODEL,
        # 4000 truncated GNG2101 mid-JSON: fifteen tasks is a lot of links.
        max_tokens=16000,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(
            course=course_name, today=today,
            tasks=task_lines, anchors=anchor_lines)}],
    )
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    return (json.loads(text).get("links", []),
            r.usage.input_tokens, r.usage.output_tokens)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    relink = "--relink" in argv
    dry = "--dry-run" in argv

    key = find_dates.api_key()
    client = anthropic.Anthropic(api_key=key)
    today = date.today().isoformat()

    db = store.connect()
    run_id = store.start_run(db, "link")
    courses = db.execute("SELECT id, name FROM courses ORDER BY name").fetchall()

    spent = 0.0
    linked = skipped = 0

    for course in courses:
        tasks = undated_tasks(db, course["id"], relink)
        if not tasks:
            continue
        anchor_rows = anchors(db, course["id"])
        if not anchor_rows:
            print(f"\n{store.course_parts(course['name'])['code']}: "
                  f"{len(tasks)} task(s), but nothing dated to tie them to")
            continue

        parts = store.course_parts(course["name"])
        print(f"\n{parts['code']}  {parts['title']}")
        try:
            links, tin, tout = ask(client, course["name"], tasks, anchor_rows, today)
        except Exception as e:
            print(f"  ! failed: {e}")
            continue
        spent += find_dates.cost("sonnet", tin, tout)

        # Only anchors that were actually offered. A title or date the model
        # produced from nowhere is exactly the failure this project refuses.
        #
        # Strict about whether the anchor exists, forgiving about how it was
        # written back. The first version compared the returned date against
        # due_date alone and rejected "2026-10-02 11:30" -- a real midterm,
        # written exactly the way the prompt had presented it. Rejecting a true
        # anchor is the same class of harm as accepting a false one.
        allowed = {(a["title"], a["due_date"]): a for a in anchor_rows}
        by_title = {}
        for a in anchor_rows:
            by_title.setdefault(a["title"], []).append(a)

        def resolve(title, raw_date):
            day = (raw_date or "")[:10]                      # drop any time
            hit = allowed.get((title, day))
            if hit is not None:
                return hit
            # Same title, one date only: the date was mangled, not invented.
            same = by_title.get(title)
            if same and len(same) == 1:
                return same[0]
            return None
        by_id = {t["id"]: t for t in tasks}
        got = set()

        for link in links:
            task = by_id.get(link.get("task_id"))
            anchor = resolve(link.get("anchor_title"), link.get("anchor_date"))
            if task is None:
                continue
            if anchor is None:
                print(f"  ?? discarded invented anchor for {task['title'][:38]!r}: "
                      f"{link.get('anchor_title')!r} {link.get('anchor_date')!r}")
                continue
            got.add(task["id"])
            print(f"  {anchor['due_date']}  {task['title'][:40]:<42} "
                  f"before {anchor['title'][:30]}")
            if not dry:
                db.execute(
                    """UPDATE dates SET due_date = ?, due_time = ?,
                       linked_to = ?, linked_why = ? WHERE id = ?""",
                    (anchor["due_date"], anchor["due_time"], anchor["title"],
                     (link.get("why") or "").strip()[:120], task["id"]))
            linked += 1

        for task in tasks:
            if task["id"] not in got:
                print(f"  --          {task['title'][:40]:<42} left undated")
                skipped += 1

    if not dry:
        store.finish_run(db, run_id, 0, 0, linked, spent)
        db.commit()
    db.close()

    print("\n" + "=" * 62)
    print(f"  {linked} task{'s' if linked != 1 else ''} given a date, "
          f"{skipped} left undated on purpose.")
    print(f"  Cost ${spent:.4f}." + ("  (dry run -- nothing written)" if dry else ""))
    print("=" * 62)


if __name__ == "__main__":
    main()
