r"""
Work out what is actually broken, in order, and say what to do about it.

    python doctor.py          run every check and print a verdict

Costs nothing -- no AI call anywhere in here.

WHY THIS EXISTS

Every failure in this project has looked like a different failure. A dead
router printed "your login expired". A dead cookie printed "renewed
successfully". Five separate Google problems printed "not authorised yet".
Each time the message sent someone to do the wrong thing, and twice that
wrong thing was "copy session.json between machines", which is copying a
bearer token for a whole university account to fix a router.

The fix is not a better message at the point of failure -- the code there
does not know enough. It is to check the layers in order and report the
first one that is actually broken. A session cannot be judged until the
network is known good; a scrape cannot be judged until the session is.

ORDER MATTERS AND IS THE WHOLE DESIGN

    stick -> network -> Brightspace session -> API key -> calendar -> freshness

Each check may only run once everything before it passed, so the advice it
gives can be trusted. "Your Brightspace login expired" is sound advice
after `reachable()` says the wire works, and dangerous nonsense before it.
"""

import shutil
import sys
from datetime import datetime, timezone

import paths
import store

OK, BAD, SKIP = "ok", "FAIL", "--"


class Report:
    """Checks in order, and the first real failure, which is the verdict."""

    def __init__(self):
        self.rows = []
        self.problem = None
        self.fix = None

    def add(self, name, state, detail, fix=None):
        self.rows.append((name, state, detail))
        if state == BAD and self.problem is None:
            self.problem, self.fix = detail, fix
        return state == OK

    def stopped(self):
        return self.problem is not None

    def skip(self, name, why="not checked -- something earlier is broken"):
        self.rows.append((name, SKIP, why))


def check_stick(r):
    """paths.py already refuses to run without the marker, so getting this
    far proves the stick is mounted. What it cannot prove is that there is
    room left on it -- a full disk fails as writes, not as an absence."""
    try:
        usage = shutil.disk_usage(paths.DATA)
    except OSError as e:
        return r.add("data disk", BAD, f"cannot read {paths.DATA}: {e}",
                     "Check the USB stick is plugged in and mounted:  df -h /mnt/data")
    free_gb = usage.free / 1e9
    if free_gb < 0.5:
        return r.add("data disk", BAD,
                     f"only {free_gb:.2f} GB free on {paths.DATA}",
                     "The stick is full. Old files in _originals/ are the "
                     "usual cause -- they can be deleted, the text is kept "
                     "separately in extracted/.")
    return r.add("data disk", OK, f"{free_gb:.0f} GB free on {paths.DATA}")


def check_network(r):
    """Any HTTP answer at all counts. A 403 or a redirect still proves the
    wire works; whether we are allowed in is the next check's question."""
    import collect
    client = collect.make_client(collect.read_session())
    try:
        ok, why = collect.reachable(client)
    finally:
        client.close()
    if not ok:
        return r.add("network", BAD, f"cannot reach Brightspace -- {why}",
                     "The Pi has no internet. It is almost always the router: "
                     "check the TP-Link has its uplink, then try again. Do NOT "
                     "copy a new session.json across -- nothing is wrong with "
                     "your login.")
    return r.add("network", OK, "Brightspace answered")


def check_session(r):
    """The file existing, being the right shape, and actually working are
    three different things, and only the third one matters."""
    import collect
    if not paths.SESSION.exists():
        return r.add("Brightspace login", BAD, "no session file at all",
                     "Nothing has ever logged in here. On your laptop:  "
                     "python update.py  -- then copy scraper/session.json "
                     f"to {paths.SESSION}")

    size = paths.SESSION.stat().st_size
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        paths.SESSION.stat().st_mtime, timezone.utc)
    shape = f"{size:,} bytes, written {age.days}d ago"

    if size < 1000:
        return r.add("Brightspace login", BAD,
                     f"{shape} -- too small to hold the sign-on cookies",
                     "This is the old Brightspace-only session format. It can "
                     "scrape once and then can never renew itself. Move it "
                     "aside and log in again on your laptop:  python update.py")

    client = collect.make_client(collect.read_session())
    try:
        if collect.session_works(client):
            return r.add("Brightspace login", OK, f"working ({shape})")
        renewed = collect.refresh_session(client)
    finally:
        client.close()

    if renewed:
        return r.add("Brightspace login", OK,
                     f"expired and renewed itself just now ({shape})")
    return r.add("Brightspace login", BAD,
                 f"expired, and renewing failed ({shape})",
                 "The uOttawa sign-on cookies have run out -- they last a "
                 "couple of weeks, and no amount of renewing gets past that. "
                 "On your LAPTOP, which has a browser:  python update.py  "
                 f"then copy scraper/session.json to {paths.SESSION} on the Pi. "
                 "The network and the stick are both fine, so this really is "
                 "the login this time.")


