r"""
Your marks, read out of what collect.py saved -- no request, no login, no cost.

    python grades.py            every course: each mark, and where you stand

collect.py asks Brightspace for three things per course and keeps them in
collected.json under "my_grades": your grade values, the gradebook's
categories, and the running final grade (often not released, which is fine).
This file only reshapes those; vault.py writes the result into
Courses/<course>/Grades.md and Dashboards/Grades.md.

WHAT THE "STANDING" IS, AND IS NOT

It is the share of the marks you could have earned *so far* that you did earn,
computed from the gradebook's own weights. It is not your final grade:
anything not yet graded is left out, not counted as zero, and a professor's
Brightspace weights do not always match the syllabus (some never enter the
final exam at all until it happens). So it is printed with what it covers, and
the syllabus rules -- a midterm replaced by a better final, a final under 40%
failing the course -- are not applied here, because they are written in a PDF,
not in the gradebook.

A grade with no score is never treated as zero. Counting an ungraded item as
0 would make a 90% student look like they are failing, which is the kind of
quiet wrongness this project exists to avoid.
"""

import json
import sys


def _num(x):
    """A number, or None. Brightspace sends null, numbers, and occasionally strings."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct(num, den):
    if num is None or den is None or den <= 0:
        return None
    return 100.0 * num / den


def rows(my_grades, structure=None):
    """One dict per graded item the student can see, in gradebook order.

    `structure` is the course's "grades" list (the gradebook layout) and is
    only used to name each item's category; everything else comes from the
    grade values themselves.
    """
    my_grades = my_grades or {}
    values = my_grades.get("values") or []
    cats = {}
    for c in my_grades.get("categories") or []:
        cid = c.get("Id")
        if cid is not None:
            cats[cid] = c.get("Name") or ""
    cat_of = {}
    for g in structure or []:
        gid = g.get("Id")
        if gid is not None:
            cat_of[str(gid)] = cats.get(g.get("CategoryId"), "")

    out = []
    for v in values:
        if not isinstance(v, dict):
            continue
        name = (v.get("GradeObjectName") or "").strip()
        if not name:
            continue
        # Category totals are themselves grade values in Brightspace; listing
        # them beside their own items would count every mark twice.
        # D2L's GradeObjectType: 7 final calculated, 8 final adjusted,
        # 9 category. The type name is checked too, in case only one is sent.
        type_name = (v.get("GradeObjectTypeName") or "").lower().replace(" ", "")
        if (v.get("GradeObjectType") in (7, 8, 9)
                or type_name in ("category", "finalcalculated", "finaladjusted",
                                 "calculatedfinal", "adjustedfinal")):
            continue
        pn, pd = _num(v.get("PointsNumerator")), _num(v.get("PointsDenominator"))
        wn, wd = _num(v.get("WeightedNumerator")), _num(v.get("WeightedDenominator"))
        comments = ((v.get("Comments") or {}).get("Text") or "").strip()
        out.append({
            "name": name,
            "category": cat_of.get(str(v.get("GradeObjectIdentifier")), ""),
            "points": pn, "out_of": pd,
            "percent": _pct(pn, pd),
            "weighted": wn, "weight": wd,
            "displayed": (v.get("DisplayedGrade") or "").strip(),
            "comments": " ".join(comments.split()),
            "graded": pn is not None,
        })
    return out


def standing(rs):
    """Where you stand on what has been graded so far.

    Uses the gradebook's weights when it has them, raw points when it does
    not. Returns (percent, covered, basis) or (None, 0, "") when nothing is
    graded yet. `covered` is how much of the course's weight that percent is
    based on, so 40 means "this is 40% of the course".
    """
    graded = [r for r in rs if r["graded"]]
    weighted = [r for r in graded if r["weight"] and r["weighted"] is not None]
    if weighted:
        got = sum(r["weighted"] for r in weighted)
        of = sum(r["weight"] for r in weighted)
        return (_pct(got, of), of, "weights")
    pointed = [r for r in graded if r["out_of"]]
    if pointed:
        got = sum(r["points"] for r in pointed)
        of = sum(r["out_of"] for r in pointed)
        return (_pct(got, of), None, "points")
    return (None, 0, "")


def final_shown(my_grades):
    """The running final grade, if the professor has released it."""
    f = (my_grades or {}).get("final") or {}
    shown = (f.get("DisplayedGrade") or "").strip()
    pct = _pct(_num(f.get("PointsNumerator")), _num(f.get("PointsDenominator")))
    if not shown and pct is None:
        return None
    return shown or f"{pct:.1f} %"


def fmt(x, places=1):
    if x is None:
        return ""
    s = f"{x:.{places}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def summary_line(rs, my_grades):
    pct, covered, basis = standing(rs)
    graded = sum(1 for r in rs if r["graded"])
    if pct is None:
        return f"Nothing graded yet ({len(rs)} item{'s' if len(rs) != 1 else ''} listed)."
    line = f"**{pct:.1f}%** on what is graded so far ({graded} item{'s' if graded != 1 else ''}"
    if basis == "weights" and covered:
        line += f", worth {fmt(covered)}% of the course)"
    else:
        line += ", by points; this gradebook has no weights)"
    shown = final_shown(my_grades)
    if shown:
        line += f". Brightspace's own running total: **{shown}**"
    return line + "."


def table(rs):
    """The marks as a markdown table, graded ones first."""
    if not rs:
        return ["_No grades visible on Brightspace for this course._"]
    lines = ["| Item | Mark | % | Weight | Comment |", "|---|---|---|---|---|"]
    for r in sorted(rs, key=lambda r: not r["graded"]):
        if r["graded"]:
            mark = f"{fmt(r['points'], 2)} / {fmt(r['out_of'], 2)}" if r["out_of"] else fmt(r["points"], 2)
        else:
            mark = "_not graded_"
        pct = f"{r['percent']:.0f}%" if r["percent"] is not None else (r["displayed"] or "")
        weight = f"{fmt(r['weight'])}%" if r["weight"] else ""
        name = r["name"] + (f" ({r['category']})" if r["category"] else "")
        comment = r["comments"][:120].replace("|", "/")
        lines.append(f"| {name.replace('|', '/')} | {mark} | {pct} | {weight} | {comment} |")
    return lines


def main(argv=None):
    import paths
    import store
    if not paths.COLLECTED.exists():
        sys.exit("No collected.json yet -- run update.py first.")
    for c in json.loads(paths.COLLECTED.read_text(encoding="utf-8")):
        if "my_grades" not in c:
            print(f"\n{c.get('name')}: no grades collected yet -- run update.py once.")
            continue
        p = store.course_parts(c.get("name") or "")
        rs = rows(c["my_grades"], c.get("grades"))
        print(f"\n{p['code']}  {p['title']}")
        print("  " + summary_line(rs, c["my_grades"]).replace("**", ""))
        for r in rs:
            if r["graded"]:
                out_of = f" / {fmt(r['out_of'], 2)}" if r["out_of"] else ""
                pct = f"  ({r['percent']:.0f}%)" if r["percent"] is not None else ""
                print(f"    {fmt(r['points'], 2)}{out_of}{pct}  {r['name']}")
            else:
                print(f"    --  {r['name']}  (not graded)")


if __name__ == "__main__":
    main()
