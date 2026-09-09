r"""
One conversation per course, with that course's material already in it.

    python chat.py MCG2360 "what is the 0.2% offset method"
    python chat.py MCG2360 --history
    python chat.py MCG2360 --clear

Mostly this is used through the web app, where it is the Ask tab. The point
is that you do not have to explain the context: the lectures, the syllabus,
your deadlines and your learning profile are already there, so a question can
be as short as it actually is in your head.

WHY THE COURSE MATERIAL IS CACHED

A course's extracted text runs to tens of thousands of tokens, and it is
identical on every question. Sent plainly it would cost roughly a dollar a
day to ask a handful of questions. Marked with cache_control it is written
once and read back at a tenth of the price, so a follow-up costs about as
much as the sentence you typed. This is the difference between a feature you
use and one you avoid using.

The material goes in the system prompt rather than the conversation for the
same reason -- it has to sit in front of everything and never change, or the
cache is invalidated on every turn.
"""

import sys
from datetime import date

import anthropic

import find_dates
import paths
import prep
import store

MODEL = "claude-sonnet-5"
KEEP_TURNS = 40          # how much of the conversation to send back
MAX_MATERIAL = 300_000   # characters of course text

INSTRUCTIONS = """You are helping one university student with one course. You \
have their lecture material, their deadlines and a description of how they \
learn. Answer their questions about this course.

How to answer:

- Answer the question that was asked, at the length it deserves. A short \
question gets a short answer. Do not pad.
- Work from the course material above wherever it covers the question, and say \
when you are going beyond it. "Your Lecture 3 gives the FCC relation as ..." is \
more useful than a general answer that may not match what they are marked on.
- Show the steps of any derivation or calculation rather than stating the \
result. Never leave them to hold three quantities in their head between lines.
- Where a concept has a physical or mechanical meaning, give it. This is a \
mechanical engineering student; an abstract answer they cannot picture is a \
worse answer.
- If the material does not answer it, say so plainly and say what would. Do not \
produce a confident-sounding version of something you are guessing at.
- Never invent a measurement, a result or a number from an experiment.

Follow the section on how this student learns as instructions, not background. \
Do not mention it, quote it, or explain that you are following it."""


def course_row(db, code_or_name):
    """Find a course by code, name, or d2l id -- whatever was typed."""
    needle = str(code_or_name or "").strip().lower()
    for row in db.execute("SELECT * FROM courses ORDER BY name").fetchall():
        parts = store.course_parts(row["name"])
        if needle in (parts["code"].lower(), str(row["d2l_id"]), row["name"].lower()):
            return row
    for row in db.execute("SELECT * FROM courses ORDER BY name").fetchall():
        if needle and needle in row["name"].lower():
            return row
    return None


def history(db, course_id, limit=KEEP_TURNS):
    rows = db.execute(
        """SELECT role, text, cost_usd, created_at FROM chats
           WHERE course_id = ? ORDER BY id DESC LIMIT ?""",
        (course_id, limit)).fetchall()
    return list(reversed(rows))


def spent(db, course_id):
    return db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM chats WHERE course_id = ?",
        (course_id,)).fetchone()[0]


def context_for(db, row):
    """Everything the model should already know before the first question."""
    parts = store.course_parts(row["name"])
    # only_mine needs the course's d2l id on each row, so the join is not
    # decoration -- without it every lab section's deadline goes in.
    dated = store.only_mine(db.execute(
        """SELECT d.*, c.d2l_id AS course_d2l_id FROM dates d
           JOIN courses c ON c.id = d.course_id
           WHERE d.course_id = ? AND d.status IN ('new','accepted')
           ORDER BY d.due_date IS NULL, d.due_date""", (row["id"],)).fetchall())

    lines = []
    for d in dated[:80]:
        when = prep.with_weekday(d["due_date"]) if d["due_date"] else "no date yet"
        tail = f"  (before {d['linked_to']})" if d["linked_to"] else ""
        lines.append(f"  {when} {d['due_time'] or ''} {d['kind'] or ''} — {d['title']}{tail}")

    return "\n".join([
        f"COURSE: {parts['code']} — {parts['title']}",
        f"Today is {prep.with_weekday(date.today().isoformat())}.",
        "",
        "--- WHAT THIS COURSE ALLOWS -----------------------------------------",
        prep.policy_for(parts["code"], paths.VAULT),
        "",
        "--- HOW THIS STUDENT LEARNS -----------------------------------------",
        prep.profile_text(),
        "",
        "--- THEIR DEADLINES IN THIS COURSE ----------------------------------",
        "\n".join(lines) or "  (none recorded)",
        "",
        "--- COURSE MATERIAL -------------------------------------------------",
        prep.material_for(row["name"])[:MAX_MATERIAL],
        "--- END -------------------------------------------------------------",
    ])


