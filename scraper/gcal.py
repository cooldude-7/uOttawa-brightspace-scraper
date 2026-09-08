r"""
Puts accepted deadlines into your Google Calendar.

    python -m pip install google-auth-oauthlib google-api-python-client
    python gcal.py --setup      authorise once, in a browser
    python gcal.py --push       send everything already accepted
    python gcal.py --list       show what this app has put there
    python gcal.py --check      compare the calendar against your decisions
    python gcal.py --remove-all take it all back out again
    python gcal.py --separate   move them to their own calendar you can hide
    python gcal.py --primary    move them back to your main calendar

Events go in your main calendar, tagged in their description so this app
can always find its own again. Nothing else is ever touched.
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import paths
import store

HERE = Path(__file__).parent
CLIENT_FILE = paths.GOOGLE_CLIENT   # downloaded from Google Cloud
TOKEN_FILE = paths.GOOGLE_TOKEN     # written after you approve

# Manage events, and create/manage calendars this app made -- not blanket
# access to every calendar in the account.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.app.created",
]
CALENDAR = "primary"
CALENDAR_NAME = "uOttawa deadlines"
TIMEZONE = "America/Toronto"

# Every event carries this so the app can find, update and remove exactly
# what it created and never anything you added yourself.
MARKER = "[brightspace-scraper]"


def service(interactive=False, force=False):
    """An authorised Calendar client, or None with an explanation printed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("  Calendar libraries missing. Run:")
        print("    python -m pip install google-auth-oauthlib google-api-python-client")
        return None

    creds = None
    if TOKEN_FILE.exists() and not force:
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    # A token issued before a scope was added stays perfectly valid, so
    # "still valid" is not the same as "allowed to do what we now need".
    # Without this check, re-running setup silently changes nothing and the
    # failure only shows up later as a 403.
    if creds and not creds.has_scopes(SCOPES):
        print("  New permission needed -- asking Google again.")
        creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if not interactive:
            return None
        if not CLIENT_FILE.exists():
            print(f"  Missing {CLIENT_FILE.name}. See the setup steps.")
            return None
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def target():
    """Which calendar events go to. Defaults to your main one."""
    return store.load_prefs().get("calendar_id") or CALENDAR


def ensure_calendar(api):
    """Find or create the app's own calendar. Returns its id.

    A calendar of its own is what gives you a checkbox: one click hides
    every deadline at once and leaves the rest of your week visible.

    The id is remembered rather than looked up by name -- listing every
    calendar in the account needs far broader permission than this app
    should hold, and remembering it also survives a rename.
    """
    prefs = store.load_prefs()
    known = prefs.get("app_calendar_id")
    if known:
        try:
            api.calendars().get(calendarId=known).execute()
            return known
        except Exception:
            pass                                  # deleted; make a new one

    created = api.calendars().insert(body={
        "summary": CALENDAR_NAME,
        "description": "Deadlines found in Brightspace. Safe to hide or delete.",
        "timeZone": TIMEZONE,
    }).execute()
    prefs["app_calendar_id"] = created["id"]
    store.save_prefs(prefs)
    return created["id"]


def event_body(row):
    """One deadline as a calendar entry."""
    course = (row["course_name"] or "").split("  ")[0].strip()
    title = f"{course}: {row['title']}" if course else row["title"]

    lines = []
    if row["source_excerpt"]:
        lines.append(f"Found in: “{' '.join(row['source_excerpt'].split())[:400]}”")
    if row["confidence"] and row["confidence"] != "high":
        lines.append(f"Confidence: {row['confidence']} -- worth double-checking.")
    lines.append(MARKER)

    body = {
        "summary": title[:250],
        "description": "\n\n".join(lines),
        "source": {"title": "Brightspace", "url": "https://uottawa.brightspace.com/d2l/home"},
    }

    if row["due_time"]:
        start = datetime.strptime(f"{row['due_date']} {row['due_time']}", "%Y-%m-%d %H:%M")
        body["start"] = {"dateTime": start.isoformat(), "timeZone": TIMEZONE}
        body["end"] = {"dateTime": (start + timedelta(hours=1)).isoformat(),
                       "timeZone": TIMEZONE}
        body["reminders"] = {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": 24 * 60},
            {"method": "popup", "minutes": 60},
        ]}
    else:
        day = datetime.strptime(row["due_date"], "%Y-%m-%d").date()
        body["start"] = {"date": day.isoformat()}
        body["end"] = {"date": (day + timedelta(days=1)).isoformat()}
        body["reminders"] = {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": 18 * 60},      # 6pm the day before
        ]}

    return body


