r"""
Shows the sentence behind every date found, so you can tell a real
extraction from an invented one.

    python show_dates.py                    everything
    python show_dates.py MCG2130            one course
    python show_dates.py MCG2130 haiku      one course, one model
"""

import json
import sys
from pathlib import Path

import paths

FOUND = paths.FOUND

if not FOUND.exists():
    raise SystemExit("No results yet. Run find_dates.py first.")

args = [a.lower() for a in sys.argv[1:]]
course_filter = next((a for a in args if a not in ("haiku", "sonnet", "opus")), None)
model_filter = next((a for a in args if a in ("haiku", "sonnet", "opus")), None)

for block in json.loads(FOUND.read_text(encoding="utf-8")):
    if course_filter and course_filter not in block["course"].lower():
        continue
    if model_filter and block["model"] != model_filter:
        continue

    print("\n" + "=" * 70)
    print(f"{block['course']}  --  {block['model']}")
    print("=" * 70)

    if not block["dates"]:
        print("  (nothing found)")
        continue

    for d in sorted(block["dates"], key=lambda x: x.get("date") or "9999"):
        excerpt = " ".join((d.get("source_excerpt") or "").split())
        when = d.get("date", "?")
        if d.get("time"):
            when += " " + d["time"]

        # A day or month of 00 cannot come from a real document.
        broken = ""
        parts = (d.get("date") or "").split("-")
        if len(parts) == 3 and ("00" in parts[1:]):
            broken = "   <-- IMPOSSIBLE DATE"

        print(f"\n  {when}  [{d.get('confidence','?')}]  {d.get('title','')}{broken}")
        print(f"     quoted: {excerpt[:220] or '(nothing quoted -- likely invented)'}")

print("\n" + "=" * 70)
print("If 'quoted' is a real sentence from the syllabus, the date is real.")
print("If it is vague, empty, or does not mention that date, it was invented.")
