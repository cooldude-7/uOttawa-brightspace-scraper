r"""
Works out your lab section by matching the due date Brightspace shows YOU
against the per-section list the professor wrote in the instructions.

    python mysection.py

Brightspace shows each student their own due date. The instructions list
every section's date. Whichever section's date matches yours, on every
assignment, is your section -- which is evidence rather than a guess.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import store

HERE = Path(__file__).parent
LOCAL = ZoneInfo("America/Toronto")


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
    return dt.astimezone(LOCAL)


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

            rows.append((name, f"{want_date} {want_time}", sections, match))
            for s in match:
                votes[s] += 1

    if not rows:
        raise SystemExit(
            "No assignment lists per-section dates in text AND carries a due "
            "date. Nothing to match against -- run find_dates.py --all first."
        )

    for name, mine, sections, match in rows:
        print(f"\n{name[:56]}")
        print(f"  Brightspace shows you: {mine}")
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
    print(f"Matched {best.upper()} on {count} of {len(rows)} assignments.")
    if len(ranked) > 1 and ranked[1][1] == count:
        tied = ", ".join(s.upper() for s, c in ranked if c == count)
        print(f"TIED with {tied} -- those sections share a slot, so this")
        print("cannot tell them apart. Ask at your lab.")
    elif count == len(rows):
        print(f"Every assignment agrees. You are {best.upper()}.")
    else:
        print(f"Other sections also matched somewhere: "
              f"{', '.join(s.upper() for s, _ in ranked[1:])}")
        print("Treat this as likely rather than certain.")


if __name__ == "__main__":
    main()