def add(api, db, row):
    """Create the event and remember its id. Returns the id, or None."""
    if not row["due_date"]:
        return None                                    # nothing to schedule yet
    try:
        created = api.events().insert(
            calendarId=target(), body=event_body(row)).execute()
    except Exception as e:
        print(f"  could not add {row['title'][:40]}: {e}")
        return None
    db.execute("UPDATE dates SET gcal_event_id = ? WHERE id = ?",
               (created["id"], row["id"]))
    return created["id"]


def remove(api, db, date_id, event_id):
    try:
        api.events().delete(calendarId=target(), eventId=event_id).execute()
    except Exception as e:
        if "410" not in str(e) and "404" not in str(e):
            print(f"  could not remove event: {e}")
    db.execute("UPDATE dates SET gcal_event_id = NULL WHERE id = ?", (date_id,))


def push(db, api=None):
    """Send every accepted deadline that is not on the calendar yet."""
    api = api or service()
    if api is None:
        return 0, "not set up"

    rows = db.execute(
        """SELECT d.*, c.name AS course_name FROM dates d
           JOIN courses c ON c.id = d.course_id
           WHERE d.status = 'accepted' AND d.gcal_event_id IS NULL
             AND d.due_date IS NOT NULL"""
    ).fetchall()

    added = 0
    for row in rows:
        if add(api, db, row):
            added += 1
            print(f"  added  {row['due_date']}  {row['title'][:44]}")
    db.commit()
    return added, None


def mine(api, where=None):
    """Events this app created, from a year back to a year ahead."""
    where = where or target()
    window_start = (date.today() - timedelta(days=365)).isoformat() + "T00:00:00Z"
    window_end = (date.today() + timedelta(days=365)).isoformat() + "T00:00:00Z"
    found, page = [], None
    while True:
        result = api.events().list(
            calendarId=where, timeMin=window_start, timeMax=window_end,
            q=MARKER, singleEvents=True, maxResults=250, pageToken=page).execute()
        for e in result.get("items", []):
            # q is a fuzzy search, so confirm the marker really is there.
            if MARKER in (e.get("description") or ""):
                found.append(e)
        page = result.get("nextPageToken")
        if not page:
            break
    return found


