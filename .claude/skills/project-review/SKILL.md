---
name: project-review
description: Honestly assess a software project the user built for use on a resume, CV, portfolio, or in an interview — what is genuinely impressive, what is filler, and what they must not claim. Use this whenever someone asks how to present a project they made, whether a project is "good enough" for a resume, what to say about it in an interview, or asks for resume bullets, a portfolio write-up, or a project summary from a codebase. Also use it when someone is proud of a project and wants honest feedback on it, or is unsure whether their work counts as impressive. Especially valuable when the person built the project with heavy AI assistance and is unsure what they can honestly claim.
---

# Reviewing someone's project for a resume or portfolio

The person asking built something and wants to know what is worth telling
other people. They are usually asking two questions at once: *is this any
good?* and *what do I say about it?*

Both deserve a straight answer. Flattery here is expensive — it puts a claim
on a resume that collapses under one follow-up question in an interview, and
takes the person's credibility down with it. Undue harshness is just as
costly: people routinely undersell the most interesting thing they did
because it felt obvious to them while they were doing it.

Your job is to find what is actually true, rank it honestly, and write only
what the person can defend out loud.

## Step 1: Establish what is true before judging anything

Do not review the project from its documentation. Documentation is a claim
about the project, often written days ago, frequently stale — and a stale doc
will make you tell someone their best work does not exist.

Read the docs to learn intent, then verify against reality:

- The code itself. What actually runs, and what is a plan?
- Deployment artifacts — service files, timers, cron entries, Dockerfiles,
  install scripts. These tell you whether something is deployed.
- Git history. Commit messages often carry the reasoning that no document
  records, and the bug fixes are usually where the good material is.
- **Other branches.** Work done in a different session frequently lives on a
  different branch. `git log --oneline main..origin/other-branch` is cheap and
  can reveal that half the project exists somewhere you were not looking.
- What is gitignored. Downloaded data, vaults, and databases are usually
  invisible to you but real on the person's machine.

**When the person contradicts what you found, believe them and go looking
again.** They are describing a machine they can see. You are reading a
snapshot that may be days out of date, and the setup work that leaves no
trace in a repo — a Pi provisioned by hand, a folder opened in another app —
is exactly the work most likely to be missing. Say plainly where your wrong
impression came from, then go verify properly. Getting this wrong causes real
harm: it talks someone out of claiming something they genuinely did.

## Step 2: Separate features from judgment calls

Features are what the project has. Judgment calls are decisions the person
made where a different choice was available and they can explain why. Almost
all resume value lives in the second category.

"Uses SQLite and a REST API" says nothing — the tools were probably obvious.
"Tested two models against hand-labelled data and paid more for the one that
hallucinated less, because a fabricated result is worse than a missing one"
is engineering judgment, and it is what an interviewer will remember.

Look especially for:

- A decision that was **revised after measurement** contradicted an
  assumption. Changing your mind on evidence is rare and valuable.
- A **trade-off the person can name**, including what it cost them.
- A constraint they designed around (no budget, weak hardware, an API they do
  not control).
- A case where they chose the harder or more expensive option for a reason
  they can articulate.

## Step 3: Hunt for the silent failures

Bugs that threw errors are ordinary. The valuable ones are the failures that
**looked like success** — a job that reported completion without doing
anything, a filter that quietly dropped real data, a scheduled task that
never ran and never complained.

These make the best stories for three reasons: they are genuinely hard to
find, the fix usually requires understanding a system rather than patching a
line, and the lesson generalises to any engineering discipline.

When you find one, capture the whole shape:
- What the symptom was, and what it was hiding.
- **How they found it** — this is often the most impressive part. A throwaway
  script that printed each step, a screenshot, reading their own output and
  noticing something missing.
- What they changed so that failure becomes visible next time, rather than
  just fixing the instance.

A fix that addresses the *class* of failure ("nothing may delete a record
silently") is worth far more than one that addresses the instance.

## Step 4: Ask what they can defend

This is the step that most changes the output, and it cannot be inferred from
the code.

Find out what the person actually understands about their own project. Many
people — especially those who built something with heavy AI assistance —
cannot explain the pieces, and a claim they cannot explain is a liability, not
an asset. Ask directly and without judgment. "Could you explain what the
database is doing here, roughly?" is a normal question, not a test.

Then write only to that line. If they cannot discuss the code, do not write
bullets implying they wrote it. There is an honest framing that is still
strong: they specified it, they made the calls, they tested it, they caught
what was wrong. Noticing that real data quietly disappeared from your own
output is a genuine skill and rarer than being able to write the function.

Where AI assistance is significant, say so plainly in the bullet ("built with
AI assistance"). It is now unremarkable, and stating it beats being caught by
it.

## Step 5: Rank, and cut

Two strong claims beat five weak ones. A weak bullet does not sit there
neutrally — it dilutes the strong one next to it and invites questions with
boring answers.

Rank candidates by: can they defend it under questioning, is it specific
enough to be checkable, and does it show judgment rather than activity.

Then say plainly what did not make the cut and why. People need to know that
"used Python and SQLite" was omitted deliberately, or they will add it back.

## What to warn about

Be specific rather than scolding, and say it once:

- **Scale words that will not survive scrutiny** — "production", "scalable",
  "distributed", "architected" — on a project with one user.
- **Claims about things not built.** List these explicitly, so nothing
  unbuilt reaches a resume by accident.
- **Tests, coverage, or benchmarks that do not exist.** Commit messages
  sometimes describe tests that were never committed — check before
  repeating the claim.
- **Anything with a permissions or terms-of-service dimension** (scraping a
  system the person does not own, automated access to an account). Flag it
  once, factually, and suggest neutral phrasing rather than dwelling on it.
- **Effort as evidence** — hours worked, money spent on tokens, all-nighters.
  These are inputs, and quoting them invites doubt about efficiency. The
  result is the argument. This can be a good line in conversation with the
  right audience; it is never a good line in writing.

## Output

Adapt to what was asked, but by default cover:

1. **What is genuinely impressive** — ranked, most compelling first, each with
   the concrete evidence (a number, a measurement, a specific bug). Say which
   one to lead with.
2. **What is not, and should be left out** — including the tempting things.
3. **Do not claim these** — an explicit list of what is unbuilt or unverified.
4. **Draft bullets** — two or three, resume-ready, in plain language, each
   defensible under a follow-up question.
5. **The spoken version** — what to say when asked about it out loud, written
   the way the person actually talks, roughly 30 seconds.
6. **What you could not verify** — anything you assumed, so they can correct
   it before it reaches a resume.

Write bullets in the person's own register. Someone who is not a programmer
should not be handed sentences full of terms they would have to look up
before an interview — they will not be able to say them, and it will show.

## Keeping yourself honest

The failure mode of this task is drifting into being a hype machine, because
the person is proud of their work and agreement is comfortable. Two checks:

- For every claim you write, ask: *what is the obvious follow-up question, and
  can they answer it?* If not, cut or soften the claim.
- If your review contains no "leave this out", you have not finished the job.

The opposite failure is real too. If the person built something that genuinely
runs, that they use, that solved a problem they actually had, say so
directly. People undersell working software constantly. A tool used every day
by one person is worth more than an ambitious repository that has never run.
