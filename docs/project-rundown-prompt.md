# Reusable prompt: get a full rundown of a project you built

Paste the block below into a fresh session opened on any project you've
built. It produces the same kind of document as `docs/how-it-works.md`.

Written from what this project's rundown had to get right the hard way —
chiefly that the notes in a repo can be days behind the running system, and
that a rundown built on stale notes talks you out of your own best work.

---

I built this project and I'm using it. I'm not a programmer — I built it with
AI and couldn't write it from scratch. I want a complete, in-depth rundown of
what it is, how it works, and how it's actually used, written so I can read it
the night before an interview and explain the project confidently in plain
language.

**First, establish what is actually true. Do not write anything yet.**

- Read the code, not just the documentation. Any notes in this repo may be
  days stale and may describe things as unbuilt that are running.
- Check deployment artifacts — systemd units, timers, cron entries,
  Dockerfiles, install scripts, config — to see what actually runs versus
  what was only planned.
- Read `git log`. Commit messages usually carry reasoning no document
  records, and the bug fixes are where the best material is.
- Check other branches: `git branch -a`, then
  `git log --oneline HEAD..origin/<other-branch>`. Work done in a different
  session often lives on a different branch, and the one I'm on may be far
  behind it.
- Note what's gitignored — databases, downloaded files, data directories.
  Those exist on my machine even though you can't see them.
- If I tell you something contradicts what you found, believe me and go look
  again. I can see the running system; you are reading a snapshot.

**Then ask me anything you cannot determine from the repo**, before writing:
whether it's deployed and where, whether a manual setup step was actually
carried out, any number you can't verify, and roughly what I can and cannot
explain about my own project. Don't guess these — a rundown written to the
wrong level is useless to me.

**Then write `docs/how-it-works.md` with:**

1. **What it does**, in one paragraph — plus a single sentence I can say out
   loud when someone asks.
2. **The pieces.** Each technology or component in one short paragraph: what
   it is in plain terms, then specifically what it does *in this project*.
   No jargon without a one-line definition.
3. **What happens on each run or each use**, as numbered steps.
4. **The failures worth telling.** Prioritise bugs that *reported success* or
   failed silently over ones that threw errors — those are the good stories.
   For each: the symptom, what it was hiding, **how it was found**, and what
   changed so that whole class of failure becomes visible next time. A fix
   that addresses the class beats one that addresses the instance.
5. **Decisions worth being asked about** — anywhere I chose the harder,
   slower or more expensive option for a reason I can state, or revised a
   decision after measuring something that contradicted an assumption.
6. **Likely questions with honest answers**, including "did you write this
   yourself?" and "what did you actually contribute?"
7. **Honest limits** — what is *not* built, *not* tested, *not* deployed, and
   anything with a permissions or terms-of-service dimension. Be explicit, so
   I never claim it by accident.

**Rules:**

- Verify every number and date against the code or a real run, and say where
  each came from and when. If you can't verify one, say so rather than
  repeating it.
- Never say tests exist without finding the test files. Commit messages
  sometimes describe tests that were never committed.
- Write for someone who is not a programmer. If I'd have to look a word up
  before an interview, define it in one line or cut it.
- Don't inflate. No "production", "scalable", "distributed" or "architected"
  on a project with one user.
- End with a list of everything you assumed or could not verify, so I can
  correct it before it reaches a resume.
