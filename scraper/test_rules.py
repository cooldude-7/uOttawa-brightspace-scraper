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

import importlib.util
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

    def test_a_stale_section_does_not_hide_every_deadline(self):
        """GNG2101 renamed its lab sections from A1-A5 to C1-C3 mid-term.
        me.json still said a4, which matches nothing -- and filtering on it
        would have hidden every real lab deadline in the course."""
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "me.json"
            prefs.write_text('{"sections": {"1": "a4"}}', encoding="utf-8")
            old_prefs, store.PREFS_PATH = store.PREFS_PATH, prefs
            try:
                db = fresh_db()
                cid = add_course(db)
                for name in ("Circuit manual submission (Section C1)",
                             "Circuit manual submission (Section C2)",
                             "Circuit manual submission (Section C3)"):
                    add_date(db, cid, name, "2026-09-22", key=name)
                rows = db.execute(
                    "SELECT d.*, c.d2l_id AS course_d2l_id FROM dates d "
                    "JOIN courses c ON c.id = d.course_id").fetchall()

                self.assertEqual(len(store.only_mine(rows)), 3,
                                 "none of them match a4, so none may be hidden")

                stale = store.stale_sections(rows)
                self.assertEqual(len(stale), 1)
                course, configured, actual = stale[0]
                self.assertEqual(configured, "a4")
                self.assertEqual(actual, ["c1", "c2", "c3"])
            finally:
                store.PREFS_PATH = old_prefs

    def test_a_section_that_does_exist_still_filters(self):
        """The guard must not become an excuse to stop filtering."""
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "me.json"
            prefs.write_text('{"sections": {"1": "c1"}}', encoding="utf-8")
            old_prefs, store.PREFS_PATH = store.PREFS_PATH, prefs
            try:
                db = fresh_db()
                cid = add_course(db)
                for name in ("Circuit manual submission (Section C1)",
                             "Circuit manual submission (Section C2)",
                             "Circuit manual submission (Section C3)"):
                    add_date(db, cid, name, "2026-09-22", key=name)
                rows = db.execute(
                    "SELECT d.*, c.d2l_id AS course_d2l_id FROM dates d "
                    "JOIN courses c ON c.id = d.course_id").fetchall()
                kept = store.only_mine(rows)
                self.assertEqual([r["title"] for r in kept],
                                 ["Circuit manual submission (Section C1)"])
                self.assertEqual(store.stale_sections(rows), [])
            finally:
                store.PREFS_PATH = old_prefs

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

    def test_an_active_quiz_with_only_last_terms_date_is_kept_undated(self):
        """Dropping it hides real work; shifting the year invents a deadline.
        GNG2101's Quiz 1 is switched on and dated 19 Sep 2025 -- a Friday,
        which a year later is a Saturday, so the year is not simply a typo."""
        import exact
        db = fresh_db()
        collected = [{
            "id": 614949, "name": "GNG2101  C01  Into Prod Dev For En/Cs  [ LAB ]  20269",
            "term": "20269", "assignments": [], "modules": [], "topics": [],
            "calendar": [], "checklists": [],
            "quizzes": [
                {"Name": "Quiz 1: Online modules", "DueDate": "2025-09-19T21:45:00.000Z",
                 "IsActive": True},
                {"Name": "Quiz 2: switched off", "DueDate": "2025-09-26T21:45:00.000Z",
                 "IsActive": False},
            ]}]
        added, dropped, shifted, undated = exact.load(
            db, collected, ("2026-08-15", "2027-01-31"))

        rows = db.execute("SELECT title, due_date, pending FROM dates").fetchall()
        self.assertEqual(len(rows), 1, "only the active one is kept")
        self.assertEqual(rows[0]["title"], "Quiz 1: Online modules")
        self.assertIsNone(rows[0]["due_date"], "no date is invented for it")
        self.assertEqual(rows[0]["pending"], 1, "it reads as not scheduled yet")
        self.assertEqual([t for _, t, _ in undated], ["Quiz 1: Online modules"])
        self.assertEqual([t for _, t, _ in dropped], ["Quiz 2: switched off"])

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
        added, dropped, shifted, _ = exact.load(db, collected, ("2026-08-15", "2027-01-31"))
        self.assertEqual(added, 1)
        row = db.execute("SELECT title, due_date, due_time FROM dates").fetchone()
        self.assertEqual(row["title"], "Arduino Pre-lab")
        self.assertEqual(row["due_date"], "2026-10-13")
        self.assertEqual(row["due_time"], "19:00", "23:00 UTC is 7pm in Ottawa in October")


