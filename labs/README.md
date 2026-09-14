# Lab screenshots to a PDF

GNG1106 wants the code and its output as screenshots, in deliverable
order, as one PDF per lab. This does that.

    python labpdf.py --folders   make Lab 1..12 inside your GNG1106 folder
    python labpdf.py 3           build Lab 3's PDF
    python labpdf.py 3 --yes     build it without the confirmation
    python labpdf.py --where     change where the GNG1106 folder is

The first run asks where your GNG1106 folder is and remembers it.

## How it knows the order

By **when you took each screenshot** — nothing else. You screenshot the
code, run it, screenshot the output, move to the next deliverable; the
order you did that in is already the order they belong in, and Windows
records it in the file.

That means no AI, no cost, nothing to be wrong about. It is not a guess
that is usually right, which matters for something being handed in.

Where it gets the time, in order of preference:

1. **the file name** — `Screenshot 2026-09-14 140211.png`. Most reliable,
   because it survives being copied between folders.
2. **inside the image**, if the camera or tool wrote it there.
3. **the file's own date** — correct unless the file has been copied, so
   any screenshot falling back to this is flagged in the listing.

It always prints the order and waits for you to say yes.

## What it does not do

No headings, no labels, no cover page, no page numbers — just your
screenshots, one per page, on Letter paper. Nothing is added that you did
not put there.

It does not read the screenshots or understand them. If two are in the
wrong order, rename one so its timestamp sorts correctly, and run again.

## Setup, once

Needs Python and Pillow:

    python -m pip install pillow
