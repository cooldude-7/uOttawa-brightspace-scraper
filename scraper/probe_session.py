r"""Show exactly where session renewal stops.

The Pi cannot renew its Brightspace session and the log only says
"could not renew". Every failure path in collect.refresh_session()
returns False without saying which one it was, so this walks the same
chain out loud: what cookies we hold, what each hop returns, and what
the page we got stuck on actually is.

    BRIGHTSPACE_DATA=/mnt/data ~/uOttawa-brightspace-scraper/.venv/bin/python probe_session.py

Prints no cookie values -- names and domains only. The session file is a
bearer token for the whole uOttawa account.
"""

import sys
from datetime import datetime, timezone

import collect
import paths


def main():
    print(f"\nsession file: {paths.SESSION}")
    if not paths.SESSION.exists():
        sys.exit("  does not exist -- nothing to renew.")
    size = paths.SESSION.stat().st_size
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        paths.SESSION.stat().st_mtime, timezone.utc)
    print(f"  {size:,} bytes, last written {age.days}d {age.seconds // 3600}h ago")
    if size < 1000:
        print("  ! under 1 KB -- this looks like the old Brightspace-only format,")
        print("    which holds no sign-on cookies and can never renew itself.")

    jar = collect.read_session()
    print(f"\ncookies held, by domain ({sum(len(v) for v in jar.values())} total):")
    for domain in sorted(jar):
        print(f"  {domain:<34} {', '.join(sorted(jar[domain]))}")

    client = collect.make_client(jar)

    print("\nis the current session still good?")
    if collect.session_works(client):
        print("  yes -- nothing to renew. The failure was temporary.")
        return
    print("  no, as expected. Walking the sign-on chain.\n")

    r = client.get(f"{collect.BASE}/d2l/home")
    print(f"  GET /d2l/home -> {r.status_code}")
    for hop in r.history:
        print(f"       via {hop.status_code}  {str(hop.url)[:100]}")
    print(f"       landed on {str(r.url)[:110]}")

    for i in range(1, 9):
        if "d2lSessionVal" in client.cookies:
            print("\n  got a Brightspace session cookie.")
            break
        parser = collect.AutoForm()
        parser.feed(r.text)
        if not parser.action or not parser.fields:
            print(f"\n  stop at hop {i}: no self-posting form on this page.")
            print(f"      form action : {parser.action!r}")
            print(f"      hidden fields: {sorted(parser.fields) or 'none'}")
            print(f"      page title   : {title_of(r.text)!r}")
            print(f"      {len(r.text):,} bytes; first text on it:")
            for line in visible_lines(r.text)[:12]:
                print(f"        {line}")
            break
        action = parser.action
        if action.startswith("/"):
            action = f"{r.url.scheme}://{r.url.host}{action}"
        print(f"  hop {i}: POST {action[:96]}")
        print(f"          fields: {', '.join(sorted(parser.fields))}")
        r = client.post(action, data=parser.fields)
        print(f"          -> {r.status_code}, now at {str(r.url)[:90]}")

    print(f"\nd2lSessionVal present: {'d2lSessionVal' in client.cookies}")
    print(f"session_works():       {collect.session_works(client)}")


def title_of(html):
    import re
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return m.group(1).strip()[:120] if m else None


def visible_lines(html):
    """Rough text of a page -- enough to recognise a login prompt."""
    import re
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 2 and not line.startswith("{"):
            out.append(line[:100])
    return out


if __name__ == "__main__":
    main()
