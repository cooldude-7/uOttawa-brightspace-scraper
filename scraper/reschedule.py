r"""
Retire stored dates that a *second document* replaced.

    python reschedule.py            show what would change, change nothing
    python reschedule.py MCG2360    just that course
    python reschedule.py --apply    do it

`supersede.py` covers the case where Brightspace publishes a new date for
the same title. This covers the harder one: a professor re-issues a
schedule in another document and words every row differently, so nothing
automatic can see that "Lab 1 session (Thu group)" is the same event as
"Lab 1: Tensile Test (Thursday Group)". MCG2360's labs moved three weeks
and the two documents share not one title.

The pairs are read from `corrections.txt`, in plain English, because which
of two documents is current is a judgement about a course -- not something
any rule here can derive. The file is meant to be read and argued with.

THE CONDITION, WHICH IS THE WHOLE POINT

A row is retired only when the row named as its replacement:

  1. exists, in the same course, and
  2. already carries a date.

So the worst a wrong line can do is nothing. There is no arrangement of
the data where this leaves you with no deadline where you had one -- the
same guarantee `supersede.py` makes, for the same reason.

A linked to-do is never retired. It borrows its anchor's date, so it looks
like a twin of whatever it hangs off; and the tasks that hang off a lab
follow the lab's new date automatically once the anchor is right.

Retired rows are marked `resolved`, never deleted, so
`python store.py --restore` brings every one back. The replacement is
accepted, unless you had already dismissed it -- nothing here may undo a
decision you made.
"""

import sys
from pathlib import Path

import store

TABLE = Path(__file__).parent / "corrections.txt"


def pairs(path=TABLE):
    """-> [(course_code, retire_this, in_favour_of, line_number)]"""
    out = []
    if not path.exists():
        return out
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#")[0].strip()
        if not line:
            continue
        bits = [b.strip() for b in line.split("|")]
        if len(bits) != 3 or not all(bits):
            print(f"  corrections.txt line {n} is not "
                  f"COURSE | old | new -- skipped:  {raw.strip()}")
            continue
        out.append((bits[0].upper(), bits[1], bits[2], n))
    return out


def event_of(title):
    """What a title names, and who for. -> ("lab 1 session", (section, group))

    `store.normalize` returns the event with the audience appended, which is
    what keeps five lab sections apart. Here the two halves are wanted
    separately: the event decides whether two rows are the same piece of
    work, and the audience decides whether they are the same person's copy
    of it.
    """
    section, group = store.audience(title)
    text = store.normalize(title)
    for tail in (group, section):
        if tail and text.endswith(" " + tail):
            text = text[: -(len(tail) + 1)]
    return text, (section, group)


def looks_like(fragment, title):
    """Does this stored title name the thing the file means?

    Anchored at the start, never "mentions anywhere". A substring test read
    "Lab 1 session" as a match for "Upload WHMIS certificate prior to Lab 1
    session" and offered a certificate reminder as the replacement for a
    lab -- which would have retired the lab and kept the reminder. Anything
    that merely refers to an event is not that event.

    A prefix is still allowed, because the same lab is written both
    "Lab 4: Precipitation Hardening" and "Lab 4: Precipitation Hardening of
    Aluminium Alloys" in one document.
    """
    want, _ = event_of(fragment)
    have, _ = event_of(title)
    return bool(want) and (have == want or have.startswith(want + " "))


def same_audience(a, b):
    """Two rows are one person's copy of the same work, or they are not.

    Without this the Thursday lab paired with the Wednesday replacement --
    a fragment naming no day matches every day. Getting this wrong retires
    a real deadline in favour of somebody else's date, which is the failure
    this whole project exists to prevent.
    """
    return event_of(a["title"])[1] == event_of(b["title"])[1]


