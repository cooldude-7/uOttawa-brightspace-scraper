# uOttawa Brightspace scraper

Scrapes a student's Brightspace, finds deadlines buried in course documents,
and surfaces them as accept/dismiss cards that create Google Calendar events.

**Read `PLAN.md` first** — it holds the architecture and, importantly, the
decisions and the reasons behind them, including several that were revised
after measurement contradicted an assumption.

The user is a mechanical engineering student, not a programmer. Explain in
plain language; avoid jargon or define it in one line. They run everything on
**Windows** in `cmd`/PowerShell and paste terminal output back.

**Whenever you give a command to run on the Pi, put the `ssh` line first, as
its own block.** They may not be connected yet, and a Pi command pasted into
Windows -- or a Windows path pasted into the Pi -- is a confusing failure that
has cost several rounds. The prompt is the tell: `luca67@LUCAPI` is the Pi,
anything else is not.

    ssh luca67@LUCAPI.local        (skip if already in)

    cd ~/uOttawa-brightspace-scraper && git pull origin main
    ...

## State

Working end to end: login → scrape → download → read → store → link tasks →
web app → calendar → vault. Running on the Pi, not the laptop.

**The Pi is running** (2026-09-08). A 3B+ wired to a TP-Link travel router,
256 GB stick formatted ext4 and mounted at `/mnt/data`, scraping hourly from
07:00 to 21:00 under a systemd timer (it began at every 30 minutes round the
clock; nothing is posted at 3am). `docs/pi-setup.md` was followed end to end on
the real hardware and corrected where it was wrong.

The laptop's database was copied across rather than starting clean, so the
decisions and the money already spent came with it. First scrape on the Pi:
204s, **$0.0000** — all 63 documents recognised as already read, which is
`text_hash` doing exactly what it was built for. Google Calendar reconciles
from the Pi.

Since then the corpus has been re-read once with a prompt that also asks for
tasks, and the tasks have been placed in the term by `link_tasks.py`. Total
spend **$2.27**. Roughly 64 items waiting, 72 accepted, 55 collapsed as
duplicates.

Two real misses were found by the user reading their own list, not by any test:
a quiz whose date lived only in a special-access override (see below), and
setup tasks that were never extractable at all because the prompt only asked
for dates.

**Overnight session renewal works** (2026-09-09), and the first morning is how
it was found not to. Every run from 08:00 failed with "could not renew"; two
bugs were in the way, and neither was the expired login everyone assumes:

1. `refresh_session()` asked whether a `d2lSessionVal` cookie existed. The
   expired one was loaded straight from `session.json`, so it saw the name it
   wanted and returned success before posting a single form. A dead cookie has
   the same name as a live one — the test has to be `session_works()`, which
   asks Brightspace rather than the jar.
2. The walk started at `/d2l/home`. Without a session that returns 272 bytes of
   JavaScript reading `window.location.hash` — no form, no `Location` header,
   nothing to follow. `/d2l/login` is where a session is *started*, and it
   redirects into the SAML chain properly.

With those fixed the Pi renews silently: `/d2l/login` → Microsoft →
`SAMLResponse` posted to `samlLogin.d2l` → working session, no password and no
second factor, because the sign-on cookies were valid the whole time.

`probe_session.py` is what found it, by printing the chain rather than
reasoning about it — cookie names and domains only, never values. Reach for it
first if renewal breaks again.

On the Pi, run things through the virtual environment, not plain `python3`:
`~/uOttawa-brightspace-scraper/.venv/bin/python`, with
`BRIGHTSPACE_DATA=/mnt/data` set.

**The phone works.** Tailscale joins the phone and the Pi to one private
network, so the cards load from anywhere without exposing anything publicly —
which matters because `web.py` has no authentication at all. The page carries a
manifest and apple-touch icons, so it sits on the home screen and opens full
screen like an app. iOS honours those over plain http, no certificate needed.

