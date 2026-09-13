r"""Exams and midterms: what is scheduled, and which courses have nothing.

    python exams.py                   every exam-ish item, by course
    python exams.py --on 2026-10-23   just that day
    python exams.py --week 2026-10-23 that week

The second question is the one worth asking. A course with no midterm on
the list is ambiguous in a way a course with one is not: the professor may
not have announced a date, or may have announced it somewhere nothing has
read, or the course may genuinely have no midterm. This prints that as
"nothing found", never as "there is no midterm" -- the difference matters,
because acting on the wrong one costs you a exam you did not study for.
"""

import re
import sys
from datetime import datetime, timedelta

import store

# Deliberately wide. A missed exam is far more expensive than a row you
# glance at and dismiss, so this errs toward showing too much.
EXAMISH = re.compile(
    r"\b(midterm|mid-term|final\s*exam|exam|test\s*\d|in-class\s*test)\b", re.I)

# Things that merely mention an exam without being one.
NOT_EXAM = re.compile(
    r"\b(practice|sample|review|prep|past|solutions?|formula\s*sheet|"
    r"study\s*guide|no\s*exam)\b", re.I)


def is_exam(row):
    title = row["title"] or ""
    if (row["kind"] or "").lower() == "exam":
        return True
    return bool(EXAMISH.search(title)) and not NOT_EXAM.search(title)


def rows(db):
    found = db.execute(
        """SELECT d.*, c.d2l_id AS course_d2l_id, c.name AS course_name
           FROM dates d JOIN courses c ON c.id = d.course_id
           WHERE d.status != 'dismissed' AND d.status != 'resolved'
           ORDER BY d.due_date IS NULL, d.due_date, d.due_time"""
    ).fetchall()
    return store.only_mine(found)


def main(argv):
    db = store.connect()
    try:
        all_rows = rows(db)
        # This term's courses, so a shell from a previous term is not
        # reported as a course missing its midterm.
        courses = {r["name"] for r in db.execute(
            "SELECT name FROM courses WHERE term = (SELECT MAX(term) FROM courses)")}
    finally:
        db.close()

    exams = [r for r in all_rows if is_exam(r)]

    on = week = None
    if "--on" in argv:
        on = argv[argv.index("--on") + 1]
    if "--week" in argv:
        day = datetime.strptime(argv[argv.index("--week") + 1], "%Y-%m-%d").date()
        start = day - timedelta(days=day.weekday())
        week = (start.isoformat(), (start + timedelta(days=6)).isoformat())

    if on:
        hits = [r for r in exams if r["due_date"] == on]
        when = datetime.strptime(on, "%Y-%m-%d").strftime("%A %-d %B %Y")
        print(f"\n  {when}\n")
        if hits:
            for r in hits:
                show(r)
        else:
            print("  Nothing on that day.")
        # An empty answer here is worth qualifying, since "no exam found"
        # and "no exam" are different claims.
        print("\n  That is what has been read so far -- see the courses with"
              "\n  nothing found below before treating the day as clear.\n")
        missing(exams, courses)
        return

    if week:
        hits = [r for r in exams if r["due_date"] and week[0] <= r["due_date"] <= week[1]]
        print(f"\n  Week of {week[0]}\n")
        for r in hits or []:
            show(r)
        if not hits:
            print("  Nothing that week.")
        print()
        missing(exams, courses)
        return

    dated = [r for r in exams if r["due_date"] and not r["pending"]]
    undated = [r for r in exams if not r["due_date"] or r["pending"]]

    print(f"\n  {len(dated)} exam{'s' if len(dated) != 1 else ''} with a date\n")
    for r in dated:
        show(r)

    if undated:
        print(f"\n  {len(undated)} named but not scheduled\n")
        for r in undated:
            show(r)

    print()
    missing(exams, courses)


def show(r):
    parts = store.course_parts(r["course_name"])
    when = r["due_date"] or "no date"
    at = f" {r['due_time']}" if r["due_time"] else ""
    if r["due_date"]:
        d = datetime.strptime(r["due_date"], "%Y-%m-%d")
        when = d.strftime("%a %d %b")
    mark = "" if r["status"] == "new" else f"  [{r['status']}]"
    print(f"    {when}{at:<9}  {parts['code']:<9} {r['title'][:46]}{mark}")
    if r["source_excerpt"]:
        print(f"                  “{' '.join(r['source_excerpt'].split())[:78]}”")


def missing(exams, courses):
    """Courses with no exam on the list at all."""
    have = {store.course_parts(r["course_name"])["code"] for r in exams}
    blank = sorted({store.course_parts(c)["code"] for c in courses} - have)
    if not blank:
        print("  Every course has at least one exam on the list.")
        return
    print("  No exam found yet in:")
    for code in blank:
        print(f"    {code}")
    print("\n  That means nothing has been read that states a date -- not that"
          "\n  the course has no midterm. Check the syllabus or ask, then say"
          "\n  so and it can be added by hand.")


if __name__ == "__main__":
    main(sys.argv[1:])
