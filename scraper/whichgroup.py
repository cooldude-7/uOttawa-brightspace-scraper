r"""
Asks Brightspace which sections and groups you are actually in.

    python whichgroup.py

Guessing a lab section from a due date hides real deadlines when it is
wrong. Brightspace stores the answer, so this reads it rather than
inferring it.
"""

from collect import LE, LP, current_courses, get, get_client, as_list


def me(client):
    who = get(client, f"/d2l/api/lp/{LP}/users/whoami") or {}
    return str(who.get("Identifier") or ""), who.get("FirstName", "")


def enrolled_ids(entry):
    """Group and section payloads spell the member list several ways."""
    for key in ("Enrollments", "Users", "UserIds"):
        value = entry.get(key)
        if isinstance(value, list):
            out = []
            for item in value:
                if isinstance(item, dict):
                    out.append(str(item.get("Identifier") or item.get("UserId") or ""))
                else:
                    out.append(str(item))
            return out
    return []


def main():
    client = get_client()
    my_id, name = me(client)
    if not my_id:
        raise SystemExit("Could not read your user id.")
    print(f"Looking up {name} (id {my_id})\n")

    courses, _ = current_courses(client)

    for course in courses:
        oid = course["id"]
        print("=" * 62)
        print(course["name"][:60])
        print("=" * 62)
        found = False

        # Sections: the registrar's split of a course (A00, C01, ...).
        for section in as_list(get(client, f"/d2l/api/lp/{LP}/{oid}/sections/")):
            sid = section.get("SectionId") or section.get("Identifier")
            detail = get(client, f"/d2l/api/lp/{LP}/{oid}/sections/{sid}") or section
            if my_id in enrolled_ids(detail):
                print(f"  SECTION: {section.get('Name') or section.get('Code')}")
                found = True

        # Groups: the professor's split for labs and teams (A1..A5).
        for cat in as_list(get(client, f"/d2l/api/lp/{LP}/{oid}/groupcategories/")):
            cid = cat.get("GroupCategoryId") or cat.get("Identifier")
            cname = cat.get("Name", "")
            groups = as_list(get(client, f"/d2l/api/lp/{LP}/{oid}/groupcategories/{cid}/groups/"))
            mine = [g for g in groups if my_id in enrolled_ids(g)]
            for g in mine:
                print(f"  GROUP:   {cname} -> {g.get('Name') or g.get('Code')}")
                found = True
            if groups and not mine:
                names = ", ".join(str(g.get("Name") or g.get("Code"))[:18] for g in groups[:8])
                print(f"  (not in any '{cname}' group yet -- exists: {names})")

        if not found:
            print("  no section or group membership visible")
        print()

    print("A GROUP line naming something like A3 or Thursday is your answer.")
    print("Nothing listed means the professor has not assigned groups in")
    print("Brightspace, and the section labels live only in the text.")


if __name__ == "__main__":
    main()
