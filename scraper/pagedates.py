r"""
Dates Brightspace shows you but does not put in its API.

A quiz or assignment restricted to particular students keeps its dates in a
per-user "special access" override. The item's own DueDate and EndDate come
back null, and the endpoint holding the override answers 403 to a student
role. So the only place the date exists, for us, is the page Brightspace
renders:

    ... alt="Special Access is required" ... Available until Sep 15, 2026 1:00 PM

GNG1106's LAB 1 is the case that found this -- a real deadline a week out,
invisible to everything the collector read, because nothing was wrong with
the code and nothing was wrong with the request. The field was simply empty.

Parsing HTML is the fallback PLAN.md section 9 kept in reserve. It is used
here as narrowly as it can be: only to supply a date that is otherwise
missing, never to override one the API gave, and every date it finds carries
a note saying it came from the page, so a wrong one is visible on the card
rather than silently trusted.

Dates on the page are already in Ottawa time -- unlike the API's, which are
UTC and need converting. Do not put these through exact.to_local().
"""

import html as html_lib
import re

BASE_PAGES = {
    "quizzes": "/d2l/lms/quizzing/user/quizzes_list.d2l",
    "assignments": "/d2l/lms/dropbox/user/folders_list.d2l",
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# "Due on Sep 19, 2026 5:45 PM" / "Available until Sep 15, 2026 1:00 PM"
PHRASE = re.compile(
    r"(Due on|Available until|Ends on|Ends|Available on|Starts on|Starts)\s+"
    r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})"
    r"(?:\s+(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?)?",
    re.I)

# Which phrasing actually means "you have missed it after this".
# "Available on" and "Starts" are when it opens, not when it is owed.
DEADLINE_WORDS = ("due on", "available until", "ends on", "ends")


def _text(fragment):
    """Tags out, entities decoded, whitespace collapsed."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment)
    t = re.sub(r"<[^>]+>", " ", t)
    return " ".join(html_lib.unescape(t).split())


def _rows(page):
    """The page split at table rows, as plain text, largest chunks first.

    Anchoring on rows keeps a date attached to the item it belongs to. A
    whole-page regex would happily give one quiz another quiz's deadline.
    """
    return [_text(r) for r in re.split(r"(?i)<tr[\s>]", page)]


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _parse(match):
    word, mon, day, year, hh, mm, ap = match.groups()
    month = MONTHS.get(mon[:3].lower())
    if not month:
        return None
    at = None
    if hh:
        hour = int(hh) % 12 + (12 if ap.lower() == "p" else 0)
        at = f"{hour:02d}:{int(mm):02d}"
    return {
        "label": word.strip(),
        "date": f"{int(year):04d}-{month:02d}-{int(day):02d}",
        "time": at,
        "deadline": word.strip().lower() in DEADLINE_WORDS,
    }


def page(client, base, org_unit, kind):
    """The rendered list page for one course, or None."""
    path = BASE_PAGES.get(kind)
    if not path:
        return None
    try:
        r = client.get(base + path, params={"ou": org_unit})
    except Exception:
        return None
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        return None
    return r.text


def found_on_page(client, base, org_unit, kind, names):
    """{name: date-dict} for the names that show a deadline on the page.

    Matching is by the item's own name inside a table row, because the
    numeric id is not reliably in every row and a name is what a person
    would use to check the result by eye.
    """
    body = page(client, base, org_unit, kind)
    if not body:
        return {}

    rows = _rows(body)
    wanted = {_norm(n): n for n in names if _norm(n)}
    out = {}

    for row in rows:
        low = _norm(row)
        for key, original in wanted.items():
            if original in out or key not in low:
                continue
            dates = [d for d in (_parse(m) for m in PHRASE.finditer(row)) if d]
            deadlines = [d for d in dates if d["deadline"]]
            if deadlines:
                # "Due on" beats "Available until" when a row carries both.
                deadlines.sort(key=lambda d: d["label"].lower() != "due on")
                out[original] = deadlines[0]
    return out


def attach(client, base, org_unit, items, kind, name_key="Name",
           date_keys=("DueDate", "EndDate")):
    """Fill in `DisplayDue` on items the API gave no date for.

    Only ever adds. An item with a real API date is left alone, so this
    cannot overwrite something Brightspace stated properly.
    """
    undated = [it for it in items
               if isinstance(it, dict)
               and not any(it.get(k) for k in date_keys)
               and it.get(name_key)]
    if not undated:
        return 0

    hits = found_on_page(client, base, org_unit, kind,
                         [it[name_key] for it in undated])
    filled = 0
    for it in undated:
        got = hits.get(it[name_key])
        if got:
            it["DisplayDue"] = got
            filled += 1
    return filled
