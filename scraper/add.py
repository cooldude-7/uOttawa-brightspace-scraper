r"""
Add a deadline by hand -- one that arrived somewhere the scraper cannot see.

    python add.py MCG2360 "Lab group registration" 2026-09-18 23:59
    python add.py MCG2360 "Lab prep session" 2026-09-17 19:00 --kind session
    python add.py MAT1341 "Read chapter 4"          (no date is fine)

    --kind K    assignment (default), quiz, lab, exam, session, todo, reading
    --from "…"  the sentence it came from -- an email, or what was said
    --new       leave it waiting in the cards instead of keeping it outright
    --dry-run   say what it would do

Brightspace is not the only place a deadline lives. A TA emails the lab
group registration cutoff, a professor says a date in a lecture, a
timetable carries a room and a time. None of that is scraped and none of
it ever will be, so without this the app is quietly wrong about the week
-- and being quietly wrong is the failure this whole project exists to
prevent.

Added by hand means kept by default: typing it out *is* the decision the
cards exist to collect, so there is nothing left to swipe. `--new` if you
would rather decide later.

Every row records where it came from, the same as an extracted one. The
difference is that `confidence` is `stated` and the excerpt says you
entered it, so nothing downstream mistakes a hand-typed date for one a
document published.
"""

import re
import sys
from datetime import date, datetime

import store


def parse_when(text):
    """A date as typed. -> YYYY-MM-DD, or None if it is not one."""
    raw = " ".join(text.split())
    # "Sept" is how people write it and is neither %b ("Sep") nor %B.
    cleaned = re.sub(r"\bSept\b", "Sep", raw, flags=re.I)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y",
                "%b %d %Y", "%B %d %Y"):
        for candidate in (raw, cleaned):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def find_course(db, code):
    rows = db.execute("SELECT * FROM courses ORDER BY name").fetchall()
    for r in rows:
        if store.course_parts(r["name"])["code"].upper() == code.upper():
            return r
    print(f"\n  No course here called {code.upper()}. You have:\n")
    for r in rows:
        p = store.course_parts(r["name"])
        print(f"      {p['code']:<10} {p['title']}")
    return None


def opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            return argv[i + 1]
    return default


def main(argv):
    plain = [a for i, a in enumerate(argv[1:], 1)
             if not a.startswith("--")
             and not (i > 1 and argv[i - 1] in ("--kind", "--from"))]
    if len(plain) < 2:
        print(__doc__.strip())
        return 2

    code, title = plain[0], plain[1]
    when = parse_when(plain[2]) if len(plain) > 2 else None
    if len(plain) > 2 and not when:
        print(f"\n  '{plain[2]}' is not a date I can read. Use 2026-09-18.")
        return 2
    clock = plain[3] if len(plain) > 3 else None
    if clock:
        # parse_time already returns "HH:MM" and accepts both shapes.
        parsed = store.parse_time(clock)
        if not parsed:
            print(f"\n  '{clock}' is not a time I can read. Use 23:59 or 11:59 PM.")
            return 2
        clock = parsed

    kind = store.norm_kind(opt(argv, "--kind", "assignment"))
    said = opt(argv, "--from") or f"Added by hand on {date.today():%d %B %Y}."
    status = "new" if "--new" in argv else "accepted"
    dry = "--dry-run" in argv

    db = store.connect()
    course = find_course(db, code)
    if not course:
        db.close()
        return 1

    # A hand-typed row that duplicates a scraped one is worse than useless:
    # it is a second card for one deadline, and the app cannot tell which is
    # real. Say so and stop rather than guessing.
    clash = [r for r in db.execute(
        "SELECT * FROM dates WHERE course_id = ? AND status != 'dismissed'",
        (course["id"],)).fetchall()
        if store.normalize(r["title"]) == store.normalize(title)]
    if clash:
        print(f"\n  {code.upper()} already has something by that name:\n")
        for r in clash:
            print(f"      {r['due_date'] or 'no date':<12} "
                  f"{store.pretty_time(r['due_time']) or '':<9} "
                  f"{r['status']:<9} {r['title']}")
        print("\n  Not adding a second. Change the title if it really is "
              "different work.")
        db.close()
        return 1

    shown = f"{when or 'no date'} {store.pretty_time(clock) if clock else ''}".strip()
    if dry:
        print(f"\n  Would add to {code.upper()}:  {title}"
              f"\n      {shown}   kind {kind}, {status}")
        db.close()
        return 0

    db.execute(
        """INSERT INTO dates (course_id, title, due_date, due_time, kind,
                              confidence, source_excerpt, pending, dedup_key,
                              status, first_seen, decided_at, resolved_title)
           VALUES (?,?,?,?,?,'stated',?,0,?,?,?,?,?)""",
        (course["id"], title, when, clock, kind, said,
         f"manual:{course['id']}:{store.normalize(title)}", status,
         store.now(), store.now() if status == "accepted" else None,
         store.normalize(title)))
    db.commit()
    db.close()

    print(f"\n  Added to {code.upper()}:  {title}")
    print(f"      {shown}   kind {kind}, {status}")
    if when and status == "accepted":
        start = store.start_by(when, kind)
        print(f"      start by {start}  (the To-do tab will say so)")
        print("\n  Put it on Google Calendar with:  python gcal.py --check")
    elif status == "new":
        print("\n  Waiting in the cards for you to keep or dismiss.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
