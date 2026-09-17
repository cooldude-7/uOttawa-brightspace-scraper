r"""
Every place a Brightspace file can hang, and which of them we have never read.

    python probe_attachments.py               all courses, from collected.json
    python probe_attachments.py GNG2101       one course
    python probe_attachments.py GNG2101 --live   also ask Brightspace directly

`collect.py` gathers twelve tabs. `download.py` downloads from exactly one
of them -- `topics`, the Content area. So a file attached to a submission
folder, an announcement, a discussion post or the course overview has never
been fetched, extracted or read, and nothing anywhere says so.

GNG2101's Project Deliverable A is the case that found it: the app knows it
is due Fri 18 Sep 11:59 PM and does not hold one word of what it asks for,
because the instructions are attached to the dropbox folder rather than
posted in Content.

The first pass costs nothing -- `collected.json` is already on disk and the
attachment lists are already in it, simply never looked at. `--live` then
asks for one real file to confirm the download endpoint before anything is
built on it. Names and sizes only; no file contents are printed.
"""

import json
import sys

import paths
import store

# Where a file can hang, and the field that lists them. The dropbox one is
# the case this was written for; the rest are the same shape of miss.
PLACES = [
    ("assignments", "Attachments", "Name"),
    ("assignments", "CustomInstructions", "Name"),
    ("announcements", "Attachments", "Title"),
    ("quizzes", "Attachments", "Name"),
    ("discussions", "Attachments", "Title"),
    ("surveys", "Attachments", "Name"),
]


def listed(item, field):
    """What `field` holds, as a list of names, or [] -- shapes vary by tab."""
    v = item.get(field)
    if not v:
        return []
    if isinstance(v, dict):                      # rich text: {Text, Html}
        text = (v.get("Text") or v.get("Html") or "").strip()
        return [f"<{len(text)} chars of text>"] if text else []
    if isinstance(v, str):
        return [f"<{len(v)} chars of text>"] if v.strip() else []
    out = []
    for f in v:
        if isinstance(f, dict):
            out.append(f"{f.get('FileName') or f.get('Name') or '?'}"
                       f"  ({f.get('Size') or f.get('FileSize') or '?'} bytes)"
                       f"  id={f.get('FileId') or f.get('Id') or '?'}")
        else:
            out.append(str(f)[:60])
    return out


def main(argv):
    only = next((a.upper() for a in argv[1:] if not a.startswith("--")), None)
    live = "--live" in argv

    collected = json.loads(paths.COLLECTED.read_text(encoding="utf-8"))
    total = 0

    for course in collected:
        code = store.course_parts(course.get("name", ""))["code"]
        if only and code.upper() != only:
            continue

        found = []
        for tab, field, title_key in PLACES:
            for item in course.get(tab) or []:
                if not isinstance(item, dict):
                    continue
                names = listed(item, field)
                if names:
                    found.append((tab, field, item.get(title_key) or "?", names,
                                  item.get("Id") or item.get("AssignmentId")))

        overview = course.get("overview") or {}
        if overview:
            names = listed(overview, "Attachments") or listed(overview, "Description")
            if names:
                found.append(("overview", "-", "Course overview", names, None))

        if not found:
            print(f"\n{code}: nothing attached outside Content.")
            continue

        print(f"\n{code} — {len(found)} item(s) carrying something never downloaded\n")
        for tab, field, title, names, ident in found:
            print(f"  [{tab}/{field}]  {str(title)[:44]}")
            for n in names:
                print(f"       {n}")
            total += len(names)

        if live:
            probe_live(course, [f for f in found if f[0] == "assignments"
                                and f[1] == "Attachments"])

    print(f"\n  {total} attachment(s)/instruction block(s) the app has never read.")
    print("  download.py walks `topics` only -- see its loop over walk_content().")
    return 0


def probe_live(course, folders):
    """Ask Brightspace for one real attachment, to confirm the endpoint.

    Reads the response's status and length only; the file is not saved and
    its contents are not printed.
    """
    if not folders:
        print("\n  --live: no dropbox attachments to try.")
        return
    import collect
    from collect import BASE, LE

    client = collect.get_client()
    oid = course["id"]
    _tab, _field, title, names, fid = folders[0]
    print(f"\n  --live: asking for the first attachment on {str(title)[:40]}")
    file_id = names[0].split("id=")[-1].strip()
    for url in (
        f"/d2l/api/le/{LE}/{oid}/dropbox/folders/{fid}/attachments/{file_id}",
        f"/d2l/le/{LE}/{oid}/dropbox/folders/{fid}/attachments/{file_id}",
    ):
        try:
            r = client.get(BASE + url, follow_redirects=True)
        except Exception as exc:
            print(f"      {url}\n          error: {type(exc).__name__}")
            continue
        kind = r.headers.get("content-type", "?").split(";")[0]
        print(f"      {url}\n          HTTP {r.status_code}  {kind}  "
              f"{len(r.content):,} bytes")
        if r.status_code == 200:
            print("          ^ this is the endpoint to build on.")
            return


if __name__ == "__main__":
    sys.exit(main(sys.argv))