def ask(db, row, question):
    """One turn. Returns (reply, cost)."""
    past = history(db, row["id"])
    messages = [{"role": r["role"], "content": r["text"]} for r in past]
    messages.append({"role": "user", "content": question})

    client = anthropic.Anthropic(api_key=find_dates.api_key())
    r = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=[
            {"type": "text", "text": INSTRUCTIONS},
            # The expensive, unchanging half. Cached so a follow-up question
            # costs about what the sentence costs, not what the course costs.
            {"type": "text", "text": context_for(db, row),
             "cache_control": {"type": "ephemeral"}},
        ],
        messages=messages,
    )
    reply = "".join(b.text for b in r.content
                    if getattr(b, "type", "") == "text").strip()

    u = r.usage
    fresh = getattr(u, "cache_creation_input_tokens", 0) or 0
    cached = getattr(u, "cache_read_input_tokens", 0) or 0
    # Cache writes cost 1.25x, reads 0.1x -- worth counting honestly so the
    # running total shown in the app is the real one.
    rates_in, rates_out = 2.0, 10.0
    cost = ((u.input_tokens + fresh * 1.25 + cached * 0.1) / 1e6 * rates_in
            + u.output_tokens / 1e6 * rates_out)

    now = store.now()
    db.execute("INSERT INTO chats (course_id, role, text, cost_usd, created_at)"
               " VALUES (?,?,?,?,?)", (row["id"], "user", question, 0, now))
    db.execute("INSERT INTO chats (course_id, role, text, cost_usd, created_at)"
               " VALUES (?,?,?,?,?)", (row["id"], "assistant", reply, cost, now))
    db.commit()
    return reply, cost


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    db = store.connect()
    if not argv:
        print("\n  python chat.py <course> \"your question\"\n")
        for c in db.execute("SELECT name FROM courses ORDER BY name"):
            p = store.course_parts(c["name"])
            print(f"    {p['code']:<10} {p['title']}")
        print()
        db.close()
        return

    row = course_row(db, argv[0])
    if row is None:
        print(f"  no course matching {argv[0]!r}")
        db.close()
        return
    parts = store.course_parts(row["name"])

    if "--clear" in argv:
        n = db.execute("DELETE FROM chats WHERE course_id = ?", (row["id"],)).rowcount
        db.commit(); db.close()
        print(f"  cleared {n} message(s) from {parts['code']}")
        return

    if "--history" in argv:
        for m in history(db, row["id"]):
            who = "you" if m["role"] == "user" else parts["code"]
            print(f"\n  [{who}] {m['text']}")
        print(f"\n  ${spent(db, row['id']):.4f} spent on this course's chat\n")
        db.close()
        return

    question = " ".join(a for a in argv[1:] if not a.startswith("--")).strip()
    if not question:
        print("  ask something.")
        db.close()
        return

    print(f"\n  {parts['code']} — {parts['title']}\n  thinking...\n")
    try:
        reply, cost = ask(db, row, question)
    except Exception as e:
        print(f"  ! failed: {e}")
        db.close()
        return
    print(reply)
    print(f"\n  (${cost:.4f}; ${spent(db, row['id']):.4f} on this course so far)\n")
    db.close()


if __name__ == "__main__":
    main()
