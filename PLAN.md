# uOttawa Brightspace → Obsidian → Calendar

A personal system that scrapes uOttawa Brightspace into an Obsidian vault, extracts every
due date and important date, and surfaces them as accept/dismiss cards with push
notifications on laptop and phone.

---

## 1. Decisions locked in

| Decision | Choice | Why |
|---|---|---|
| Auth | Sign-on cookies stored too (**revised 2026-09-08**) | Brightspace-only meant re-authenticating every morning, which defeats unattended scraping. See §2 |
| Login | Real browser on the laptop, human does MFA | MFA is a one-time step; there is no safe way to automate it |
| Scraping | Authenticated JSON calls to `/d2l/api/...`, no headless browser | Runs in tens of MB on the Pi; survives D2L UI reskins |
| Always-on host | **Raspberry Pi 3B+** (Zero 2 W as spare) | 1 GB RAM and wired Ethernet — double the headroom and a far more reliable link than the Zero's 2.4 GHz-only Wi-Fi |
| Heavy lifting | Main computer (initial scrape + file extraction) | CPU. Storage is no longer the reason — 256 GB on the Pi holds the whole archive |
| Vault sync | Private git repo, Obsidian Git on laptop only | Free, versioned, diffable. Phone gets the web app, not the vault |
| Vault scope | Everything, files text-extracted to markdown | Full-text searchable brain |
| Cards | Check → Google Calendar, X → dismissed permanently | Predictable; nothing hits the real calendar unreviewed |
| Notifications | PWA web push | One app on both devices, no extra install |

---

## 2. Why there is no browser on the Pi

**Use the 3B+, not the Zero 2 W.** 1 GB of RAM instead of 512 MB, a faster clock, and — the
part that matters most for something running unattended for a semester — wired Ethernet
instead of 2.4 GHz-only Wi-Fi. Keep the Zero 2 W as a spare.

Even so, the design stays browser-free. Headless Chromium wants 400–800 MB on real pages, so
on 1 GB it would run but leave nothing for the app, and Playwright ships no official arm64
Linux Chromium build (you would be wiring up the system one by hand).

The scraper does not need one. Brightspace's own web UI is a JavaScript client that talks to a REST
API at `/d2l/api/lp/...` (learning platform) and `/d2l/api/le/...` (learning environment),
authenticated by nothing but the session cookie. Once the laptop hands over a valid
`d2lSessionVal`, the Pi's entire job is HTTP GETs returning JSON. That is `httpx` and
`json.loads` — trivially within budget.

The browser is needed exactly once, for login, and that happens on the laptop.

### The auth loop

1. Laptop runs a headed Chromium; you enter your uoAccess credentials and tap Authenticator.
2. Microsoft Entra issues its session cookies, redirects into Brightspace, which issues
   `d2lSessionVal` / `d2lSecureSessionVal`.
3. The helper extracts **only the `d2l*` cookies** and POSTs them to the Pi over the tunnel,
   encrypted at rest. Your password and Microsoft session never leave the laptop.
4. The Pi replays those cookies every 30 minutes.
5. When they expire, the Pi pushes a notification: "Brightspace session expired — reconnect."
   You run the helper again. Expected cadence: days to two weeks (measured in Phase 1).

### Revised, 2026-09-08: sign-on cookies are stored

The estimate above was wrong. Measured, the Brightspace session lasted **under 24 hours** —
logged in one evening, dead by 09:37 the next morning. Steps 4 and 5 never happened; it went
straight from login to full re-login.

That is survivable while running by hand and fatal to the point of the project. A Pi that
stops every morning waiting for a phone tap is not scraping every 30 minutes, and a deadline
posted during the dead hours goes unseen until someone notices.

So the scraper now keeps the identity provider's cookies as well, per domain, and renews
itself: when the Brightspace session dies it replays the sign-on redirect chain, which the
provider still recognises, and gets a fresh session with no password and no second factor.
Sign-on pages hand the browser a form that submits itself with JavaScript; with no browser
present the form is parsed and posted directly.

**What this costs.** That cookie is a bearer token for the whole uOttawa Microsoft account,
not just Brightspace. Anyone who can read the session file can be you — on email and OneDrive,
not only on course pages. It is written owner-only and gitignored, but that is protection
against accidental disclosure, not against someone with a shell on the machine. The honest
mitigation is that the file lives on hardware you own, behind your home network, and that the
Pi should be treated as holding something valuable: no port forwarding, tunnel only, and a
password worth having.

