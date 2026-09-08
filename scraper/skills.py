r"""
The skills: plain markdown telling the app how to handle each kind of work.

These are seeded once and then they are yours. Nothing overwrites them, ever
-- not a re-scrape, not a rebuild, not an upgrade. Edit them in Obsidian like
any other note; the next `prep.py` run uses whatever they say.

That is the whole point of them being files rather than code. A prompt buried
in a Python string is something you would have to ask someone to change. A
note in your own vault is something you change at 2am when you notice the
lab prep is missing the thing you always forget.

The study skill is the one that reads `profile/study-profile.md`, if you have
written one. That file stays out of the vault and out of git on purpose.
"""

SKILLS = {
"assignment.md": """---
skill: assignment
---

# Assignment

How to set up an assignment before I work on it.

## What to gather

- The assignment description and any rubric or marking scheme.
- The lecture material it draws on, by name, so I can open it.
- Anything the course outline says about format, length, or submission.
- Whether a template or starter file was posted.

## What to produce

- A one-paragraph plain statement of what is actually being asked.
- The marking scheme as a checklist, so nothing is left unclaimed.
- A structure with headings, and under each, a note on what belongs there
  and which lecture or reading it comes from.
- A list of what I need to find out or decide before I can start.
- An honest estimate of how long this will take and what to do first.

## Rules

- Follow `course-policies.md` for this course. It decides how far to go --
  whether to set the work up or to draft it.
- Where the requirements are ambiguous, say so rather than picking one.
- Quote the source for anything about marks or deadlines.
""",

"lab.md": """---
skill: lab
---

# Lab

How to get me ready for a lab session.

## What to gather

- The pre-lab: modules, quizzes, readings, anything due before I arrive.
- The procedure, summarised in steps.
- Equipment, software or accounts I need working beforehand.
- Safety requirements and what to wear or bring.
- What gets submitted afterwards, and when.

## What to produce

- A short "before I go" checklist, in the order I should do it.
- The procedure in my own terms, short enough to read on the walk over.
- The theory I need to actually understand, not the whole chapter.
- What data I will be collecting and what I will have to do with it.
- Anything that has to be set up on a laptop -- flag it early, these are
  what get missed.

## Rules

- Follow `course-policies.md` for this course. Where a course allows AI through
  the report, draft it -- but the results, the measured data and the graphs
  drawn from them are mine, always, in every course. Leave those as clearly
  marked gaps for me to fill.
- Never invent a measurement, a reading, a trend or an error value. A blank
  waiting for real data is useful; a plausible number is a fabrication that
  ends up in a submission.
- Pre-lab items are the priority. Turning up unprepared usually costs marks
  before the lab has even started.
- If the lab report format is known, sketch it now.
""",

"quiz.md": """---
skill: quiz
---

# Quiz or test

How to prepare me for a quiz, midterm or exam.

## What to gather

- What it covers -- lectures, chapters, topics, stated explicitly.
- The format: length, question style, open or closed book, calculator rules.
- Any practice material, past papers or example questions posted.
- What the professor said in class or in announcements about it.

## What to produce

- The topic list, ordered by how likely it is to be tested and how shaky I am.
- Practice questions in the format the real thing uses, with worked answers
  kept separate so I can try first.
- The formulas, definitions and derivations worth memorising.
- Common mistakes on this material.
- A study schedule working back from the date.

## Rules

- Practice questions must come from the course's own material and level, not
  a generic version of the topic.
- Say plainly which topics the material does not cover well enough to
  prepare from.
""",

"study.md": """---
skill: study
---

# Study material

How to turn course material into something I can actually learn from.

## What to gather

- The lecture notes and readings for the topic.
- Where this topic connects to the rest of the course.
- Anything assessed on it -- quizzes, assignments, exam weight.

## What to produce

- An explanation from first principles, building up rather than summarising.
- Worked examples, fully shown.
- Active recall questions -- things I answer, not things I read.
- A spaced schedule: when to revisit this before the assessment.

## How I learn

If `profile/study-profile.md` exists, follow it. It is written from my
psychoeducational report and it says how my attention, memory and processing
actually work -- which is more useful than a general idea of good study habits.

Where it conflicts with the conventional advice, the profile wins.

## Rules

- Explain, do not summarise. A shorter version of a lecture I did not
  understand is still a lecture I do not understand.
- Break work into pieces that can be finished in one sitting.
- Never present something as certain when the material is unclear.
""",

"course-policies.md": """---
skill: policies
---

# What each course allows

Every prep run reads this and follows the rule for that course. Professors
differ, and one of them changing their mind mid-term is not something anyone
should have to edit code for.

Write it however you like. Plain sentences are fine.

## GNG2101

AI permitted throughout the lab report EXCEPT the results and graphs actually
measured in person -- those must be mine, from the real data. Use must be
declared; there is an AI log and an attestation to submit.

## MCG2360

_Not confirmed yet. Until it says otherwise, treat this as: help me prepare,
understand and check my own work, but do not draft what I submit._

## MCG2130

_Not confirmed yet -- as above._

## MAT1341

_Not confirmed yet -- as above._

## GNG1106

_Not confirmed yet -- as above._

## The rule when a course is not listed

Prepare, explain, structure and check. Do not draft the submission. An unlisted
course is an unknown policy, and guessing generously is the wrong way to be
wrong.

## Always

- Real measured data, real results and real graphs are mine. Never invent a
  number, a reading or a trend, and never fill a gap in my data.
- Anything drafted gets logged to `AI use log.md`, so the declaration is
  already written when it is asked for.
""",

"README.md": """---
skill: readme
---

# Skills

Each file here tells the app how to handle one kind of work. They are yours to
edit -- nothing overwrites them.

Change one and the next `prep.py` run follows the new version. If lab prep
keeps missing something you care about, add a line to `lab.md` saying so.

`study.md` reads `profile/study-profile.md` if it exists. That is the private
one, written from your psychoeducational report; it lives outside the vault and
outside git, and nothing syncs it anywhere.
""",
}


def seed(folder, dry=False, report=None):
    """Write any skill that is not there yet. Never touch one that is."""
    made = 0
    for name, text in SKILLS.items():
        path = folder / name
        if path.exists():
            continue
        if not dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        if report is not None:
            report["wrote"].append(path)
        made += 1
    return made
