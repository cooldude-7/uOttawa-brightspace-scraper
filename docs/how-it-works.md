# How this app works — the plain-language version

Written to be read the night before an interview. No code, no jargon that
isn't explained.

`docs/project-summary.md` has the evidence and the numbers. This is the same
thing said out loud.

---

## What it does, in one paragraph

Most of my professors don't fill in the due-date fields in Brightspace. The
real deadlines are written into course outlines, lecture slides and
announcements — sometimes one sentence on slide 30. So staying on top of them
meant opening every document by hand. This app does that instead: it logs
into Brightspace, downloads every course document, reads them with an AI
model, and shows each date it finds as a card with a check and an X. Check
puts it on my Google Calendar. X dismisses it forever. Nothing reaches my
calendar without me approving it. It runs by itself on a Raspberry Pi in my
room, once an hour, and I review the cards on my phone.

**The one-sentence version:** *"It reads my course documents and finds the
deadlines Brightspace never lists, and I approve each one before it goes on
my calendar."*

**If they ask what it costs:** about three cents a run, because documents
that haven't changed are skipped entirely — one run read 2 documents and
skipped 67.

> Every figure in this document came from a real run on **2026-09-09**. They
> move: total spend went from $2.27 to $2.56 in a single day. Quote *cost per
> run* rather than total spend — a per-run figure is the measured result of a
> design decision, where a running total is just an input and invites the
> wrong question. Re-run and re-read before quoting any of them out loud.

---

## The pieces, and what each one does here

**Python** — the language the whole thing is written in. About 5,400 lines
across 22 files.

**Brightspace's API** — an API is how one program asks another for
information directly, instead of a human clicking a website. Brightspace's
own site is a program asking its API for your courses and displaying the
answer; my app asks the same API the same questions. That's why it never
needs to open a browser or pretend to click anything.

**The session file** — logging in with a password and two-factor is something
only a human can do, so it's done once in a real browser and the "you are
logged in" token is saved. Every run after that reuses it, and renews it
automatically when it expires. *(That file is effectively a key to my whole
university account, so it's kept off GitHub and the Pi isn't exposed to the
internet.)*

**Text extraction** — a PDF or PowerPoint isn't text a program can read;
it's a layout. Separate tools pull the words out of PDFs, PowerPoints, Word
files and spreadsheets so the AI has something to read.

**The Claude API** — how the app sends a document's text to the AI model and
gets an answer back automatically, instead of me pasting into a chat window.
The answer comes back in a fixed structure — a list of `{title, date, kind,
confidence, the sentence it came from}` — rather than as a paragraph, which
is what makes it something the app can store and act on.

**SQLite** — a database that lives in a single file. It remembers every
document already read, every deadline found, and every decision I made. This
is what makes re-running nearly free, and why it never asks me twice about
something I dismissed.

**FastAPI** — the small web server that puts the cards on a page.

**Tailscale** — a private network between my phone and the Pi. The app has no
password on it, so instead of putting it on the public internet I made it
reachable only from my own devices. That was a deliberate trade: it's the
entire security boundary.

**systemd timer** — the Pi's scheduler. It's what makes the whole thing run
without me.

**Obsidian vault** — every document, announcement and assignment is also
written out as organised notes, one folder per course, pushed to a private
GitHub repo on every run. *(The push is wired into every run, but whether the
Pi's key is actually installed is a manual setup step — check it on the Pi
before saying the vault syncs from there.)* It doubles as a searchable archive and as
per-course files I can load into Claude Projects to ask questions about my
own coursework.

---

## What happens on each run

Once an hour, 07:00 to 21:00:

1. **Log in** — reuse the saved session, renew it silently if it's expired.
2. **List my courses** — Brightspace returns 22 course shells including old
   terms and non-academic ones. Each carries a term code (`20269` is Fall
   2026), so filtering to the current term picks out my five real courses
   with nothing configured by hand.
3. **Take the easy dates first** — assignments and quizzes sometimes do have
   a real due-date field. Those are free and exact, so they're stored before
   any AI is involved, and the AI is told to ignore them so nothing is
   reported twice.
4. **Download every document** and pull the text out.
5. **Cheap filter** — documents with almost no date-like text are dropped
   before the AI sees them.
6. **Read the rest with AI** — each document goes to the model, which returns
   the deadlines it found and the sentence it read each one from.
7. **Second pass across the whole course** — one document can't know when the
   Arduino lab is, so "install the Arduino IDE" comes back with no date. A
   separate step looks at the whole course and ties each undated task to the
   dated thing it has to come before. If there's no real thing to attach it
   to, it stays undated on purpose rather than being given a guessed date.
8. **Collapse duplicates** — the same deadline turns up in six documents
   worded six ways.
9. **Rebuild the vault** and push it.
10. **Show me the cards** on my phone.

**Why hourly and not every 30 minutes,** which was the original plan: nothing
gets posted at 3am. Running through the night cost money and Brightspace
requests to discover that nothing had changed. A deadline posted at 21:30 is
picked up at 07:00, which is still long before I'd have acted on it.

---

## The failures worth telling

All four are the same shape, and it's the thing I'd most want to say in an
interview: **the failure you have to design against isn't the one that throws
an error — it's the one that looks like it worked.**

### 1. Session renewal that had never once worked

