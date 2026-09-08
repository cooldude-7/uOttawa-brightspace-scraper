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

Working end to end on the laptop: login → scrape → download → read → store →
web app → calendar. About $1.40 of API spend so far.

Not built yet:
- **The Pi.** Always-on scraping every 30 min, and PWA push notifications.
  Hardware: Raspberry Pi 3B+ (1 GB), 256 GB USB stick, unformatted. See
  `docs/pi-setup.md` — written but never executed.
- **The Obsidian vault.** Untouched since planning. All documents are already
  downloaded and text-extracted, so this is mostly foldering and frontmatter.

## Commands

Everything runs from `scraper/`.

```
python update.py           check Brightspace for anything new (the main one)
python web.py              the web app; prints a phone-reachable address
python cards.py            same list in the terminal; --review walks it
python gcal.py --check     reconcile calendar against stored decisions
python store.py --tidy     collapse duplicate events
python mysection.py        work out which lab section the user is in
```

`update.py` chains `collect.py` → `exact.py` → `download.py` → `find_dates.py`.

## How it fits together

- **collect.py** — session handling and every Brightspace tab. `get_client()`
  reuses a saved session, silently renews it through the sign-on redirect
  chain, and only then asks for a browser login.
- **exact.py** — due dates Brightspace states outright on assignments and
  quizzes. Cheap and exact; ran before anything is read.
- **download.py** — fetches files, extracts text from PDF/PPTX/DOCX/XLSX.
- **find_dates.py** — sends documents to Claude for dates written in prose.
  This is the product, not a fallback.
- **store.py** — SQLite. Change detection, duplicate collapsing, decisions.
- **web.py** + `static/index.html` — FastAPI and one HTML page, no build step
  (it has to run on the Pi).
- **gcal.py** — Google Calendar.

## Things that will bite

- **Never store a real deadline behind a rule that can silently drop it.** A
  title-cleaning rule once deleted two of the user's lab deadlines. Merges
  mark rows rather than deleting them, `--restore` undoes them, and anything
  dropped is printed by name. Keep it that way.
- **Course shells are reused between terms.** Stale dates from previous
  offerings appear in folder text *and* in Brightspace's own due-date fields —
  one assignment still says June. Everything is filtered to the term window.
- **Section-specific deadlines.** One assignment lists five different dates
  for lab sections A1–A5. The section marker is part of an event's identity,
  or A2 and A5 collapse into one. The user is **A4**, established by matching
  their personal due date against the text, stored in `me.json`.
- **Recall over precision, but never invention.** Haiku 4.5 fabricated a
  whole semester of dates from "8 weekly assignments will be given"; Sonnet 5
  correctly returned nothing. Use **Sonnet 5**. Every date must carry the
  sentence it came from.
- **Don't hold a SQLite write open across a network call.** That caused
  "database is locked" on every rapid tap.
- **Windows has no timezone database.** `zoneinfo` fails; the Ottawa offset is
  computed directly in `exact.py`.
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
