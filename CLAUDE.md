# uOttawa Brightspace scraper

Scrapes a student's Brightspace, finds deadlines buried in course documents,
and surfaces them as accept/dismiss cards that create Google Calendar events.

**Read `PLAN.md` first** — it holds the architecture and, importantly, the
decisions and the reasons behind them, including several that were revised
after measurement contradicted an assumption.

The user is a mechanical engineering student, not a programmer. Explain in
plain language; avoid jargon or define it in one line. They run everything on
**Windows** in `cmd`/PowerShell and paste terminal output back.

## State

Working end to end: login → scrape → download → read → store → link tasks →
web app → calendar → vault. Running on the Pi, not the laptop.

**The Pi is running** (2026-09-08). A 3B+ wired to a TP-Link travel router,
256 GB stick formatted ext4 and mounted at `/mnt/data`, scraping every 30
minutes under a systemd timer. `docs/pi-setup.md` was followed end to end on
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

**Not proven yet: overnight session renewal.** The Brightspace session dies in
under 24 hours; the Pi is supposed to replay the sign-on chain and renew itself
without anyone tapping a phone. The session file was regenerated in the new
per-domain format for exactly this, but the real test is the first morning.

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

Not built yet:
- **`vault.py` is not in the automatic cycle.** `update.py` chains through
  `link_tasks.py` and stops; the vault only refreshes when run by hand, so it
  goes stale between scrapes. Adding it (and `--push`) to `update.py` is small
  and probably the next thing worth doing.
- **`prep.py` has never run against the real API.** Verified only with the call
  mocked — the prompt is right, the output is unproven.
- **Push notifications.** Nothing tells you a deadline appeared; you have to
  open the app. Web push needs a secure context — worth testing whether
  Tailscale's `*.ts.net` certificates satisfy that before buying a domain.
- **A heartbeat.** Nothing tells you the Pi has stopped, either. This matters
  more now that it is being trusted.
- **The vault is not synced yet.** `obsidian-brain` exists as a private repo;
  the deploy key and `vault.py --push` are `docs/pi-setup.md` Part 4, not done.

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
python vault.py            build the Obsidian vault (no API cost)
python prep.py --list      work waiting to be prepared, with ids
python prep.py 412         run the right skill against one item
```

`update.py` chains `collect.py` → `exact.py` → `download.py` → `find_dates.py`
→ `link_tasks.py`.

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
  (it has to run on the Pi).
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
- **Course shells are reused between terms.** Stale dates from previous
  offerings appear in folder text *and* in Brightspace's own due-date fields —
  one assignment still says June. Everything is filtered to the term window.
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
- **What the app may write is the user's professor's call, not ours.**
  `Skills/course-policies.md` holds it per course, in plain English the user
  edits. A course not listed there defaults to prepare-not-draft. One rule sits
  above the policy file and is not negotiable because it protects the user:
  measured data, results and graphs are never generated — not a reading, not a
  trend, not a plausible number in a table. A fabricated measurement does not
  stay in a draft, it gets submitted.
- **The vault must never eat the user's writing.** A generated note carries
  `generated: true`; `vault.py` refuses to overwrite any file without it, so
  deleting that line claims a note permanently. `Notes/` is never generated at
  all, and `Skills/` is seeded once. Weakening this to "just regenerate
  everything" would cost the user work they cannot get back.
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
- **Secrets stay out of git**: `api_key.txt`, `google_client.json`,
  `google_token.json`, `session.json`. The session file now holds sign-on
  cookies — a bearer token for the whole uOttawa account. Treat it seriously,
  especially once it lives on the Pi.

## Working style that has worked here

Verify against reality rather than reasoning about it — render the page and
look at it, print what an endpoint actually returned, test the upgrade path
rather than the fresh install. Several real bugs surfaced only that way. The
user reads their own list and spots what a test cannot; when they say
something is wrong, they have been right.
