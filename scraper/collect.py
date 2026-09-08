r"""
Logs into Brightspace once, remembers the session, and pulls everything from
this term's courses.

    python scraper\collect.py

First run opens a browser so you can log in. After that it reuses the saved
session and runs without asking. When the session eventually expires it opens
the browser again on its own.

Read-only. Every request is a GET.
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

BASE = "https://uottawa.brightspace.com"
HERE = Path(__file__).parent
SESSION_FILE = HERE / "session.json"
OUT_FILE = HERE / "collected.json"

LP, LE = "1.63", "1.97"
LOGIN_TIMEOUT_S = 300
MAX_CONTENT_DEPTH = 8

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ------------------------------------------------------------------ session

async def browser_login():
    print("Opening a browser. Log in and approve the prompt on your phone.\n")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(f"{BASE}/d2l/home", wait_until="domcontentloaded")

        for elapsed in range(LOGIN_TIMEOUT_S):
            if any(c["name"] == "d2lSessionVal" for c in await ctx.cookies()):
                await asyncio.sleep(2)
                cookies = await ctx.cookies()
                await browser.close()
                print(f"Logged in after {elapsed}s.\n")
                # Brightspace-scoped cookies only; the Microsoft session stays
                # in the browser and is never saved to disk.
                return {
                    c["name"]: c["value"]
                    for c in cookies
                    if "brightspace.com" in c.get("domain", "")
                }
            if elapsed and elapsed % 15 == 0:
                print(f"  waiting... {elapsed}s")
            await asyncio.sleep(1)

        await browser.close()
    sys.exit("Timed out waiting for login.")


def make_client(cookies):
    return httpx.Client(
        cookies=cookies,
        headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"},
        follow_redirects=True,
        timeout=30.0,
    )


def session_works(client):
    try:
        r = client.get(f"{BASE}/d2l/api/lp/{LP}/users/whoami")
        return r.status_code == 200 and "json" in r.headers.get("content-type", "")
    except Exception:
        return False


def get_client():
    """Reuse the saved session if it still works, otherwise log in again."""
    if SESSION_FILE.exists():
        try:
            cookies = json.loads(SESSION_FILE.read_text())
            client = make_client(cookies)
            if session_works(client):
                print("Reusing saved session.\n")
                return client
            print("Saved session expired.\n")
        except Exception:
            pass

    cookies = asyncio.run(browser_login())
    SESSION_FILE.write_text(json.dumps(cookies, indent=2))
    client = make_client(cookies)
    if not session_works(client):
        sys.exit("Logged in but the session did not work. Something changed.")
    return client


# ------------------------------------------------------------------ fetching

def get(client, path, **params):
    """GET one endpoint. Returns parsed JSON, or None on any failure."""
    try:
        r = client.get(BASE + path, params=params or None)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            return r.json()
        print(f"    ! {path} -> {r.status_code}")
    except Exception as e:
        print(f"    ! {path} -> {e!r}")
    return None


TERM_RE = re.compile(r"\b(20\d{3})\b")


def term_of(name):
    """Pull the trailing term stamp out of a course name, e.g. 20269."""
    found = TERM_RE.findall(name or "")
    return found[-1] if found else None


def current_courses(client, term_override=None):
    """This term's real courses, filtered out of the full enrollment list."""
    data = get(client, f"/d2l/api/lp/{LP}/enrollments/myenrollments/")
    if not data:
        sys.exit("Could not read enrollments.")

    courses = []
    for item in data.get("Items", []):
        ou = item.get("OrgUnit", {})
        if ou.get("Type", {}).get("Code") != "Course Offering":
            continue
        courses.append({
            "id": ou.get("Id"),
            "name": (ou.get("Name") or "").strip(),
            "term": term_of(ou.get("Name")),
        })

    terms = sorted({c["term"] for c in courses if c["term"]})
    if not terms:
        print("No term stamps found; keeping every course.")
        return courses, None

    # Newest term present. Self-correcting across the Sept/Jan rollover:
    # new courses appear with a higher stamp and win automatically.
    term = term_override or terms[-1]
    keep = [c for c in courses if c["term"] == term]
    print(f"Terms seen: {', '.join(terms)}  ->  using {term}")
    print(f"{len(keep)} of {len(courses)} courses are from this term.\n")
    return keep, term


