r"""
Run a skill against one real piece of work.

    python prep.py --list           what is waiting, with its id
    python prep.py 412              prepare that item
    python prep.py --next           the soonest thing not yet prepared
    python prep.py 412 --skill study

It reads the skill for that kind of work, the policy for that course, the
item's own description, and everything extracted from the course, and writes
the result into the item's note in the vault.

WHAT IT WILL AND WILL NOT DO

Whatever `Skills/course-policies.md` says. That file is the authority, not this
one, because professors differ and one of them changing their mind mid-term is
not a reason to edit Python. A course not listed there is treated as unknown,
which means prepare rather than draft -- guessing generously is the wrong way
to be wrong.

One rule sits above the policy file and is not negotiable, because it protects
the user rather than restricting them: measured data is never invented. Not a
reading, not a trend, not an error bar, not a plausible-looking number in a
table. Results and graphs from a real experiment are the student's, and a
fabricated one does not stay in a draft -- it ends up submitted.

Every run appends to `AI use log.md` and `ai-use-log.csv` in the vault, because
GNG2101 requires an AI log and an attestation, and a log written afterwards
from memory is worth very little.
"""

import csv
import sys
from datetime import date, datetime

import anthropic

import find_dates
import paths
import store
import vault
from download import safe_name

MODEL = "claude-sonnet-5"
MAX_MATERIAL = 400_000       # characters of course text; the window is 1M tokens

PROMPT = """You are preparing one piece of university coursework for a student.

Course: {course}
Item: {title}
Kind: {kind}
Due: {due}
Today: {today}
{who}

Where the item came from:
{excerpt}

--- WHAT THIS COURSE ALLOWS -------------------------------------------------
{policy}

--- HOW TO HANDLE THIS KIND OF WORK -----------------------------------------
{skill}

--- HOW THIS STUDENT LEARNS -------------------------------------------------
{profile}

--- OTHER DATES IN THIS COURSE ----------------------------------------------
{dates}

--- COURSE MATERIAL ---------------------------------------------------------
{material}
--- END ---------------------------------------------------------------------

Write markdown, starting at heading level 2. No preamble, no sign-off -- this
goes straight into the student's notes.

Follow the "how this student learns" section as a set of instructions, not as
background. It describes measured working-memory, attention and task-initiation
patterns, and material built against it is materially more useful than material
built on general study advice. Where it conflicts with conventional advice, it
wins. Never refer to it, quote it, or explain that you are following it -- just
produce work shaped that way.

Above everything else: never invent a measurement, a result, a data point or a
graph from an experiment. If the work needs real data the student has not
collected yet, leave a clearly marked gap saying what goes there. A blank
waiting for a real reading is useful. A plausible number is a fabrication that
ends up in a submission.

Say plainly when the material does not tell you something, rather than
producing a confident-looking version of it.

What you write is a finished document the student will study from, not a
record of how you got there. Do not narrate checking, second-guessing or
correcting yourself in it -- no "wait", no "let me verify", no abandoned
arithmetic left mid-line. Do that work before you write; the page should
contain only the corrected version. Flagging genuine uncertainty about the
COURSE MATERIAL is different and is wanted -- that belongs in plain sentences,
not in crossed-out working.
"""


def skill_for(kind, root, override=None):
    """The skill file for this kind of work, as the user has edited it."""
    name = override or {
        "assignment": "assignment", "lab": "lab",
        "quiz": "quiz", "midterm": "quiz", "final_exam": "quiz",
        "presentation": "assignment", "reading": "study", "other": "assignment",
    }.get(kind, "assignment")
    path = root / "Skills" / f"{name}.md"
    if not path.exists():
        return name, ""
    return name, path.read_text(encoding="utf-8", errors="replace")


def policy_for(course_code, root):
    """The section of course-policies.md for this course, or the fallback."""
    path = root / "Skills" / "course-policies.md"
    if not path.exists():
        return "No policy file. Prepare and explain; do not draft the submission."
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks, current, keep = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                blocks[current] = "\n".join(keep).strip()
            current, keep = line[3:].strip(), []
        elif current:
            keep.append(line)
    if current:
        blocks[current] = "\n".join(keep).strip()

    mine = blocks.get(course_code)
    always = blocks.get("Always", "")
    fallback = blocks.get("The rule when a course is not listed", "")
    if mine:
        return f"{course_code}: {mine}\n\nAlways:\n{always}"
    return (f"{course_code} is not listed in the policy file.\n{fallback}\n\n"
            f"Always:\n{always}")


