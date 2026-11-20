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
            cats[cid] = (c.get("Name") or "", _num(c.get("Weight")))
    cat_of = {}
    for g in structure or []:
        gid = g.get("Id")
        if gid is not None and g.get("CategoryId") in cats:
            cat_of[str(gid)] = cats[g.get("CategoryId")]

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
        cat_name, cat_weight = cat_of.get(str(v.get("GradeObjectIdentifier")), ("", None))
        in_category = bool(cat_name)
        # An item inside a category carries its weight *within* that category
        # (a lab worth 14.7% of Labs), not of the course -- measured on the
        # real GNG1106 gradebook, whose twelve lab weights sum to 100. Scale by
        # the category's own weight to get its share of the course. Without
        # the category's weight the course share is unknown, not guessed.
        if in_category:
            share = (cat_weight * wd / 100.0) if (cat_weight is not None and wd is not None) else None
            share_got = (cat_weight * wn / 100.0) if (cat_weight is not None and wn is not None) else None
        else:
            share, share_got = wd, wn
        out.append({
            "name": name,
            "category": cat_name,
            "points": pn, "out_of": pd,
            "percent": _pct(pn, pd),
            "weighted": wn, "weight": wd,
            "course_weight": share, "course_got": share_got,
            "in_category": in_category,
            "displayed": (v.get("DisplayedGrade") or "").strip(),
            "comments": " ".join(comments.split()),
            "graded": pn is not None,
            # 5 calculated, 6 formula: a professor's own subtotal row, like
            # GNG2101's "Labs total". Shown, never added to the items it sums.
            "is_total": v.get("GradeObjectType") in (5, 6)
                        or type_name in ("calculated", "formula"),
        })
    return out


def standing(rs):
    """Where you stand on what has been graded so far.

    Uses the gradebook's weights when it has them, raw points when it does
    not. Returns (percent, covered, basis) or (None, 0, "") when nothing is
    graded yet. `covered` is how much of the course's weight that percent is
    based on, so 40 means "this is 40% of the course".
    """
    graded = [r for r in rs if r["graded"] and not r.get("is_total")]
    # Any graded item that has a weight but whose course share cannot be
    # worked out would make the percentage quietly wrong -- and falling back
    # to raw points would hide that the gradebook is weighted at all. So say
    # nothing rather than something half-right.
    if any(r["weight"] and r.get("course_weight") is None for r in graded):
        return (None, 0, "unknown")
    weighted = [r for r in graded if r.get("course_weight") and r.get("course_got") is not None]
    if weighted:
        got = sum(r["course_got"] for r in weighted)
        of = sum(r["course_weight"] for r in weighted)
        return (_pct(got, of), of, "weights")
    pointed = [r for r in graded if r["out_of"]]
    # Raw points only mean something when every item is on the same scale:
    # adding a pre-lab out of 100 to a lab out of 1 lets the big numbers
    # drown the rest. GNG2101's gradebook is exactly that.
    if pointed and len({r["out_of"] for r in pointed}) == 1:
        got = sum(r["points"] for r in pointed)
        of = sum(r["out_of"] for r in pointed)
        return (_pct(got, of), None, "points")
    if pointed:
        return (None, 0, "mixed")
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
    graded = sum(1 for r in rs if r["graded"] and not r.get("is_total"))
    if pct is None:
        if basis == "mixed":
            return (f"{graded} items graded. Brightspace has no weights for this course "
                    "and the marks are on different scales, so there is no honest single "
                    "percentage here: use the syllabus weights.")
        if basis == "unknown":
            return (f"{graded} items graded. Some category weights were not readable, so "
                    "no overall percentage is shown rather than a wrong one.")
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
    lines = ["| Item | Mark | % | Of course | Comment |", "|---|---|---|---|---|"]
    for r in sorted(rs, key=lambda r: not r["graded"]):
        if r["graded"]:
            mark = f"{fmt(r['points'], 2)} / {fmt(r['out_of'], 2)}" if r["out_of"] else fmt(r["points"], 2)
        else:
            mark = "_not graded_"
        pct = f"{r['percent']:.0f}%" if r["percent"] is not None else (r["displayed"] or "")
        cw = r.get("course_weight")
        weight = f"{fmt(cw, 2)}%" if cw else ""
        name = r["name"] + (f" ({r['category']})" if r["category"] else "")
        if r.get("is_total"):
            name += " · _subtotal, not added_"
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
