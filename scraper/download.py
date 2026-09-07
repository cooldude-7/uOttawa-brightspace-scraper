r"""
Downloads every file from this term's courses and pulls the text out of them.

    python -m pip install pymupdf python-pptx python-docx
    python download.py

Files land in _originals\, extracted text in extracted\.
Run from inside the scraper folder.
"""

import re
import sys
from pathlib import Path

from collect import BASE, LE, current_courses, get, get_client, walk_content

HERE = Path(__file__).parent
ORIGINALS = HERE / "_originals"
EXTRACTED = HERE / "extracted"

# Text pullers are optional; report what's missing instead of crashing.
READERS = {}
try:
    import pymupdf
    READERS["pdf"] = lambda p: "\n".join(pg.get_text() for pg in pymupdf.open(p))
except ImportError:
    try:
        from pypdf import PdfReader
        READERS["pdf"] = lambda p: "\n".join(
            (pg.extract_text() or "") for pg in PdfReader(p).pages
        )
    except ImportError:
        pass

try:
    from pptx import Presentation

    def _pptx(path):
        out = []
        for i, slide in enumerate(Presentation(path).slides, 1):
            out.append(f"\n--- slide {i} ---")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    out.append(shape.text_frame.text)
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    out.append(f"[speaker notes] {notes}")
        return "\n".join(out)

    READERS["pptx"] = _pptx
except ImportError:
    pass

try:
    import docx

    READERS["docx"] = lambda p: "\n".join(
        para.text for para in docx.Document(p).paragraphs
    )
except ImportError:
    pass

try:
    import openpyxl

    def _xlsx(path):
        out = []
        for sheet in openpyxl.load_workbook(path, data_only=True).worksheets:
            out.append(f"\n--- sheet: {sheet.title} ---")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    out.append("\t".join(cells))
        return "\n".join(out)

    READERS["xlsx"] = _xlsx
except ImportError:
    pass

READERS["txt"] = lambda p: Path(p).read_text(encoding="utf-8", errors="replace")


def safe_name(text, limit=80):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (text or "untitled").strip())
    return (cleaned[:limit] or "untitled").rstrip(". ")


def extension(url, title):
    for candidate in (url or "", title or ""):
        match = re.search(r"\.([A-Za-z0-9]{1,5})(?:\?|$)", candidate)
        if match:
            return match.group(1).lower()
    return ""


def download_topic(client, topic, folder):
    """Fetch one file. Returns (path, note) -- path is None if skipped."""
    url = topic.get("url")
    if not url:
        return None, "no link"

    kind = (topic.get("type") or "").lower()
    if kind and kind not in ("file", "contentservice"):
        return None, f"not a file ({kind})"

    full = url if url.startswith("http") else BASE + ("" if url.startswith("/") else "/") + url
    ext = extension(url, topic.get("title"))
    name = safe_name(topic.get("title")) + (f".{ext}" if ext else "")
    dest = folder / name

    if dest.exists() and dest.stat().st_size > 0:
        return dest, "already had it"

    try:
        r = client.get(full)
    except Exception as e:
        return None, f"failed: {e!r}"

    if r.status_code != 200:
        return None, f"failed: HTTP {r.status_code}"
    if b"<html" in r.content[:400].lower() and ext not in ("html", "htm"):
        return None, "got a web page, not a file"

    folder.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest, f"{len(r.content) // 1024} KB"


def extract(path, out_dir):
    ext = path.suffix.lstrip(".").lower()
    reader = READERS.get(ext)
    if not reader:
        return None, f"can't read .{ext}"
    try:
        text = reader(str(path))
    except Exception as e:
        return None, f"unreadable: {e!r}"

    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if len(text) < 20:
        return None, "no text (probably a scan)"

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (path.stem + ".txt")
    out.write_text(text, encoding="utf-8")
    return out, f"{len(text.split()):,} words"


def main():
    if "pdf" not in READERS:
        print("No PDF reader installed. Run:")
        print("    python -m pip install pymupdf python-pptx python-docx")
        print("If pymupdf will not install on Python 3.14, use pypdf instead:")
        print("    python -m pip install pypdf python-pptx python-docx\n")

    client = get_client()
    courses, _ = current_courses(client)

    grand_files = grand_text = 0
    words_total = 0

    for course in courses:
        code = safe_name(course["name"], 40)
        print(f"\n{course['name']}")
        _, topics = walk_content(client, course["id"])
        if not topics:
            print("  (no files posted yet)")
            continue

        got = read = 0
        links = []
        for topic in topics:
            path, note = download_topic(client, topic, ORIGINALS / code)
            label = (topic.get("title") or "?")[:46]
            if not path:
                print(f"  skip  {label:<48} {note}")
                if note.startswith("not a file"):
                    links.append(topic)
                continue
            got += 1
            out, detail = extract(path, EXTRACTED / code)
            if out:
                read += 1
                words_total += int(detail.split()[0].replace(",", ""))
                print(f"  read  {label:<48} {detail}")
            else:
                print(f"  saved {label:<48} {detail}")

        if links:
            # These are usually assignment dropboxes or quizzes, which the
            # assignments and quizzes endpoints already report with real due
            # dates. Recorded rather than discarded so that can be checked.
            folder = EXTRACTED / code
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "_links.txt").write_text(
                "\n".join(f"{t.get('title','?')}\t{t.get('url','')}" for t in links),
                encoding="utf-8",
            )

        grand_files += got
        grand_text += read
        note = f", {len(links)} links noted" if links else ""
        print(f"  -> {got} downloaded, {read} readable{note}")

    print("\n" + "=" * 64)
    print(f"{grand_files} files downloaded, {grand_text} turned into readable text")
    print(f"about {words_total:,} words to search for deadlines")
    print(f"\nOriginals: {ORIGINALS}")
    print(f"Text:      {EXTRACTED}")


if __name__ == "__main__":
    main()
