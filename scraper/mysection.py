r"""
Works out your lab section by matching the due date Brightspace shows YOU
against the per-section list the professor wrote in the instructions.

    python mysection.py

Brightspace shows each student their own due date. The instructions list
every section's date. Whichever section's date matches yours, on every
assignment, is your section -- which is evidence rather than a guess.
"""

import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import store

HERE = Path(__file__).parent


def nth_sunday(year, month, n):
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def ottawa_offset(moment):
    """Hours behind UTC in Ottawa, without needing a timezone database.

    Windows Python ships no IANA data, and requiring a package for one
    subtraction is not worth it. Canada has used the same rule since 2007:
    daylight time from the second Sunday in March to the first Sunday in
    November, changing at 02:00 local.
    """
    year = moment.year
    starts = datetime.combine(nth_sunday(year, 3, 2),
                              datetime.min.time(), timezone.utc) + timedelta(hours=7)
    ends = datetime.combine(nth_sunday(year, 11, 1),
                            datetime.min.time(), timezone.utc) + timedelta(hours=6)
    return -4 if starts <= moment < ends else -5


def local(stamp):
    """Brightspace stores UTC; deadlines are written in Ottawa time."""
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt + timedelta(hours=ottawa_offset(dt))


def main():
    collected = json.loads((HERE / "collected.json").read_text(encoding="utf-8"))
    found = json.loads((HERE / "found_dates.json").read_text(encoding="utf-8"))

    # Extracted dates, keyed by the document they came from.
    by_doc = {}
    for block in found:
        by_doc.setdefault(block["document"], []).extend(block["dates"])

    votes = Counter()
    rows = []

    for course in collected:
        for a in course.get("assignments", []):
            mine = local(a.get("DueDate"))
            name = a.get("Name") or ""
            candidates = by_doc.get(name) or []
            if not mine or not candidates:
                continue

            sections = {}
            for d in candidates:
                section = store.audience(d.get("title"))[0]
                if section and d.get("date"):
                    sections[section] = (d["date"], d.get("time") or "")
            if len(sections) < 2:
                continue        # not a per-section list

            want_date = mine.strftime("%Y-%m-%d")
            want_time = mine.strftime("%H:%M")
            match = [s for s, (dd, tt) in sections.items()
                     if dd == want_date and (not tt or tt == want_time)]

            # A due date nowhere near any listed date is a stale field left
            # from a previous offering, not a failed match. It should not
            # count against the section it cannot possibly confirm.
            listed = {dd for dd, _ in sections.values()}
            stale = not match and want_date < min(listed)

            rows.append((name, f"{want_date} {want_time}", sections, match, stale,
                         course["id"]))
            for s in match:
                votes[s] += 1

    if not rows:
        raise SystemExit(
            "No assignment lists per-section dates in text AND carries a due "
            "date. Nothing to match against -- run find_dates.py --all first."
        )

    for name, mine, sections, match, stale, _oid in rows:
        print(f"\n{name[:56]}")
        flag = "   <-- STALE, before every listed date" if stale else ""
        print(f"  Brightspace shows you: {mine}{flag}")
        for s in sorted(sections):
            dd, tt = sections[s]
            mark = "  <-- matches you" if s in match else ""
            print(f"    {s.upper():<4} {dd} {tt}{mark}")

    print("\n" + "=" * 58)
    if not votes:
        print("Your due date matched no section's listed date.")
        print("The professor may be setting one date for everyone, in which")
        print("case it says nothing about your section -- ask at your lab.")
        return

    ranked = votes.most_common()
    best, count = ranked[0]
    usable = [r for r in rows if not r[4]]
    stale_count = len(rows) - len(usable)

    print(f"Matched {best.upper()} on {count} of {len(usable)} usable assignments.")
    if stale_count:
        print(f"({stale_count} ignored -- Brightspace's due date there is stale.)")

    rivals = [s.upper() for s, c in ranked[1:]]
    if len(ranked) > 1 and ranked[1][1] == count:
        print(f"TIED with {', '.join(s.upper() for s, c in ranked if c == count)}"
              " -- those sections share a slot, so this cannot tell them apart.")
        return
    if count < len(usable):
        print(f"Some did not match{' (also seen: ' + ', '.join(rivals) + ')' if rivals else ''}.")
        print("Treat this as likely rather than certain.")
        return

    print(f"Every usable assignment agrees. You are {best.upper()}.")
    course_id = usable[0][5]
    if "--save" in sys.argv:
        prefs = store.load_prefs()
        prefs.setdefault("sections", {})[str(course_id)] = best
        store.save_prefs(prefs)
        print(f"\nSaved. Other sections will be hidden from your cards.")
        print("Undo with:  python cards.py --all-sections")
    else:
        print(f"\nTo hide the other sections:  python mysection.py --save")


if __name__ == "__main__":
    main()