**The vault is built** and larger in scope than §6 planned: a note per course,
every document and announcement, user-editable `Skills/`, and `prep.py` to run
a skill against a real item under the course's own AI policy. See `PLAN.md`
Phase 3.

`prep.py` has run for real: a quiz and a lab report, ~$0.20 each. Three
defects came out of reading those two documents — it hedged about which lab
group the user is in, left a self-correction in the finished page, and named
Thursday 17 September as "Wed 17 Sep". All three fixed; the weekday one by
computing weekdays in Python and forbidding the model from deriving its own.

**The heartbeat is built** (2026-09-09). The `runs` table existed but nothing
wrote to it, so a dead scrape and a quiet week were indistinguishable — the
list simply stopped growing. `update.py` now records every run either way
(catching `BaseException`, because `get_client()` raises `SystemExit` when a
login is needed and that is the failure most worth recording), `/api/health`
reports the age of the last clean run, and the app's header turns into a
warning past three hours — but only inside the 07:00–21:00 window, since the
overnight gap is the schedule working. Note the limit: it runs *on* the Pi, so
it catches a failing scrape while the Pi is alive. A Pi that is off cannot
serve the page at all, which is its own, louder signal.

**The vault syncs.** `vault.py --push` pushes to the private `obsidian-brain`
repo on every scrape, pulling and rebasing first so the laptop can write too.
The Mac and the laptop both hold clones; Obsidian's Git plugin pulls every
five minutes so neither needs a command.

**The app has four views**: the deadline cards, **Agenda** (the term laid out
by day, with ✓/✕ on each row and a drag-to-dismiss detail sheet), **To-do**
(what you have taken on, ordered by when to begin, with somewhere to cross it
off), and Notes.
Agenda and the card exit animations are built on springs — see the physics
block in `static/index.html`, distilled from Apple's *Designing Fluid
Interfaces*. Cards leave in the direction of the decision.

Not built yet:
- **Push notifications.** Nothing tells you a deadline appeared; you have to
  open the app. Web push needs a secure context — worth testing whether
  Tailscale's `*.ts.net` certificates satisfy that before buying a domain.
- **An off-Pi dead-man's switch.** The heartbeat above cannot report a Pi that
  is fully dead. Something outside the Pi would have to notice the silence.

## Commands

Everything runs from `scraper/`.

```
python update.py           check Brightspace for anything new (the main one)
python web.py              the web app; prints a phone-reachable address
python cards.py            same list in the terminal; --review walks it
python gcal.py --check     reconcile calendar against stored decisions
python gcal.py --prune     drop other sections' deadlines already accepted
python store.py --tidy     collapse duplicate events
python mysection.py        work out which lab section the user is in
python link_tasks.py       give undated to-dos a place in the term
python test_rules.py       the rules that must never break (no deps, ~0.1s)
python backup.py           dump the database into the vault
python backup.py --restore rebuild a database from that dump
python exams.py            every exam, and which courses have none yet
python exams.py --on 2026-10-23   just that day
python store.py --section GNG2101 C1             which lab section you are in
python store.py --year-typo MCG2130 2025 2026    a year the prof mistyped
python vault.py            build the Obsidian vault (no API cost)
python vault.py --bundle   one file per course, to upload to a Claude Project
python prep.py --list      work waiting to be prepared, with ids
python prep.py 412         run the right skill against one item
```

Separately, `labs/labpdf.py` turns a folder of GNG1106 lab screenshots into
the PDF to hand in -- ordered by when each was taken, nothing added. It runs
on the student's laptop, not the Pi, and shares nothing with the scraper. See
`labs/README.md`.

`update.py` chains `collect.py` → `exact.py` → `download.py` → `find_dates.py`
→ `link_tasks.py` → `vault.py`. The last two are wrapped so a failure there
cannot take a scrape down with it — deadlines are the product.

On the Pi the same commands run, but under systemd rather than by hand — see
`docs/pi-setup.md` Part 2.