import contextlib


@contextlib.contextmanager
def lab_files(named):
    """A real extracted-files tree, because that is where posted_work reads."""
    import json as _json
    import paths
    from download import safe_name
    course = {"id": 3, "name": "MAT1341  B00  Intro. To Linear Algebra [ LEC ] 20269",
              "term": "20269"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old_ex, old_col = paths.EXTRACTED, paths.COLLECTED
        paths.EXTRACTED = root / "extracted"
        paths.COLLECTED = root / "collected.json"
        paths.COLLECTED.write_text(_json.dumps([course]), encoding="utf-8")
        folder = paths.EXTRACTED / safe_name(course["name"], 40)
        folder.mkdir(parents=True)
        for stem, text in named.items():
            (folder / f"{stem}.txt").write_text(text, encoding="utf-8")
        db = fresh_db()
        try:
            yield db
        finally:
            paths.EXTRACTED, paths.COLLECTED = old_ex, old_col


class PostedWorkWithNoDeadline(unittest.TestCase):
    """A DGD question sheet says nothing about when to do it, so the date
    extractor correctly finds nothing and it never reached the list."""

    def test_titles_that_are_work_and_titles_that_only_look_like_it(self):
        import posted_work as pw
        for title in ("Linear_Algebra___DGD_1 questions", "Tutorial 3 problem set",
                      "Practice problems - Chapter 4", "Week 2 exercises",
                      "Worksheet 4"):
            self.assertTrue(pw.looks_like_work(title), title)
        for title in ("DGD 2 solutions", "Practice problems - answer key",
                      "Linear_Algebra_Lecture_1(filled)_Introduction",
                      "Frequently asked questions", "MCG2360_Syllabus_Fall_2026",
                      "Lecture_3", "(Fall 2025) MCG 2130 Final exam"):
            self.assertFalse(pw.looks_like_work(title), title)

    def test_it_finds_files_the_documents_table_never_heard_of(self):
        """The bug this replaced: it read the documents table, where a row
        only appears once find_dates judged the text worth an API call --
        and that test is "has dates or task language", which a question
        sheet has neither of. The documents it exists to find are exactly
        the ones missing from there. It reads the extracted files instead."""
        import posted_work as pw
        with lab_files({"Linear_Algebra___DGD_1 questions": "1. Find the span of...",
                        "Linear_Algebra___Lecture_2": "Vectors and dot products"}) as db:
            found = pw.find(db)
            self.assertEqual([t for _, _, t, _ in found],
                             ["Linear_Algebra___DGD_1 questions"])
            # and nothing was in the documents table at all
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)

    def test_it_is_stored_undated_and_kept_out_of_link_tasks(self):
        import posted_work as pw
        with lab_files({"Linear_Algebra___DGD_1 questions": "1. Find the span...",
                        "Linear_Algebra___Lecture_2": "Vectors and dot products"}) as db:
            added, names = pw.load(db)
            self.assertEqual(added, 1, "the lecture is not work to do")
            row = db.execute("SELECT * FROM dates").fetchone()
            self.assertEqual(row["title"], "Linear Algebra DGD 1 questions")
            self.assertIsNone(row["due_date"], "no date is invented")
            self.assertEqual(row["kind"], "todo")
            self.assertEqual(row["pending"], 0,
                             "no deadline exists -- not waiting on the professor")
            self.assertEqual(row["linked_to"], "",
                             "marked unanchorable so link_tasks never pays for it")

            # A second scrape must not add it again.
            self.assertEqual(pw.load(db)[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM dates").fetchone()[0], 1)

    def test_dismissing_one_keeps_it_dismissed(self):
        import posted_work as pw
        with lab_files({"Tutorial 3 problem set": "questions here"}) as db:
            pw.load(db)
            db.execute("UPDATE dates SET status = 'dismissed'")
            self.assertEqual(pw.load(db)[0], 0,
                             "a false positive dismissed once stays gone")


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


class SupersededDatesAreRetiredSafely(unittest.TestCase):
    """A correction rule outlives the typo, and its rows stay behind.

    MCG2130: the professor had last year's dates, --year-typo shifted them,
    then he fixed his own dates. Both sets ended up stored, a day apart, and
    the stale one sits later -- the direction that loses marks. The retiring
    rule must never fire unless the real date is already on file.
    """

    def _collected(self, d2l, name, items):
        return [{"id": d2l, "name": name,
                 "assignments": [{"Name": n, "DueDate": w} for n, w in items]}]

    def _find(self, db, collected, only=None):
        import json

        import paths
        import supersede
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collected.json"
            path.write_text(json.dumps(collected), encoding="utf-8")
            old, paths.COLLECTED = paths.COLLECTED, path
            try:
                return supersede.find(db, only)
            finally:
                paths.COLLECTED = old

    def test_the_stale_twin_goes_and_the_real_one_stays(self):
        db = fresh_db()
        name = "MCG2130  A00  (2026 Fall) Thermodynamics I  20269"
        cid = add_course(db, d2l=7, name=name)
        # 2026-09-19T03:00Z is Fri 18 Sep 23:00 in Ottawa -- the real deadline.
        real = add_date(db, cid, "Assignment #1", "2026-09-18", "23:00", key="k1")
        stale = add_date(db, cid, "Assignment #1", "2026-09-19", "23:00", key="k2")
        db.commit()

        found = self._find(db, self._collected(
            7, name, [("Assignment #1", "2026-09-19T03:00:00.000Z")]))

        self.assertEqual([r["id"] for r, _c, _p in found], [stale],
                         "only the date Brightspace no longer publishes goes")
        self.assertNotIn(real, [r["id"] for r, _c, _p in found],
                         "the real deadline must never be retired")

    def test_nothing_goes_when_the_real_date_is_not_on_file(self):
        """The condition that makes this safe at all.

        If the only stored row disagrees with Brightspace, that is a date to
        look at, not one to delete -- retiring it would leave the course with
        no record of the deadline whatsoever.
        """
        db = fresh_db()
        name = "MCG2130  A00  (2026 Fall) Thermodynamics I  20269"
        cid = add_course(db, d2l=7, name=name)
        add_date(db, cid, "Assignment #1", "2026-09-19", "23:00", key="k3")
        db.commit()

        found = self._find(db, self._collected(
            7, name, [("Assignment #1", "2026-09-19T03:00:00.000Z")]))
        self.assertEqual(found, [], "never retire the only record of a deadline")

    def test_an_item_brightspace_stopped_publishing_is_left_alone(self):
        """Silence is not a correction. A withdrawn item keeps its rows."""
        db = fresh_db()
        name = "MCG2130  A00  (2026 Fall) Thermodynamics I  20269"
        cid = add_course(db, d2l=7, name=name)
        add_date(db, cid, "Assignment #1", "2026-09-18", "23:00", key="k4")
        add_date(db, cid, "Assignment #1", "2026-09-19", "23:00", key="k5")
        db.commit()

        found = self._find(db, self._collected(
            7, name, [("Assignment #9", "2026-12-01T03:00:00.000Z")]))
        self.assertEqual(found, [])

    def test_two_real_published_dates_both_survive(self):
        """A due date and a close date are both real. Neither supersedes."""
        db = fresh_db()
        name = "MCG2130  A00  (2026 Fall) Thermodynamics I  20269"
        cid = add_course(db, d2l=7, name=name)
        add_date(db, cid, "Assignment #1", "2026-09-18", "23:00", key="k6")
        add_date(db, cid, "Assignment #1", "2026-09-19", "23:00", key="k7")
        db.commit()

        found = self._find(db, self._collected(7, name, [
            ("Assignment #1", "2026-09-19T03:00:00.000Z"),
            ("Assignment #1", "2026-09-20T03:00:00.000Z")]))
        self.assertEqual(found, [], "both dates are published, so both are real")

    def test_a_linked_todo_is_never_retired(self):
        """It carries its anchor's date, so it always looks like a twin."""
        db = fresh_db()
        name = "MCG2130  A00  (2026 Fall) Thermodynamics I  20269"
        cid = add_course(db, d2l=7, name=name)
        add_date(db, cid, "Assignment #1", "2026-09-18", "23:00", key="k8")
        add_date(db, cid, "Assignment #1", "2026-09-19", "23:00",
                 linked="Assignment #1", key="k9")
        db.commit()

        found = self._find(db, self._collected(
            7, name, [("Assignment #1", "2026-09-19T03:00:00.000Z")]))
        self.assertEqual(found, [], "a linked to-do is not a stale duplicate")

    def test_another_course_is_untouched(self):
        db = fresh_db()
        mcg = "MCG2130  A00  (2026 Fall) Thermodynamics I  20269"
        gng = "GNG2101  C01  Into Prod Dev For En/Cs  [ LAB ]  20269"
        a = add_course(db, d2l=7, name=mcg)
        b = add_course(db, d2l=8, name=gng)
        add_date(db, a, "Assignment #1", "2026-09-18", "23:00", key="k10")
        add_date(db, a, "Assignment #1", "2026-09-19", "23:00", key="k11")
        add_date(db, b, "Deliverable A", "2026-09-18", "23:00", key="k12")
        add_date(db, b, "Deliverable A", "2026-09-19", "23:00", key="k13")
        db.commit()

        collected = (self._collected(7, mcg, [("Assignment #1", "2026-09-19T03:00:00.000Z")])
                     + self._collected(8, gng, [("Deliverable A", "2026-09-19T03:00:00.000Z")]))
        found = self._find(db, collected, only="MCG2130")
        self.assertEqual({c for _r, c, _p in found}, {"MCG2130"})



class HandAddedDeadlines(unittest.TestCase):
    """Not every deadline is on Brightspace. A TA emails one; add.py takes it."""

    def _add(self, args):
        import add
        return add.main(["add.py"] + args)

    def _course(self, tmp):
        import store as s
        db = s.connect()
        now = s.now()
        db.execute("INSERT INTO courses (d2l_id, code, name, term, first_seen,"
                   " last_seen) VALUES (9,'MCG2360','MCG2360  A02  Engineering"
                   " Materials I [ LEC ] 20269','20269',?,?)", (now, now))
        db.commit()
        db.close()

    def _sandbox(self):
        """A real database in a temporary BRIGHTSPACE_DATA, as add.py expects."""
        import importlib
        import os
        tmp = tempfile.mkdtemp()
        Path(tmp, ".brightspace-data").write_text("x", encoding="utf-8")
        os.environ["BRIGHTSPACE_DATA"] = tmp
        import paths
        import store as s
        importlib.reload(paths)
        s.DB_PATH = paths.DB
        s._prepared = False
        self._course(tmp)
        return s

    def test_a_hand_typed_row_never_duplicates_a_scraped_one(self):
        """Two cards for one deadline and the app cannot say which is real."""
        s = self._sandbox()
        self.assertEqual(self._add(["MCG2360", "Lab group registration",
                                    "2026-09-18", "23:59"]), 0)
        self.assertEqual(self._add(["MCG2360", "Lab group registration",
                                    "2026-09-18", "23:59"]), 1,
                         "the second add must be refused")
        db = s.connect()
        n = db.execute("SELECT COUNT(*) FROM dates").fetchone()[0]
        db.close()
        self.assertEqual(n, 1)

    def test_the_time_is_stored_twenty_four_hour(self):
        """Typed as 7:00 PM, stored as 19:00 -- or the calendar never gets it."""
        s = self._sandbox()
        self._add(["MCG2360", "Lab prep session", "2026-09-17", "7:00 PM",
                   "--kind", "session"])
        db = s.connect()
        row = db.execute("SELECT due_time FROM dates").fetchone()
        db.close()
        self.assertEqual(row["due_time"], "19:00")

    def test_a_date_it_cannot_read_is_refused_not_guessed(self):
        s = self._sandbox()
        self.assertEqual(self._add(["MCG2360", "Thing", "next friday"]), 2)
        db = s.connect()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM dates").fetchone()[0], 0)
        db.close()



class AttachmentsOnEveryTab(unittest.TestCase):
    """collect.py gathers twelve tabs; download.py downloaded from one.

    GNG2101's ten deliverable briefs, every template and every lab manual
    hang off submission folders. None was ever fetched, and nothing said so
    -- no error, no skip line. Silence is the bug being guarded here.
    """

    class _Reply:
        headers = {}

        def __init__(self, code, content=b""):
            self.status_code, self.content = code, content

    def _client(self, ok_ids=(), status=404):
        outer = self

        class Client:
            def get(self, url, **kw):
                if any(str(i) in url for i in ok_ids):
                    return outer._Reply(200, outer._pdf())
                return outer._Reply(status)

        return Client()

    def _pdf(self):
        import pymupdf
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 100), "Instructions for Project A. "
                                              "Submit a signed team contract.")
        return doc.tobytes()

    def _sandbox(self):
        import importlib
        import os
        tmp = tempfile.mkdtemp()
        Path(tmp, ".brightspace-data").write_text("x", encoding="utf-8")
        os.environ["BRIGHTSPACE_DATA"] = tmp
        import paths
        importlib.reload(paths)
        import download
        importlib.reload(download)
        return download, paths

    def _course(self, attachments=True):
        return {"id": 614949,
                "name": "GNG2101  C01  Into Prod Dev For En/Cs  [ LAB ]  20269",
                "assignments": [{
                    "Id": 410521, "Name": "Project Deliverable A",
                    "Attachments": ([{"FileId": 25599705,
                                      "FileName": "Instructions_Project_A.pdf"}]
                                    if attachments else []),
                    "CustomInstructions": {
                        "Text": "<p>Submit <b>one PDF</b> per team.</p>"}}]}

    def test_a_file_that_will_not_download_is_named_never_swallowed(self):
        dl, _paths = self._sandbox()
        _got, _read, _words, fails = dl.download_attachments(
            self._client(ok_ids=(), status=403), self._course(), "GNG2101")
        self.assertEqual(len(fails), 1, "a failed attachment must be reported")
        self.assertIn("Instructions_Project_A.pdf", fails[0][1])
        self.assertIn("403", fails[0][2])

    def test_typed_instructions_are_kept_as_plain_text(self):
        """553 characters of what Deliverable A asks for, and no request."""
        dl, paths_ = self._sandbox()
        dl.download_attachments(self._client(), self._course(attachments=False),
                                "GNG2101")
        written = list((paths_.EXTRACTED / "GNG2101").glob("*(instructions).txt"))
        self.assertEqual(len(written), 1)
        body = written[0].read_text(encoding="utf-8")
        self.assertIn("Submit", body)
        self.assertNotIn("<b>", body, "HTML tags must not reach the extractor")

    def test_a_file_already_read_is_not_fetched_again(self):
        """Same reason text_hash exists: a routine scrape must cost nothing."""
        dl, _paths = self._sandbox()
        course = self._course()
        first = dl.download_attachments(self._client(ok_ids=(25599705,)),
                                        course, "GNG2101")
        self.assertEqual(first[0], 1, "downloaded once")

        asked = []

        class Counting:
            def get(self, url, **kw):
                asked.append(url)
                return AttachmentsOnEveryTab._Reply(200, b"")

        second = dl.download_attachments(Counting(), course, "GNG2101")
        self.assertEqual(second[0], 0, "must not download it a second time")
        self.assertEqual(asked, [], "must not even ask")



