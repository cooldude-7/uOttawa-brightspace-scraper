r"""
The memory. A SQLite database that remembers what has been read, what
deadlines were found, and which ones you have already dealt with.

Two things earn their keep here:

  text_hash   a fingerprint of each document. Unchanged means already read,
              so it is never sent to the AI twice. This is what makes running
              every 30 minutes cost nothing.

  dedup_key   a fingerprint of each deadline. A re-scrape that finds the same
              deadline again matches the key, sees you already dismissed it,
              and stays quiet. Without this you would dismiss the same midterm
              every half hour.
"""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import paths

DB_PATH = paths.DB
PREFS_PATH = paths.PREFS


# "MCG2130  A00  (2026 Fall) Thermodynamics I 20269"
#  -> code MCG2130, section A00, title "Thermodynamics I"
COURSE_NAME = re.compile(
    r"^\s*(?P<code>[A-Z]{2,4}\s?\d{4})\s+(?P<section>[A-Z0-9]{2,4})\s+(?P<rest>.*)$")


def course_parts(name):
    """Split a Brightspace course name into something a person recognises.

    The full name carries the code, the section, sometimes a bracketed
    component and always the five-digit term stamp -- none of which help
    someone who cannot remember whether MCG2360 is materials or thermo.
    """
    name = " ".join(str(name or "").split())
    m = COURSE_NAME.match(name)
    if not m:
        return {"code": name[:8].strip(), "section": "", "title": name}

    rest = m.group("rest")
    rest = re.sub(r"\s*\b\d{5}\b\s*$", "", rest)        # trailing term stamp
    rest = re.sub(r"\[[^\]]*\]", " ", rest)               # [ LEC ] / [ LAB ]
    rest = re.sub(r"^\s*\(\s*\d{4}[^)]*\)\s*", "", rest)  # leading (2026 Fall)
    rest = " ".join(rest.split())

    return {"code": " ".join(m.group("code").split()),
            "section": m.group("section"),
            "title": rest or name}


def load_prefs():
    """Your lab section and group, so other people's deadlines stay hidden."""
    if PREFS_PATH.exists():
        try:
            return json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_prefs(prefs):
    PREFS_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY,
    d2l_id      INTEGER UNIQUE NOT NULL,
    code        TEXT,
    name        TEXT NOT NULL,
    term        TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    course_id    INTEGER NOT NULL REFERENCES courses(id),
    kind         TEXT NOT NULL,
    title        TEXT NOT NULL,
    text_hash    TEXT NOT NULL,
    word_count   INTEGER,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    last_read_at TEXT,
    UNIQUE (course_id, kind, title)
);

CREATE TABLE IF NOT EXISTS dates (
    id             INTEGER PRIMARY KEY,
    course_id      INTEGER NOT NULL REFERENCES courses(id),
    document_id    INTEGER REFERENCES documents(id),
    title          TEXT NOT NULL,
    due_date       TEXT,
    due_time       TEXT,
    kind           TEXT,
    confidence     TEXT,
    source_excerpt TEXT,
    pending        INTEGER NOT NULL DEFAULT 0,
    dedup_key      TEXT UNIQUE NOT NULL,
    resolved_title TEXT,
    status         TEXT NOT NULL DEFAULT 'new',
    gcal_event_id  TEXT,
    first_seen     TEXT NOT NULL,
    decided_at     TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    mode        TEXT,
    docs_seen   INTEGER DEFAULT 0,
    docs_read   INTEGER DEFAULT 0,
    dates_found INTEGER DEFAULT 0,
    cost_usd    REAL DEFAULT 0,
    error       TEXT
);

