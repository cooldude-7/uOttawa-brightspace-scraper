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

import json
import re
import sys

import paths
import store
from download import safe_name

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


def words(title):
    """A title with its separators turned into spaces.

    An underscore is a word character to a regex, so `\btutorial\b` does not
    match "Tutorial_1" -- the whole thing reads as one word. Professors name
    files that way constantly: Tutorial_1, Lecture_2, Linear_Algebra___DGD_1.
    The rule silently matched none of them, which is exactly the kind of
    quiet miss this project exists to prevent, and the student found it by
    asking why their tutorial questions were not in the list.
    """
    return re.sub(r"[_\-.]+", " ", title or "")


def looks_like_work(title):
    clean = words(title)
    return bool(WORK.search(clean)) and not NOT_WORK.search(clean)


def find(db):
    """-> [(course_id, course_name, title, document_id|None)] worth listing.

    Read off the extracted files on disk, NOT the documents table. A row
    only reaches that table when find_dates judged the text worth an API
    call, and its test is "does this contain dates or task language" -- so
    a sheet of practice questions, which has neither, never gets one. The
    exact documents this exists to find are the ones deliberately excluded
    from there. Looking in the table found nothing out of 43 files, which
    is how this was noticed.
    """
    try:
        courses = json.loads(paths.COLLECTED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        courses = []
    by_folder = {safe_name(c["name"], 40): c for c in courses}

    out = []
    root = paths.EXTRACTED
    for folder in sorted(root.iterdir()) if root.exists() else []:
        if not folder.is_dir():
            continue
        course = by_folder.get(folder.name)
        if not course:
            continue
        course_id = store.upsert_course(
            db, course["id"], course["name"], course.get("term"))
        for f in sorted(folder.glob("*.txt")):
            if looks_like_work(f.stem):
                out.append((course_id, course["name"], f.stem, None))
    return out


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
            total = sum(1 for folder in (paths.EXTRACTED.iterdir()
                                         if paths.EXTRACTED.exists() else [])
                        if folder.is_dir() for _ in folder.glob("*.txt"))
            print(f"\n  out of {total} extracted files. "
                  f"Nothing stored -- add --store.\n")
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
