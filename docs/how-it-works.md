# How this app works — the plain-language version

Written to be read the night before an interview. No code, no jargon that
isn't explained. Everything here is true of what actually runs today.

---

## What it does, in one paragraph

Most of my professors don't fill in the due-date fields in Brightspace. The
real deadlines are written into course outlines, lecture slides and
announcements — sometimes one sentence on slide 30. So staying on top of
them meant opening every document by hand. This app does that instead: it
logs into Brightspace, downloads every course document, reads them with an
AI model, pulls out every date it finds, and shows each one to me as a card
with a check and an X. Check puts it on my Google Calendar. X dismisses it
forever. Nothing reaches my calendar without me approving it.

**The one-sentence version:** *"It reads my course documents and finds the
deadlines that Brightspace never lists, and I approve each one before it
goes on my calendar."*

---

## The pieces, and what each one actually does here

**Python** — the programming language the whole thing is written in. Nothing
more interesting than that; it's the language, the way a drawing might be in
metric.

**Brightspace's API** — an API is a way for one program to ask another
program for information directly, instead of a human clicking through a
website. Brightspace's own site is really just a program asking its API for
your courses and displaying the answer. My app asks the same API the same
questions. That's why it doesn't need to open a browser or pretend to click
anything — it asks directly and gets back plain data.

