r"""
Your deadlines, and the check / X decision on each one.

    python cards.py                 everything waiting on you
    python cards.py MCG2130         one course
    python cards.py --accepted      what you have said yes to
    python cards.py --dismissed     what you have said no to
    python cards.py --review        go through them one at a time

Deciding here writes to the database. Later the same decisions come from
tapping a card on your phone; this is the same thing without the web page.
"""

import sys
from datetime import date, datetime

import store

WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def when(row):
    """A date a human reads at a glance, with how soon it is."""
    if row["pending"]:
        return "no date yet", ""
    raw = row["due_date"]
    if not raw:
        return "no date", ""
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return raw, ""
    days = (d - date.today()).days
    label = f"{WEEKDAY[d.weekday()]} {d.strftime('%d %b')}"
    if row["due_time"]:
        label += f" {row['due_time']}"
    if days < 0:
        return label, "past"
    if days == 0:
        return label, "TODAY"
    if days == 1:
        return label, "tomorrow"
    if days <= 7:
        return label, f"{days} days"
    return label, f"{days} days"


def show(rows, course_filter=None):
    by_course = {}
    for r in rows:
        if course_filter and course_filter.lower() not in (r["course_name"] or "").lower():
            continue
        by_course.setdefault(r["course_name"], []).append(r)

    if not by_course:
        print("Nothing here.")
        return 0

    shown = 0
    for course, items in by_course.items():
        print(f"\n{course[:60]}")
        print("-" * 60)
        for r in items:
            label, soon = when(r)
            mark = {"high": "  ", "medium": " ?", "low": "??"}.get(r["confidence"], "  ")
            print(f"  [{r['id']:>3}] {mark} {label:<22} {soon:<10} {r['title'][:44]}")
            shown += 1
    return shown


def review(db):
    """One at a time: read the evidence, then decide."""
    rows = store.cards(db, "new")
    if not rows:
        print("Nothing waiting.")
        return

    print(f"{len(rows)} to go through. y = keep, n = dismiss, s = skip, q = stop\n")
    for i, r in enumerate(rows, 1):
        label, soon = when(r)
        print("=" * 64)
        print(f"{i} of {len(rows)}   {r['course_name'][:48]}")
        print(f"\n  {r['title']}")
        print(f"  {label}   {soon}   ({r['kind']}, {r['confidence']} confidence)")
        if r["source_excerpt"]:
            print(f"\n  found in: \"{' '.join(r['source_excerpt'].split())[:200]}\"")

        answer = ""
        while answer not in ("y", "n", "s", "q"):
            answer = input("\n  keep it? [y/n/s/q] ").strip().lower()

        if answer == "q":
            break
        if answer == "s":
            continue
        store.decide(db, r["id"], "accepted" if answer == "y" else "dismissed")
        db.commit()
        print("  kept" if answer == "y" else "  dismissed")


def main():
    db = store.connect()
    args = sys.argv[1:]

    if "--review" in args:
        review(db)
        db.close()
        return

    status = "new"
    for flag, name in (("--accepted", "accepted"), ("--dismissed", "dismissed")):
        if flag in args:
            status = name
    course = next((a for a in args if not a.startswith("--")), None)

    rows = store.cards(db, status)
    count = show(rows, course)

    s = store.summary(db)
    print(f"\n{'-' * 60}")
    print(f"{count} shown   |   {s['new']} waiting, {s['pending']} undated, "
          f"{s['accepted']} kept, {s['dismissed']} dismissed")
    if status == "new" and count:
        print("\nGo through them:  python cards.py --review")
    db.close()


if __name__ == "__main__":
    main()
