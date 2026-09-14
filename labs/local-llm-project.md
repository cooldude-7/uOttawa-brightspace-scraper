# Brief: a local-model version of the lab screenshot tool

Hand this whole file to a fresh Claude Code chat. It is everything that
session needs to start: who it is building for, what already exists and
why, what the model is actually for, and where the line is.

---

## Who you are building for

Luca — a **mechanical engineering student at uOttawa, not a programmer**.
Explain things in plain language and define any jargon in one line. He
works on **Windows in PowerShell** and pastes terminal output back, so
give commands as complete lines he can copy, one block at a time, and say
what he should expect to see.

He will tell you when something is wrong, and he has been right every
time. Take it seriously rather than explaining why it should work.

## The hardware

- **Lenovo Legion Pro 5i**, RTX 5060 laptop GPU (**8 GB VRAM**), 32 GB RAM
- Windows, PowerShell
- Git and Python may not be installed yet — check first:
  `git --version`, `python --version`. If `python` opens the Microsoft
  Store, that is the App Execution Aliases setting, not a broken install.
- Installs: `winget install --id Git.Git -e` and
  `winget install --id Python.Python.3.13 -e`, then **a new PowerShell
  window** before checking again.

## The job

**GNG1106 Engineering Computation.** The labs are written in **C**. Each
lab has several *deliverables*, and for each one he screenshots the code
and screenshots its output. He wants to drop the screenshots into a
folder and get back the PDF to hand in.

Fixed requirements, from him:

- One PDF per lab, named **`LucaMaric_p<lab>_1.pdf`** — the number after
  the `p` is the lab number, the trailing `1` never changes. So Lab 3 is
  `LucaMaric_p3_1.pdf`.
- **Images in order, one per page. Nothing added** — no headings, no
  labels, no cover page, no page numbers. He was asked and was specific.
- Folder layout: a `GNG1106` folder with `Lab 1`, `Lab 2`, … inside it.

Things that make it harder than it first sounds:

- **The deliverable number is usually not visible in the screenshot.** He
  knows which is which from the lab instructions, and those live inside a
  Brightspace *quiz*, so they are awkward to fetch — assume he may have to
  paste them in by hand.
- **Screenshots are usually separate** (code, then output) **but sometimes
  one image holds both.** The count per deliverable is not always two.

## What already exists

`labs/labpdf.py` in this repo. It works, it is tested, and it is the
skeleton to build on — the folder handling, the ordering, the
confirmation step and the PDF building are all done.

Its approach: **order the screenshots by when they were taken.** He
shoots the code, runs it, shoots the output, moves to the next
deliverable — so the order he took them in already *is* deliverable
order, and Windows records it in the filename
(`Screenshot 2026-09-14 140211.png`). Preference is the name's timestamp,
then anything inside the image, then the file's own date — the last of
which is wrong if a file was copied, so anything relying on it is flagged
in the listing.

That is free, instant, and exactly right rather than probably right. It
always prints the order and waits for a yes before writing anything.

**Do not throw this away.** It is the check on whatever the model says.

## What the model is actually for

He wants to build this with a **local vision model** on his own machine —
partly because it is his coursework on his own hardware, partly because he
wants to learn it. That is a reasonable thing to want and a good fit here:
low volume, private data, and the model sorts *his* images rather than
generating anything.

**Make the model's job easy.** The tempting design — "read the C code,
understand it, match it to deliverable 3" — is the hard version. Dense
code text in a screenshot is difficult OCR for a small model and it is
exactly where silent mistakes come from.

The easy version gets most of the value:

- **"Editor or terminal?"** Trivially visual. A small model is reliable
  at this.
- **"What filename is in the title bar?"** Short text, moderate OCR, high
  value — `lab3_d2.c` hands you the deliverable number directly.
- **"Does this one image contain both code and output?"** Also visual.

Those three, combined with the timestamps that already work, give a
system where the model and the clock **check each other**. When they
agree, proceed. When they disagree, say so and ask him. That is better
than either alone, and it is the design to aim for.

### Running it

[Ollama](https://ollama.com) installs on Windows and exposes a local HTTP
API that Python can call — simplest path. An 8 GB card runs a 7–8B vision
model at 4-bit comfortably. **Check Ollama's current model library rather
than trusting any specific name from training data**; that space moves
fast. Have him try one, measure it on a real lab, and change it if it is
weak.

Measure before believing: time per image, and how often the model's
grouping disagrees with the timestamp order on real screenshots.

## The line that does not move

**This is submitted for marks.** A mis-ordered PDF, or a screenshot filed
under the wrong deliverable, costs him grades — and he would not notice
until it came back.

So:

- **Never write the PDF without showing what it is about to do and
  getting a yes.** The existing script does this; keep it.
- **When the model and the timestamps disagree, stop and ask.** Do not
  pick a winner silently.
- **Never reorder on the model's word alone** where the timestamps are
  unambiguous.
- He asked for reliability explicitly: *"i want whatever is added to be
  100% confident. this app needs to be reliable."* You cannot promise a
  guess is right — what you can promise is that nothing new breaks what
  already works, and that a wrong guess is always visible before it
  matters.

## Suggested shape

A **separate repository** from the Brightspace scraper. They share no
code, run on different machines, and keeping them apart means a broken
experiment here cannot touch the thing he relies on for deadlines.

Start by copying `labs/labpdf.py` across as the base, then add:

1. A way to run **both** approaches on the same folder and print where
   they differ. This is the whole experiment — without it there is no
   evidence the model is earning its place.
2. The Ollama call, asking the three easy questions per image.
3. Grouping into deliverables, with the timestamps as the tiebreak.
4. A place to paste the lab's deliverable list when he has it, since that
   is the only thing that can name a deliverable with certainty.

## Ask him these before you start

- Has he actually done a lab yet, and can he share a real folder of
  screenshots? Everything here is guesswork until it runs on real ones.
- Does his C file naming carry the deliverable number (`lab3_d2.c`)? If
  so, that is the strongest signal available and it changes the design.
- Does he take screenshots in strict deliverable order, or does he
  sometimes go back and redo one? Redoing breaks pure timestamp ordering
  and is the main case where a model helps.
- Full-screen screenshots or cropped? Cropped-to-the-window is much
  easier for a small model to read.

## Working style that has worked on his other project

Verify against reality rather than reasoning about it. Render the thing
and look at it; print what the tool actually returned; test the upgrade
path rather than the fresh install. Several real bugs surfaced only that
way, and a few were found by him reading his own output rather than by
any test.

Write down decisions and the reasons behind them as you go, in a
`CLAUDE.md` in the new repo, so the next session starts informed.
