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
import os
import re
import stat
import sys
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import pagedates
import paths

BASE = "https://uottawa.brightspace.com"
HERE = Path(__file__).parent
SESSION_FILE = paths.SESSION
OUT_FILE = paths.COLLECTED

LP, LE = "1.63", "1.97"
LOGIN_TIMEOUT_S = 300
MAX_CONTENT_DEPTH = 8

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ------------------------------------------------------------------ session

async def browser_login():
    # Imported here rather than at the top of the file on purpose. This is the
    # one function that needs a browser, and the Pi deliberately has none --
    # see PLAN.md section 2. Everything else in this module is plain HTTP, so
    # the Pi must be able to import it without Playwright installed.
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise SystemExit(
            "\nA full login is needed, and that needs a browser.\n\n"
            "This machine has no browser installed, which is expected if it is\n"
            "the Pi. Log in on your laptop instead:\n\n"
            "    python update.py\n\n"
            "then copy the refreshed session.json across to the Pi.\n"
        )

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
                return keep_cookies(cookies)
            if elapsed and elapsed % 15 == 0:
                print(f"  waiting... {elapsed}s")
            await asyncio.sleep(1)

        await browser.close()
    sys.exit("Timed out waiting for login.")


# Brightspace's own session lasts hours. The identity provider's cookies
# last far longer, and holding them is what lets the scraper re-authenticate
# without a person tapping a phone -- the difference between a Pi that runs
# unattended and one that stops every morning.
SSO_DOMAINS = ("brightspace.com", "uottawa.ca", "microsoftonline.com",
               "microsoft.com", "live.com", "msauth.net", "msftauth.net",
               "msauthimages.net")


def keep_cookies(cookies):
    """Cookies worth storing, as {domain: {name: value}}.

    Kept per-domain rather than flattened: sending a Microsoft cookie to
    Brightspace, or the reverse, is at best useless and at worst leaks one
    site's session to another.
    """
    jar = {}
    for c in cookies:
        domain = (c.get("domain") or "").lstrip(".")
        if any(d in domain for d in SSO_DOMAINS):
            jar.setdefault(domain, {})[c["name"]] = c["value"]
    return jar


def write_session(jar):
    SESSION_FILE.write_text(json.dumps(jar, indent=2), encoding="utf-8")
    try:
        # Owner-only. On Windows this is largely cosmetic, but the file is
        # gitignored and the real protection is the machine itself -- anyone
        # with your login can read it, so treat the Pi accordingly.
        os.chmod(SESSION_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def read_session():
    if not SESSION_FILE.exists():
        return {}
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    # Older files stored a flat {name: value} of Brightspace cookies only.
    if data and all(isinstance(v, str) for v in data.values()):
        return {"uottawa.brightspace.com": data}
    return data


class AutoForm(HTMLParser):
    """Finds the form an SSO page would submit with JavaScript.

    Single sign-on hands the browser a page whose only content is a form
    that posts itself. Without a browser to run that script, the form has
    to be found and posted directly.
    """

    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}
        self._in_form = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._in_form = True
            self.action = a.get("action")
        elif tag == "input" and self._in_form:
            name = a.get("name")
            if name and a.get("type", "hidden").lower() == "hidden":
                self.fields[name] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False


def make_client(jar):
    client = httpx.Client(
        headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"},
        follow_redirects=True,
        timeout=30.0,
    )
    for domain, cookies in jar.items():
        for name, value in cookies.items():
            client.cookies.set(name, value, domain=domain)
    return client


# The cookies Brightspace itself issues, as opposed to the sign-on cookies
# that earn them. These are the short-lived half.
D2L_SESSION_COOKIES = ("d2lSessionVal", "d2lSecureSessionVal",
                       "d2lSameSiteCanaryA", "d2lSameSiteCanaryB")


def drop_d2l_session(client):
    """Throw away the expired Brightspace cookies before renewing.

    They have to go, and not only for tidiness: an expired d2lSessionVal is
    indistinguishable from a fresh one by name, so leaving it in the jar
    once made renewal report success without doing anything at all.
    """
    for name in D2L_SESSION_COOKIES:
        client.cookies.delete(name)


def refresh_session(client):
    """Walk the sign-on redirect chain to earn a fresh Brightspace session.

    The identity provider still recognises its own cookies, so it issues a
    new assertion without asking for a password or a second factor. Returns
    True if the session actually answers an API call afterwards -- asking
    whether a cookie exists is not the same question, which is the bug this
    signature used to have.
    """
    drop_d2l_session(client)

    try:
        r = client.get(f"{BASE}/d2l/home")
    except Exception:
        return False

    for _ in range(8):
        if session_works(client):
            return True
        parser = AutoForm()
        try:
            parser.feed(r.text)
        except Exception:
            return False
        if not parser.action or not parser.fields:
            return False
        action = parser.action
        if action.startswith("/"):
            action = f"{r.url.scheme}://{r.url.host}{action}"
        try:
            r = client.post(action, data=parser.fields)
        except Exception:
            return False

    return session_works(client)


def session_works(client):
    try:
        r = client.get(f"{BASE}/d2l/api/lp/{LP}/users/whoami")
        return r.status_code == 200 and "json" in r.headers.get("content-type", "")
    except Exception:
        return False


def current_jar(client):
    """Whatever the client holds now, back in per-domain form."""
    jar = {}
    for c in client.cookies.jar:
        domain = (c.domain or "").lstrip(".")
        if any(d in domain for d in SSO_DOMAINS):
            jar.setdefault(domain, {})[c.name] = c.value
    return jar


def get_client():
    """A working session: reuse it, renew it, or as a last resort ask you."""
    jar = read_session()
    if jar:
        client = make_client(jar)
        if session_works(client):
            print("Reusing saved session.\n")
            return client

        # Brightspace's session is short-lived, but the sign-on cookies
        # usually are not -- so try to renew before troubling anyone.
        print("Brightspace session expired; renewing without a login...")
        if refresh_session(client) and session_works(client):
            write_session(current_jar(client))
            print("Renewed.\n")
            return client
        print("Could not renew -- a full login is needed.\n")

    jar = asyncio.run(browser_login())
    write_session(jar)
    client = make_client(jar)
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
    assignments = get(client, f"/d2l/api/le/{LE}/{oid}/dropbox/folders/") or []

    # An item restricted to particular students keeps its dates in a
    # special-access override the API will not serve a student role, so the
    # only copy we can reach is the one on the rendered page. This fills those
    # in and touches nothing that already has a date -- see pagedates.py.
    filled = pagedates.attach(client, BASE, oid, quizzes, "quizzes")
    filled += pagedates.attach(client, BASE, oid, assignments, "assignments")
    if filled:
        print(f"    {filled} date{'s' if filled != 1 else ''} found only on the "
              f"page, not in the API")

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
        "assignments": assignments,
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