## How it fits together

- **collect.py** — session handling and every Brightspace tab. `get_client()`
  reuses a saved session, silently renews it through the sign-on redirect
  chain, and only then asks for a browser login.
- **exact.py** — due dates Brightspace states outright on assignments and
  quizzes. Cheap and exact; ran before anything is read.
- **download.py** — fetches files, extracts text from PDF/PPTX/DOCX/XLSX.
- **find_dates.py** — sends documents to Claude for dates written in prose.
  This is the product, not a fallback.
- **link_tasks.py** — the second pass over what was read. `find_dates.py` sees
  one document at a time, so a task like "install the Arduino IDE" comes back
  undated: that document has no idea when the Arduino lab is. This one sees the
  whole course at once and ties each task to the dated thing it must precede.
  It may only pick from anchors it was given — an invented one is discarded and
  printed — and a task with no real anchor stays undated on purpose.
- **vault.py** + **skills.py** — the Obsidian vault. A note per course holding
  what is due, what is new and what to read, plus every extracted document and
  announcement as its own note. Deterministic and free — it only reshapes what
  is already in the database. `skills.py` seeds the editable markdown in
  `Skills/` once and never touches it again.
- **Per-course chat lives in Claude Projects, not in this app.** One project
  per course, with `Bundles/<course>.md` as its knowledge. An Ask tab was
  built into the web app and reverted (177edb0, then its revert): Projects is
  a far better interface, works on both the user's devices, takes a photo of a
  whiteboard, and costs nothing beyond a subscription they already pay for,
  where every question through the API costs money. The bundle is the price of
  that choice — it has to be re-run and re-uploaded when a course posts a lot
  of new material.
- **prep.py** — runs a skill against one real item and writes the result into
  its note in `Work/`. How far it goes is decided by
  `Skills/course-policies.md`, not by code: GNG2101's professor permits AI
  through a lab report except the measured results and graphs, and requires it
  declared; other courses are unconfirmed and default to prepare-not-draft.
  Every run appends to `AI use log.md` and `ai-use-log.csv`, because GNG2101
  submits an AI log and an attestation. A prepared note loses its
  `generated: true` marker, so it belongs to the user from then on.
- **store.py** — SQLite. Change detection, duplicate collapsing, decisions.
  `course_parts()` turns "MCG2360  A00  Engineering Materials I [ LEC ] 20269"
  into a code and a title, because nobody remembers the codes.
- **web.py** + `static/index.html` — FastAPI and one HTML page, no build step
  (it has to run on the Pi). Four views -- Deadlines, To-do, Agenda, and a
  **Notes** view that reads the vault, so a prep document can be read on the
  phone without Obsidian. Markdown is rendered server-side; maths comes from KaTeX
  on a CDN and degrades to raw TeX if that cannot load.
- **gcal.py** — Google Calendar.
- **paths.py** — where data lives. Unset, everything sits next to the code as
  it always has; `BRIGHTSPACE_DATA=/mnt/data` moves it all to the Pi's USB
  stick. Nothing else in the codebase builds a data path itself.

## Things that will bite

- **Never store a real deadline behind a rule that can silently drop it.** A
  title-cleaning rule once deleted two of the user's lab deadlines. Merges
  mark rows rather than deleting them, `--restore` undoes them, and anything
  dropped is printed by name. Keep it that way.
- **Some real deadlines are in no API field at all.** An item restricted to
  particular students keeps its dates in a special-access override; `DueDate`
  and `EndDate` come back `null` and the `specialaccess` endpoint answers 403
  to a student. The date exists only on the rendered page. `pagedates.py`
  parses it from there — the one place HTML parsing is used, and it only ever
  fills a date that is missing, never overrides one the API gave. GNG1106's
  LAB 1 was missing this way and nobody would have known. `probe_quiz.py`
  is how it was tracked down, if it happens again elsewhere.
