r"""
Stores the due dates Brightspace states outright, as opposed to the ones
buried in documents.

Assignments, quizzes, content folders and calendar entries carry real date
fields. Those never reach the AI -- it is told to ignore them so it does
not re-report what is already known -- which meant nothing was storing
them either. This closes that gap.
"""

from datetime import date, datetime, timedelta, timezone

import store


def nth_sunday(year, month, n):
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def to_local(stamp):
    """UTC timestamp -> (YYYY-MM-DD, HH:MM) in Ottawa time, or (None, None).

    Canada's daylight rule since 2007: second Sunday in March to first
    Sunday in November. Computed rather than looked up, because Windows
    Python ships no timezone database.
    """
    if not stamp:
        return None, None
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    year = dt.year
    starts = datetime.combine(nth_sunday(year, 3, 2), datetime.min.time(),
                              timezone.utc) + timedelta(hours=7)
    ends = datetime.combine(nth_sunday(year, 11, 1), datetime.min.time(),
                            timezone.utc) + timedelta(hours=6)
    local = dt + timedelta(hours=-4 if starts <= dt < ends else -5)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def entries(course):
    """Everything in one course that states a date outright."""
    out = []

    # DisplayDue is the date Brightspace only shows on the page, filled in by
    # pagedates.py. It is a dict rather than a UTC stamp, and load() below
    # tells the two apart -- page dates are already Ottawa time.
    for a in course.get("assignments", []):
        when = a.get("DueDate") or a.get("DisplayDue")
        if when:
            out.append((a.get("Name"), when, "assignment"))

    for q in course.get("quizzes", []):
        when = q.get("DueDate") or q.get("EndDate") or q.get("DisplayDue")
        if when:
            out.append((q.get("Name"), when, "quiz"))

    for m in course.get("modules", []):
        if m.get("due"):
            out.append((m.get("title"), m["due"], "assignment"))

    for e in course.get("calendar", []):
        when = e.get("StartDateTime") or e.get("EndDateTime")
        if when:
            out.append((e.get("Title"), when, "session"))

    for cl in course.get("checklists", []):
        for item in cl.get("items", []):
            if item.get("due"):
                out.append((item.get("Name") or item.get("name"),
                            item["due"], "assignment"))

    return [(title, when, kind) for title, when, kind in out if title]


def load(db, collected, window=(None, None)):
    """Store them. -> (added, dropped, shifted), the last two as
    [(course, title, date)] and [(course, title, was, now)].

    Dropped and shifted items are returned rather than counted, because a
    rule that silently discards -- or silently moves -- a deadline is
    exactly the failure this project exists to prevent. If it ever touches
    a real one, it has to be visible.
    """
    start, end = window
    added = 0
    dropped = []
    shifted = []

    for course in collected:
        course_id = store.upsert_course(
            db, course["id"], course["name"], course.get("term"))

        for title, stamp, kind in entries(course):
            if isinstance(stamp, dict):
                # Straight off the page, and already in Ottawa time -- putting
                # it through to_local() would shift it by five hours.
                due, at = stamp.get("date"), stamp.get("time")
                note = (f'Brightspace shows "{stamp.get("label")} ..." on the '
                        f"{kind} list page. It is not in the API: this item is "
                        "restricted to particular students, so its date is a "
                        "special-access override.")
            else:
                due, at = to_local(stamp)
                note = "Brightspace lists this due date on the item itself."
            if not due:
                continue

            # A year the professor mistyped, in a course you have said so
            # about in me.json. Without this the date below looks exactly
            # like a leftover and is dropped.
            fixed, why = store.fix_year(course["name"], due)
            if why:
                shifted.append((course["name"][:26], title, due, fixed))
                due, note = fixed, f"{note} {why}"

            # A course shell reused between terms keeps its old due dates --
            # one GNG2101 assignment still says June. Storing that would put
            # a deadline months in the past on the list.
            if start and not (start <= due <= end):
                dropped.append((course["name"][:26], title, due))
                continue

            added += store.save_dates(db, course_id, None, [{
                "title": title,
                "date": due,
                "time": at,
                "kind": kind,
                "confidence": "high",
                "source_excerpt": note,
            }])

    return added, dropped, shifted
