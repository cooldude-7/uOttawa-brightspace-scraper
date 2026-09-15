r"""
Check what a year-typo correction actually put in the database.

    python probe_year.py              every course with a year typo declared
    python probe_year.py MCG2130      just that one
    python probe_year.py MCG2130 --raw   every date field Brightspace returns

`store.fix_year()` corrects a year a professor mistyped, and the risk it
carries is that the correction outlives the problem. Once the professor
fixes his own dates in Brightspace the rule stops firing -- but the rows it
already wrote stay, sitting beside the new correct ones as a second deadline
a day or so away. MCG2130 was exactly this: eight assignments, sixteen rows.

So this prints every stored date in the corrected year with where it came
from and when it first appeared, and flags any title stored on more than one
date. `--raw` prints what Brightspace publishes today, which is what decides
it: a date no longer in that list is a leftover, not a deadline.

It reads the database and changes nothing.
"""

import datetime
import re
import sys

import store


def weekday(iso):
    try:
        return datetime.date.fromisoformat(iso).strftime("%a")
    except (TypeError, ValueError):
        return "???"


DATE_FIELD = re.compile(r"date|due|end|start", re.I)


def raw_dump(code):
    """Print every date field Brightspace returns for one course.

    The `dates` table records what was stored, not which field it came from,
    so when a title turns up twice this is the only way to tell whether
    Brightspace really published two dates or something here duplicated one.
    """
    import json

    import paths

    collected = json.loads(paths.COLLECTED.read_text(encoding="utf-8"))
    courses = collected if isinstance(collected, list) else collected.get("courses", [])
    for course in courses:
        if store.course_parts(course.get("name", ""))["code"].upper() != code:
            continue
        print(f"\n{course.get('name', '')}\n")
        for source in ("assignments", "quizzes", "modules", "topics",
                       "calendar", "checklists"):
            items = course.get(source) or []
            if not items:
                continue
            print(f"  [{source}]  {len(items)} item(s)")
            for item in items:
                if source == "checklists":
                    item_list = item.get("items") or []
                else:
                    item_list = [item]
                for one in item_list:
                    if not isinstance(one, dict):
                        continue
                    title = (one.get("Name") or one.get("name")
                             or one.get("Title") or one.get("title") or "?")
                    dates = {k: v for k, v in one.items()
                             if DATE_FIELD.search(k) and v
                             and not isinstance(v, (list, dict))}
                    if not dates:
                        continue
                    print(f"    {str(title)[:38]}")
                    for k, v in sorted(dates.items()):
                        print(f"        {k:<26} {v}")
        return
    print(f"No course matching {code} in collected.json.")


def main(argv):
    only = argv[1].upper() if len(argv) > 1 else None
    if only and "--raw" in argv:
        raw_dump(only)
        return 0
    typos = store.load_prefs().get("year_typos") or {}
    if not typos:
        print("No year typos declared in me.json -- nothing to check.")
        return 0

    db = store.connect()
    rows = db.execute(
        "SELECT d.title, d.due_date, d.due_time, d.status, d.first_seen,"
        "       c.name AS course, doc.title AS doc"
        "  FROM dates d JOIN courses c ON c.id = d.course_id"
        "  LEFT JOIN documents doc ON doc.id = d.document_id"
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

        print(f"  {'due':<16} {'time':<8} {'status':<9} {'first seen':<11} "
              f"{'from':<22} title")
        for r in mine:
            now = r["due_date"]
            src = r["doc"] or "(Brightspace field)"
            time = store.pretty_time(r["due_time"]) if r["due_time"] else "-"
            print(f"  {now} {weekday(now):<4} {time:<8} "
                  f"{(r['status'] or '?'):<9} {str(r['first_seen'])[:10]:<11} "
                  f"{str(src)[:22]:<22} {str(r['title'])[:30]}")

        # A title stored twice on two dates is the thing worth seeing.
        seen = {}
        for r in mine:
            seen.setdefault(str(r["title"]).strip(), []).append(r)
        twins = {k: v for k, v in seen.items() if len(v) > 1}
        if twins:
            print(f"\n  {len(twins)} title(s) stored on more than one date:")
            for title, rows_ in sorted(twins.items()):
                days = ", ".join(f"{x['due_date']} {weekday(x['due_date'])}"
                                 for x in rows_)
                print(f"    {title[:34]:<34} {days}")
            print("\n  Compare each against `--raw`. A date Brightspace no longer"
                  " publishes is a\n  leftover from an earlier scrape, not a second"
                  " deadline.")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