- **The table of contents returns `DueDate: null` even when the item has
  one.** GNG2101's Arduino Pre-lab is due 13 October; `/content/toc` says
  nothing, and the date exists only on the item's own
  `/content/topics/{id}` endpoint. `collect.fill_topic_dates()` asks for it,
  caching each answer against the item's `LastModifiedDate` so a routine
  scrape re-asks for nothing -- without that it is hundreds of requests an
  hour. And `exact.py` had no loop over `topics` at all, so even a date the
  table of contents *did* carry went nowhere. Both halves were needed; either
  alone still loses the deadline.
- **A SCORM package is a deadline with nothing to download.** `TopicType` 11
  / `ContentService`, with a `d2l:brightspace:content:...` URL that is an
  internal identifier, not a path -- prefixing the host gives a 400, and
  `/file` and `DirectFileTopicDownload` answer 404 and 403. There is no file.
  The three GNG2101 pre-labs are these. Their value is the due date, which
  the rule above now recovers.
- **A content item's description is a place deadlines hide.** Brightspace
  lets a professor write a paragraph under an item ("Arduino Pre-lab") as
  well as under a folder. `find_dates.py` read folder descriptions from the
  start and item descriptions not at all, so "install the IDE before the lab"
  written there reached nothing -- no error, no missing file, simply never
  looked at. Now read as kind `item`. It also partly covers an item whose
  attachment will not download: the file is lost, the instructions around it
  are not.
- **Course shells are reused between terms.** Stale dates from previous
  offerings appear in folder text *and* in Brightspace's own due-date fields —
  one assignment still says June. Everything is filtered to the term window.
- **A professor can type the wrong year, and it looks identical to a stale
  shell.** MCG2130's eight assignments were dated 2025 in the syllabus *and*
  in Brightspace's due-date fields; the professor said in class that the
  syllabus was mistyped. The term filter dropped all eight. `store.fix_year()`
  corrects it, but only per course and only from `me.json` — never as a
  "looks a year off, shift it" heuristic, because that would also rewrite the
  genuinely stale dates a reused shell carries, and those are meant to be
  dropped. Set one with `python store.py --year-typo MCG2130 2025 2026`.
  Every shifted date is printed on every scrape, like every dropped one.
- **Section-specific deadlines.** One assignment lists five different dates
  for lab sections A1–A5. The section marker is part of an event's identity,
  or A2 and A5 collapse into one. The user is **A4** in GNG2101, established by
  matching their personal due date against the text, and the **Thursday** group
  in MCG2360 (A02 tutorial, Thu 19:00 — from their timetable, since
  `mysection.py` works out sections but not day groups). Both live in
  `me.json`. `store.only_mine()` applies it, and the card list, the vault and
  `prep.py` all go through it — it was once inline in `cards()` and the other
  two showed all five sections. `gcal.py --prune` cleans up anything accepted
  before the filter knew.
- **A stale date and a mistyped year look identical, and the weekday tells
  them apart.** GNG2101's Quiz 1 is switched on and dated 19 Sep **2025**,
  opening 5:00 PM and due 5:45 PM -- a 45-minute slot. 19 Sep 2025 was a
  Friday; a year later it is a Saturday, and a timed quiz does not move to a
  Saturday. The same course's Mill/lathe pre-lab is correctly dated Tue 29
  Sep **2026, 7:00 PM**, matching the student's own lab slot -- so this
  professor does update dates, and one stale item is stale rather than
  mistyped. MCG2130's eight assignments were the opposite: every weekday held.
  Check the weekday before ever reaching for `--year-typo`.
- **An active quiz carrying only last term's date is stored with no date.**
  Dropping it hides real work; shifting the year invents a deadline. Neither
  is acceptable, so `exact.py` stores it `pending` -- named but not scheduled
  -- which is exactly what is true about it. Scoped to quizzes Brightspace
  still has switched on (`IsActive`), because a reused shell is otherwise
  full of stale items and this would become noise.
