r"""
The Obsidian vault: one brain per course, rebuilt from what has been scraped.

    python vault.py             build or refresh it
    python vault.py --push      build, then commit and push it
    python vault.py --bundle    also write one file per course, for uploading
                                into a Claude Project
    python vault.py --dry-run   say what it would write, write nothing

Everything here is deterministic -- database rows and already-extracted text
turned into markdown. No API calls, nothing to pay for, safe to run as often
as you like.

THE RULE THAT MATTERS

A generated note carries `generated: true` in its frontmatter, and this
refuses to overwrite any file that does not have it. Delete that line from a
note and the note becomes yours: it will be left alone from then on, forever.
Your own writing lives in Notes/ and is never touched at all.

This is the same principle as everything else here -- a rule that can silently
destroy something the user made is not worth the convenience. Losing a week of
lecture notes to a re-scrape would be far worse than a stale file.

LAYOUT

    Courses/MCG2130 - Thermodynamics I/
        _MCG2130.md            what is due, what is new, what to read
        Content/               each lecture and handout as a note
        Announcements/
        Work/                  one note per assignment, lab and quiz
        Notes/                 yours
    Skills/                    how to handle each kind of work -- you edit these
    Dashboards/Upcoming.md     everything due, across all five courses
"""

import json
import re
import sys
from datetime import date, datetime

import paths
import store
from download import safe_name

MARK = "generated: true"

# Written once, then yours. See skills.py for what they are.
import skills as skills_mod


