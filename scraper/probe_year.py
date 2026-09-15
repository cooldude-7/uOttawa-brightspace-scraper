r"""
Check a year-typo correction against the weekday it lands on.

    python probe_year.py              every course with a year typo declared
    python probe_year.py MCG2130      just that one

`store.fix_year()` corrects a year a professor mistyped by replacing the
year and keeping the month and day: 2025-09-19 becomes 2026-09-19. That is
the obvious reading of "he meant 2026", and for a date a professor typed
fresh it is right.

It is wrong for a date that came from last year's course shell, and that is
the common case. A year is 52 weeks plus a day, so the same calendar date
one year later falls on the *next* weekday: Friday 19 Sep 2025 becomes
Saturday 19 Sep 2026. Coursework does not move to Saturday. The professor
reusing a shell means the same Friday, which is 18 Sep -- 364 days on, not
365.

The two readings differ by exactly one day, which is small enough to go
unnoticed and large enough to miss a deadline. This prints both for every
date a typo rule touched, with the weekday on each, so the student can say
which one matches what they were told. It reads the database and changes
nothing.
"""

import datetime
import sys

import store


def weekday(iso):
    try:
        return datetime.date.fromisoformat(iso).strftime("%a")
    except (TypeError, ValueError):
        return "???"


def same_weekday(iso, right_year):
    """The date in `right_year` that keeps `iso`'s weekday. -> iso | None.

    Nearest to the naive same-date answer, so it is that date give or take
    a few days rather than a different week.
    """
    try:
        was = datetime.date.fromisoformat(iso)
        naive = datetime.date(right_year, was.month, was.day)
    except (TypeError, ValueError):
        return None
    for delta in (0, -1, 1, -2, 2, -3, 3):
        candidate = naive + datetime.timedelta(days=delta)
        if candidate.weekday() == was.weekday():
            return candidate.isoformat()
    return None


def main(argv):
    only = argv[1].upper() if len(argv) > 1 else None
    typos = store.load_prefs().get("year_typos") or {}
    if not typos:
        print("No year typos declared in me.json -- nothing to check.")
        return 0

    db = store.connect()
    rows = db.execute(
        "SELECT d.title, d.due_date, d.due_time, c.name AS course"
        "  FROM dates d JOIN courses c ON c.id = d.course_id"
        " WHERE d.due_date IS NOT NULL AND d.due_date != ''"
        " ORDER BY d.due_date"
    ).fetchall()

    for code, rule in sorted(typos.items()):
        code = code.upper()
        if only and code != only:
            continue
        try:
            wrong, right = int(rule["wrong"]), int(rule["right"])
        except (KeyError, TypeError, ValueError):
            print(f"{code}: rule in me.json is malformed -- skipped.")
            continue

        mine = [r for r in rows
                if store.course_parts(r["course"])["code"].upper() == code
                and r["due_date"].startswith(f"{right}-")]
        print(f"\n{code}  {wrong} -> {right}   ({len(mine)} dated item(s) stored)")
        if not mine:
            print("  Nothing stored in the corrected year. Either the scrape has "
                  "not run since, or these items were dropped.")
            continue

        print(f"  {'was':<16} {'stored now':<16} {'same weekday':<16} title")
        disagree = 0
        for r in mine:
            now = r["due_date"]
            was = f"{wrong}{now[4:]}"
            keep = same_weekday(was, right)
            flag = ""
            if keep and keep != now:
                disagree += 1
                flag = "  <-- differs"
            time = store.pretty_time(r["due_time"]) if r["due_time"] else ""
            print(f"  {was} {weekday(was):<4} "
                  f"{now} {weekday(now):<4} "
                  f"{(keep or '-'):<11} {weekday(keep) if keep else '':<4} "
                  f"{str(r['title'])[:32]} {time}{flag}")

        if disagree:
            print(f"\n  {disagree} of {len(mine)} land on a different weekday than "
                  f"they did in {wrong}.")
            print("  If the professor reused last year's shell, the 'same weekday' "
                  "column is what he means.")
            print("  If he typed these dates fresh for this year, 'stored now' is.")
            print("  Check one against a document or what he said in class, then "
                  "tell me which -- do not guess.")
        else:
            print("\n  Every date keeps its weekday. The correction is consistent "
                  "either way.")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
