r"""The rules that must never break.

Not a test suite for everything -- a guard on the handful of behaviours
where being wrong is expensive and silent. Every case here is a bug that
actually happened, or a rule written in CLAUDE.md under "things that will
bite". Each one, if it broke, would cost a real deadline or a real piece of
the user's writing, and would do it without an error message.

    python test_rules.py          run them
    python -m unittest test_rules -v

No dependencies beyond the standard library, so it runs on the Pi.
"""

import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import store


_OPEN = []


def fresh_db():
    """An in-memory database with the real schema and migrations."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(store.SCHEMA)
    store.migrate(db)
    _OPEN.append(db)
    return db


def tearDownModule():
    # Python 3.13 warns about connections left open, and a suite that prints
    # warnings is one whose real output stops being read.
    while _OPEN:
        try:
            _OPEN.pop().close()
        except Exception:
            pass


def add_course(db, d2l=1, name="GNG2101  C01  Into Prod Dev For En/Cs  [ LAB ]  20269"):
    now = store.now()
    db.execute("INSERT INTO courses (d2l_id, code, name, term, first_seen, last_seen)"
               " VALUES (?,?,?,'20269',?,?)", (d2l, name.split()[0], name, now, now))
    return db.execute("SELECT id FROM courses WHERE d2l_id = ?", (d2l,)).fetchone()[0]


def add_date(db, course_id, title, due=None, time_=None, kind="assignment",
             status="new", linked=None, excerpt="x", key=None):
    db.execute(
        """INSERT INTO dates (course_id, title, due_date, due_time, kind, confidence,
                              source_excerpt, pending, dedup_key, status, first_seen,
                              linked_to, resolved_title)
           VALUES (?,?,?,?,?,'high',?,0,?,?,?,?,?)""",
        (course_id, title, due, time_, kind, excerpt, key or title, status,
         store.now(), linked, store.normalize(title)))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class StaleDatesAreDropped(unittest.TestCase):
    """Course shells are reused between terms; last year's dates come with them."""

    def test_a_year_typo_moves_only_the_course_you_declared(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "me.json"
            prefs.write_text(
                '{"year_typos": {"MCG2130": {"wrong": 2025, "right": 2026,'
                ' "why": "the professor mistyped it"}}}', encoding="utf-8")
            old, store.PREFS_PATH = store.PREFS_PATH, prefs
            try:
                mcg = "MCG2130  A00  (2026 Fall) Thermodynamics I 20269"
                gng = "GNG2101  C01  Into Prod Dev For En/Cs  [ LAB ]  20269"

                fixed, why = store.fix_year(mcg, "2025-09-19")
                self.assertEqual(fixed, "2026-09-19")
                self.assertTrue(why)

                # The whole point: another course's genuinely stale date is
                # left alone. A "looks a year off, shift it" rule would
                # resurrect last year's deadlines as this year's.
                self.assertEqual(store.fix_year(gng, "2025-09-19"), ("2025-09-19", None))
                # A different wrong year is not the one declared.
                self.assertEqual(store.fix_year(mcg, "2024-09-19"), ("2024-09-19", None))
                # A date already right is untouched.
                self.assertEqual(store.fix_year(mcg, "2026-10-02"), ("2026-10-02", None))
            finally:
                store.PREFS_PATH = old


class OtherPeoplesSectionsStayHidden(unittest.TestCase):
    """Five lab groups produce five times the deadlines; four were never yours."""

    def test_only_mine_keeps_yours_and_the_unlabelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "me.json"
            prefs.write_text('{"sections": {"1": "A4"}}', encoding="utf-8")
            old, store.PREFS_PATH = store.PREFS_PATH, prefs
            try:
                db = fresh_db()
                cid = add_course(db)
                add_date(db, cid, "Circuit manual submission - A4")
                add_date(db, cid, "Circuit manual submission - A2", key="a2")
                add_date(db, cid, "Midterm 1", key="mid")   # names no section
                rows = db.execute(
                    "SELECT d.*, c.d2l_id AS course_d2l_id FROM dates d "
                    "JOIN courses c ON c.id = d.course_id").fetchall()
                kept = {r["title"] for r in store.only_mine(rows)}
                self.assertIn("Circuit manual submission - A4", kept)
                self.assertIn("Midterm 1", kept, "a deadline naming no section is everyone's")
                self.assertNotIn("Circuit manual submission - A2", kept)
            finally:
                store.PREFS_PATH = old

    def test_case_does_not_hide_your_own_deadline(self):
        """A hand-edited me.json saying "A4" must behave like "a4"."""
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "me.json"
            prefs.write_text('{"sections": {"1": "A4"}}', encoding="utf-8")
            old, store.PREFS_PATH = store.PREFS_PATH, prefs
            try:
                db = fresh_db()
                cid = add_course(db)
                add_date(db, cid, "Lab report (a4)")
                rows = db.execute(
                    "SELECT d.*, c.d2l_id AS course_d2l_id FROM dates d "
                    "JOIN courses c ON c.id = d.course_id").fetchall()
                self.assertEqual(len(store.only_mine(rows)), 1)
            finally:
                store.PREFS_PATH = old


class LinkedTasksDoNotShadowRealDeadlines(unittest.TestCase):
    """A linked to-do borrows its anchor's date and time, so by event_key it
    IS the anchor. It must never stand in for one."""

    def test_a_real_deadline_at_a_linked_tasks_slot_is_still_stored(self):
        db = fresh_db()
        cid = add_course(db)
        add_date(db, cid, "Lab 5 (A4)", "2026-10-11", "19:00", kind="lab")
        add_date(db, cid, "Install the Arduino IDE", "2026-10-11", "19:00",
                 kind="todo", linked="Lab 5 (A4)", key="todo")
        added = store.save_dates(db, cid, None, [{
            "title": "Quiz 3", "date": "2026-10-11", "time": "19:00",
            "kind": "quiz", "confidence": "high", "source_excerpt": "Quiz 3 due Thu 19:00"}])
        self.assertEqual(added, 1, "the to-do must not absorb a genuine deadline")

    def test_tidy_does_not_collapse_an_anchor_into_its_task(self):
        db = fresh_db()
        cid = add_course(db)
        anchor = add_date(db, cid, "Lab 5", "2026-10-11", "19:00", kind="lab")
        todo = add_date(db, cid, "Install the Arduino IDE and the drivers",
                        "2026-10-11", "19:00", kind="todo", linked="Lab 5", key="todo")
        store.tidy(db)
        status = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM dates")}
        self.assertEqual(status[anchor], "new", "the real lab was merged away")
        self.assertEqual(status[todo], "new")


class DecisionsSurviveEverything(unittest.TestCase):
    def test_a_dismissed_deadline_is_never_resurrected(self):
        """The real sequence: stored by a scrape, dismissed, scraped again."""
        db = fresh_db()
        cid = add_course(db)
        found = {"title": "Soldering lab", "date": "2026-10-01", "time": "23:59",
                 "kind": "assignment", "confidence": "high",
                 "source_excerpt": "Soldering lab due 1 October."}
        self.assertEqual(store.save_dates(db, cid, None, [found]), 1)
        db.execute("UPDATE dates SET status = 'dismissed', decided_at = ?",
                   (store.now(),))

        self.assertEqual(store.save_dates(db, cid, None, [found]), 0,
                         "the next scrape must not put it back")
        rows = db.execute("SELECT status FROM dates").fetchall()
        self.assertEqual([r["status"] for r in rows], ["dismissed"])

    def test_an_accepted_deadline_is_not_duplicated_by_the_next_scrape(self):
        db = fresh_db()
        cid = add_course(db)
        found = {"title": "Midterm 1", "date": "2026-10-02", "time": "11:30",
                 "kind": "midterm", "confidence": "high", "source_excerpt": "z"}
        store.save_dates(db, cid, None, [found])
        db.execute("UPDATE dates SET status = 'accepted', gcal_event_id = 'evt1'")
        self.assertEqual(store.save_dates(db, cid, None, [found]), 0)
        self.assertEqual(
            db.execute("SELECT gcal_event_id FROM dates").fetchone()[0], "evt1",
            "the calendar event must survive a re-scrape")


class StartDatesAndKinds(unittest.TestCase):
    """Nothing is ever stored as kind 'exam' -- the extractor says 'midterm'."""

    def test_a_midterm_gets_the_exam_lead_time(self):
        self.assertEqual(store.lead_days("midterm"), store.lead_days("exam"))
        self.assertEqual(store.lead_days("final_exam"), store.lead_days("exam"))
        self.assertGreater(store.lead_days("midterm"), store.lead_days("assignment"))

    def test_start_by_works_back_from_the_due_date(self):
        self.assertEqual(store.start_by("2026-10-23", "midterm"), "2026-10-13")
        self.assertEqual(store.start_by("2026-09-17", "lab"), "2026-09-13")
        self.assertIsNone(store.start_by(None, "lab"))
        self.assertIsNone(store.start_by("not a date", "lab"))


class HandingInIsAnswered(unittest.TestCase):
    def test_the_three_kinds_of_answer(self):
        db = fresh_db()
        cid = add_course(db)
        add_date(db, cid, "Arduino lab + BOM submission", "2026-09-23", "23:59",
                 excerpt="Brightspace lists this due date on the item itself.")
        ide = add_date(db, cid, "Install the Arduino IDE", "2026-09-22", kind="todo",
                       excerpt="Install the Arduino IDE on your laptop.", key="ide")
        glasses = add_date(db, cid, "Bring safety glasses", "2026-09-25", kind="todo",
                           excerpt="Bring your own safety glasses.", key="gl")
        exam = add_date(db, cid, "Midterm 1", "2026-10-02", "11:30", kind="midterm",
                        excerpt="Midterm 1 in the lecture room.", key="ex")

        answers = store.submission_map(db, db.execute("SELECT * FROM dates").fetchall())
        self.assertEqual(answers[ide][0], "yes",
                         "should find the Arduino submission folder")
        self.assertEqual(answers[glasses][0], "no",
                         "no folder matches, so there is nowhere to hand it in")
        self.assertEqual(answers[exam][0], "no", "an exam is sat, not submitted")
        self.assertTrue(all(why for _, why in answers.values()),
                        "every answer carries its reason")


class TimesAreReadable(unittest.TestCase):
    def test_display_and_parse_agree(self):
        for stored, shown in (("19:00", "7:00 PM"), ("00:30", "12:30 AM"),
                              ("12:00", "12:00 PM"), ("23:59", "11:59 PM")):
            self.assertEqual(store.pretty_time(stored), shown)
            self.assertEqual(store.parse_time(stored), stored)
            self.assertEqual(store.parse_time(shown), stored)

    def test_a_twelve_hour_string_still_reaches_the_calendar(self):
        """gcal.py parses this; a raise here means a deadline with no event."""
        self.assertEqual(store.parse_time("7:00 PM"), "19:00")
        self.assertIsNone(store.parse_time("sometime next week"))
        self.assertEqual(store.pretty_time(""), "")


class TheVaultNeverEatsYourWriting(unittest.TestCase):
    def test_a_note_without_the_marker_is_left_alone(self):
        import vault
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            mine = "# My own notes\n\nI wrote this by hand.\n"
            path.write_text(mine, encoding="utf-8")
            report = {"kept": [], "same": 0, "wrote": []}
            wrote = vault.write(path, "---\ngenerated: true\n---\n# Replaced\n",
                                report=report)
            self.assertFalse(wrote)
            self.assertEqual(path.read_text(encoding="utf-8"), mine)
            self.assertEqual(len(report["kept"]), 1)

    def test_a_generated_note_is_replaced(self):
        import vault
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            path.write_text("---\ngenerated: true\n---\n# Old\n", encoding="utf-8")
            new = "---\ngenerated: true\n---\n# New\n"
            self.assertTrue(vault.write(path, new, report={"kept": [], "same": 0, "wrote": []}))
            self.assertEqual(path.read_text(encoding="utf-8"), new)


class PageDatesOnlyFillGaps(unittest.TestCase):
    """The one place HTML is parsed. It matched by substring, so "Quiz 1"
    could take "Quiz 10"'s date and store it as high confidence."""

    def test_an_item_takes_its_own_row(self):
        import pagedates
        html = ("<table>"
                "<tr><td>Quiz 10 - Chapter 10</td><td>Due on Sep 20, 2026 8:00 AM</td></tr>"
                "<tr><td>Quiz 1 - Retake</td><td>Due on Oct 1, 2026 9:00 AM</td></tr>"
                "<tr><td>Quiz 1</td><td>Due on Sep 14, 2026 8:30 AM</td></tr>"
                "</table>")
            
        original, pagedates.page = pagedates.page, lambda *a, **k: html
        try:
            out = pagedates.found_on_page(None, "https://x", 1, "quizzes", ["Quiz 1"])
            self.assertEqual(out["Quiz 1"]["date"], "2026-09-14")
        finally:
            pagedates.page = original


class ContentItemDueDates(unittest.TestCase):
    """The table of contents returns DueDate as null even when the item has
    one. GNG2101's Arduino Pre-lab is a SCORM package: no file to download,
    no description, and a real deadline reachable only on its own endpoint."""

    def test_a_topics_own_endpoint_is_asked_when_the_toc_says_nothing(self):
        import collect
        asked = []

        def fake_get(client, path, **kw):
            asked.append(path)
            return {"DueDate": "2026-10-13T23:00:00.000Z"}

        with tempfile.TemporaryDirectory() as tmp:
            old_get, collect.get = collect.get, fake_get
            old_cache, collect.TOPIC_DATES = collect.TOPIC_DATES, Path(tmp) / "topic_dates.json"
            try:
                topics = [
                    {"id": 7746068, "title": "Arduino Pre-lab", "due": None,
                     "modified": "2026-09-09T21:12:21.387Z"},
                    {"id": 7746089, "title": "Lecture slides", "due": None,
                     "modified": "2025-08-29T21:36:06.557Z"},
                    {"id": 7746090, "title": "Already dated", "due": "2026-10-01T03:59:00.000Z",
                     "modified": "2026-09-01T00:00:00.000Z"},
                ]
                found = collect.fill_topic_dates(None, 614949, topics)
                self.assertEqual(found, 2)
                self.assertEqual(topics[0]["due"], "2026-10-13T23:00:00.000Z")
                self.assertEqual(len(asked), 2, "an item that already has a date is not re-asked")

                # Second scrape, nothing modified: the cache answers, so a
                # routine run does not re-ask Brightspace for every item.
                asked.clear()
                for t in topics[:2]:
                    t["due"] = None
                collect.fill_topic_dates(None, 614949, topics)
                self.assertEqual(asked, [], "unchanged items must come from the cache")
                self.assertEqual(topics[0]["due"], "2026-10-13T23:00:00.000Z")

                # Brightspace says the item changed: ask again.
                topics[0]["due"] = None
                topics[0]["modified"] = "2026-10-01T10:00:00.000Z"
                collect.fill_topic_dates(None, 614949, topics)
                self.assertEqual(len(asked), 1, "a changed item is re-asked")
            finally:
                collect.get = old_get
                collect.TOPIC_DATES = old_cache

    def test_exact_stores_a_content_items_date(self):
        import exact
        db = fresh_db()
        collected = [{
            "id": 614949, "name": "GNG2101  C01  Into Prod Dev For En/Cs  [ LAB ]  20269",
            "term": "20269", "assignments": [], "quizzes": [], "modules": [],
            "calendar": [], "checklists": [],
            "topics": [
                {"id": 1, "title": "Arduino Pre-lab", "due": "2026-10-13T23:00:00.000Z"},
                {"id": 2, "title": "Lecture slides", "due": None},
            ]}]
        added, dropped, shifted = exact.load(db, collected, ("2026-08-15", "2027-01-31"))
        self.assertEqual(added, 1)
        row = db.execute("SELECT title, due_date, due_time FROM dates").fetchone()
        self.assertEqual(row["title"], "Arduino Pre-lab")
        self.assertEqual(row["due_date"], "2026-10-13")
        self.assertEqual(row["due_time"], "19:00", "23:00 UTC is 7pm in Ottawa in October")


class BackupRoundTrip(unittest.TestCase):
    """The database is the one irreplaceable thing here."""

    def test_every_decision_survives_a_dump_and_restore(self):
        import backup, paths
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path, vault_dir = root / "brightspace.db", root / "vault"
            old_db, old_vault = paths.DB, paths.VAULT
            old_prepared = store._prepared
            paths.DB = store.DB_PATH = db_path
            paths.VAULT = vault_dir
            store._prepared = False
            try:
                db = store.connect()
                cid = add_course(db)
                add_date(db, cid, "Midterm 1", "2026-10-02", "11:30",
                         kind="midterm", status="accepted")
                add_date(db, cid, "Assignment #1", "2026-09-18", "17:00", key="a1")
                add_date(db, cid, "Old thing", "2026-06-01", status="dismissed", key="old")
                db.execute("UPDATE dates SET done_at = ? WHERE dedup_key = 'a1'",
                           (store.now(),))
                db.execute("INSERT INTO documents (course_id, kind, title, text_hash,"
                           " word_count, first_seen, last_seen, last_read_at)"
                           " VALUES (?,'file','Syllabus','abc123',1935,?,?,?)",
                           (cid, store.now(), store.now(), store.now()))
                db.commit()
                db.close()

                backup.dump(quiet=True)
                db_path.unlink()
                store._prepared = False
                backup.restore([])

                store._prepared = False
                db = store.connect()
                statuses = [r["status"] for r in db.execute("SELECT status FROM dates ORDER BY id")]
                done = db.execute("SELECT COUNT(*) FROM dates WHERE done_at IS NOT NULL").fetchone()[0]
                digest = db.execute("SELECT text_hash FROM documents").fetchone()[0]
                runs = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                db.close()

                self.assertEqual(statuses, ["accepted", "new", "dismissed"])
                self.assertEqual(done, 1, "a crossed-off item stayed crossed off")
                self.assertEqual(digest, "abc123",
                                 "text_hash is why a scrape costs nothing")
                self.assertEqual(runs, 0, "runs is left out of the dump on purpose")
            finally:
                paths.DB = store.DB_PATH = old_db
                paths.VAULT = old_vault
                store._prepared = old_prepared


if __name__ == "__main__":
    unittest.main(verbosity=2)