def walk_content(client, oid):
    """Everything in a course's Content area, however deeply nested.

    Prefers the table-of-contents endpoint, which returns the whole tree in
    one request. Falls back to walking module by module if that is not
    available -- the earlier version only ever saw the top layer, because a
    module's Structure lists its children as stubs with no children of
    their own.
    """
    modules, topics = [], []
    seen_modules, seen_topics = set(), set()

    def add_module(m):
        mid = m.get("Id") or m.get("ModuleId")
        if mid in seen_modules:
            return
        seen_modules.add(mid)
        modules.append({
            "id": mid,
            "title": m.get("Title"),
            "due": m.get("ModuleDueDate") or m.get("DueDate"),
            "start": m.get("ModuleStartDate") or m.get("StartDate"),
            "end": m.get("ModuleEndDate") or m.get("EndDate"),
            "description": (m.get("Description") or {}).get("Text", "") or "",
            "modified": m.get("LastModifiedDate"),
        })

    def add_topic(t):
        tid = t.get("Id") or t.get("TopicId")
        if tid in seen_topics:
            return
        seen_topics.add(tid)
        topics.append({
            "id": tid,
            "title": t.get("Title"),
            "url": t.get("Url"),
            "type": t.get("TypeIdentifier"),
            "due": t.get("DueDate"),
            "description": (t.get("Description") or {}).get("Text", "") or "",
            "modified": t.get("LastModifiedDate"),
        })

    # --- preferred: whole tree in one call ---------------------------
    toc = get(client, f"/d2l/api/le/{LE}/{oid}/content/toc")
    if isinstance(toc, dict) and toc.get("Modules") is not None:
        def descend(module, depth):
            if depth > MAX_CONTENT_DEPTH:
                return
            add_module(module)
            for t in module.get("Topics") or []:
                add_topic(t)
            for sub in module.get("Modules") or []:
                descend(sub, depth + 1)

        for module in toc.get("Modules") or []:
            descend(module, 0)
        return modules, topics

    # --- fallback: walk each module's structure endpoint --------------
    def visit(mid, depth):
        if depth > MAX_CONTENT_DEPTH or mid in seen_modules:
            return
        children = get(client, f"/d2l/api/le/{LE}/{oid}/content/modules/{mid}/structure/")
        for child in children or []:
            if child.get("Type") == 0:
                add_module(child)
                visit(child.get("Id"), depth + 1)
            else:
                add_topic(child)

    root = get(client, f"/d2l/api/le/{LE}/{oid}/content/root/")
    for module in root or []:
        add_module(module)
        visit(module.get("Id"), 0)
        for child in module.get("Structure") or []:
            if child.get("Type") == 0:
                visit(child.get("Id"), 1)
            else:
                add_topic(child)
    return modules, topics