- **A section setting can go stale, and then it hides everything.** GNG2101's
  lab sections were A1–A5 and became **C1–C3**; `me.json` still said `a4`,
  which matches nothing, so `only_mine()` would have dropped every real lab
  deadline in the course as somebody else's — silently, which is the exact
  failure mode this project exists to prevent. `only_mine()` now skips
  filtering for any course whose deadlines name no section resembling the one
  on file, and `store.stale_sections()` reports it; `update.py` prints it at
  the end of every scrape. Showing a few extra rows is a nuisance; hiding a
  real deadline is not recoverable.
- **What the app may write is the user's professor's call, not ours.**
  `Skills/course-policies.md` holds it per course, in plain English the user
  edits. A course not listed there defaults to prepare-not-draft. One rule sits
  above the policy file and is not negotiable because it protects the user:
  measured data, results and graphs are never generated — not a reading, not a
  trend, not a plausible number in a table. A fabricated measurement does not
  stay in a draft, it gets submitted.
- **The database is the one irreplaceable thing.** The vault is pushed to
  git every scrape; the database holds every decision and the `text_hash`
  memory that is the only reason a scrape costs $0.00 rather than re-reading
  90,000 words, and it lived on one USB stick. `backup.py` writes a SQL dump
  into the vault each scrape so the existing push carries it off the Pi.
  Text, not a binary copy: git stores successive versions of text as small
  differences, and a dump can be read and restored with nothing but sqlite3.
  The `runs` table is left out deliberately -- it gains a row every hour
  whether or not anything changed, and including it would make the vault
  commit hourly forever and bury the history of real changes.
- **The vault must never eat the user's writing.** A generated note carries
  `generated: true`; `vault.py` refuses to overwrite any file without it, so
  deleting that line claims a note permanently. `Notes/` is never generated at
  all, and `Skills/` is seeded once. Weakening this to "just regenerate
  everything" would cost the user work they cannot get back.
- **Done is a third axis, not a status.** Accepted says a deadline is real
  and yours; dismissed says it is not. Neither ever said you had finished it,
  so the kept list only grew. `done_at` is that, and the To-do tab is where it
  gets crossed off. Crossing something off never touches Google Calendar --
  it still happened.
- **No row is ever kind `exam`.** The extractor's enum says `midterm` and
  `final_exam`; `exact.py` emits assignment, quiz and session. Every rule
  keyed on `"exam"` -- the 10-day lead time, the hand-in logic, `exams.py` --
  was dead for a day without anything failing: a midterm simply got the
  3-day lead of an unknown kind. `store.norm_kind()` folds the aliases; key
  on its output, and if you add a kind, add it there.
- **A linked to-do carries its anchor's date and time, so by `event_key` it
  *is* the anchor.** The twin check in `save_dates()` and pass 1 of `tidy()`
  both skip rows with `linked_to` set, or the to-do's longer title would win
  the bucket and mark the real lab resolved -- silently, printing a count.
- **Start dates are derived, never stored.** `store.start_by()` works back
  from the due date by `LEAD_DAYS` for the kind of thing (exam 10 days, quiz
  5, lab 4, assignment 3, to-do 2), overridable per kind in `me.json`. Derived
  so a corrected date or a retuned lead time moves everything at once and
  cannot strand a stale start date. The number that matters is not any single
  lead time but how many items sit in "start now" at once: if fifteen do, that
  reads exactly like none of them do. The To-do tab colours a start date only
  within a day of it, for the same reason.