"""

# Separate from the tables above: an index naming a column that migration is
# about to add cannot be created before migration has run.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_dates_status ON dates(status, due_date);
CREATE INDEX IF NOT EXISTS idx_dates_resolved ON dates(course_id, resolved_title);
CREATE INDEX IF NOT EXISTS idx_docs_course ON documents(course_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_prepared = False


def connect():
    # Without a timeout SQLite gives up the instant another connection holds
    # the write lock, which a web request and a scrape happening together
    # will do routinely. Thirty seconds is far longer than any write here.
    db = sqlite3.connect(DB_PATH, timeout=30.0)
    db.row_factory = sqlite3.Row
    # Append-style writes rather than scattered ones -- see PLAN.md section 4.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA foreign_keys=ON")

    # Schema and migration are process-wide work, not per-connection work.
    # Running them on every request took a write lock for no reason.
    global _prepared
    if not _prepared:
        db.executescript(SCHEMA)
        migrate(db)
        db.executescript(INDEXES)
        _prepared = True
    return db


def restore(db):
    """Undo automatic merges, so a corrected tidy can redo them properly.

    Only rows retired by tidy come back; anything you decided yourself is
    left alone. Merged rows are marked, never deleted, precisely so a bad
    merge rule stays recoverable.
    """
    changed = db.execute(
        "UPDATE dates SET status = 'new', decided_at = NULL WHERE status = 'resolved'"
    ).rowcount
    # Comparison keys were computed by the old rule and are now wrong.
    for row in db.execute("SELECT id, title FROM dates").fetchall():
        db.execute("UPDATE dates SET resolved_title = ? WHERE id = ?",
                   (normalize(row["title"]), row["id"]))
    db.commit()
    return changed


def migrate(db):
    """Add columns a previous version's database will not have."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(dates)")}
    if "resolved_title" not in columns:
        db.execute("ALTER TABLE dates ADD COLUMN resolved_title TEXT")
    # A task with no date of its own, tied to something that has one: "install
    # the Arduino IDE" belongs before Lab 5, not in week one. linked_to names
    # what it must precede, and the due_date is that thing's date -- inferred,
    # never stated, so it is labelled as such wherever it is shown.
    if "linked_to" not in columns:
        db.execute("ALTER TABLE dates ADD COLUMN linked_to TEXT")
    if "linked_why" not in columns:
        db.execute("ALTER TABLE dates ADD COLUMN linked_why TEXT")
    missing = db.execute(
        "SELECT id, title FROM dates WHERE resolved_title IS NULL").fetchall()
    for row in missing:
        db.execute("UPDATE dates SET resolved_title = ? WHERE id = ?",
                   (normalize(row["title"]), row["id"]))
    if missing:
        db.commit()


def informative(row):
    """Rank rows so the survivor of a merge is the most useful one."""
    confidence = {"high": 0, "medium": 1, "low": 2}.get(row["confidence"], 3)
    vague = 1 if row["kind"] in (None, "other") else 0
    # A longer title usually carries the section or group; prefer it on ties.
    return (confidence, vague, -len(row["title"] or ""))


def tidy(db):
    """Collapse events already stored more than once.

    The same exam mentioned in six documents arrived as six rows before
    titles were compared loosely. Within a course, rows naming the same
    event keep the most informative one -- a real date beats no date, and
    higher confidence beats lower -- and the rest are marked resolved so
    they leave the list without being treated as dismissed.
    """
    rows = db.execute("SELECT * FROM dates WHERE status = 'new' ORDER BY id").fetchall()
    merged = 0

    # Pass 1: dated rows describing one event at one time for one audience.
    slots = {}
    for row in rows:
        if not row["due_date"]:
            continue
        slots.setdefault(
            event_key(row["course_id"], row["due_date"], row["due_time"], row["title"]),
            []).append(row)

    survivors = {}
    for key, bucket in slots.items():
        bucket.sort(key=informative)
        survivors[key] = bucket[0]
        for loser in bucket[1:]:
            db.execute("UPDATE dates SET status = 'resolved', decided_at = ? WHERE id = ?",
                       (now(), loser["id"]))
            merged += 1

    # Pass 2: undated rows, which have no date to key on, fall back to the
    # loosened title -- and any whose date has since been learned is retired.
    by_title = {}
    dated_titles = {(r["course_id"], r["resolved_title"])
                    for r in survivors.values() if r["due_date"]}
    for row in rows:
        if row["due_date"]:
            continue
        ident = (row["course_id"], row["resolved_title"])
        if ident in dated_titles:
            db.execute("UPDATE dates SET status = 'resolved', decided_at = ? WHERE id = ?",
                       (now(), row["id"]))
            merged += 1
            continue
        by_title.setdefault(ident, []).append(row)

    for bucket in by_title.values():
        bucket.sort(key=informative)
        for loser in bucket[1:]:
            db.execute("UPDATE dates SET status = 'resolved', decided_at = ? WHERE id = ?",
                       (now(), loser["id"]))
            merged += 1

    db.commit()
    return merged