def collect_course(client, course):
    oid, name = course["id"], course["name"]
    print(f"  {name}")

    modules, topics = walk_content(client, oid)

    quizzes_raw = get(client, f"/d2l/api/le/{LE}/{oid}/quizzes/") or {}
    quizzes = quizzes_raw.get("Objects", []) if isinstance(quizzes_raw, dict) else []

    # The calendar endpoint needs an explicit window -- this is what the probe
    # was missing. Two months back for context, six ahead for deadlines.
    now = datetime.now(timezone.utc)
    events = get(
        client,
        f"/d2l/api/le/{LE}/{oid}/calendar/events/myEvents/",
        startDateTime=(now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        endDateTime=(now + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    ) or []

    return {
        "id": oid,
        "name": name,
        "term": course["term"],
        "modules": modules,
        "topics": topics,
        "announcements": get(client, f"/d2l/api/le/{LE}/{oid}/news/") or [],
        "assignments": get(client, f"/d2l/api/le/{LE}/{oid}/dropbox/folders/") or [],
        "quizzes": quizzes,
        "grades": get(client, f"/d2l/api/le/{LE}/{oid}/grades/") or [],
        "calendar": events if isinstance(events, list) else [],
        "discussions": collect_discussions(client, oid),
        "checklists": collect_checklists(client, oid),
        "surveys": as_list(get(client, f"/d2l/api/le/{LE}/{oid}/surveys/")),
        "overview": get(client, f"/d2l/api/le/{LE}/{oid}/overview") or {},
    }


def as_list(data):
    """Some endpoints return a bare list, others wrap it in Objects/Items."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("Objects", "Items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def collect_discussions(client, oid):
    """Forums, their topics, and the posts inside them.

    Professors routinely answer "when is this due" in a discussion thread and
    nowhere else, so the post bodies matter, not just the topic titles.
    """
    out = []
    for forum in as_list(get(client, f"/d2l/api/le/{LE}/{oid}/discussions/forums/")):
        fid = forum.get("ForumId") or forum.get("Id")
        if fid is None:
            continue
        topics = []
        for topic in as_list(get(
                client, f"/d2l/api/le/{LE}/{oid}/discussions/forums/{fid}/topics/")):
            tid = topic.get("TopicId") or topic.get("Id")
            posts = as_list(get(
                client,
                f"/d2l/api/le/{LE}/{oid}/discussions/forums/{fid}/topics/{tid}/posts/",
            )) if tid is not None else []
            topics.append({
                "id": tid,
                "title": topic.get("Name") or topic.get("Title"),
                "description": topic.get("Description"),
                "start": topic.get("StartDate"),
                "end": topic.get("EndDate"),
                "due": topic.get("DueDate"),
                "posts": [
                    {"subject": p.get("Subject"), "body": p.get("Message"),
                     "date": p.get("DatePosted")}
                    for p in posts[:60]
                ],
            })
        out.append({
            "id": fid,
            "title": forum.get("Name") or forum.get("Title"),
            "description": forum.get("Description"),
            "topics": topics,
        })
    return out


def collect_checklists(client, oid):
    """Checklist items carry their own due dates and are a tab of their own."""
    out = []
    for cl in as_list(get(client, f"/d2l/api/le/{LE}/{oid}/checklists/")):
        cid = cl.get("ChecklistId") or cl.get("Id")
        items = []
        for cat in as_list(get(
                client, f"/d2l/api/le/{LE}/{oid}/checklists/{cid}/categories/")) if cid else []:
            kid = cat.get("CategoryId") or cat.get("Id")
            for item in as_list(get(
                    client,
                    f"/d2l/api/le/{LE}/{oid}/checklists/{cid}/categories/{kid}/items/")):
                items.append({
                    "name": item.get("Name"),
                    "description": item.get("Description"),
                    "due": item.get("DueDate"),
                })
        out.append({"id": cid, "name": cl.get("Name"),
                    "description": cl.get("Description"), "items": items})
    return out


# ------------------------------------------------------------------ summary

def count_dates(course):
    """Dates that arrive as real fields -- no reading, no guessing."""
    exact = []
    for a in course["assignments"]:
        if a.get("DueDate"):
            exact.append((a.get("Name"), a["DueDate"], "assignment"))
    for q in course["quizzes"]:
        if q.get("DueDate") or q.get("EndDate"):
            exact.append((q.get("Name"), q.get("DueDate") or q.get("EndDate"), "quiz"))
    for m in course["modules"]:
        if m.get("due"):
            exact.append((m.get("title"), m["due"], "module"))
    for e in course["calendar"]:
        when = e.get("StartDateTime") or e.get("EndDateTime")
        if when:
            exact.append((e.get("Title"), when, "calendar"))
    return exact


def main(term_override=None, client=None):
    if term_override is None and len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        term_override = sys.argv[1]
    client = client or get_client()

    courses, term = current_courses(client, term_override)
    if not courses:
        sys.exit("No courses matched. Pass a term code to override, e.g. 20269")

    print("Collecting:\n")
    collected = [collect_course(client, c) for c in courses]

    OUT_FILE.write_text(json.dumps(collected, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    total_exact = 0
    total_prose = 0
    for c in collected:
        exact = count_dates(c)
        prose = len(c["announcements"]) + sum(1 for m in c["modules"] if m["description"])
        total_exact += len(exact)
        total_prose += prose
        posts = sum(len(t["posts"]) for d in c.get("discussions", [])
                    for t in d.get("topics", []))
        checks = sum(len(cl["items"]) for cl in c.get("checklists", []))
        short = c["name"][:36]
        print(f"{short:<38} {len(exact):>3} dates {len(c['topics']):>4} files "
              f"{len(c.get('announcements', [])):>3} posts {len(c.get('quizzes', [])):>3} quiz "
              f"{len(c.get('assignments', [])):>3} asgn {posts:>3} disc {checks:>3} chk")
    print("=" * 64)
    print(f"{total_exact} deadlines found as exact dates -- no AI needed for these.")
    print(f"{total_prose} announcements and descriptions to read for dates written in text.")
    print(f"\nSaved to: {OUT_FILE}")


if __name__ == "__main__":
    main()
