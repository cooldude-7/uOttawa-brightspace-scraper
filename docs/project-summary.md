# Project summary — facts, decisions, and what went wrong

Written to be read at the start of a new chat, so a conversation about this
project (a portfolio piece, a product, anything) starts from what is true
rather than from a reconstruction. `CLAUDE.md` is the working guide and
`PLAN.md` holds the architecture; this is the evidence.

Every number here came from a real run and is dated. If you are reading this
much later, re-run and check rather than quoting it.

---

## What it does

A uOttawa mechanical engineering student's deadlines are scattered across
Brightspace: some in the assignment fields, some written in prose inside a
syllabus PDF, some in a slide deck, some only in an announcement. Missing one
is expensive and entirely preventable.

This scrapes Brightspace, reads course documents with an LLM to find dates
stated in text, and surfaces each one as an accept/dismiss card on the
student's phone. Accepted ones become Google Calendar events. It also builds
an Obsidian vault — a note per course, every document and announcement — and
can run a per-work-type "skill" against a real assignment under that course's
own AI policy.

It runs unattended on a Raspberry Pi.

## What is actually running (as of 2026-09-09)

- Raspberry Pi 3B+, wired to a travel router, 256 GB USB stick as ext4 at
  `/mnt/data`. A `.brightspace-data` marker file is required before anything
  writes there, because `/mnt/data` existing does not mean the stick mounted.
- Hourly scrape, 07:00–21:00, under a systemd timer.
- Reachable from the phone over Tailscale, on the home screen as a PWA. Not
  exposed publicly — `web.py` has no authentication, and that decision drove
  the choice of Tailscale over a tunnel.
- The vault pushes to a private GitHub repo (`obsidian-brain`) on every run.
- Google Calendar reconciles from the Pi.

Latest full run: **73 seconds, $0.0258**, 5 courses, 41 files, ~82,000 words
of extracted text. 67 of 69 documents were recognised as unchanged and skipped
— that is `text_hash` doing its job, and it is why a routine run is nearly
free. **$2.56 spent in total, all time.** 33 deadlines waiting, 29 undated
tasks, 70 accepted, 9 dismissed.

Roughly 5,400 lines of Python across 22 modules, plus one HTML page with no
build step (it has to run on a Pi).

## Decisions worth explaining

**Sonnet, not Haiku.** Haiku 4.5 invented an entire semester of dates from the
sentence "8 weekly assignments will be given". Sonnet 5 correctly returned
nothing. Every extracted date must carry the sentence it came from. The rule
is recall over precision, but never invention — a fabricated deadline is worse
than a missed one, because it is trusted.

**Per-course chat lives in Claude Projects, not in this app.** An Ask tab was
built and then reverted. Projects works on both of the user's devices, takes a
photo of a whiteboard, and costs nothing beyond a subscription already paid
for, where every API question costs money. The price of that choice is a
bundle file per course that has to be regenerated and re-uploaded.

**HTML parsing exactly once, and only to fill a gap.** Everything comes from
the API except one case: an item restricted to particular students keeps its
dates in a special-access override, `DueDate` comes back `null`, and the
`specialaccess` endpoint answers 403 to a student. The date exists only on the
rendered page. `pagedates.py` reads it there and will only ever fill a date
that is missing, never override one the API gave.

**Two passes, because one document cannot know.** `find_dates.py` sees one
document at a time, so "install the Arduino IDE" comes back undated — that
document has no idea when the Arduino lab is. `link_tasks.py` sees the whole
course and ties each task to the dated thing it must precede. It may only pick
from anchors it was given; an invented anchor is discarded and printed, and a
task with no real anchor stays undated on purpose.

**What the app may write is the professor's call.** `Skills/course-policies.md`
holds it per course in plain English the user edits; an unlisted course
defaults to prepare-not-draft. One rule sits above the policy file and is not
negotiable: measured data, results and graphs are never generated — not a
reading, not a trend, not a plausible number in a table. A fabricated
measurement does not stay in a draft, it gets submitted.

**The vault must never eat the user's writing.** A generated note carries
`generated: true` and `vault.py` refuses to overwrite any file without it, so
deleting that one line claims a note permanently.

## Bugs found by looking, not by reasoning

These are the ones worth telling, because each was found by printing what
actually happened rather than by thinking about what should have.

**Two real misses, found by the user reading their own list.** Not by any
test: a quiz whose date lived only in a special-access override, and setup
tasks that were never extractable at all because the prompt only asked for
dates. Both changed the design.

**Silent session renewal (2026-09-09).** The Pi failed every hourly run from
08:00 with "a full login is needed". The sign-on cookies were valid the whole
time. Two bugs, both of which presented in the log as an expired login:

1. The renewal check asked whether a `d2lSessionVal` cookie existed. The
   expired one was loaded straight from the session file, so it saw the name it
   wanted and returned success without posting a single form. A dead cookie has
   the same name as a live one; the test has to ask Brightspace, not the jar.
2. The walk started at `/d2l/home`, which without a session returns 272 bytes
   of JavaScript reading `window.location.hash` — no form, no `Location`
   header, nothing to follow. `/d2l/login` is where a session is *started*.

Found with a throwaway probe that printed the chain hop by hop. Renewal had
never once succeeded before this, so the feature had been "built" and untested
for days.

**A title-cleaning rule silently deleted two lab deadlines.** Since then,
merges mark rows rather than deleting them, `--restore` undoes them, and
anything dropped is printed by name.

**Section-specific deadlines collapsing into one.** An assignment lists five
different dates for lab sections A1–A5; without the section marker in an
event's identity, A2 and A5 became the same row.

**`anthropic==0.69.0` lacks `output_config`**, so all 63 documents failed on
the Pi. Four versions were tested empirically before pinning `>=0.110`.

**A three-minute scrape logged nothing** under systemd — Python buffers stdout
to a pipe. `PYTHONUNBUFFERED=1`.

**`Persistent=true` is inert on a monotonic timer**, so a scrape missed while
the Pi was off never ran. Switched to `OnCalendar`.

**A CSS class named `.note` collided with the card's evidence button.** Found
only by screenshotting the page and looking at it.

**prep.py called Thursday 17 September "Wed 17 Sep".** Fixed by computing
weekdays in Python and forbidding the model from deriving its own.

## Known limits

- `web.py` has no authentication. Tailscale is the entire security boundary.
- The heartbeat runs *on* the Pi, so it reports a failing scrape but cannot
  report a dead Pi. That needs an off-Pi dead-man's switch.
- No push notifications yet — you have to open the app.
- Course shells are reused between terms, so stale dates appear in Brightspace's
  own due-date fields. Everything is filtered to the term window, which is
  blunt: MCG2130 currently shows eight assignments dated 2025 that may be this
  year's, shifted.
- It is built for one student. Every user would need their own uOttawa login,
  and the whole thing depends on a system the university controls and has not
  agreed to.

## Working style that produced the above

Verify against reality rather than reasoning about it: render the page and look
at it, print what the endpoint actually returned, test the upgrade path rather
than the fresh install. Several of the bugs above surfaced only that way. The
user reads their own list and spots what a test cannot; when they say something
is wrong, they have been right.