def fingerprint(text):
    """Ignore whitespace churn so a reformat does not read as a change."""
    return hashlib.sha256(" ".join((text or "").split()).encode()).hexdigest()


SECTION_RE = re.compile(r"\b([a-e])\s*(\d)\b")
GROUP_RE = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")


def audience(title):
    """Who a deadline is for: a lab section (A3) or a day group (Wednesday).

    This is the part of a title that must NOT be collapsed. Section A2 and
    section A5 hand the same thing in at the same hour on the same day, and
    they are two deadlines, not one.
    """
    text = (title or "").lower()
    section = SECTION_RE.search(text)
    group = GROUP_RE.search(text)
    return (f"{section.group(1)}{section.group(2)}" if section else "",
            group.group(1) if group else "")


def normalize(title):
    """Reduce a title to the event it names, keeping who it is for.

    The same exam turns up in six documents worded six ways -- "Lab exam",
    "Lab exam (content covered may be tested)", "Lab exam (hands-on practical
    skills)". Those parentheticals are the document describing the event, not
    part of its identity, so they are dropped.

    But the section frequently lives inside the parentheses too -- "Soldering
    lab submission (Group A4)" -- and stripping it made five separate section
    deadlines look like one. The audience is therefore pulled out first and
    put back afterwards.
    """
    section, group = audience(title)
    text = re.sub(r"\([^)]*\)", " ", (title or "").lower())
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return " ".join(filter(None, [text, section, group]))


def dedup_key(course_id, title, due_date):
    """Same deadline, same key -- however many times it is rediscovered."""
    raw = f"{course_id}|{normalize(title)}|{due_date or 'pending'}"
    return hashlib.sha256(raw.encode()).hexdigest()


def event_key(course_id, due_date, due_time, title):
    """Identity of a real-world event, independent of how it was worded.

    Two runs of the extractor describe the same submission as "Circuit manual
    submission - A4 (Lab 2)" and "Circuit manual submission - Section A4", and
    the same report as "Lab 1 Report Submission" and "Lab 1 Report Due". No
    amount of title cleaning reconciles those, but a course, a date, a time
    and an audience do.
    """
    section, group = audience(title)
    raw = f"{course_id}|{due_date or ''}|{due_time or ''}|{section}|{group}"
    return hashlib.sha256(raw.encode()).hexdigest()


def pending_key(course_id, title):
    """Identity of an event regardless of whether its date is known yet."""
    return hashlib.sha256(f"{course_id}|{normalize(title)}".encode()).hexdigest()


# ------------------------------------------------------------------ writes

def upsert_course(db, d2l_id, name, term, code=None):
    stamp = now()
    db.execute(
        """INSERT INTO courses (d2l_id, code, name, term, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(d2l_id) DO UPDATE SET
               name = excluded.name, term = excluded.term, last_seen = excluded.last_seen""",
        (d2l_id, code, name, term, stamp, stamp),
    )
    return db.execute("SELECT id FROM courses WHERE d2l_id = ?", (d2l_id,)).fetchone()["id"]