def slug(text, limit=60):
    """A filename Obsidian and every filesystem will accept."""
    text = re.sub(r"[\\/:*?\"<>|#\^\[\]]", "-", str(text or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return (text[:limit].strip() or "untitled")


def front(fields):
    lines = ["---", MARK]
    for k, v in fields.items():
        if v is None or v == "":
            continue
        v = str(v).replace("\n", " ")
        lines.append(f"{k}: {v}" if not re.search(r"[:#\[\]{}]", v) else f'{k}: "{v}"')
    lines.append("---")
    return "\n".join(lines)


def write(path, body, dry=False, report=None):
    """Write a generated note, unless a human has claimed the file.

    Claimed means the `generated: true` line is gone -- either the user
    deleted it deliberately, or the file was never ours to begin with.
    """
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
        # No marker in the frontmatter means a person owns this file now.
        if MARK not in existing[:400]:
            if report is not None:
                report["kept"].append(path)
            return False
        if existing == body:
            if report is not None:
                report["same"] += 1
            return False
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    if report is not None:
        report["wrote"].append(path)
    return True


def course_dir(root, course_name):
    p = store.course_parts(course_name)
    name = f"{p['code']} - {p['title']}" if p["title"] else p["code"]
    return root / "Courses" / slug(name, 70), p


def when(row):
    d, t = row["due_date"], row["due_time"]
    if not d:
        return ""
    try:
        pretty = datetime.strptime(d, "%Y-%m-%d").strftime("%a %d %b")
    except ValueError:
        pretty = d
    return f"{pretty}{' ' + t if t else ''}"


def line_for(row, today):
    """One deadline as a checklist line, with the why if it was inferred."""
    left = ""
    if row["due_date"]:
        try:
            days = (datetime.strptime(row["due_date"], "%Y-%m-%d").date() - today).days
            left = (" **overdue**" if days < 0 else " **today**" if days == 0
                    else f" ({days}d)" if days <= 21 else "")
        except ValueError:
            pass
    tail = f"  ·  before {row['linked_to']}" if row["linked_to"] else ""
    return f"- [ ] **{when(row) or 'no date'}** — {row['title']}{left}{tail}"


def bundle(dry=False):
    """One markdown file per course, holding everything about it.

    For dropping into a Claude Project as its knowledge. The vault is the
    right shape for reading and the wrong shape for uploading -- forty files
    to drag per course, five times over. This is the same content in one file.

    Written into the vault so it syncs to the laptop with everything else, and
    can be picked up from the Obsidian clone rather than fetched off the Pi.
    """
    root = paths.VAULT
    today = date.today()
    db = store.connect()
    made = []

    for course in db.execute("SELECT * FROM courses ORDER BY name").fetchall():
        parts = store.course_parts(course["name"])
        raw = {}
        if paths.COLLECTED.exists():
            for c in json.loads(paths.COLLECTED.read_text(encoding="utf-8")):
                if c.get("id") == course["d2l_id"]:
                    raw = c
                    break

        rows = store.only_mine(db.execute(
            """SELECT d.*, c.d2l_id AS course_d2l_id FROM dates d
               JOIN courses c ON c.id = d.course_id
               WHERE d.course_id = ? AND d.status IN ('new','accepted')
               ORDER BY d.due_date IS NULL, d.due_date, d.due_time""",
            (course["id"],)).fetchall())

        out = [front({"course": parts["code"], "kind": "bundle",
                      "title": f"{parts['code']} — everything",
                      "built": today.isoformat()}),
               "", f"# {parts['code']} — {parts['title']}", "",
               f"Everything this course has posted, in one file, as of "
               f"{today.strftime('%d %B %Y')}. Built for uploading into a "
               f"Claude Project; the vault itself is the readable version.", ""]

        dated = [r for r in rows if r["due_date"] and r["kind"] != "todo"]
        tasks = [r for r in rows if r["kind"] == "todo"]
        waiting = [r for r in rows if not r["due_date"] and r["kind"] != "todo"]

        out += ["## Deadlines", ""]
        out += [f"- **{when(r) or 'no date'}** — {r['title']}"
                f"{'  (' + r['kind'] + ')' if r['kind'] else ''}" for r in dated] \
               or ["_None recorded._"]
        out += [""]
        if tasks:
            out += ["## Things to do", ""]
            out += [f"- {r['title']}"
                    f"{'  — before ' + r['linked_to'] if r['linked_to'] else ''}"
                    for r in tasks] + [""]
        if waiting:
            out += ["## Announced, no date yet", ""]
            out += [f"- {r['title']}" for r in waiting] + [""]

        anns = raw.get("announcements") or []
        if anns:
            out += ["## Announcements", ""]
            for a in anns:
                body = ((a.get("Body") or {}).get("Text") or "").strip()
                if not body:
                    continue
                out += [f"### {a.get('Title') or 'untitled'}"
                        f"  ({(a.get('StartDate') or '')[:10] or 'undated'})",
                        "", body, ""]

        folder = paths.EXTRACTED / safe_name(course["name"], 40)
        if folder.is_dir():
            out += ["## Course material", ""]
            for f in sorted(folder.glob("*.txt")):
                if f.name == "_links.txt":
                    continue
                out += [f"### {f.stem}", "",
                        f.read_text(encoding="utf-8", errors="replace").strip(), ""]

        text = "\n".join(out)
        path = root / "Bundles" / f"{slug(parts['code'])} - {slug(parts['title'], 40)}.md"
        if not dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        made.append((path, len(text.split())))

    if not dry:
        readme = root / "Bundles" / "README.md"
        readme.write_text(
            "---\n" + MARK + "\n---\n\n# Bundles\n\n"
            "One file per course, holding everything that course has posted.\n\n"
            "These exist to be uploaded into a Claude Project — one project per\n"
            "course, one file each — so you can ask questions with the whole\n"
            "course already loaded. Rebuild them with `python vault.py --bundle`\n"
            "when a course has posted a lot of new material, and re-upload.\n\n"
            "They duplicate what is in `Courses/`, which is the version meant for\n"
            "reading. If the duplication clutters Obsidian's search, add this\n"
            "folder under Settings → Files & Links → Excluded files.\n",
            encoding="utf-8")
    db.close()

    print("\n  bundles for uploading into a Claude Project:")
    for path, words in made:
        print(f"    {words:>7,} words   {path.name}")
    print(f"\n  in {root / 'Bundles'}")
    if dry:
        print("  (dry run -- nothing written)")
    return made


def build(dry=False):
    root = paths.VAULT
    today = date.today()
    report = {"wrote": [], "kept": [], "same": 0}

    db = store.connect()
    courses = db.execute("SELECT * FROM courses ORDER BY name").fetchall()

    collected = []
    if paths.COLLECTED.exists():
        collected = json.loads(paths.COLLECTED.read_text(encoding="utf-8"))
    by_d2l = {c["id"]: c for c in collected}

    all_due = []

    for course in courses:
        cdir, parts = course_dir(root, course["name"])
        raw = by_d2l.get(course["d2l_id"], {})

        rows = db.execute(
            """SELECT d.*, c.d2l_id AS course_d2l_id FROM dates d
               JOIN courses c ON c.id = d.course_id
               WHERE d.course_id = ? AND d.status IN ('new','accepted')
               ORDER BY d.due_date IS NULL, d.due_date, d.due_time""",
            (course["id"],)).fetchall()
        # Your section's work, not all five lab groups'.
        rows = store.only_mine(rows)

        dated = [r for r in rows if r["due_date"]]
        tasks = [r for r in rows if r["kind"] == "todo"]
        waiting = [r for r in rows if not r["due_date"] and r["kind"] != "todo"]
        all_due += [(course, r) for r in dated if r["kind"] != "todo"]

        # ------------------------------------------------ the course note
        body = [front({
            "course": parts["code"], "course_name": parts["title"],
            "section": parts["section"], "kind": "course",
            "d2l_id": course["d2l_id"], "term": course["term"],
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }), "", f"# {parts['code']} — {parts['title']}", ""]

        upcoming = [r for r in dated
                    if r["due_date"] >= today.isoformat() and r["kind"] != "todo"]
        body += ["## Due", ""]
        body += [line_for(r, today) for r in upcoming[:25]] or ["_Nothing upcoming._"]
        body += [""]

        if tasks:
            body += ["## To do", ""]
            body += [line_for(r, today) for r in tasks] + [""]

        if waiting:
            body += ["## Announced, no date yet", ""]
            body += [f"- {r['title']}" for r in waiting] + [""]

        material_dir = paths.EXTRACTED / safe_name(course["name"], 40)
        material = sorted(f.stem for f in material_dir.glob("*.txt")
                          if f.name != "_links.txt") if material_dir.is_dir() else []
        if material:
            body += ["## Material", ""]
            body += [f"- [[{slug(m)}]]" for m in material] + [""]

        anns = raw.get("announcements") or []
        if anns:
            body += ["## Recent announcements", ""]
            for a in anns[:8]:
                body += [f"- [[{slug(a.get('Title') or 'untitled')}]]"]
            body += [""]

        body += ["## My notes", "", "Anything you write goes in `Notes/`. "
                 "This file is rebuilt on every scrape, so do not write here.", ""]

        write(cdir / f"_{parts['code']}.md", "\n".join(body), dry, report)
        if not dry:
            (cdir / "Notes").mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------- material notes
        if material_dir.is_dir():
            for f in sorted(material_dir.glob("*.txt")):
                if f.name == "_links.txt":
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
                note = "\n".join([
                    front({"course": parts["code"], "kind": "content",
                           "title": f.stem,
                           "scraped_at": datetime.now().strftime("%Y-%m-%d")}),
                    "", f"# {f.stem}", "",
                    f"_From {parts['code']} — {parts['title']}. "
                    f"Extracted text; the original is in `_originals`._", "",
                    text.strip(), ""])
                write(cdir / "Content" / f"{slug(f.stem)}.md", note, dry, report)

        # ------------------------------------------------- announcements
        for a in anns:
            title = a.get("Title") or "untitled"
            text = ((a.get("Body") or {}).get("Text") or "").strip()
            if not text:
                continue
            posted = (a.get("StartDate") or "")[:10]
            note = "\n".join([
                front({"course": parts["code"], "kind": "announcement",
                       "title": title, "posted": posted}),
                "", f"# {title}", "", f"_Posted {posted or 'unknown'}._", "",
                text, ""])
            name = f"{posted + ' - ' if posted else ''}{slug(title, 50)}"
            write(cdir / "Announcements" / f"{name}.md", note, dry, report)

        # ------------------------------------------------- work stubs
        for r in rows:
            if r["kind"] in ("todo", "session", None):
                continue
            note = "\n".join([
                front({"course": parts["code"], "kind": r["kind"],
                       "title": r["title"], "due": r["due_date"] or "",
                       "due_time": r["due_time"] or "",
                       "status": r["status"], "prepared": "no"}),
                "", f"# {r['title']}", "",
                f"**{parts['code']} — {parts['title']}**  ·  "
                f"{when(r) or 'no date yet'}", "",
                "## Where this came from", "",
                f"> {' '.join((r['source_excerpt'] or '').split())}", "",
                "## Preparation", "",
                f"_Not prepared yet. Run:_ `python prep.py {r['id']}`", "",
                "## My work", "", ""])
            write(cdir / "Work" / f"{slug(r['title'], 60)}.md", note, dry, report)

    # ---------------------------------------------------------- dashboard
    all_due.sort(key=lambda cr: (cr[1]["due_date"], cr[1]["due_time"] or ""))
    lines = [front({"kind": "dashboard",
                    "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}),
             "", "# Upcoming", ""]
    shown = 0
    for course, r in all_due:
        if r["due_date"] < today.isoformat():
            continue
        p = store.course_parts(course["name"])
        lines.append(f"- [ ] **{when(r)}** · {p['code']} — {r['title']}")
        shown += 1
        if shown >= 60:
            break
    if not shown:
        lines.append("_Nothing upcoming._")
    write(root / "Dashboards" / "Upcoming.md", "\n".join(lines) + "\n", dry, report)

    skills_mod.seed(root / "Skills", dry, report)
    db.close()

    print(f"\n  vault: {root}")
    print(f"  {len(report['wrote'])} written, {report['same']} unchanged, "
          f"{len(report['kept'])} left alone because you had edited them")
    for p in report["kept"][:10]:
        print(f"      kept  {p.relative_to(root)}")
    if dry:
        print("  (dry run -- nothing was written)")
    return report


def push(root, quiet=False):
    """Commit and push the vault, if it has been set up as a git repo.

    The vault is its own repository, separate from the code. That is not
    tidiness: the profile written from the psychoeducational report lives at
    `profile/`, a sibling of `vault/` and outside it, so it is not merely
    gitignored here -- it is not in the directory that gets pushed at all.
    """
    import subprocess
    if not (root / ".git").exists():
        # Called from every scrape, so saying this every half hour would be
        # noise in the log. Worth saying when a person asked for it.
        if not quiet:
            print("\n  not a git repo yet -- see docs/pi-setup.md Part 4")
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    def git(*args, check=True):
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, check=check)

    git("add", "-A")
    if not git("status", "--porcelain").stdout.strip():
        if not quiet:
            print("\n  nothing changed, nothing pushed")
        return
    git("commit", "-m", f"vault: {stamp}")
    # -u origin HEAD so the very first push sets its own upstream, rather
    # than failing with advice about push.autoSetupRemote.
    r = git("push", "-u", "origin", "HEAD", check=False)
    if r.returncode:
        print(f"\n  push failed: {(r.stderr or '').strip().splitlines()[-1:]}")
    else:
        print(f"\n  pushed to {git('remote', 'get-url', 'origin').stdout.strip()}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    build(dry="--dry-run" in argv)
    if "--bundle" in argv:
        bundle(dry="--dry-run" in argv)
    if "--push" in argv and "--dry-run" not in argv:
        push(paths.VAULT)


if __name__ == "__main__":
    main()
