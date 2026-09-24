r"""
Tell someone, off the Pi, when something is wrong.

    python notify.py --test      send a test message to your phone
    python notify.py --status    show what is configured

Two different jobs, because they fail in opposite ways.

A PUSH says what broke. `update.py` sends the doctor's verdict when a
scrape fails, so you learn about it on your phone rather than three days
later when you happen to open the app.

A HEARTBEAT says nothing at all, on purpose -- a plain request after every
good scrape. A service outside the house expects them and emails you when
they stop. This is the only half that can report a Pi that is off, or one
that has lost its internet, because by definition such a Pi cannot send
anything. The push above is useless there: the machine that would send it
is the machine that is broken. On 2026-09-23 the Pi kept its LAN and lost
its uplink for three days, and nothing said a word.

SET UP

Both are a single URL in a file, and both do nothing at all until the file
exists -- no account, no code change, nothing to break.

    /mnt/data/notify.txt      where to push failures
    /mnt/data/heartbeat.txt   where to ping after every good scrape

A URL here is a secret: anyone holding it can send you messages, or fake
your heartbeat. Kept beside api_key.txt in the data folder, never in git.

DELIBERATELY BORING

urllib from the standard library, not httpx: this runs in the failure
path, and something that reports a broken scrape must not be able to break
one. Every call swallows everything, is capped at ten seconds, and returns
whether it worked rather than raising.
"""

import sys
import urllib.error
import urllib.request

import paths
import store

NOTIFY = paths.DATA / "notify.txt"
HEARTBEAT = paths.DATA / "heartbeat.txt"
TIMEOUT = 10


def configured(path):
    """The URL in a file, or None. Blank lines and # comments ignored."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line.startswith("http"):
            return line
    return None


def post(url, body=b"", headers=None):
    """-> (worked, why not). Never raises, whatever the network does."""
    try:
        req = urllib.request.Request(url, data=body or None,
                                     headers=headers or {})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= r.status < 400, None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send(title, message):
    """Push one message. Does nothing, quietly, when nothing is configured."""
    url = configured(NOTIFY)
    if not url:
        return False
    ok, _why = post(url, str(message).encode("utf-8"),
                    {"Title": str(title)[:100], "Priority": "high",
                     "Tags": "warning"})
    return ok


def beat(ok=True):
    """Say the scrape finished. A failure pings <url>/fail where the service
    understands it, so a failing Pi is reported as failing rather than as
    silence -- silence is reserved for a Pi that cannot speak at all."""
    url = configured(HEARTBEAT)
    if not url:
        return False
    worked, _why = post(url if ok else url.rstrip("/") + "/fail")
    return worked


def is_new(db, verdict):
    """Has this exact problem already been reported?

    Without this the hourly timer sends fourteen identical notifications a
    day, and a notification you have learned to swipe away is not a
    notification. One per distinct problem: a new verdict, or the first
    failure after things had been working.
    """
    row = db.execute(
        """SELECT error FROM runs WHERE mode = 'update' AND finished_at IS NOT NULL
           ORDER BY id DESC LIMIT 1 OFFSET 1""").fetchone()
    return not row or (row["error"] or "") != (verdict or "")


def main(argv):
    if "--status" in argv or not argv:
        for what, path in (("failure push", NOTIFY), ("heartbeat", HEARTBEAT)):
            url = configured(path)
            where = f"{url.split('//')[-1][:38]}..." if url else "not set up"
            print(f"  {what:<14} {where}")
            if not url:
                print(f"                 put a URL in {path}")
        return 0

    if "--test" in argv:
        if not configured(NOTIFY):
            print(f"  Nothing configured. Put a URL in {NOTIFY} first.")
            return 1
        ok = send("Brightspace scraper", "This is a test. Everything is fine.")
        print("  sent -- check your phone" if ok else "  failed to send")
        return 0 if ok else 1

    if "--beat" in argv:
        print("  pinged" if beat(True) else "  no heartbeat URL configured")
        return 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