def check_api_key(r):
    """Reading documents is the only thing here that costs money, and it is
    the product. A missing key is not the same as an empty balance."""
    if not paths.API_KEY.exists():
        return r.add("AI key", BAD, f"no key file at {paths.API_KEY}",
                     "Deadlines in prose cannot be read without it. Put the "
                     f"key in {paths.API_KEY}.")
    key = paths.API_KEY.read_text(encoding="utf-8").strip()
    if len(key) < 24:
        return r.add("AI key", BAD, f"key file holds only {len(key)} characters",
                     "That is too short to be a real key -- it may have been "
                     "truncated when it was pasted.")
    return r.add("AI key", OK, f"present ({key[:14]}...{key[-4:]})")


def check_calendar(r):
    """Already names which of its five problems it hit, so pass it through."""
    try:
        import gcal
    except Exception as e:
        return r.add("Google Calendar", SKIP, f"not checked: {e}")
    try:
        if gcal.service():
            return r.add("Google Calendar", OK, "authorised")
    except Exception as e:
        return r.add("Google Calendar", BAD, f"{type(e).__name__}: {e}",
                     "Re-authorise with:  python gcal.py --setup --port 8765")
    # service() prints its own reason above. Accepted deadlines still get
    # found and stored without it, so this is never the headline problem.
    return r.add("Google Calendar", BAD,
                 "not authorised (the reason is printed just above)",
                 "Re-authorise with:  python gcal.py --setup --port 8765 -- "
                 "and note a Google app left in Testing expires its tokens "
                 "every 7 days no matter what.")


def check_freshness(r):
    db = store.connect()
    try:
        last = store.last_success(db)
        failed_at, error = store.last_error(db)
    finally:
        db.close()
    if not last:
        return r.add("last good scrape", BAD, "there has never been one",
                     "Run  python update.py  and watch what it says.")
    try:
        hours = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        return r.add("last good scrape", OK, last)
    when = f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f} days ago"
    if hours > 24:
        return r.add("last good scrape", BAD, f"{when} ({last[:16]})",
                     "Everything above passed, so try a scrape by hand and "
                     "read the output:  python update.py")
    return r.add("last good scrape", OK, when)


def diagnose():
    """Every check, in order, stopping the chain at the first real break."""
    r = Report()
    order = [("data disk", check_stick), ("network", check_network),
             ("Brightspace login", check_session), ("AI key", check_api_key),
             ("Google Calendar", check_calendar),
             ("last good scrape", check_freshness)]
    for name, run in order:
        if r.stopped():
            r.skip(name)
            continue
        try:
            run(r)
        except Exception as e:
            r.add(name, BAD, f"the check itself failed -- {type(e).__name__}: {e}")
    return r


def verdict(r):
    """One line for the app's banner, in place of a raw exception."""
    if not r.problem:
        return None
    first = (r.fix or "").split(". ")[0]
    return f"{r.problem}. {first}." if first else r.problem


def main():
    r = diagnose()
    print("\n-- what is working " + "-" * 41)
    for name, state, detail in r.rows:
        mark = {OK: "  ok  ", BAD: " FAIL ", SKIP: "  --  "}[state]
        print(f"{mark}{name:<20} {detail}")

    print("\n-- what to do " + "-" * 46)
    if not r.problem:
        print("  Nothing. Every check passed.\n")
        return 0
    print(f"  {r.problem}\n")
    for line in (r.fix or "No fix is known for this one.").split("  "):
        if line.strip():
            print(f"  {line.strip()}")
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
