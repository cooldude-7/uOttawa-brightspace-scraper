r"""Keep a recoverable copy of the database inside the vault.

The vault is pushed to a private git repo every scrape. The database is
not, and it is the irreplaceable half: every accept and dismiss, every
crossing-off, and the `text_hash` memory that is the only reason a scrape
costs $0.00 instead of re-reading 90,000 words. All of it lives on one USB
stick, and sticks fail without warning.

So each scrape writes a plain SQL dump into the vault, where the existing
push carries it to GitHub. Text rather than a binary copy, deliberately:
git stores successive versions of text as small differences, and a dump can
be read, inspected and restored by hand with no software but sqlite3.

    python backup.py            write the dump
    python backup.py --restore  rebuild a database from it (asks first)

The `runs` table is left out. It gains a row every hour whether or not
anything about your courses changed, which would make the vault commit
hourly forever and bury the history of real changes. Losing it costs the
heartbeat its memory of past runs and nothing else.
"""

import sqlite3
import sys
from datetime import datetime, timezone

import paths

SKIP_TABLES = {"runs", "sqlite_sequence"}
DUMP = lambda: paths.VAULT / "_backup" / "deadlines.sql"


def dump(quiet=False):
    """Write the dump. Returns the path, or None if there is nothing to copy."""
    if not paths.DB.exists():
        if not quiet:
            print("  no database yet -- nothing to back up")
        return None

    # A read-only connection over the live file. sqlite3's iterdump reads
    # through the same WAL the scrape writes to, so this is consistent
    # without stopping anything or holding a write lock.
    db = sqlite3.connect(f"file:{paths.DB}?mode=ro", uri=True)
    try:
        lines = []
        for line in db.iterdump():
            # iterdump emits CREATE/INSERT per table in order; skipping the
            # noisy table means skipping its CREATE and all its INSERTs.
            if any(f'"{t}"' in line or f" {t} " in line for t in SKIP_TABLES):
                continue
            lines.append(line)
        rows = sum(1 for line in lines if line.startswith("INSERT"))
    finally:
        db.close()

    body = (
        f"-- Brightspace scraper database, {datetime.now(timezone.utc).isoformat()}\n"
        f"-- {rows:,} rows. The runs table is deliberately not included.\n"
        f"--\n"
        f"-- To restore onto a clean machine:\n"
        f"--     python backup.py --restore\n"
        f"-- or, by hand:\n"
        f"--     sqlite3 brightspace.db < deadlines.sql\n\n"
        + "\n".join(lines) + "\n"
    )

    out = DUMP()
    out.parent.mkdir(parents=True, exist_ok=True)
    # Only rewrite when something actually changed, so an unchanged database
    # does not produce a vault commit.
    if out.exists() and out.read_text(encoding="utf-8").split("\n", 2)[2:] == body.split("\n", 2)[2:]:
        if not quiet:
            print(f"  database unchanged ({rows:,} rows)")
        return out
    out.write_text(body, encoding="utf-8")
    if not quiet:
        print(f"  database backed up: {rows:,} rows -> {out}")
    return out


def restore(argv):
    src = DUMP()
    if not src.exists():
        sys.exit(f"No dump at {src}")
    if paths.DB.exists() and "--force" not in argv:
        sys.exit(
            f"{paths.DB} already exists.\n"
            "Restoring would replace it. Move it aside first, or pass --force\n"
            "if you are certain the current one is the broken copy.")

    db = sqlite3.connect(paths.DB)
    try:
        db.executescript(src.read_text(encoding="utf-8"))
        db.commit()
    finally:
        db.close()

    # The dump leaves the runs table out, so a restored file is missing it.
    # Applying the schema explicitly rather than relying on store.connect()
    # to do it: connect() initialises the schema once per process, so in any
    # process that has already opened a database it would skip this and hand
    # back a file that breaks on the first heartbeat.
    import store
    db = sqlite3.connect(paths.DB)
    try:
        db.executescript(store.SCHEMA)
        db.commit()
    finally:
        db.close()

    db = store.connect()
    try:
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("courses", "documents", "dates", "runs")}
    finally:
        db.close()
    print(f"  restored from {src}")
    for table, n in counts.items():
        print(f"    {table:<12} {n:,}")
    print("\n  The runs table is empty by design -- the heartbeat starts fresh.")


if __name__ == "__main__":
    if "--restore" in sys.argv:
        restore(sys.argv[1:])
    else:
        dump()
