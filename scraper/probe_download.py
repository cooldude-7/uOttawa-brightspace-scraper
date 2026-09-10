r"""Show why a file will not download.

Three GNG2101 pre-labs come back HTTP 400 on every scrape, and a pre-lab
is exactly the kind of document that says "install this before Tuesday" --
so they are worth recovering rather than skipping quietly.

download_topic() reports the status code and nothing else, which is not
enough to fix anything. This prints the topic record Brightspace gave us,
the URL that was built from it, and what the server actually said.

    BRIGHTSPACE_DATA=/mnt/data ~/uOttawa-brightspace-scraper/.venv/bin/python probe_download.py
    ...                                                   probe_download.py Arduino

With no argument it tries every topic that is currently failing.
"""

import sys

import collect
import download


def main():
    want = " ".join(sys.argv[1:]).lower()
    client = collect.get_client()

    import json
    import paths
    courses = json.loads(paths.COLLECTED.read_text(encoding="utf-8"))

    tried = 0
    for course in courses:
        _, topics = collect.walk_content(client, course["id"])
        for topic in topics or []:
            title = topic.get("title") or "?"
            if want and want not in title.lower():
                continue
            kind = (topic.get("type") or "").lower()
            if kind and kind not in ("file", "contentservice"):
                continue
            url = topic.get("url")
            if not url:
                continue
            full = url if url.startswith("http") else \
                collect.BASE + ("" if url.startswith("/") else "/") + url
            try:
                r = client.get(full)
            except Exception as e:
                print(f"\n{title}\n  request failed: {e!r}")
                continue
            if r.status_code == 200 and not want:
                continue                      # only the broken ones, unless asked

            tried += 1
            print(f"\n{course['name'][:40]}")
            print(f"  {title}")
            print(f"  topic record : {json.dumps({k: v for k, v in topic.items() if k != 'text'})[:300]}")
            print(f"  built url    : {full[:150]}")
            print(f"  -> {r.status_code}  {r.headers.get('content-type')}  {len(r.content):,} bytes")
            for hop in r.history:
                print(f"     via {hop.status_code} {str(hop.url)[:110]}")
            body = r.text[:400].replace("\n", " ")
            print(f"  body         : {body}")

            # The id is in the URL; the API can hand back the topic's own
            # record, which may name a different download route.
            tid = topic.get("id") or topic.get("TopicId")
            if tid:
                api = f"/d2l/api/le/{collect.LE}/{course['id']}/content/topics/{tid}"
                got = collect.get(client, api)
                print(f"  api topic    : {json.dumps(got)[:300] if got else 'no answer'}")
                for route in (f"{api}/file", f"/d2l/le/content/{course['id']}/topics/files/download/{tid}/DirectFileTopicDownload"):
                    rr = client.get(collect.BASE + route)
                    print(f"  {route[-52:]:<52} -> {rr.status_code} {len(rr.content):,}b {rr.headers.get('content-type')}")

    if not tried:
        print("\nNothing failed. Every topic downloaded.")


if __name__ == "__main__":
    main()