A full login is still needed when the provider's cookies themselves expire — weeks, typically
— and the app asks for it with a notification rather than failing silently.

---

## 3. Architecture

```
┌─ Your laptop ────────────────────┐      ┌─ Pi 3B+ ───────────────────────────┐
│                                  │      │                                    │
│  login_helper.py                 │      │  FastAPI app (uvicorn, 1 worker)   │
│    headed Chromium → MFA         │─────▶│    /api/session   ← cookie intake  │
│    extracts d2l* cookies         │ POST │    /api/cards     → the to-do list │
│                                  │      │    /api/decide    ← check / X      │
│  full_scrape.py  (once/term)     │      │    static/        → the PWA        │
│    all courses, all files        │      │                                    │
│    PDF/PPTX/DOCX → markdown      │      │  APScheduler: incremental_scrape   │
│    writes vault/                 │      │    every 30 min                    │
│         │                        │      │         │                          │
│         ▼                        │      │         ▼                          │
│  Obsidian ◀── git pull ──────────┼──────┼─ git push ──▶ private GitHub repo  │
│                                  │      │                                    │
└──────────────────────────────────┘      │  SQLite: items, dates, decisions   │
                                          │  web-push (VAPID) → phone/laptop   │
                                          │  cloudflared → HTTPS               │
                                          └───────────────┬────────────────────┘
                                                          │
                                              Brightspace /d2l/api  ·  Google Calendar
```

Two entry points, one codebase. `full_scrape.py` runs on the laptop and does the expensive
work; the Pi only ever runs `incremental_scrape`, which fetches small JSON diffs.

---

## 4. Stack

**Backend — Python 3.11**
- `fastapi` + `uvicorn` (single worker) — ~80 MB resident
- `httpx` — the entire Brightspace client
- `sqlite3` — **not Postgres**. Postgres would run on 1 GB but earns nothing here: one
  writer, a few thousand rows, no real concurrency
- ~~`apscheduler`~~ — **revised 2026-09-08: a systemd timer instead.** Not installed. The
  Pi already runs systemd, so a timer is one file and no dependency, and it buys two things
  APScheduler inside the app cannot: a scrape that hangs or crashes cannot take the web app
  down with it, and `Persistent=true` catches up a window missed while the Pi was off.
  `journalctl -u brightspace-update` is then the scrape log, for free
- `pywebpush` — VAPID push
- `google-auth` + `google-api-python-client` — Calendar
- `cryptography` (Fernet) — cookie and refresh-token encryption at rest

**File extraction (laptop only)**
- `pymupdf4llm` — PDF → markdown directly, fast, good with slide layouts
- `python-pptx`, `python-docx`
- No OCR. Scanned handouts get a stub note linking the original; add OCR later if it turns
  out your profs actually post scans.

**Frontend — React + Vite, built on the laptop, committed as static assets.** No Node on the
Pi. FastAPI serves the `dist/` folder. Service worker + web app manifest for PWA install.

**Infra**
- `cloudflared` (arm64) — free HTTPS + a stable hostname, no port forwarding, no dynamic DNS.
  Web push *requires* HTTPS, so this is load-bearing, not a nicety.
- `systemd` units for the app and the tunnel; `zram` enabled for swap headroom.
- **SQLite, the vault, and the file archive all live on the 256 GB USB stick, not the
  microSD.** A database committing writes every 30 minutes for a year is exactly the workload
  that kills SD cards. The SD holds the OS only.
- **One environment variable decides where data lives.** `BRIGHTSPACE_DATA=/mnt/data` on the
  Pi; unset on the laptop, where everything stays next to the code exactly as it always has.
  `paths.py` is the single place that resolves it.
- **A marker file proves the stick is actually mounted.** `nofail` means a Pi that boots
  without the stick comes up anyway, leaving `/mnt/data` as an ordinary empty folder *on the
  SD card* — so "the folder exists" proves nothing and the app would cheerfully start a fresh
  empty database there. A `.brightspace-data` file written onto the stick's own filesystem is
  the check that actually distinguishes the two, and its absence is a hard stop.
- **Reformat the stick to ext4 before using it.** It almost certainly ships as exFAT or NTFS,
  and both are wrong here: exFAT has no POSIX permissions, no symlinks, and no reliable file
  locking, which makes SQLite genuinely corruption-prone rather than merely slow — and git
  loses file modes on it. `mkfs.ext4`, then mount by UUID in `/etc/fstab` so a reboot with the
  stick in a different port doesn't silently start a fresh empty database.