def who_text(course_d2l_id):
    """The student's own lab section and day group for this course.

    Without it the model hedges about the student's own week -- "Lab 1 (if
    Wednesday group) / (if Thursday group)" -- when me.json has known the
    answer all along. The deadlines were already filtered correctly; only the
    prose did not know.
    """
    prefs = store.load_prefs()
    key = str(course_d2l_id)
    bits = []
    if prefs.get("sections", {}).get(key):
        bits.append(f"lab section {prefs['sections'][key].upper()}")
    if prefs.get("groups", {}).get(key):
        bits.append(f"the {prefs['groups'][key].capitalize()} lab group")
    if not bits:
        return ("This student's lab section for this course is not known. Do "
                "not guess it, and do not write a schedule that depends on it.")
    return ("This student is in " + " and ".join(bits) + ". Everything below is "
            "already filtered to them -- write their schedule as fact, never "
            "as \"if you are in group X\".")


def profile_text():
    """How the student learns, written from their assessment.

    Applies to every skill, not just study: task initiation and working memory
    change how a lab prep or an assignment breakdown should be shaped, not only
    how flashcards are written. Absent, nothing is claimed about them.
    """
    path = paths.PROFILE / "study-profile.md"
    if not path.exists():
        return ("(No profile written. Use ordinary good practice, and do not "
                "guess at how this student learns.)")
    return path.read_text(encoding="utf-8", errors="replace")


def material_for(course_name):
    parts = []
    folder = paths.EXTRACTED / safe_name(course_name, 40)
    if folder.is_dir():
        for f in sorted(folder.glob("*.txt")):
            if f.name == "_links.txt":
                continue
            parts.append(f"### {f.stem}\n{f.read_text(encoding='utf-8', errors='replace')}")
    joined = "\n\n".join(parts)
    if len(joined) > MAX_MATERIAL:
        joined = joined[:MAX_MATERIAL] + "\n\n[...material truncated...]"
    return joined or "(nothing extracted for this course yet)"


def waiting(db):
    """Work waiting to be prepared -- yours only, not every lab section's."""
    rows = db.execute(
        """SELECT d.*, c.name AS course_name, c.d2l_id AS course_d2l_id
           FROM dates d JOIN courses c ON c.id = d.course_id
           WHERE d.status IN ('new','accepted')
             AND d.kind NOT IN ('todo','session')
           ORDER BY d.due_date IS NULL, d.due_date, d.due_time""").fetchall()
    return store.only_mine(rows)


def note_path(root, course_name, title):
    cdir, _ = vault.course_dir(root, course_name)
    return cdir / "Work" / f"{vault.slug(title, 60)}.md"


def is_prepared(root, row):
    """Has this one already been through a skill?"""
    path = note_path(root, row["course_name"], row["title"])
    if not path.exists():
        return False
    return "prepared:" in path.read_text(encoding="utf-8", errors="replace")[:400]