**The session file** — logging in with a password and two-factor is
something only a human can do. So it's done once in a real browser, and the
"you are logged in" token that Brightspace hands back is saved to a file.
Every run after that reuses it. When it expires, the app quietly signs
itself back in through the university's login system without asking me
again. *(This file is sensitive — it's effectively a key to my whole
university account, so it's kept out of the shared code.)*

**Text extraction** — a PDF or PowerPoint isn't text a program can read
directly; it's a layout. Separate tools pull the readable words out of PDFs,
PowerPoints, Word files and spreadsheets so the AI has something to read.

**The Claude API** — this is how the app sends a document's text to the AI
model and gets an answer back automatically, instead of me pasting things
into a chat window. The important part is that the answer comes back in a
fixed structure — a list of `{title, date, kind, confidence, the sentence it
came from}` — rather than as a paragraph. That makes it something the app
can store and act on.

**SQLite** — a database that lives in a single file on the laptop. It
remembers every document already read, every deadline found, and every
decision I made. This is what makes the app cheap and quiet to re-run: if a
document hasn't changed since last time, it's skipped entirely — not
re-downloaded, not re-read, no AI cost. And because it remembers what I
dismissed, it never asks me about the same thing twice.

**FastAPI** — the small web server that puts the cards on a page, so I can
review deadlines on my phone instead of in a terminal.

**Google Calendar API** — how checking a card creates a real calendar event.
The app made its own separate "uOttawa" calendar, so my deadlines can be
hidden with one checkbox and never mix into my personal one.

---

## What happens when I run it

One command, `python update.py`, and it goes in this order:

1. **Log in** — reuse the saved session; renew it silently if it's expired.
2. **List my courses** — Brightspace returns 22 course shells, including old
   terms and non-academic ones. Each carries a term code (`20259` is Fall
   2025, `20269` is Fall 2026), so filtering to the current term picks out
   exactly my five real courses with nothing configured by hand.
3. **Take the easy dates first** — assignments and quizzes sometimes *do*
   have a real due-date field. Those are free and exact, so they're stored
   before any AI is involved, and the AI is told to ignore them so it doesn't
   report the same thing twice.
4. **Download every document** and pull the text out of it.
5. **Cheap filter** — any document with almost no date-like text is dropped
   before the AI ever sees it. Roughly half the documents never cost
   anything.
6. **Read the rest with AI** — each surviving document goes to the model,
   which returns the deadlines it found and, for each one, the sentence it
   read it from.
7. **Store and de-duplicate** — the same deadline turns up in six documents
   worded six different ways. They get collapsed into one card.
8. **Show me the list** — check or X on each.

A routine run costs close to nothing, because step 5 and the "already read
this" memory mean almost nothing gets sent to the AI twice. Total spend
so far is about $1.40.

---

## The three things that went wrong, and what I changed

These are the useful part. Each one is a decision, not just a fix.

### 1. The cheap model invented deadlines

Before committing to a model I tested two against three real syllabi I'd
already checked by hand.

On the Thermodynamics outline the cheaper model returned **11 deadlines. The
more expensive one returned zero — and zero was correct.** All eleven were
fabricated. It had read the sentence *"8 weekly assignments will be given in
total"*, and invented eight specific due dates by counting forward from it.
Both midterms came back as dates ending in `-00`, made up from *"the midterm
dates will be decided in class."*

**What I changed:** paid for the better model, and made every card display
the exact sentence the date came from, so a wrong one is obvious in a
glance instead of requiring me to go open Brightspace.

**Why it matters:** a made-up deadline is worse than no app at all. Someone
who trusts a hallucinated midterm date is worse off than someone who never
had the tool. The point of the app is to be trusted, so recall matters when
*finding* dates and never when *inventing* them.

### 2. Two of my real lab deadlines silently disappeared

The same event gets described differently in different documents — "Lab exam",
"Lab exam (hands-on practical skills)". So there's a rule that strips the
part in brackets before comparing, to recognise those as one event.

But my lab section was *also* written in brackets. "Soldering lab submission
(Group A4)" and "(Group A3)" both reduced to the same text, so the app
merged them and two real deadlines vanished from a list that had correctly
found all five sections.

I only caught it because I read my own list and noticed labs were missing.
No automatic test would have found it — the app was doing exactly what it
was told.

**What I changed:** the section is pulled out and kept *before* the brackets
are stripped, so A3 and A4 stay separate. And more importantly, the app now
treats two entries as the same event based on course, date, time and lab
section — **not on how they're worded** — because no amount of text-cleaning
reconciles "Lab 1 Report Submission" with "Lab 1 Report Due".

**The rule that came out of it:** nothing is allowed to silently delete a
real deadline. Merges mark rows instead of deleting them, there's a command
to undo them, and anything removed gets printed by name so I can see it.
The bug was fixable; a system where that kind of bug is *invisible* is the
actual problem.

### 3. Tapping two cards quickly broke the app

Tapping check on a card sometimes failed with "database is locked."

The cause: when I checked a card, the app locked the database, then called
Google to create the calendar event, and only released the lock once Google
answered. Anything else I tapped during that second or two hit a locked
database and failed.

**What I changed:** the decision is saved and the lock released *first*, and
the calendar event id is written back separately afterwards. The lesson
generalises — don't hold a lock on something while you wait on a network.

---

## Questions I might get, and honest answers

**"Did you write this yourself?"**
I built it with AI. I couldn't have written it from scratch. What I did was
decide what it needed to do, and then check hard whether it actually did —
which is where all three of those problems came from.

**"So what did you actually contribute?"**
The judgment. Testing two models against hand-checked syllabi instead of
picking on price. Deciding a fabricated deadline is a worse failure than a
missed one, and requiring every date to show its source sentence. Noticing
two lab deadlines had disappeared when nothing flagged it.

**"What's the hardest thing you learned?"**
That the failure you have to design against isn't the one that throws an
error — it's the one that looks like it worked. The lab deadlines didn't
crash anything. The list just quietly got shorter.

**"Why not just use the Brightspace calendar?"**
Because it's mostly empty. That gap is the entire reason the app exists.

---

## What it does *not* do (don't claim these)

- It runs on my laptop, on demand. The always-on Raspberry Pi version is
  designed and documented but **not built or deployed**.
- No phone push notifications yet.
- No Obsidian vault yet.
- There are no automated tests committed.
- One user, one term, a few thousand rows. It's a personal tool, not a
  product.