def main():
    argv = sys.argv[1:]

    if "--scopes" in argv:
        if not TOKEN_FILE.exists():
            print("  No token yet. Run: python gcal.py --setup")
            return
        granted = json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("scopes", [])
        print("\n  Google granted this app:")
        for sc in granted:
            print(f"    {sc}")
        print("\n  Needed:")
        for sc in SCOPES:
            mark = "yes" if sc in granted else "NO  <-- missing"
            print(f"    {mark:<16} {sc}")
        return

    if "--setup" in argv:
        api = service(interactive=True, force="--force" in argv)
        if api is None:
            return
        TOKEN_FILE.exists() and print(f"\n  Authorised. Token saved to {TOKEN_FILE.name}")
        print("  Now run:  python gcal.py --push")
        return

    api = service()
    if api is None:
        print("  Not authorised yet. Run:  python gcal.py --setup")
        return

    if "--separate" in argv or "--primary" in argv:
        prefs = store.load_prefs()
        old_target = target()

        if "--primary" in argv:
            new_target, label = CALENDAR, "your main calendar"
        else:
            new_target, label = ensure_calendar(api), f'"{CALENDAR_NAME}"'

        if new_target == old_target:
            print(f"  Already using {label}.")
            return

        # Move rather than delete and recreate, so reminders, colours and
        # anything you edited by hand survive the change.
        moving = mine(api, old_target)
        print(f"  Moving {len(moving)} event(s) to {label}...")
        for e in moving:
            try:
                api.events().move(calendarId=old_target, eventId=e["id"],
                                  destination=new_target).execute()
            except Exception as err:
                print(f"    could not move {e.get('summary','')[:40]}: {err}")

        prefs["calendar_id"] = new_target
        store.save_prefs(prefs)
        print(f"  Done. New deadlines will go to {label}.")
        if new_target != CALENDAR:
            print("  In Google Calendar it appears under 'My calendars' --")
            print("  untick it to hide every deadline at once.")
        return

    if "--check" in argv:
        db = store.connect()
        accepted = db.execute(
            "SELECT COUNT(*) FROM dates WHERE status = 'accepted'").fetchone()[0]
        dated = db.execute(
            "SELECT COUNT(*) FROM dates WHERE status = 'accepted' "
            "AND due_date IS NOT NULL").fetchone()[0]
        linked = db.execute(
            "SELECT COUNT(*) FROM dates WHERE status = 'accepted' "
            "AND gcal_event_id IS NOT NULL").fetchone()[0]
        # An event id on something you did not keep means a stale event.
        orphan_ids = db.execute(
            "SELECT id, title FROM dates WHERE status != 'accepted' "
            "AND gcal_event_id IS NOT NULL").fetchall()
        missing = db.execute(
            "SELECT id, due_date, title FROM dates WHERE status = 'accepted' "
            "AND due_date IS NOT NULL AND gcal_event_id IS NULL").fetchall()
        db.close()

        on_calendar = len(mine(api))

        print(f"\n  Kept, in total            {accepted}")
        print(f"  ... of those, with a date {dated}   (undated ones cannot be scheduled)")
        print(f"  ... recorded as scheduled {linked}")
        print(f"  Events actually on Google  {on_calendar}")

        if dated == linked == on_calendar and not orphan_ids:
            print("\n  Everything matches.")
            return

        print()
        if missing:
            print(f"  {len(missing)} kept but never sent -- run: python gcal.py --push")
            for row in missing[:8]:
                print(f"      {row['due_date']}  {row['title'][:44]}")
        if orphan_ids:
            print(f"  {len(orphan_ids)} event(s) for cards you no longer keep:")
            for row in orphan_ids[:8]:
                print(f"      {row['title'][:52]}")
        if on_calendar != linked:
            print(f"  Google has {on_calendar} but {linked} are recorded here --")
            print("  an event was probably deleted in Google Calendar directly.")
        return

    if "--list" in argv:
        events = mine(api)
        where = target()
        print(f"\n  {len(events)} event(s) on "
              f"{'your main calendar' if where == CALENDAR else CALENDAR_NAME}:\n")
        for e in events:
            when = e["start"].get("dateTime") or e["start"].get("date")
            print(f"    {when[:16]}  {e.get('summary', '')[:52]}")
        if events:
            print("\n  Remove them all:  python gcal.py --remove-all")
        return

    if "--remove-all" in argv:
        events = mine(api)
        if not events:
            print("  Nothing of ours on the calendar.")
            return
        print(f"  Removing {len(events)} event(s)...")
        for e in events:
            try:
                api.events().delete(calendarId=target(), eventId=e["id"]).execute()
            except Exception as err:
                print(f"    failed: {err}")
        db = store.connect()
        db.execute("UPDATE dates SET gcal_event_id = NULL")
        db.commit()
        db.close()
        print("  Done. Your calendar is back to how it was.")
        return

    db = store.connect()
    added, problem = push(db, api)
    db.close()
    if problem:
        print(f"  {problem}")
    else:
        print(f"\n  {added} event(s) added." if added else "\n  Nothing new to add.")


if __name__ == "__main__":
    main()