- **"Do I have to hand something in" gets answered, not deferred.**
  `store.submission_map()` decides from three facts already on file: an
  assignment or quiz from Brightspace *is* a submission folder, a document
  saying submit/upload/dropbox says so outright, and otherwise the **absence**
  of any matching folder in that course is itself evidence -- Brightspace
  lists every folder a course has, so if none of them is this, there is
  nowhere to hand it in. Task titles match folder titles on one distinctive
  word or two ordinary ones, which is how "install the Arduino IDE" finds
  "Arduino lab + BOM submission". An earlier version said "check if anything
  to hand in" and the user rightly rejected it: telling them to go and look
  is the work the app exists to do. What it genuinely cannot know is a
  professor collecting paper in class, so "no" is worded as what was checked,
  not as a promise.
- **Times are stored 24-hour and shown 12-hour.** `store.pretty_time()` for
  display, `store.parse_time()` for the one place a time is parsed rather
  than shown -- building a calendar event. That parse used `strptime(...,
  "%H:%M")` directly, so a model returning "7:00 PM" instead of the 19:00 the
  prompt asks for would have raised and quietly cost an accepted deadline its
  calendar entry.
- **A linked date is inferred, not stated.** `linked_to` being set is what
  says so, and the card shows "before Lab 5" in a different colour for exactly
  that reason. Never let a linked date render as though a document published it.
- **Recall over precision, but never invention.** Haiku 4.5 fabricated a
  whole semester of dates from "8 weekly assignments will be given"; Sonnet 5
  correctly returned nothing. Use **Sonnet 5**. Every date must carry the
  sentence it came from.
- **Don't hold a SQLite write open across a network call.** That caused
  "database is locked" on every rapid tap.
- **`/mnt/data` existing does not mean the stick is mounted.** It is an
  ordinary empty folder on the SD card when the stick is absent, and writing
  there starts a silent fresh database. `paths.py` requires a
  `.brightspace-data` marker file and stops hard without it. Don't weaken that.
- **The Pi has no browser, on purpose.** So `collect.py` imports Playwright
  inside `browser_login()`, not at module level. Moving that import back to the
  top breaks the Pi entirely.
- **Windows has no timezone database.** `zoneinfo` fails; the Ottawa offset is
  computed directly in `exact.py`.
- **An old `session.json` cannot renew itself.** Files from before the 2026-09-08
  revision hold only the four Brightspace cookies — about 190 bytes — and the
  Pi will scrape once and then be stuck at the next expiry. One carrying the
  sign-on cookies is around 7 KB across several domains. Check the size before
  trusting it; the fix is to move it aside and log in again.
- **Renewal has two entry points and only one of them works.** `/d2l/home` is
  where a browser lands once it already has a session; starting one goes
  through `/d2l/login`. And never test a session by looking for a cookie by
  name — expired and fresh are indistinguishable that way. Both mistakes were
  made here and both looked exactly like an expired login in the logs.
- **Secrets stay out of git**: `api_key.txt`, `google_client.json`,
  `google_token.json`, `session.json`. The session file now holds sign-on
  cookies — a bearer token for the whole uOttawa account. Treat it seriously,
  especially once it lives on the Pi.

## Before you change anything

    python test_rules.py

Sixteen tests, standard library only, a tenth of a second. They are not
coverage -- they are a guard on the handful of behaviours where being wrong
is expensive **and silent**: the year-typo rule firing on a course it was
not declared for, a linked to-do absorbing a real deadline, another
section's deadlines reaching the list, a dismissed item coming back, the
vault overwriting something you wrote, `pretty_time`/`parse_time`
disagreeing so an accepted deadline never reaches the calendar.

Every case in there is a bug that actually happened. They were checked by
deliberately re-breaking each rule and confirming the suite failed, which is
the only evidence a test is worth having.

If you add a rule of that kind, add a test. If a test fails, the code is
wrong until proven otherwise -- the last time one disagreed with the code it
was the test that was wrong, but that is the exception and it took evidence.

## Working style that has worked here

Verify against reality rather than reasoning about it — render the page and
look at it, print what an endpoint actually returned, test the upgrade path
rather than the fresh install. Several real bugs surfaced only that way. The
user reads their own list and spots what a test cannot; when they say
something is wrong, they have been right.