- **Run SQLite in WAL mode** (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL`). Cheap
  flash sticks have poor random-write performance, and on the 3B+ the USB bus is shared with
  Ethernet (its NIC is USB-attached), so the two contend. WAL turns the write pattern into
  appends and makes that contention a non-issue at this workload.

---

## 5. Data model (SQLite)

```sql
courses(id, d2l_org_unit_id, code, name, term, active)

items(id, course_id, kind, d2l_id, title, url, body_md, file_path,
      content_hash, first_seen, last_seen, updated_at)
      -- kind: announcement | content_topic | assignment | quiz | discussion | grade

dates(id, item_id, course_id, title, due_at, kind, confidence,
      source_excerpt, dedup_key, status, gcal_event_id, created_at, decided_at)
      -- kind: due | exam | reading | other
      -- status: pending | accepted | dismissed

push_subscriptions(id, endpoint, p256dh, auth, created_at)
scrape_runs(id, started_at, finished_at, mode, items_new, items_changed, error)
secrets(key, ciphertext)   -- brightspace cookies, google refresh token
```

Two mechanisms carry most of the weight:

**`content_hash`** — change detection. An item whose hash is unchanged is skipped entirely:
no re-extraction, no LLM call, no vault rewrite. This is what makes a 30-minute cadence cost
approximately nothing.

**`dedup_key`** — `sha256(course_id + normalized_title + due_date)`. This is what makes
"X dismisses forever" actually work. A re-scrape that re-detects the same date matches the
key, sees `status = dismissed`, and stays silent. Without it you would re-dismiss the same
midterm every half hour.

---

## 6. Obsidian vault layout

```
vault/
  Courses/
    CSI 2110 — Data Structures and Algorithms/
      _Course.md              ← MOC: links, upcoming dates, grade summary
      Announcements/
        2026-09-05 — Welcome to CSI 2110.md
      Content/
        Module 1 — Introduction/
          Lecture 1 — Big-O.md          ← extracted from Lecture1.pdf
      Assignments/
        Assignment 1.md         ← description, rubric, due date
      Discussions/
  Dashboards/
    Upcoming.md               ← Dataview query across all frontmatter
```

Every note carries YAML frontmatter:

```yaml
course: CSI 2110
kind: assignment
d2l_id: 1284471
url: https://uottawa.brightspace.com/d2l/le/...
due: 2026-09-28T23:59
scraped_at: 2026-09-07T14:32
```

Frontmatter is the point — it makes the whole vault queryable from Dataview, so
`Dashboards/Upcoming.md` is a live view rather than a generated file that goes stale.

**Binaries stay out of git, but they no longer stay off the Pi.** Original PDFs and decks go
in a gitignored `_originals/`, and with 256 GB the Pi keeps a full copy alongside the laptop's.
Only markdown extractions are committed — a semester of slide decks would bloat the repo into
uselessness. (git-lfs is the escape hatch if you ever want them versioned.)

That extra copy unlocks something the earlier storage budget ruled out: **the web app can serve
the original file.** A card for "Assignment 2" can link straight to the actual PDF, opened on
your phone from anywhere via the tunnel — no Brightspace login, no hunting through content
modules. Worth building in Phase 5 while the card UI is already open.

---

## 7. Date extraction

**Reading documents is the product, not a fallback.** Brightspace's structured date fields
and its calendar are badly incomplete in practice — professors do not fill them in
consistently. Real deadlines live scattered through course outlines, lecture slides,
announcement text, and assorted PDFs. That gap is the entire reason this app exists, and the
design must treat prose as the primary source rather than the leftovers.

**Source 1 — structured fields.** `DueDate` on assignments and quizzes, `ModuleDueDate` on
modules, plus calendar events. Free, exact, no API cost — take them, but expect them to cover
only a fraction of the real deadlines. A floor, not a ceiling.

**Source 2 — every document, read.** Course outlines, slide decks, handouts, announcements,
module descriptions. This is the main path and it has to be thorough: a missed deadline here
is the failure the whole project is meant to prevent. Two stages, because sending unfiltered
text is wasteful:

**Stage 1 — cheap filter.** Regex + `dateutil` scan for date-shaped strings. Documents with no
date-like text at all are dropped before any API call. Syllabi and assignment descriptions
always pass through regardless. This alone removes roughly half the corpus.

**Stage 2 — the model.** Survivors go to Claude with structured outputs
(`output_config.format`, JSON schema pinned) returning `{title, due_at, kind, confidence,
source_excerpt}[]`. `source_excerpt` matters: the card shows you the sentence the date came
from, so you can judge a bad extraction in one glance instead of opening Brightspace.

Nothing is ever auto-added to your calendar. Every date lands as `pending` and waits for a
check or an X.

### Which model, and what it costs

Rough sizing for a 5-course term reading every document: ~750 items averaging ~5K tokens
after extraction ≈ 3.75M input tokens, ~225K output.

| Model | In / Out per MTok | Initial scrape | Ongoing |
|---|---|---|---|
| Haiku 4.5 | $1 / $5 | ~$5 | pennies/day |
| Sonnet 5 | $2 / $10 | ~$10 | ~$0.20/day |
| Opus 5 | $5 / $25 | ~$24 | ~$0.50/day |

**DECIDED (2026-09-07): Sonnet 5.** Measured on three real syllabi, and the gap was not
subtle. On the Thermodynamics outline Haiku 4.5 returned 11 dates and Sonnet returned 0.
Sonnet was right: every Haiku date was fabricated. All eight "weekly assignments" cited the
same dateless sentence — *"8 weekly assignments will be given in total"* — from which it
counted forward and manufactured specific due dates. Both midterms cited *"The midterm dates
will be decided in class"* and were returned as `2026-10-00` and `2026-11-00`. On the Materials
outline Haiku also added ten tutorial sessions with nothing to submit.

The failure mode matters more than the count. Fabricated deadlines are worse than no app at
all: a student who trusts a hallucinated midterm date is worse off than one who never had the
tool. Recall bias is right for *finding* dates, never for *inventing* them, and Haiku could
not hold that line. At roughly $0.07 per full syllabus pass the price difference is
irrelevant next to that.

Original reasoning, kept because the method is reusable:

<details><summary>Measure before committing</summary> Pulling a deadline out of a clean announcement
is easy; finding one buried on slide 34 of a course outline, or phrased as "the report is due
the Friday after reading week," is not. Since reading documents *is* the product, accuracy
matters more than the token bill here.

Plan: build a small test set from real course outlines with the deadlines hand-labelled, then
run Haiku 4.5 and Sonnet 5 against it and compare. A missed midterm costs far more than the
~$5 difference. Provisional split if the results are close — Haiku 4.5 for short announcements,
Sonnet 5 for long documents like outlines and decks, where the reasoning is harder.

Recall bias is deliberate: it is better to surface a date that turns out to be nothing (one
click to dismiss) than to miss a real one. Tune the prompt to over-report.

</details>

### Dates that do not exist yet

The Thermodynamics syllabus says the midterm dates *"will be decided in class"* and that
assignments come *"weekly"* with no schedule. Those are not extraction failures — the
information genuinely is not written down yet. It will arrive later, in an announcement.

This is the strongest argument for the recurring scrape, and it deserves a feature of its
own: **pending items**. When a document promises a deliverable without a date ("two midterms,
dates TBA", "8 weekly assignments"), record it as an open expectation rather than discarding
it. The card reads *"MCG2130 — 2 midterms, dates not announced yet"*, and when an announcement
finally names one, it resolves against that expectation and becomes a real card.

Without this, a course whose professor announces everything verbally looks identical to a
course with no deadlines. With it, the app can say what it is still waiting on — which is the
question a student actually has in week one.

Prompt caching is a minor lever here: the extraction system prompt is short relative to each
document, and Haiku's minimum cacheable prefix may not even be met. The real savings are in
Stage 1 and in `content_hash` skipping unchanged items.

---

## 8. Build phases

**Phase 0 — Recon spike. PASSED (2026-09-07).**

The student role reads the REST API cleanly: 19 endpoints OK, 0 denied, 0 missing, 0 non-JSON,
across content, announcements, assignments, quizzes, grades, and calendar events on three
sample courses. **Pin `lp=1.63`, `le=1.97`** — the versions this instance serves. The
HTML-parsing fallback is off the table; build the JSON design as written.

Session cookies needed, and no others: `d2lSessionVal`, `d2lSecureSessionVal`,
`d2lSameSiteCanaryA`, `d2lSameSiteCanaryB`. The Brightspace-only scope in §2 is confirmed
sufficient.

`myenrollments` returns **22 org units**, including non-academic ones (residence modules,
orientation) and courses from prior terms. Every course name carries a term stamp — `20259`
= Fall 2025, `20261` = Winter 2026, `20265` = Summer 2026, `20269` = Fall 2026 (year + 1/5/9
for winter/summer/fall). Filtering to the highest term code present yields exactly the five
current courses, with no hand-configuration.

The calendar endpoint returned 400 on all three courses — it requires a date range, which the
probe did not send. Not a permissions problem; fixed in the collector.

**Assignments and quizzes carry their due dates as structured fields** (`DueDate`, `StartDate`,
`EndDate`), and content modules carry `ModuleDueDate`. This is a material finding — see §7.

<details><summary>Original gate criteria</summary>
Verify with your real cookies: does `/d2l/api/` answer a student session? Which of
`/le/*/content`, `/le/*/dropbox`, `/le/*/news`, `/le/*/quizzes`, `/lp/*/enrollments` are
readable at student permission level? Is an `X-Csrf-Token` header required (D2L serves one at
`/d2l/lp/auth/xsrf-tokens`), and on which verbs? Query `/d2l/api/versions/` for supported
API versions. **If the API turns out to be closed to student roles, we fall back to
authenticated HTML fetch + `selectolax` parsing — still no browser, but more brittle.**
Decide this before writing anything else.

</details>

**Phase 1 — Auth pipeline.** `login_helper.py`, encrypted cookie storage, `/api/session`
intake, session-validity probe. Deliverable: the Pi can authenticate unattended, and we have a
real measurement of how long a session survives.

**Phase 2 — Scraper core.** Course enumeration, per-kind fetchers, SQLite schema,
`content_hash` change detection, `incremental_scrape` on a timer. Deliverable: new
announcements appear in the DB within 30 minutes.

**Phase 3 — Vault builder (laptop).** Full scrape, file download, PDF/PPTX/DOCX → markdown,
frontmatter, folder layout, git commit + push. Deliverable: a real Obsidian vault you can open.

**Phase 4 — Date extraction.** Two-stage pipeline, `dedup_key` logic, `pending` queue.
Deliverable: a populated card list, sortable by course.

**Phase 5 — Web app + PWA.** Card UI with check/X, per-course filtering, service worker,
VAPID push, install-to-home-screen. Deliverable: notifications on your phone.
*Note: iOS only permits web push once the site is added to the Home Screen — one-time step.*

**Phase 6 — Google Calendar.** OAuth (scope `calendar.events`), a dedicated "uOttawa"
calendar so nothing pollutes your personal one, accept → insert, X → dismiss.

**Phase 7 — Deploy. Partly done (2026-09-08).** Storage layout, the `BRIGHTSPACE_DATA` split,
systemd units for the web app and a 30-minute timer, and `deploy/install.sh` to put them in
place: written and tested, see `docs/pi-setup.md`. Logs come from journald, so log rotation
needs nothing. Still outstanding: cloudflared, zram, and the heartbeat that pushes you a
notification if a scrape has not succeeded in 3 hours.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| ~~**D2L API closed to student role**~~ — **retired 2026-09-07.** Phase 0 confirmed full read access at student role | — |
| Session expires faster than hoped → reconnect fatigue | Measured in Phase 1. Fallback: widen cookie scope, or a one-click browser extension instead of the script |
| Pi runs out of RAM | Much less pressing on 1 GB. Still: SQLite not Postgres, no browser, no Node, prebuilt frontend, zram. Heavy work stays on the laptop |
| microSD wear from constant writes | DB and vault live on a USB stick; the SD card holds the OS only |
| Date extraction false positives | Nothing auto-adds. Confidence score + `source_excerpt` on every card |
| Acceptable-use policy | This reads your own courses with your own credentials, but automated access may still sit outside uOttawa's AUP. Scrape politely: sequential requests, delays between calls, honest User-Agent, no faster than every 30 min. Worth reading the policy yourself before deploying |
| Vault repo bloat | Binaries gitignored; markdown only |

---

## 10. Running costs

- Pi 3B+: owned (budget ~$10 for a USB stick to spare the SD card)
- Cloudflare Tunnel: free
- Private GitHub repo: free
- Claude API: ~$5 one-time + a few dollars per term on Haiku 4.5
- Google Calendar API: free

**Roughly $5 to stand up, under $5/term to run.**

---

## 11. Model recommendation for building this

- **Opus 5** for Phase 0 (probing undocumented D2L endpoints), Phase 1 (the auth flow), and
  anything that breaks in a non-obvious way. Wrong architectural calls here cost a rewrite.
- **Sonnet 5** for Phases 3–6 once the shape is fixed — vault writer, card UI, Calendar
  integration, PWA plumbing. Well-specified work at 40% of the cost.
- **Haiku 4.5** *inside* the app, for date extraction (see §7).
