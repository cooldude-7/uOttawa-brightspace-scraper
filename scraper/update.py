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
import link_tasks
import paths
import store
import vault


def main():
    """Run a scrape, and record whether it worked.

    The recording is the point of the wrapper. Without a row saying a scrape
    finished cleanly, a dead Pi and a quiet week look identical -- which is
    exactly how the app could go stale for days without anyone noticing.
    """
    argv = sys.argv[1:]

    db = store.connect()
    run_id = store.start_run(db, "update")
    db.commit()
    db.close()

    try:
        scrape(argv)
    except BaseException as e:
        # BaseException, not Exception: collect.get_client() raises SystemExit
        # when a full login is needed, and that is precisely the failure most
        # worth recording rather than letting exit silently.
        db = store.connect()
        store.finish_run(db, run_id, 0, 0, 0, 0,
                         error=f"{type(e).__name__}: {e}"[:400])
        db.commit()
        db.close()
        raise

    db = store.connect()
    store.finish_run(db, run_id, 0, 0, 0, 0)
    db.commit()
    db.close()


def plural(n):
    return "" if n == 1 else "s"


def scrape(argv):
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

        # Tasks arrive undated, because the document that says to install the
        # Arduino IDE does not know when the Arduino lab is. This is the pass
        # that does know -- it sees the whole course at once. Only tasks not
        # yet linked cost anything, so a routine run does no work here.
        if not quiet:
            print("\n-- placing tasks in the term " + "-" * 31)
        try:
            link_tasks.main([])
        except Exception as e:
            # Never let this sink a scrape. Deadlines are the product; timing
            # a to-do is a convenience on top of them.
            print(f"  could not link tasks: {e}")

    # The vault is built from what the scrape just stored, so it belongs at
    # the end of the scrape rather than as a thing to remember. Deterministic
    # and free -- it only reshapes rows that are already there.
    if not quiet:
        print("\n-- vault " + "-" * 51)
    try:
        vault.build()
        vault.push(paths.VAULT, quiet=True)
    except Exception as e:
        # Same reasoning as link_tasks: a vault that failed to rebuild is an
        # inconvenience, a scrape that died taking the deadlines with it is not.
        print(f"  could not rebuild the vault: {e}")

    db = store.connect()
    after = store.summary(db)
    db.close()

    found = after["new"] + after["pending"] - before["new"] - before["pending"]
    # Documents and announcements, counted separately from deadlines. A lecture
    # posted with no date in it is still news -- it is a note in the vault and
    # something to read. Reporting only deadlines made a scrape that picked up
    # five new documents say "Nothing new since last time", which is both wrong
    # and the kind of wrong that stops you trusting the tool.
    added = after["documents"] - before["documents"]
    spent = after["spent"] - before["spent"]
    elapsed = int(time.time() - started)

    print("\n" + "=" * 62)
    if found:
        print(f"  {found} new deadline{plural(found)} found.")
    if added:
        print(f"  {added} new document{plural(added)} or announcement{plural(added)}"
              f" -- now in the vault.")
    if not found and not added:
        print("  Nothing new since last time.")
    print(f"  {after['new']} waiting, {after['pending']} not scheduled yet.")
    print(f"  Took {elapsed}s, cost ${spent:.4f} (${after['spent']:.2f} all time).")
    print("=" * 62)
    if found > 0:
        print("\n  See them:  python web.py     (or python cards.py)")


if __name__ == "__main__":
    main()
