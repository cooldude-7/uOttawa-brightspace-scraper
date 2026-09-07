r"""
Prints the raw shape of each course's table of contents, so we can see
whether files are genuinely absent or just parsed under the wrong name.

    python scraper\inspect_toc.py
"""

import json

from collect import LE, current_courses, get, get_client


def summarize(node, depth, counts):
    """Walk whatever shape came back, tallying keys and leaf-looking nodes."""
    pad = "  " * depth
    if isinstance(node, dict):
        keys = sorted(node.keys())
        counts["keys"].update(keys)
        title = node.get("Title") or node.get("Name") or ""
        ident = node.get("Id") or node.get("ModuleId") or node.get("TopicId")
        listy = {k: len(v) for k, v in node.items() if isinstance(v, list) and v}
        if depth <= 2:
            print(f"{pad}- {title[:44]!r} id={ident} lists={listy or '-'}")
        if node.get("Url") or node.get("TypeIdentifier"):
            counts["leaves"] += 1
        for k, v in node.items():
            if isinstance(v, (list, dict)):
                summarize(v, depth + 1, counts)
    elif isinstance(node, list):
        for child in node[:60]:
            summarize(child, depth, counts)


def main():
    client = get_client()
    courses, _ = current_courses(client)

    for c in courses:
        print("\n" + "=" * 66)
        print(c["name"])
        print("=" * 66)

        toc = get(client, f"/d2l/api/le/{LE}/{c['id']}/content/toc")
        if toc is None:
            print("  table of contents unavailable")
        else:
            print(f"  top-level keys: {sorted(toc.keys()) if isinstance(toc, dict) else type(toc).__name__}")
            counts = {"keys": set(), "leaves": 0}
            summarize(toc, 1, counts)
            print(f"\n  every key seen anywhere: {sorted(counts['keys'])}")
            print(f"  nodes that look like actual files: {counts['leaves']}")

        # Cross-check against the older endpoint, which reports differently.
        root = get(client, f"/d2l/api/le/{LE}/{c['id']}/content/root/")
        if isinstance(root, list):
            stubs = sum(len(m.get("Structure") or []) for m in root)
            print(f"  content/root: {len(root)} top folders, {stubs} direct children")

    print("\nIf 'nodes that look like actual files' is near zero across the board,")
    print("the courses really are still empty this early in term.")


if __name__ == "__main__":
    main()
