r"""Documents that are themselves work, with no deadline attached.

    python posted_work.py            list what matches, store nothing
    python posted_work.py --store    store them as undated to-dos

A sheet of DGD questions says nothing about when to do it, so `find_dates`
correctly finds no date in it and it never reaches the list -- the document
IS the work, rather than mentioning work. That gap is what this fills.

Recognised by title, deliberately. Asking the model "is this itself work?"
would be more accurate on odd names but means re-reading every document,
and this is a convenience rather than a deadline: the cost of being wrong
is one row dismissed, not a missed submission. A title rule is also a rule
the student can read and argue with, which a model's judgement is not.

Nothing here invents a date. These are stored with no date at all, which
is the truth about them.
"""

import re
import sys

import store

# Titles that name work to be done. Word boundaries throughout -- "questions"
# must not fire on "questionnaire".
WORK = re.compile(
    r"\b(dgd|questions?|problem\s*set|problems|exercises?|practice|worksheet|"
    r"tutorial)\b", re.I)

# Titles that merely look like it. "Solutions" is the answers rather than
# the work, and a lecture handed out already filled in is not homework.
NOT_WORK = re.compile(
    r"\b(solutions?|answers?|answer\s*key|faq|frequently\s+asked|filled|"
    r"marking|rubric|syllabus)\b", re.I)


def tidy(title):
    """A file name as a person would write it."""
    return " ".join(re.sub(r"[_]+", " ", title or "").split())


def looks_like_work(title):
    return bool(WORK.search(title or "")) and not NOT_WORK.search(title or "")


def find(db):
    """-> [(course_id, course_name, document_title)] worth listing."""
    rows = db.execute(
        """SELECT d.id, d.course_id, d.title, c.name AS course_name
           FROM documents d JOIN courses c ON c.id = d.course_id
           WHERE d.kind = 'file'
           ORDER BY c.name, d.title""").fetchall()
    return [(r["course_id"], r["course_name"], r["title"], r["id"])
            for r in rows if looks_like_work(r["title"])]


def load(db):
    """Store each as an undated to-do. Returns (added, [names])."""
    added, names = 0, []
    for course_id, course_name, title, doc_id in find(db):
        n = store.save_dates(db, course_id, doc_id, [{
            "title": tidy(title),
            "date": None,
            "time": None,
            "kind": "todo",
            "confidence": "high",
            "source_excerpt": (
                "Posted in this course and its title says it is work to do. "
                "No deadline was stated anywhere, so none is shown -- this is "
                "something to get through, not something due."),
        }])
        if n:
            # Marked as considered-and-unanchored, the same way link_tasks
            # marks a task it could not place. These have no anchor by
            # definition -- the whole point is that nothing states a date --
            # so this keeps them out of link_tasks entirely rather than
            # paying a penny each scrape to be told so again.
            db.execute(
                """UPDATE dates SET linked_to = '' WHERE course_id = ?
                   AND title = ? AND due_date IS NULL AND linked_to IS NULL""",
                (course_id, tidy(title)))
            names.append((store.course_parts(course_name)["code"], tidy(title)))
        added += n
    return added, names


def main(argv):
    db = store.connect()
    try:
        matches = find(db)
        print(f"\n  {len(matches)} document(s) look like work to do\n")
        for _, course_name, title, _ in matches:
            print(f"    {store.course_parts(course_name)['code']:<9} {tidy(title)[:58]}")

        if "--store" not in argv:
            total = db.execute(
                "SELECT COUNT(*) FROM documents WHERE kind = 'file'").fetchone()[0]
            print(f"\n  out of {total} files. Nothing stored -- add --store.\n")
            return

        added, names = load(db)
        db.commit()
        print(f"\n  {added} added as undated to-do{'s' if added != 1 else ''}"
              + (" (the rest were already there)" if added < len(matches) else ""))
        for code, title in names:
            print(f"    {code:<9} {title[:58]}")
        print()
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
