r"""
What is in one folder of a course, and what of it reached the vault.

    python section.py GNG2101                 every folder, with counts
    python section.py GNG2101 "lecture 5"     just that one, item by item

Reads `collected.json` off the disk. No requests, no session needed, no
cost -- so it answers "did it get everything from that lecture?" even when
the scraper cannot log in.

WHY IT DID NOT EXIST BEFORE

Because it could not. `walk_content()` returned topics as one flat list and
threw away which module each sat under, so nothing in the codebase knew
that a document belonged to "Lecture 5" -- not the vault, not the cards,
not a probe. The question had no answer rather than a wrong one, which is
the quieter version of the same failure. `collect.add_topic()` now keeps
the parent, at no extra request, because the tree is already in the
response being walked.

A folder collected before that change has no parent recorded, so its items
show up under "(folder not recorded)" until the next scrape. That is
stated rather than hidden -- it is not the same as an empty folder.
"""

import json
import re
import sys

import paths
import store


def norm(text):
    """A name with punctuation, spacing and file extensions taken off.

    A topic titled "Instructions_Project_A.pdf" lands on disk as
    `Instructions_Project_A.pdf.pdf` and is extracted to
    `Instructions_Project_A.pdf.txt`, while one titled without an extension
    does not double it up. Comparing the bare words sidesteps having to
    reproduce that here and stay in step with it.
    """
    text = (text or "").lower()
    for _ in range(3):
        text = re.sub(r"\.(pdf|pptx?|docx?|xlsx?|txt|zip|html?)$", "", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def on_disk(code):
    """Every extracted document for a course, by normalised name."""
    folder = paths.EXTRACTED / code
    if not folder.exists():
        return {}
    return {norm(f.stem): f for f in folder.glob("*.txt")}


def courses():
    if not paths.COLLECTED.exists():
        sys.exit(f"\nNo {paths.COLLECTED} yet -- run update.py first.\n")
    return json.loads(paths.COLLECTED.read_text(encoding="utf-8"))


def folders(course):
    """-> {folder title: [topic, ...]}, in the order Brightspace lists them."""
    out = {}
    for t in course.get("topics", []):
        out.setdefault(t.get("module") or "(folder not recorded)", []).append(t)
    return out


def describe(topic, have):
    """Did this one reach the vault, and if not, is that expected?"""
    kind = (topic.get("type") or "").lower()
    key = norm(topic.get("title"))
    if key in have:
        return "ok", f"{have[key].stat().st_size:,} bytes of text"
    if kind and kind not in ("file", "contentservice"):
        return "--", f"nothing to download ({kind})"
    if kind == "contentservice":
        return "--", "SCORM package -- there is no file, only its due date"
    if not topic.get("url"):
        return "--", "no link on it"
    return "MISSING", "has a file, and no text for it here"


def main(argv):
    code = (argv[0] if argv else "").upper()
    wanted = " ".join(argv[1:]).lower()
    if not code:
        sys.exit(__doc__)

    for course in courses():
        parts = store.course_parts(course.get("name", ""))
        if parts["code"].upper() != code:
            continue
        have = on_disk(parts["code"])
        groups = folders(course)

        if not wanted:
            print(f"\n{parts['code']} -- {len(groups)} folder(s)\n")
            for title, items in groups.items():
                states = [describe(t, have)[0] for t in items]
                missing = states.count("MISSING")
                print(f"  {len(items):>3} item(s)  "
                      f"{'MISSING ' + str(missing) if missing else 'all here'}"
                      f"   {title[:56]}")
            print(f"\n  Name one to see inside it:  python section.py {code} "
                  f"\"lecture 5\"\n")
            return 0

        hits = {t: i for t, i in groups.items() if wanted in t.lower()}
        if not hits:
            print(f"\n  No folder in {code} matching {wanted!r}. They are:\n")
            for title in groups:
                print(f"    {title[:70]}")
            print()
            return 1

        for title, items in hits.items():
            print(f"\n-- {title} " + "-" * max(4, 58 - len(title)))
            for t in items:
                state, why = describe(t, have)
                mark = {"ok": "  ok  ", "--": "  --  ", "MISSING": " MISS "}[state]
                print(f"{mark}{(t.get('title') or '?')[:46]:<48} {why}")
            missing = sum(1 for t in items if describe(t, have)[0] == "MISSING")
            print(f"\n  {len(items)} item(s), {missing} with no text stored.")
            if missing:
                print("  Run  python update.py  -- a file that will not "
                      "download is printed by name.")
        print()
        return 0

    sys.exit(f"\nNo course called {code} in {paths.COLLECTED}.\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
