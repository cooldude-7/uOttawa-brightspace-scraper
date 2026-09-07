"""
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
    """Course content is a tree of modules; flatten it into a list."""
    modules, topics = [], []

    def visit(module, depth):
        if depth > MAX_CONTENT_DEPTH:
            return
        modules.append({
            "id": module.get("Id"),
            "title": module.get("Title"),
            "due": module.get("ModuleDueDate"),
            "start": module.get("ModuleStartDate"),
            "end": module.get("ModuleEndDate"),
            "description": (module.get("Description") or {}).get("Text", ""),
            "modified": module.get("LastModifiedDate"),
        })
        for child in module.get("Structure") or []:
            if child.get("Type") == 0:               # nested module
                sub = get(client, f"/d2l/api/le/{LE}/{oid}/content/modules/{child['Id']}/structure/")
                if isinstance(sub, list):
                    for entry in sub:
                        if entry.get("Type") == 0:
                            visit(entry, depth + 1)
                        else:
                            topics.append(as_topic(entry))
                continue
            topics.append(as_topic(child))

    def as_topic(t):
        return {
            "id": t.get("Id"),
            "title": t.get("Title"),
            "url": t.get("Url"),
            "due": t.get("DueDate"),
            "modified": t.get("LastModifiedDate"),
        }

    root = get(client, f"/d2l/api/le/{LE}/{oid}/content/root/")
    for module in root or []:
        visit(module, 0)
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
    }


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


def main():
    term_override = sys.argv[1] if len(sys.argv) > 1 else None
    client = get_client()

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
        short = c["name"][:44]
        print(f"{short:<46} {len(exact):>3} dates  {len(c['topics']):>4} files")
    print("=" * 64)
    print(f"{total_exact} deadlines found as exact dates -- no AI needed for these.")
    print(f"{total_prose} announcements and descriptions to read for dates written in text.")
    print(f"\nSaved to: {OUT_FILE}")


if __name__ == "__main__":
    main()
