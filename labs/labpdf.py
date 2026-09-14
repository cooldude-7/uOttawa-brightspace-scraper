r"""Turn a folder of lab screenshots into the PDF to hand in.

    python labpdf.py 3          build Lab 3
    python labpdf.py 3 --yes    build it without asking
    python labpdf.py --folders  make Lab 1..12 under the course folder
    python labpdf.py --where    show (or change) where the course folder is

Screenshots go in one page each, in the order you took them, and nothing
is added -- no headings, no labels, no cover page. The output is named
the way the course wants it: LucaMaric_p3_1.pdf for Lab 3.

The order comes from when each screenshot was taken, not from the file
name or from anything a model guessed. You screenshot the code, run it,
screenshot the output, move on -- so the time you took them already IS
the order, recorded by Windows in the file. That is free, instant, and
exactly right rather than probably right, which matters for something
being submitted for marks.

It always prints the order and waits for you to say yes.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
CONFIG = HERE / "labpdf.json"
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

# The course wants LucaMaric_p<lab>_1.pdf. The trailing 1 does not change.
NAME = "LucaMaric_p{lab}_1.pdf"

# Letter, because that is what Canada prints on. 150 dpi is plenty for a
# screenshot and keeps the file small enough to upload.
DPI = 150
PAGE = (int(8.5 * DPI), int(11 * DPI))
MARGIN = int(0.4 * DPI)


# ---------------------------------------------------------------- settings

def settings():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def course_folder(ask=False):
    """Where the GNG1106 folder lives, asked once and remembered."""
    saved = settings().get("course_folder")
    if saved and not ask:
        root = Path(saved)
        if root.is_dir():
            return root
        print(f"  {root} is not there any more.\n")

    print("Where is your GNG1106 folder?")
    print(r"  e.g.  C:\Users\lucam\Documents\GNG1106")
    typed = input("  path: ").strip().strip('"')
    if not typed:
        sys.exit("  nothing typed -- stopping.")
    root = Path(typed)
    if not root.is_dir():
        sys.exit(f"  {root} does not exist. Make it first, or check the spelling.")
    CONFIG.write_text(json.dumps({"course_folder": str(root)}, indent=1), encoding="utf-8")
    print(f"  remembered: {root}\n")
    return root


# ------------------------------------------------------------------ order

# Windows names a screenshot "Screenshot 2026-09-14 140211.png"; macOS uses
# "Screenshot 2026-09-14 at 14.02.11.png". Both carry the moment it was
# taken, which survives copying between folders where the file's own
# timestamp does not.
STAMP = re.compile(
    r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})"           # date
    r"[ _a-z]{0,4}"                                 # "at", spaces
    r"(\d{2})[.\-_]?(\d{2})[.\-_]?(\d{2})?", re.I)  # time


def taken_at(path):
    """When the screenshot was taken. -> (datetime, how it was worked out)."""
    m = STAMP.search(path.name)
    if m:
        y, mo, d, h, mi, s = m.groups()
        try:
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0)), "name"
        except ValueError:
            pass

    try:
        from PIL import Image
        with Image.open(path) as im:
            exif = im.getexif()
            for tag in (36867, 306):            # DateTimeOriginal, DateTime
                raw = exif.get(tag)
                if raw:
                    return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S"), "exif"
    except Exception:
        pass

    return datetime.fromtimestamp(path.stat().st_mtime), "file"


def shots(folder):
    found = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in IMAGES
             and not p.name.startswith((".", "~"))]
    return sorted(((p, *taken_at(p)) for p in found), key=lambda t: t[1])


# ------------------------------------------------------------------- build

def build(pages, out):
    from PIL import Image

    sheets = []
    for path in pages:
        with Image.open(path) as im:
            im = im.convert("RGB")
            room = (PAGE[0] - 2 * MARGIN, PAGE[1] - 2 * MARGIN)
            scale = min(room[0] / im.width, room[1] / im.height)
            # Never blow a screenshot up past its own size -- enlarging it
            # only makes the text blurry.
            scale = min(scale, 1.0)
            fitted = im.resize((max(1, int(im.width * scale)),
                                max(1, int(im.height * scale))), Image.LANCZOS)
            sheet = Image.new("RGB", PAGE, "white")
            sheet.paste(fitted, ((PAGE[0] - fitted.width) // 2,
                                 (PAGE[1] - fitted.height) // 2))
            sheets.append(sheet)

    sheets[0].save(out, "PDF", resolution=DPI, save_all=True,
                   append_images=sheets[1:])
    return out


# -------------------------------------------------------------------- main

def main(argv):
    if "--where" in argv:
        course_folder(ask=True)
        return

    root = course_folder()

    if "--folders" in argv:
        made = []
        for n in range(1, 13):
            d = root / f"Lab {n}"
            if not d.exists():
                d.mkdir()
                made.append(d.name)
        print(f"  {root}")
        print(f"  created {', '.join(made)}" if made else "  every Lab folder already there")
        return

    numbers = [a for a in argv if a.isdigit()]
    if not numbers:
        sys.exit(__doc__.strip().split("\n\n")[1])
    lab = numbers[0]

    folder = root / f"Lab {lab}"
    if not folder.is_dir():
        sys.exit(f"  {folder} does not exist. Run  python labpdf.py --folders")

    found = shots(folder)
    if not found:
        sys.exit(f"  no screenshots in {folder}")

    print(f"\n  Lab {lab} -- {len(found)} screenshot{'s' if len(found) != 1 else ''}, "
          f"in the order you took them\n")
    guessed = 0
    for i, (path, when, how) in enumerate(found, 1):
        note = {"name": "", "exif": "  (from the image)",
                "file": "  (from the file date -- check this one)"}[how]
        guessed += how == "file"
        print(f"   {i:>2}. {when:%a %d %b %H:%M:%S}  {path.name[:48]}{note}")

    if guessed:
        print(f"\n  {guessed} of these had no timestamp in the name, so the file's")
        print("  own date was used. That is right unless the file was copied.")

    out = folder / NAME.format(lab=lab)
    print(f"\n  -> {out.name}")
    if out.exists():
        print("     (replacing the one already there)")

    if "--yes" not in argv:
        if input("\n  Build it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("  stopped, nothing written.")
            return

    try:
        build([p for p, _, _ in found], out)
    except ImportError:
        sys.exit("\n  Pillow is not installed. Run:\n"
                 "      python -m pip install pillow")
    size = out.stat().st_size / 1024
    print(f"\n  {out}")
    print(f"  {len(found)} pages, {size:,.0f} KB. Open it and check before submitting.")


if __name__ == "__main__":
    main(sys.argv[1:])