def see_document(db, course_id, kind, title, text):
    """Record that a document exists. Returns (document_id, needs_reading).

    needs_reading is False when the text is byte-for-byte what was read last
    time, which is the whole point -- no second AI call for a file nobody
    touched.
    """
    stamp = now()
    digest = fingerprint(text)
    words = len((text or "").split())

    row = db.execute(
        "SELECT id, text_hash, last_read_at FROM documents "
        "WHERE course_id = ? AND kind = ? AND title = ?",
        (course_id, kind, title),
    ).fetchone()

    if row is None:
        cur = db.execute(
            """INSERT INTO documents
               (course_id, kind, title, text_hash, word_count, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (course_id, kind, title, digest, words, stamp, stamp),
        )
        return cur.lastrowid, True

    unchanged = row["text_hash"] == digest and row["last_read_at"] is not None
    db.execute(
        "UPDATE documents SET text_hash = ?, word_count = ?, last_seen = ? WHERE id = ?",
        (digest, words, stamp, row["id"]),
    )
    return row["id"], not unchanged


def mark_read(db, document_id):
    db.execute("UPDATE documents SET last_read_at = ? WHERE id = ?", (now(), document_id))


def save_dates(db, course_id, document_id, dates):
    """Store found deadlines. Returns how many were genuinely new.

    A deadline already on file keeps whatever you decided about it -- this
    never resurrects something you dismissed.
    """
    added = 0
    for d in dates:
        due = (d.get("date") or "").strip() or None
        key = dedup_key(course_id, d.get("title"), due)
        existing = db.execute("SELECT id FROM dates WHERE dedup_key = ?", (key,)).fetchone()
        if existing:
            continue

        core = normalize(d.get("title"))

        if due:
            # Already have this event, at this time, for this audience? Then
            # this is the same deadline worded differently.
            twin = db.execute(
                """SELECT id FROM dates
                   WHERE course_id = ? AND due_date = ? AND status != 'dismissed'""",
                (course_id, due),
            ).fetchall()
            mine = event_key(course_id, due, d.get("time") or None, d.get("title"))
            if any(event_key(course_id, due,
                             db.execute("SELECT due_time FROM dates WHERE id = ?",
                                        (t["id"],)).fetchone()[0],
                             db.execute("SELECT title FROM dates WHERE id = ?",
                                        (t["id"],)).fetchone()[0]) == mine for t in twin):
                continue

            # A real date for something we were only waiting on: this is the
            # expectation being fulfilled, so retire the placeholder rather
            # than leaving both on the list.
            # Keyed on the absence of a date, not on the extractor's pending
            # flag -- the flag is advisory, the missing date is the fact.
            db.execute(
                """UPDATE dates SET status = 'resolved', decided_at = ?
                   WHERE course_id = ? AND due_date IS NULL AND status = 'new'
                     AND resolved_title = ?""",
                (now(), course_id, core),
            )
        else:
            # Do not raise a placeholder for something already dated, or for
            # an expectation already on the list under different wording.
            clash = db.execute(
                """SELECT id FROM dates
                   WHERE course_id = ? AND resolved_title = ? AND status != 'dismissed'""",
                (course_id, core),
            ).fetchone()
            if clash:
                continue
        db.execute(
            """INSERT INTO dates
               (course_id, document_id, title, due_date, due_time, kind, confidence,
                source_excerpt, pending, dedup_key, resolved_title, status, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (course_id, document_id, d.get("title"), due, d.get("time") or None,
             d.get("kind"), d.get("confidence"), d.get("source_excerpt"),
             1 if d.get("pending") else 0, key, core, now()),
        )
        added += 1
    return added


def decide(db, date_id, status):
    """Accept or dismiss one card. Dismissed is permanent."""
    if status not in ("accepted", "dismissed", "new", "resolved"):
        raise ValueError(f"unknown status: {status}")
    db.execute("UPDATE dates SET status = ?, decided_at = ? WHERE id = ?",
               (status, now(), date_id))


def start_run(db, mode):
    return db.execute("INSERT INTO runs (started_at, mode) VALUES (?, ?)",
                      (now(), mode)).lastrowid


def finish_run(db, run_id, seen, read, found, cost, error=None):
    db.execute(
        """UPDATE runs SET finished_at = ?, docs_seen = ?, docs_read = ?,
           dates_found = ?, cost_usd = ?, error = ? WHERE id = ?""",
        (now(), seen, read, found, cost, error, run_id),
    )


# ------------------------------------------------------------------ reads

def cards(db, status="new", mine_only=True):
    """Deadlines awaiting a decision, soonest first, undated ones last.

    Where your section is known, other sections' deadlines are dropped: a
    course listing five lab groups produces five times the cards, and four
    of them were never yours to act on.
    """
    rows = db.execute(
        """SELECT d.*, c.d2l_id AS course_d2l_id, c.name AS course_name,
                  c.code AS course_code
           FROM dates d JOIN courses c ON c.id = d.course_id
           WHERE d.status = ?
           ORDER BY d.pending ASC, d.due_date IS NULL ASC,
                    d.due_date ASC, d.due_time ASC""",
        (status,),
    ).fetchall()

    return only_mine(rows) if mine_only else rows


def only_mine(rows, course_key="course_d2l_id"):
    """Drop deadlines that belong to somebody else's section or lab group.

    A course listing five lab groups produces five times the deadlines and
    four of them were never yours. Shared by the card list, the vault and
    prep.py -- it was written inline in cards() and the other two showed all
    five sections, which is both noise and a way to prepare the wrong one.
    """
    prefs = load_prefs()
    sections = prefs.get("sections", {})
    groups = prefs.get("groups", {})
    if not sections and not groups:
        return rows

    def same(a, b):
        # Case-insensitive on purpose. audience() yields "a4" and mysection.py
        # saves what it yields, so the two agree -- but a hand-edited me.json
        # saying "A4" would silently hide the user's own deadlines, and a rule
        # that quietly drops a real deadline is the one failure this project
        # exists to prevent.
        return str(a).strip().lower() == str(b).strip().lower()

    kept = []
    for row in rows:
        course = str(row[course_key])
        section, group = audience(row["title"])
        # A deadline naming no section belongs to everyone; only one naming
        # somebody else's is dropped.
        if section and course in sections and not same(section, sections[course]):
            continue
        if group and course in groups and not same(group, groups[course]):
            continue
        kept.append(row)
    return kept


def summary(db):
    def count(sql, *args):
        return db.execute(sql, args).fetchone()[0]

    return {
        "courses": count("SELECT COUNT(*) FROM courses"),
        "documents": count("SELECT COUNT(*) FROM documents"),
        "read": count("SELECT COUNT(*) FROM documents WHERE last_read_at IS NOT NULL"),
        "unread": count("SELECT COUNT(*) FROM documents WHERE last_read_at IS NULL"),
        "new": count("SELECT COUNT(*) FROM dates WHERE status='new' AND pending=0"),
        "pending": count("SELECT COUNT(*) FROM dates WHERE status='new' AND pending=1"),
        "accepted": count("SELECT COUNT(*) FROM dates WHERE status='accepted'"),
        "dismissed": count("SELECT COUNT(*) FROM dates WHERE status='dismissed'"),
        "resolved": count("SELECT COUNT(*) FROM dates WHERE status='resolved'"),
        "spent": count("SELECT COALESCE(SUM(cost_usd), 0) FROM runs"),
    }


if __name__ == "__main__":
    import sys

    db = connect()
    if "--restore" in sys.argv:
        print(f"Restored {restore(db)} rows merged by an earlier rule.")
    if "--tidy" in sys.argv:
        print(f"Collapsed {tidy(db)} duplicate entries.\n")
    print(f"Database ready at {DB_PATH}")
    for key, value in summary(db).items():
        print(f"  {key:<12} {value}")
    db.close()
