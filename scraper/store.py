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
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "brightspace.db"

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

CREATE INDEX IF NOT EXISTS idx_dates_status ON dates(status, due_date);
CREATE INDEX IF NOT EXISTS idx_docs_course ON documents(course_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    # Append-style writes rather than scattered ones -- see PLAN.md section 4.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    return db


def fingerprint(text):
    """Ignore whitespace churn so a reformat does not read as a change."""
    return hashlib.sha256(" ".join((text or "").split()).encode()).hexdigest()


def normalize(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def dedup_key(course_id, title, due_date):
    """Same deadline, same key -- however many times it is rediscovered."""
    raw = f"{course_id}|{normalize(title)}|{due_date or 'pending'}"
    return hashlib.sha256(raw.encode()).hexdigest()


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
        db.execute(
            """INSERT INTO dates
               (course_id, document_id, title, due_date, due_time, kind, confidence,
                source_excerpt, pending, dedup_key, status, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (course_id, document_id, d.get("title"), due, d.get("time") or None,
             d.get("kind"), d.get("confidence"), d.get("source_excerpt"),
             1 if d.get("pending") else 0, key, now()),
        )
        added += 1
    return added


def decide(db, date_id, status):
    """Accept or dismiss one card. Dismissed is permanent."""
    if status not in ("accepted", "dismissed", "new"):
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

def cards(db, status="new"):
    """Deadlines awaiting a decision, soonest first, undated ones last."""
    return db.execute(
        """SELECT d.*, c.name AS course_name, c.code AS course_code
           FROM dates d JOIN courses c ON c.id = d.course_id
           WHERE d.status = ?
           ORDER BY d.pending ASC, d.due_date ASC, d.due_time ASC""",
        (status,),
    ).fetchall()


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
        "spent": count("SELECT COALESCE(SUM(cost_usd), 0) FROM runs"),
    }


if __name__ == "__main__":
    db = connect()
    print(f"Database ready at {DB_PATH}")
    for key, value in summary(db).items():
        print(f"  {key:<12} {value}")
    db.close()
