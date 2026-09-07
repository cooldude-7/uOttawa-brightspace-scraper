"""
Phase 0 probe: does Brightspace's REST API answer a student session?

Opens a real browser so you can log in with MFA, borrows the resulting
Brightspace cookies, then tries every endpoint the scraper design depends on
and writes a report.

    python -m pip install playwright httpx
    python -m playwright install chromium
    python probe.py

Nothing is written to Brightspace. Every request is a GET.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

BASE = "https://uottawa.brightspace.com"
OUT = Path(__file__).parent
LOGIN_TIMEOUT_S = 300


# ---------------------------------------------------------------- login

async def get_cookies():
    """Open a browser, wait for a real login, return Brightspace cookies only."""
    print("Opening a browser. Log into Brightspace and approve the MFA prompt.")
    print("This script waits; it does not type anything for you.\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(f"{BASE}/d2l/home", wait_until="domcontentloaded")

        got = None
        for elapsed in range(LOGIN_TIMEOUT_S):
            cookies = await ctx.cookies()
            if any(c["name"] == "d2lSessionVal" for c in cookies):
                got = cookies
                print(f"\nLogged in (detected after {elapsed}s). Closing browser.\n")
                await asyncio.sleep(2)
                got = await ctx.cookies()
                break
            if elapsed and elapsed % 15 == 0:
                print(f"  still waiting... {elapsed}s")
            await asyncio.sleep(1)

        await browser.close()

    if not got:
        sys.exit("Timed out waiting for login. Nothing probed.")

    # Brightspace-scoped cookies only -- Microsoft session cookies are
    # deliberately left behind. See PLAN.md section 2.
    kept = {
        c["name"]: c["value"]
        for c in got
        if "brightspace.com" in c.get("domain", "") or "uottawa" in c.get("domain", "")
    }
    print(f"Kept {len(kept)} Brightspace cookies: {', '.join(sorted(kept))}\n")
    return kept


# ---------------------------------------------------------------- probing

def probe(client, path, label):
    """GET one endpoint, return a result dict. Never raises."""
    try:
        r = client.get(BASE + path, timeout=30.0)
    except Exception as e:
        return {"label": label, "path": path, "status": None, "error": repr(e)}

    ctype = r.headers.get("content-type", "")
    result = {
        "label": label,
        "path": path,
        "status": r.status_code,
        "content_type": ctype,
        "bytes": len(r.content),
    }

    if "json" in ctype:
        try:
            data = r.json()
            result["json"] = True
            result["shape"] = describe(data)
            result["raw_head"] = json.dumps(data)[:600]
        except Exception as e:
            result["json"] = False
            result["error"] = f"declared json but failed to parse: {e!r}"
    else:
        result["json"] = False
        # An HTML login page is the classic "your session did not carry" tell.
        head = r.text[:300].lower()
        result["looks_like_login"] = "sign in" in head or "login" in head
        result["raw_head"] = r.text[:300]

    return result


def describe(data):
    """One-line shape summary so the report stays readable."""
    if isinstance(data, list):
        if not data:
            return "empty list"
        first = data[0]
        keys = sorted(first.keys())[:12] if isinstance(first, dict) else type(first).__name__
        return f"list[{len(data)}], first item keys: {keys}"
    if isinstance(data, dict):
        return f"object, keys: {sorted(data.keys())[:12]}"
    return type(data).__name__


def main():
    cookies = asyncio.run(get_cookies())

    client = httpx.Client(
        cookies=cookies,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        },
        follow_redirects=True,
    )

    results = []

    # --- 1. What API versions does this instance serve? ---------------
    print("Probing API versions...")
    versions = probe(client, "/d2l/api/versions/", "API versions")
    results.append(versions)

    lp, le = "1.30", "1.60"          # sensible fallbacks
    try:
        r = client.get(BASE + "/d2l/api/versions/", timeout=30.0)
        for product in r.json():
            code = product.get("ProductCode")
            latest = product.get("LatestVersion")
            if code == "lp" and latest:
                lp = latest
            elif code == "le" and latest:
                le = latest
        print(f"  latest lp={lp}  le={le}")
    except Exception as e:
        print(f"  could not read versions ({e!r}); falling back to lp={lp} le={le}")

    # --- 2. Identity and enrollments ----------------------------------
    print("Probing identity and enrollments...")
    results.append(probe(client, f"/d2l/api/lp/{lp}/users/whoami", "whoami"))
    results.append(probe(client, f"/d2l/api/lp/{lp}/enrollments/myenrollments/", "my enrollments"))

    # --- 3. Does an XSRF token exist / is it needed? -------------------
    results.append(probe(client, "/d2l/lp/auth/xsrf-tokens", "XSRF token endpoint"))

    # --- 4. Per-course endpoints the scraper needs --------------------
    org_units = []
    try:
        r = client.get(BASE + f"/d2l/api/lp/{lp}/enrollments/myenrollments/", timeout=30.0)
        for item in r.json().get("Items", []):
            ou = item.get("OrgUnit", {})
            if ou.get("Type", {}).get("Code") == "Course Offering":
                org_units.append((ou.get("Id"), ou.get("Name")))
    except Exception as e:
        print(f"  could not enumerate courses ({e!r})")

    print(f"Found {len(org_units)} course offerings.")
    for oid, name in org_units[:3]:                     # 3 courses is enough to prove it
        print(f"  probing course {oid} ({name})...")
        for path, label in [
            (f"/d2l/api/le/{le}/{oid}/content/root/",        "content root"),
            (f"/d2l/api/le/{le}/{oid}/news/",                "announcements"),
            (f"/d2l/api/le/{le}/{oid}/dropbox/folders/",     "assignments"),
            (f"/d2l/api/le/{le}/{oid}/quizzes/",             "quizzes"),
            (f"/d2l/api/le/{le}/{oid}/grades/",              "grades"),
            (f"/d2l/api/le/{le}/{oid}/calendar/events/myEvents/", "calendar events"),
        ]:
            results.append(probe(client, path, f"[{oid}] {label}"))

    write_report(results, org_units, lp, le)


def write_report(results, org_units, lp, le):
    ok = [r for r in results if r.get("status") == 200 and r.get("json")]
    denied = [r for r in results if r.get("status") in (401, 403)]
    missing = [r for r in results if r.get("status") == 404]
    html = [r for r in results if r.get("status") == 200 and not r.get("json")]

    lines = [
        "# Phase 0 probe report",
        "",
        f"Run: {datetime.now().isoformat(timespec='seconds')}",
        f"API versions used: lp={lp}, le={le}",
        f"Course offerings found: {len(org_units)}",
        "",
        "## Verdict",
        "",
    ]

    if ok and not html:
        lines += [
            "**The REST API answers a student session.** Proceed with the JSON design in",
            "PLAN.md -- no headless browser needed on the Pi.",
        ]
    elif html:
        lines += [
            "**Some endpoints returned HTML instead of JSON.** If those are login pages, the",
            "session did not carry; if they are real pages, those resources are UI-only and",
            "need an HTML fallback. Check `looks_like_login` below.",
        ]
    else:
        lines += [
            "**The API did not answer.** Fall back to authenticated HTML fetch + parsing.",
            "This changes the scraper design -- revisit PLAN.md section 2 before building.",
        ]

    lines += [
        "",
        f"- {len(ok)} endpoints returned usable JSON",
        f"- {len(denied)} denied (401/403) -- student role lacks permission",
        f"- {len(missing)} not found (404) -- wrong version or not exposed",
        f"- {len(html)} returned non-JSON",
        "",
        "## Courses",
        "",
    ]
    for oid, name in org_units:
        lines.append(f"- `{oid}` — {name}")

    lines += ["", "## Endpoint results", ""]
    for r in results:
        status = r.get("status")
        mark = "OK  " if status == 200 and r.get("json") else "FAIL"
        lines.append(f"### {mark}  {r['label']}")
        lines.append("")
        lines.append(f"- `{r['path']}`")
        lines.append(f"- status: {status}  ·  content-type: {r.get('content_type','n/a')}")
        if r.get("shape"):
            lines.append(f"- shape: {r['shape']}")
        if r.get("looks_like_login"):
            lines.append("- **looks like a login page — session did not carry**")
        if r.get("error"):
            lines.append(f"- error: `{r['error']}`")
        if r.get("raw_head"):
            lines.append("")
            lines.append("```")
            lines.append(r["raw_head"])
            lines.append("```")
        lines.append("")

    path = OUT / "phase0-report.md"
    path.write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"  {len(ok)} OK   {len(denied)} denied   {len(missing)} missing   {len(html)} non-JSON")
    print(f"  Report written to: {path}")
    print("=" * 60)
    print("\nThe report contains your course names and possibly grades.")
    print("It is gitignored. Send it to me and I'll read the verdict.")


if __name__ == "__main__":
    main()