The Pi failed every hourly run from 08:00 with "a full login is needed" — but
the sign-on cookies were valid the entire time.

The renewal check asked whether a `d2lSessionVal` cookie *existed*. The
expired one was loaded straight out of the session file, so the check saw the
name it was looking for and reported success **without posting a single
form**. The feature had shipped days earlier and had never worked once, and
nothing ever said so.

**A dead cookie has the same name as a live one. The test has to ask
Brightspace, not the jar.**

Found by writing a throwaway probe that printed the login redirect chain hop
by hop — at which point it was obvious the walk was also starting at the
wrong page (`/d2l/home` returns 272 bytes of JavaScript when you're logged
out; `/d2l/login` is where a session actually starts).

### 2. The cheap model invented deadlines

Before committing to a model I tested two against three real syllabi I'd
checked by hand. On the Thermodynamics outline the cheaper model returned
**11 deadlines; the better one returned zero — and zero was correct.** All
eleven were fabricated. It had read *"8 weekly assignments will be given in
total"* and invented eight specific due dates by counting forward. Both
midterms came back as dates ending in `-00`, made up from *"the midterm dates
will be decided in class."*

**What I changed:** paid for the better model, and made every card display
the exact sentence the date came from, so a wrong one is obvious at a glance.
A made-up deadline is worse than no app at all, because it gets trusted.

### 3. Two of my real lab deadlines silently disappeared

A rule stripped the text in brackets before comparing titles, so "Lab exam"
and "Lab exam (hands-on practical skills)" would be recognised as one event.
But my lab section was also in brackets — "(Group A4)" and "(Group A3)" both
reduced to the same text, and two real deadlines vanished from a list that
had correctly found all five sections.

I only caught it by reading my own list and noticing labs were missing. No
test would have found it; the app did exactly what it was told.

**What I changed:** two entries now count as the same event based on course,
date, time and lab section — **not on wording** — because no amount of
text-cleaning reconciles "Lab 1 Report Submission" with "Lab 1 Report Due".
And nothing may silently delete a deadline: merges mark rows instead of
removing them, there's a command to undo them, and anything dropped is
printed by name.

### 4. A missed scrape that never caught up

The timer was set to re-run a scrape it had missed while the Pi was off. That
setting does nothing on the kind of timer I'd used, so a missed window was
just skipped — silently, with no error anywhere.

### 5. Four fifths of the list was somebody else's work

The filter that shows only my own lab section was written inside the function
that builds the phone cards — so the cards were right, but the vault and the
prep step never had it and listed all five sections. Most of what those showed
was other people's deadlines.

Pulling it out into one shared filter turned up the next instance before it
happened: `me.json` holds my section, and a hand-edited `"A4"` instead of
`"a4"` would have matched nothing — **silently hiding my own deadlines while
showing everyone else's.** The comparison ignores case now.

**Two smaller ones in the same family:** a three-minute scrape logged
absolutely nothing under the Pi's scheduler, because Python holds back its
output when nothing's watching. And a styling name collided with the card's
evidence button — found only by taking a screenshot of the page and looking
at it.

---

## One decision I'd want to be asked about

The app can prepare work — gather the rubric, pull the relevant lecture
material — and what it's allowed to do is set per course, in a plain-English
file I edit myself, because the professor's policy is the professor's call.

One rule sits above that file and isn't negotiable: **it never generates
measured data, results or graphs.** Not a reading, not a trend, not a
plausible-looking number in a table.

The reason is simple: a fabricated measurement doesn't stay in a draft. It
gets submitted.

The vault has the same principle in a different place — a generated note is
marked as generated, and the app refuses to overwrite any note that isn't. If
I delete that one line, the note is mine forever. A rule that can silently
destroy something I wrote isn't worth the convenience.

---

## Questions I might get

**"Did you write this yourself?"**
I built it with AI. I couldn't have written it from scratch. What I did was
decide what it needed to do and then check hard whether it actually did —
which is where every problem above came from.

**"So what did you actually contribute?"**
The judgment. Testing two models against hand-checked syllabi instead of
picking on price. Deciding a fabricated deadline is a worse failure than a
missed one. Noticing two lab deadlines had disappeared when nothing flagged
it. Deciding the app must never invent a measurement.

**"What's the hardest thing you learned?"**
That the failure you have to design against isn't the one that throws an
error — it's the one that looks like it worked. Renewal reported success
without doing anything. The deadline list just quietly got shorter. Neither
of those shows up unless someone checks the output against reality.

**"Why not just use the Brightspace calendar?"**
Because it's mostly empty. That gap is the entire reason the app exists.

---

## Honest limits (don't overclaim these)

- **No push notifications yet** — I have to open the app.
- **The web app has no login.** Tailscale is the only thing protecting it,
  which is why it's not on the public internet.
- **The heartbeat runs on the Pi**, so it can tell me a scrape failed but not
  that the Pi is dead. That needs something outside the Pi.
- **Stale dates from reused course shells.** Everything is filtered to the
  current term, which is blunt — one course still shows assignments dated
  2025 that may be this year's, shifted.
- **No automated tests.** Some commit messages describe tests; no test file
  was ever committed on either branch. Say "I verified it by hand against real
  data" — true, and still worth credit. Never say "wrote tests".
- **Built for one student.** Anyone else would need their own uOttawa login,
  and it depends on a system the university controls and hasn't agreed to.
