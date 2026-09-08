r"""
One command: check Brightspace for anything new and add it to your list.

    python update.py              normal run
    python update.py --quiet      only report what changed
    python update.py --no-read    fetch and download, skip the AI step

Logging in happens once and is remembered; documents already read are
never read again, so a routine run costs close to nothing. This is what
gets scheduled to run every 30 minutes later.
"""

import sys
import time
from datetime import datetime

import collect
import download
import exact
import find_dates
import store


def main():
    argv = sys.argv[1:]
    quiet = "--quiet" in argv
    started = time.time()

    db = store.connect()
    before = store.summary(db)
    db.close()

    if not quiet:
        print(f"\n{datetime.now().strftime('%a %d %b, %H:%M')} -- checking Brightspace\n")

    # One session shared across all three steps, so the login is not
    # re-checked three times.
    client = collect.get_client()

    if not quiet:
        print("-- courses and tabs " + "-" * 40)
    collect.main(client=client)

    # Dates Brightspace states outright, before anything is read: cheap,
    # exact, and previously falling through the gap between the two.
    if not quiet:
        print("\n-- due dates Brightspace already knows " + "-" * 21)
    import json

    import paths

    collected = json.loads(paths.COLLECTED.read_text(encoding="utf-8"))
    window = find_dates.term_window(find_dates.current_term())
    db = store.connect()
    got, dropped = exact.load(db, collected, window)
    db.commit()
    db.close()
    print(f"  {got} stored" + (f", {len(dropped)} outside this term:" if dropped else ""))
    for course, title, due in dropped:
        print(f"      {due}  {course:<26} {str(title)[:34]}")
    if dropped:
        print("      (left over from a previous offering -- say if one of these is real)")

    if not quiet:
        print("\n-- files " + "-" * 51)
    download.main(client=client)

    if "--no-read" not in argv:
        if not quiet:
            print("\n-- reading for deadlines " + "-" * 35)
        find_dates.main(["--all", "--model=sonnet"])

    db = store.connect()
    after = store.summary(db)
    db.close()

    found = after["new"] + after["pending"] - before["new"] - before["pending"]
    spent = after["spent"] - before["spent"]
    elapsed = int(time.time() - started)

    print("\n" + "=" * 62)
    if found > 0:
        print(f"  {found} new deadline{'s' if found != 1 else ''} found.")
    else:
        print("  Nothing new since last time.")
    print(f"  {after['new']} waiting, {after['pending']} not scheduled yet.")
    print(f"  Took {elapsed}s, cost ${spent:.4f} (${after['spent']:.2f} all time).")
    print("=" * 62)
    if found > 0:
        print("\n  See them:  python web.py     (or python cards.py)")


if __name__ == "__main__":
    main()
