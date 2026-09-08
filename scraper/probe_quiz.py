r"""
Find where Brightspace keeps a date it shows you but does not put on the item.

    python probe_quiz.py 2026-09-15

GNG1106's "LAB 1" displays "Available until Sep 15, 2026 1:00 PM" while its
own EndDate and DueDate are both null -- it is flagged
AllowOnlyUsersWithSpecialAccess, so the real date is an override stored
somewhere else. This tries the plausible places and reports which of them
actually contains the date you pass in.

Nothing here is authoritative. It is a probe: endpoint names are guesses,
404s are expected and are the point. Whatever comes back 200 *and* contains
the date is where the answer lives.
"""

import json
import sys

import collect
from collect import BASE, LE, LP


def body_of(client, path, **params):
    try:
        r = client.get(BASE + path, params=params or None)
    except Exception as e:
        return None, f"error {e!r}"
    kind = r.headers.get("content-type", "")
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    if "json" not in kind:
        return r.text, f"200 but {kind.split(';')[0] or 'no type'}"
    return r.text, "200 json"


def main():
    wanted = sys.argv[1] if len(sys.argv) > 1 else "2026-09-15"
    client = collect.get_client()
    courses, _ = collect.current_courses(client)

    print(f"\nLooking for {wanted!r} in places Brightspace might hide it.\n")

    for course in courses:
        ou = course["id"]
        quizzes = collect.get(client, f"/d2l/api/le/{LE}/{ou}/quizzes/") or []
        if isinstance(quizzes, dict):
            quizzes = quizzes.get("Objects", quizzes.get("Items", []))
        if not quizzes:
            continue

        print("=" * 66)
        print(course["name"])

        for q in quizzes:
            qid = q.get("QuizId")
            print(f"\n  quiz {qid}  {str(q.get('Name'))[:44]}")
            print(f"    on the quiz itself: Due={q.get('DueDate')} "
                  f"End={q.get('EndDate')} Start={q.get('StartDate')}")
            if q.get("AllowOnlyUsersWithSpecialAccess"):
                print("    flagged AllowOnlyUsersWithSpecialAccess")

            candidates = [
                f"/d2l/api/le/{LE}/{ou}/quizzes/{qid}",
                f"/d2l/api/le/{LE}/{ou}/quizzes/{qid}/",
                f"/d2l/api/le/{LE}/{ou}/quizzes/{qid}/specialaccesses/",
                f"/d2l/api/le/{LE}/{ou}/quizzes/{qid}/specialaccess/",
                f"/d2l/api/le/{LE}/{ou}/quizzes/{qid}/restrictions/",
                f"/d2l/api/le/{LE}/{ou}/quizzes/{qid}/attempts/",
            ]
            for path in candidates:
                text, note = body_of(client, path)
                hit = " <-- CONTAINS THE DATE" if text and wanted in text else ""
                print(f"      {note:<22} {path.replace(f'/d2l/api/le/{LE}/{ou}', '...')}{hit}")

        # Course-wide places the date might surface instead.
        print("\n  course-wide:")
        wide = [
            (f"/d2l/api/le/{LE}/{ou}/calendar/events/myEvents/",
             {"startDateTime": "2026-08-15T00:00:00.000Z",
              "endDateTime": "2027-01-31T00:00:00.000Z"}),
            (f"/d2l/api/le/{LE}/{ou}/calendar/events/",
             {"startDateTime": "2026-08-15T00:00:00.000Z",
              "endDateTime": "2027-01-31T00:00:00.000Z"}),
            (f"/d2l/api/le/{LE}/{ou}/content/myItems/", {}),
            (f"/d2l/api/lp/{LP}/enrollments/myenrollments/", {}),
        ]
        for path, params in wide:
            text, note = body_of(client, path, **params)
            hit = " <-- CONTAINS THE DATE" if text and wanted in text else ""
            print(f"      {note:<22} {path.replace(f'/d2l/api/le/{LE}/{ou}', '...')}{hit}")

    print("\nAnything marked CONTAINS THE DATE is where to read it from.")
    print("If nothing is marked, the API does not expose it and the date only")
    print("exists in the page Brightspace renders -- a different problem.\n")


if __name__ == "__main__":
    main()
