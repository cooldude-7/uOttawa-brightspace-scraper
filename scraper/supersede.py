r"""
Retire stored dates that Brightspace has since replaced.

    python supersede.py               list them, change nothing
    python supersede.py MCG2130       just that course
    python supersede.py --apply       retire them

A correction rule outlives the problem it was written for. MCG2130 is the
case: the professor had last year's dates in Brightspace, `--year-typo
MCG2130 2025 2026` shifted them, and then he fixed his own dates. The
shifted rows stayed. Eight assignments, sixteen rows, each pair a day
apart -- and the stale one sits a day *later* than the real deadline,
which is the direction that loses marks.

Deduplication cannot help: a different date is a different `event_key`,
which is exactly what keeps five lab sections apart.

THE CONDITION, WHICH IS THE WHOLE POINT

A row is retired only when all of these hold:

  1. Brightspace publishes a date for this title **today**.
  2. The row's own date is **not** one of the dates it publishes.
  3. Another stored row for the same title **already holds** a date it
     does publish.

Condition 3 is what makes this safe. Nothing is ever retired unless the
real date is already on file, so there is no arrangement of the data
where this loses a deadline -- the worst case is that it does nothing.
An item Brightspace has stopped publishing is left alone, because silence
is not the same as a correction, and a date read out of a document is
never touched at all.

Rows are marked `resolved`, never deleted -- the same state `--tidy` uses
and `store.py --restore` undoes. Every one is printed by name.
"""

import json
import sys

import exact
import paths
import store


def published(course):
    """Every (date, time) Brightspace states for this course today.

    -> {normalised title: {(date, time), ...}}
    """
    out = {}
    for title, when, _kind, _active in exact.entries(course):
        if isinstance(when, dict):          # a page date, already Ottawa time
            day, clock = when.get("date"), when.get("time")
        else:
            day, clock = exact.to_local(when)
        if day:
            out.setdefault(store.normalize(title), set()).add((day, clock or None))
    return out


def find(db, only=None):
    """-> [(row, course_code, {dates Brightspace publishes for it})]"""
    collected = json.loads(paths.COLLECTED.read_text(encoding="utf-8"))
    by_d2l = {c.get("id"): c for c in collected}
    out = []

    for course in db.execute("SELECT * FROM courses ORDER BY name").fetchall():
        code = store.course_parts(course["name"])["code"]
        if only and code.upper() != only.upper():
            continue
        raw = by_d2l.get(course["d2l_id"])
        if not raw:
            continue
        now_pub = published(raw)
        if not now_pub:
            continue

        rows = db.execute(
            """SELECT * FROM dates
                WHERE course_id = ? AND status IN ('new', 'accepted')
                  AND due_date IS NOT NULL AND due_date != ''
                  AND (linked_to IS NULL OR linked_to = '')
                ORDER BY due_date""", (course["id"],)).fetchall()

        # What each title currently has on file, so condition 3 can be checked.
        on_file = {}
        for r in rows:
            on_file.setdefault(store.normalize(r["title"]), set()).add(
                (r["due_date"], r["due_time"] or None))

        for r in rows:
            key = store.normalize(r["title"])
            pub = now_pub.get(key)
            # Condition 1. It overlaps condition 3 -- nothing published means
            # nothing to match on file either -- but it is the one that says
            # *why* a withdrawn item is left alone, so it stays explicit.
            if not pub:
                continue
            mine = (r["due_date"], r["due_time"] or None)
            if mine in pub:                               # 2. this is a real one
                continue
            if not (on_file.get(key, set()) & pub):       # 3. real date on file?
                continue
            out.append((r, code, pub))

    return out


def main(argv):
    only = next((a for a in argv[1:] if not a.startswith("--")), None)
    apply_it = "--apply" in argv

    db = store.connect()
    found = find(db, only)

    if not found:
        print("\n  Nothing superseded. Every stored date is one Brightspace "
              "still publishes,\n  or the real one is not on file to compare "
              "against.")
        db.close()
        return 0

    print(f"\n  {len(found)} stored date(s) Brightspace has replaced:\n")
    print(f"  {'stored':<17} {'now published':<17} {'status':<9} title")
    calendared = []
    for r, code, pub in found:
        real = ", ".join(f"{d} {store.pretty_time(t) if t else ''}".strip()
                         for d, t in sorted(pub))
        stored = f"{r['due_date']} {store.pretty_time(r['due_time']) if r['due_time'] else ''}"
        print(f"  {stored.strip():<17} {real:<17} {(r['status'] or '?'):<9} "
              f"{code} {str(r['title'])[:30]}")
        if r["gcal_event_id"]:
            calendared.append(r)

    if not apply_it:
        print("\n  Nothing changed. Read the list above -- if any of those is a "
              "real deadline,\n  say so rather than running this. Otherwise:"
              "\n\n      python supersede.py" + (f" {only}" if only else "") +
              " --apply\n")
        db.close()
        return 0

    for r, _code, _pub in found:
        db.execute("UPDATE dates SET status = 'resolved', decided_at = ? WHERE id = ?",
                   (store.now(), r["id"]))
    db.commit()
    db.close()

    print(f"\n  Retired {len(found)}. They are marked `resolved`, not deleted --"
          "\n  `python store.py --restore` brings them back.")
    if calendared:
        print(f"\n  {len(calendared)} of them had a Google Calendar event. "
              "Clear those with:\n      python gcal.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