def plan(db, only=None, table=TABLE):
    """What would change. -> (moves, skipped, ids_that_are_yours)

    The schedule moved for every lab group, so every group's old row is
    retired -- but accepting another group's replacement would push their
    labs into your Google Calendar, which is what `--prune` exists to clean
    up after. Retire theirs, accept only yours.

    `only_mine` is asked once, about every row at once, and never row by
    row: it deliberately stops filtering a course whose deadlines name no
    group resembling the one on file, so that a stale setting cannot hide
    everything. Handed a single Wednesday row, that guard sees a course with
    no Thursday in it and returns the row unfiltered -- correct for the
    guard, useless as an answer to "is this mine".
    """
    courses = {c["id"]: store.course_parts(c["name"])["code"].upper()
               for c in db.execute("SELECT id, name FROM courses")}
    rows = db.execute(
        """SELECT d.id, d.course_id, c.d2l_id AS course_d2l_id, d.title,
                  d.due_date, d.due_time, d.kind, d.status, d.linked_to,
                  d.resolved_title, d.gcal_event_id
             FROM dates d JOIN courses c ON c.id = d.course_id
            WHERE d.status IN ('new', 'accepted')""").fetchall()

    mine = {r["id"] for r in store.only_mine(rows)}
    moves, skipped = [], []
    for code, old, new, line in pairs(table):
        if only and code != only.upper():
            continue
        here = [r for r in rows if courses.get(r["course_id"]) == code]

        # The replacement first. Nothing is retired on the strength of a
        # row that is not there, or that has no date of its own to offer.
        keeps = [r for r in here if looks_like(new, r["title"])
                 and not r["linked_to"] and r["due_date"]]
        if not keeps:
            why = ("no dated row matches the replacement"
                   if any(looks_like(new, r["title"]) for r in here)
                   else "nothing matches the replacement")
            skipped.append((f"{code} line {line}: {old} -> {new}", why))
            continue

        retire = [r for r in here if looks_like(old, r["title"])
                  and not r["linked_to"]
                  and r["id"] not in {k["id"] for k in keeps}]
        if not retire:
            skipped.append((f"{code} line {line}: {old} -> {new}",
                            "nothing left to retire"))
            continue

        for r in retire:
            # Each group's own copy, or nothing. A Thursday row is never
            # retired on the strength of a Wednesday date.
            ours = [k for k in keeps if same_audience(r, k)]
            if not ours:
                who = " / ".join(x for x in event_of(r["title"])[1] if x)
                skipped.append((f"{code} line {line}: {r['title'][:44]}",
                                f"no replacement for {who or 'this group'}"))
                continue
            moves.append((r, ours[0]))
    return moves, skipped, mine


def apply(db, moves, mine):
    stamp = store.now()
    for old, new in moves:
        db.execute(
            "UPDATE dates SET status = 'resolved', decided_at = ? WHERE id = ?",
            (stamp, old["id"]))
        if new["id"] not in mine:
            continue
        # Never resurrect something dismissed on purpose.
        db.execute(
            """UPDATE dates SET status = 'accepted', decided_at = ?
                 WHERE id = ? AND status = 'new'""", (stamp, new["id"]))
    db.commit()


def when(row):
    if not row["due_date"]:
        return "no date"
    return row["due_date"] + (f" {store.pretty_time(row['due_time'])}"
                              if row["due_time"] else "")


def main(argv):
    only = next((a for a in argv if not a.startswith("--")), None)
    db = store.connect()
    try:
        moves, skipped, mine = plan(db, only)

        if not moves:
            print("\n  Nothing to reschedule.\n")
        else:
            print(f"\n-- {len(moves)} row(s) replaced " + "-" * 34)
            for old, new in moves:
                cal = "  (in your calendar)" if old["gcal_event_id"] else ""
                theirs = "" if new["id"] in mine else "  (not your group)"
                print(f"  retire  {when(old):<22} {old['title'][:48]}{cal}")
                print(f"  keep    {when(new):<22} {new['title'][:48]}{theirs}")
                print()

        for what, why in skipped:
            print(f"  left alone -- {why}:  {what}")

        if "--apply" not in argv:
            if moves:
                print("  Nothing changed. Add --apply to do it.\n")
            return

        apply(db, moves, mine)
        print(f"  {len(moves)} retired, replacements accepted.")
        if any(old["gcal_event_id"] for old, _ in moves):
            print("  Run  python gcal.py --tidy  to remove the calendar "
                  "events they left behind.")
        print("  Undo the lot with  python store.py --restore\n")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
