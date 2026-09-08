r"""
Puts accepted deadlines into your Google Calendar.

    python -m pip install google-auth-oauthlib google-api-python-client
    python gcal.py --setup      authorise once, in a browser
    python gcal.py --push       send everything already accepted
    python gcal.py --list       show what this app has put there
    python gcal.py --remove-all take it all back out again

Events go in your main calendar, tagged in their description so this app
can always find its own again. Nothing else is ever touched.
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import store

HERE = Path(__file__).parent
CLIENT_FILE = HERE / "google_client.json"      # downloaded from Google Cloud
TOKEN_FILE = HERE / "google_token.json"        # written after you approve

# Permission to manage events, not to read or reshape your calendars.
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CALENDAR = "primary"
TIMEZONE = "America/Toronto"

# Every event carries this so the app can find, update and remove exactly
# what it created and never anything you added yourself.
MARKER = "[brightspace-scraper]"


def service(interactive=False):
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
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
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
            calendarId=CALENDAR, body=event_body(row)).execute()
    except Exception as e:
        print(f"  could not add {row['title'][:40]}: {e}")
        return None
    db.execute("UPDATE dates SET gcal_event_id = ? WHERE id = ?",
               (created["id"], row["id"]))
    return created["id"]


def remove(api, db, date_id, event_id):
    try:
        api.events().delete(calendarId=CALENDAR, eventId=event_id).execute()
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


def mine(api):
    """Events this app created, from a year back to a year ahead."""
    window_start = (date.today() - timedelta(days=365)).isoformat() + "T00:00:00Z"
    window_end = (date.today() + timedelta(days=365)).isoformat() + "T00:00:00Z"
    found, page = [], None
    while True:
        result = api.events().list(
            calendarId=CALENDAR, timeMin=window_start, timeMax=window_end,
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

    if "--setup" in argv:
        api = service(interactive=True)
        if api is None:
            return
        TOKEN_FILE.exists() and print(f"\n  Authorised. Token saved to {TOKEN_FILE.name}")
        print("  Now run:  python gcal.py --push")
        return

    api = service()
    if api is None:
        print("  Not authorised yet. Run:  python gcal.py --setup")
        return

    if "--list" in argv:
        events = mine(api)
        print(f"\n  {len(events)} event(s) put there by this app:\n")
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
                api.events().delete(calendarId=CALENDAR, eventId=e["id"]).execute()
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