class AFailedReadIsNotAReadDocument(unittest.TestCase):
    """The credit ran out mid-scrape and 29 documents were marked read.

    `ask()` returned [] on an API failure, identical to "the model read this
    and found nothing", and mark_read() ran either way. Those documents are
    skipped on every future run -- silently, forever. Instructions_Project_H
    through J went that way, along with a GNG1106 announcement saying
    Assignment 1 was posted.
    """

    @unittest.skipUnless(importlib.util.find_spec("anthropic"),
                         "needs the anthropic SDK; runs on the Pi, where it "
                         "is installed and where this guard matters")
    def test_ask_reports_failure_as_none_not_empty(self):
        """[] means read-and-empty. None means never asked. Not the same."""
        import find_dates

        class Boom:
            class messages:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("credit balance is too low")

        dates, tin, tout = find_dates.ask(
            Boom(), "claude-sonnet-5", "GNG2101", "Instructions_Project_H",
            "Some text with a date in it.", (None, None))
        self.assertIsNone(dates, "a failed call must not look like an empty one")
        self.assertEqual((tin, tout), (0, 0))

    def test_forget_read_clears_only_documents_with_no_dates(self):
        db = fresh_db()
        cid = add_course(db)
        now = store.now()
        for title, has_date in (("Read and found a date", True),
                                ("Marked read, nothing stored", False)):
            db.execute(
                "INSERT INTO documents (course_id, kind, title, text_hash,"
                " word_count, first_seen, last_seen, last_read_at)"
                " VALUES (?,'file',?,?,10,?,?,?)",
                (cid, title, title, now, now, now))
            doc = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            if has_date:
                add_date(db, cid, f"Deadline from {title}", "2026-10-01",
                         key=f"k-{title}")
                db.execute("UPDATE dates SET document_id = ? WHERE dedup_key = ?",
                           (doc, f"k-{title}"))
        db.commit()

        rows = store.forget_read(db, dry=False)
        self.assertEqual([r["title"] for r in rows],
                         ["Marked read, nothing stored"])
        still = db.execute(
            "SELECT title FROM documents WHERE last_read_at IS NOT NULL"
        ).fetchall()
        self.assertEqual([r["title"] for r in still], ["Read and found a date"],
                         "a document that produced a date stays read")



