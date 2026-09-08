r"""
Find where Brightspace keeps a date it shows you but does not publish.

    python probe_quiz.py 2026-09-15

GNG1106's "LAB 1" displays "Available until Sep 15, 2026 1:00 PM" while the
quiz object returns EndDate null and DueDate null. It is flagged
AllowOnlyUsersWithSpecialAccess, so the real date is a per-user override.

Round 1 established that the API will not give it to a student: the
specialaccess endpoint answers 403, not 404 -- it exists and is closed to us.
So this round asks two further questions. Is there a newer API version that
exposes it? And failing that, does the ordinary web page carry it?

The date is searched for in several written forms, not just ISO. Round 1
looked only for "2026-09-15", which a rendered page would never contain --
so a hit could have been missed even at the right URL.
"""

import json
import re
import sys

import collect
from collect import BASE, LE, LP

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def forms(iso):
    """The same date as ISO, as Brightspace renders it, and near variants."""
    y, m, d = (int(x) for x in iso.split("-"))
    full, abbr = MONTHS[m - 1], MONTHS[m - 1][:3]
    return [iso, f"{abbr} {d}, {y}", f"{full} {d}, {y}",
            f"{abbr}. {d}, {y}", f"{d} {abbr} {y}", f"{m}/{d}/{y}"]


def fetch(client, path, **params):
    try:
        r = client.get(BASE + path, params=params or None)
    except Exception as e:
        return None, f"error {e!r}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    return r.text, f"200 {(r.headers.get('content-type','') or '').split(';')[0]}"


def report(client, label, path, needles, **params):
    text, note = fetch(client, path, **params)
    hit = ""
    if text:
        found = [n for n in needles if n in text]
        if found:
            hit = f"  <-- HAS IT ({found[0]!r})"
    print(f"      {note:<22} {label}{hit}")
    return text if hit else None


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else "2026-09-15"
    needles = forms(iso)
    client = collect.get_client()
    courses, _ = collect.current_courses(client)

    print(f"\nSearching for {iso} written any of these ways:")
    for n in needles:
        print(f"    {n!r}")

    # ---------------------------------------------------------- versions
    print("\n" + "=" * 66)
    print("What API versions does this instance admit to?")
    text, note = fetch(client, "/d2l/api/versions/")
    newest_le = LE
    if text:
        try:
            for product in json.loads(text):
                code = product.get("ProductCode")
                vers = [v.get("Version") for v in product.get("SupportedVersions", [])]
                if code in ("le", "lp"):
                    print(f"  {code}: latest {product.get('LatestVersion')}  "
                          f"({len(vers)} supported, highest few: {vers[-4:]})")
                if code == "le" and product.get("LatestVersion"):
                    newest_le = product["LatestVersion"]
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            print(f"  could not parse: {e!r}")
    else:
        print(f"  {note}")
    print(f"  pinned in collect.py: le={LE}   newest reported: {newest_le}")

    # ------------------------------------------------------------- quizzes
    for course in courses:
        ou = course["id"]
        quizzes = collect.get(client, f"/d2l/api/le/{LE}/{ou}/quizzes/") or []
        if isinstance(quizzes, dict):
            quizzes = quizzes.get("Objects", quizzes.get("Items", []))
        if not quizzes:
            continue

        print("\n" + "=" * 66)
        print(course["name"])

        for q in quizzes:
            qid = q.get("QuizId")
            print(f"\n  quiz {qid}  {str(q.get('Name'))[:44]}")

            if newest_le != LE:
                print(f"    same quiz, but asked at le={newest_le}:")
                report(client, f"le={newest_le} .../quizzes/{qid}",
                       f"/d2l/api/le/{newest_le}/{ou}/quizzes/{qid}", needles)
                report(client, f"le={newest_le} .../quizzes/",
                       f"/d2l/api/le/{newest_le}/{ou}/quizzes/", needles)

        # ------------------------------------------------- the actual page
        print("\n  the page you looked at, as HTML:")
        for label, path, params in [
            ("quizzes_list.d2l", "/d2l/lms/quizzing/user/quizzes_list.d2l", {"ou": ou}),
            ("quizzes_list (alt)", "/d2l/lms/quizzing/user/quizzes_list.d2l",
             {"ou": ou, "isprv": "0"}),
            ("dropbox list", "/d2l/lms/dropbox/user/folders_list.d2l", {"ou": ou}),
            ("course home", "/d2l/home/" + str(ou), {}),
        ]:
            got = report(client, label, path, needles, **params)
            if got:
                # Show the sentence it sits in, so we know what to parse.
                for n in needles:
                    for m in re.finditer(re.escape(n), got):
                        chunk = re.sub(r"<[^>]+>", " ", got[max(0, m.start()-260):m.end()+90])
                        print("        ...", " ".join(chunk.split())[-230:])
                        break
                    else:
                        continue
                    break

    print("\nHAS IT marks where the date really lives.")
    print("If only the HTML has it, the API is closed and parsing that page is")
    print("the honest fix -- PLAN.md section 9 always kept that as the fallback.\n")


if __name__ == "__main__":
    main()