def log_use(root, row, parts, skill_name, produced):
    """The declaration, written as it happens rather than remembered later."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = root / "AI use log.md"
    if not md.exists():
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(
            "# AI use log\n\nEvery time this app used Claude to prepare a piece "
            "of work. Kept so the declaration is already written when a course "
            "asks for one.\n\nMeasured results and graphs are never AI-generated "
            "-- see `Skills/course-policies.md`.\n\n", encoding="utf-8")
    with md.open("a", encoding="utf-8") as f:
        f.write(f"- **{stamp}** · {parts['code']} · {row['title']} · "
                f"skill `{skill_name}` · {MODEL} · {produced}\n")

    csv_path = root / "ai-use-log.csv"
    new = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["when", "course", "item", "kind", "skill", "model",
                        "what it produced", "what was not AI"])
        w.writerow([stamp, parts["code"], row["title"], row["kind"] or "",
                    skill_name, MODEL, produced,
                    "measured results, data and graphs"])


def prepare(db, row, root, skill_override=None):
    parts = store.course_parts(row["course_name"])
    skill_name, skill_text = skill_for(row["kind"], root, skill_override)
    if not skill_text:
        print(f"  no skill file for {skill_name!r} -- run vault.py first")
        return None

    others = db.execute(
        """SELECT title, due_date, due_time, kind FROM dates
           WHERE course_id = ? AND due_date IS NOT NULL AND status != 'dismissed'
           ORDER BY due_date LIMIT 60""", (row["course_id"],)).fetchall()

    prompt = PROMPT.format(
        course=f"{parts['code']} — {parts['title']}",
        title=row["title"], kind=row["kind"] or "assignment",
        due=vault.when(row) or "no date given",
        today=date.today().isoformat(),
        excerpt=" ".join((row["source_excerpt"] or "").split()) or "(nothing recorded)",
        policy=policy_for(parts["code"], root),
        skill=skill_text,
        who=who_text(row["course_d2l_id"]),
        profile=profile_text(),
        dates="\n".join(f"  {o['due_date']} {o['due_time'] or ''} {o['kind'] or ''} — {o['title']}"
                        for o in others) or "  (none)",
        material=material_for(row["course_name"]))

    client = anthropic.Anthropic(api_key=find_dates.api_key())
    r = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in r.content
                   if getattr(b, "type", "") == "text").strip()
    spent = find_dates.cost("sonnet", r.usage.input_tokens, r.usage.output_tokens)

    path = note_path(root, row["course_name"], row["title"])
    keep = ""
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="replace")
        if "## My work" in old:
            keep = old.split("## My work", 1)[1].strip()

    # No `generated: true`. Once prepared, this note belongs to the student and
    # vault.py will never overwrite it again.
    body = "\n".join([
        "---",
        f"course: {parts['code']}",
        f"kind: {row['kind'] or 'assignment'}",
        f'title: "{row["title"]}"',
        f"due: {row['due_date'] or ''}",
        f"prepared: {date.today().isoformat()}",
        f"skill: {skill_name}",
        "ai_used: true",
        "---", "",
        f"# {row['title']}", "",
        f"**{parts['code']} — {parts['title']}**  ·  "
        f"{vault.when(row) or 'no date yet'}", "",
        f"> {' '.join((row['source_excerpt'] or '').split())}", "",
        text, "",
        "## My work", "", keep, ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")

    log_use(root, row, parts, skill_name,
            f"prepared the {row['kind'] or 'item'} using the {skill_name} skill")
    return path, spent


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = paths.VAULT
    db = store.connect()
    rows = waiting(db)

    if "--list" in argv or not argv:
        print("\n  id    due          course     what")
        for r in rows[:50]:
            p = store.course_parts(r["course_name"])
            print(f"  {r['id']:<5} {(r['due_date'] or 'no date'):<12} "
                  f"{p['code']:<10} {r['title'][:46]}")
        print("\n  python prep.py <id>      prepare one")
        print("  python prep.py --next    the soonest one not prepared yet\n")
        db.close()
        return

    skill = None
    for a in argv:
        if a.startswith("--skill"):
            skill = a.split("=", 1)[1] if "=" in a else argv[argv.index(a) + 1]

    if "--next" in argv:
        target = next((r for r in rows
                       if r["due_date"] and not is_prepared(root, r)), None)
    else:
        ids = [a for a in argv if a.isdigit()]
        if not ids:
            print("  give an id, or --next.  python prep.py --list")
            db.close()
            return
        target = next((r for r in rows if r["id"] == int(ids[0])), None)

    if target is None:
        print("  nothing to prepare.")
        db.close()
        return

    p = store.course_parts(target["course_name"])
    print(f"\n  {p['code']} — {target['title']}")
    print(f"  {vault.when(target) or 'no date'}\n  working...")
    try:
        result = prepare(db, target, root, skill)
    except Exception as e:
        print(f"  ! failed: {e}")
        db.close()
        return
    db.close()
    if result:
        path, spent = result
        print(f"\n  written to {path.relative_to(root)}")
        print(f"  cost ${spent:.4f}, logged to 'AI use log.md'\n")


if __name__ == "__main__":
    main()