class WorkSheetsAreRecognisedByTitle(unittest.TestCase):
    """An underscore is a word character, so `\\btutorial\\b` missed Tutorial_1.

    Professors name files that way almost exclusively -- Tutorial_1,
    Problem_Set_3, Linear_Algebra___DGD_1. The rule matched none of them and
    said nothing, and the student found it by asking why their tutorial
    questions were not in the to-do list.
    """

    def test_underscored_titles_are_matched(self):
        import posted_work
        for title in ("Tutorial_1", "Tutorial_2", "Problem_Set_3",
                      "Linear_Algebra___DGD_1 questions", "Practice-Questions",
                      "worksheet_4"):
            with self.subTest(title=title):
                self.assertTrue(posted_work.looks_like_work(title),
                                f"{title} is work and must be recognised")

    def test_the_false_friends_still_do_not_match(self):
        """Being too eager here fills the list with lecture slides."""
        import posted_work
        for title in ("Linear_Algebra___Lecture_1", "MCG2360_Syllabus_Fall_2026",
                      "LCA_exercise_solution", "Tutorial_1_solutions",
                      "Linear_Algebra___Lecture_2 (filled)dot product",
                      "MCG 2360 - Marking Scheme", "Exercises_answer_key"):
            with self.subTest(title=title):
                self.assertFalse(posted_work.looks_like_work(title),
                                 f"{title} is not work to do")



if __name__ == "__main__":
    unittest.main(verbosity=2)
