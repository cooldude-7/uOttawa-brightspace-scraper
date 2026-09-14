r"""Can this machine search the vault by meaning? Measure, do not guess.

Searching ~90,000 words by meaning needs a small model that turns text into
numbers. It cannot invent anything -- it produces no text, only coordinates,
and a search returns your own sentences back. But it has to run on a Pi 3B+
with 1 GB of RAM, and that is a real question, not a formality.

    BRIGHTSPACE_DATA=/mnt/data ~/uOttawa-brightspace-scraper/.venv/bin/python probe_embed.py

Reports the machine, the corpus, and what an index would cost -- with no
install and nothing written. If an embedding runtime is already present it
also embeds a sample and times it. Add --full to embed the whole vault.

Nothing here touches the database, the scrape, or the vault's files.
"""

import os
import sys
import time
from pathlib import Path

import paths

# Long enough to hold an idea, short enough that a hit points at one
# paragraph rather than a whole lecture.
CHUNK_WORDS = 180
OVERLAP_WORDS = 40


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def machine():
    print("\n-- this machine " + "-" * 46)
    print(f"  python       {sys.version.split()[0]}  on  {sys.platform}")
    try:
        import platform
        print(f"  cpu          {platform.machine()}  x{os.cpu_count()}")
    except Exception:
        pass

    total = avail = swap = None
    try:
        meminfo = dict()
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            meminfo[key] = int(rest.strip().split()[0]) * 1024
        total, avail = meminfo.get("MemTotal"), meminfo.get("MemAvailable")
        swap = meminfo.get("SwapTotal")
    except Exception:
        print("  memory       (not readable -- not a Linux box)")

    if total:
        print(f"  memory       {human(total)} total, {human(avail)} free now")
        print(f"  swap         {human(swap)}")
        if avail and avail < 250 * 1024 * 1024:
            print("  ! under 250 MB free. Embedding will swap, which on an SD")
            print("    card is both slow and hard on the card.")
    return avail


def corpus():
    """The vault as it would be indexed."""
    root = paths.VAULT
    print("\n-- the vault " + "-" * 49)
    if not root.exists():
        print(f"  {root} does not exist yet -- run vault.py first.")
        return []

    chunks = []
    notes = words = 0
    for f in sorted(root.rglob("*.md")):
        # Skills/ and Bundles/ are inputs and copies, not things to search.
        rel = f.relative_to(root).as_posix()
        if rel.startswith(("Skills/", "Bundles/")):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        body = [w for w in text.split() if w]
        if len(body) < 30:
            continue
        notes += 1
        words += len(body)
        step = CHUNK_WORDS - OVERLAP_WORDS
        for i in range(0, len(body), step):
            piece = body[i:i + CHUNK_WORDS]
            if len(piece) >= 30:
                chunks.append((rel, " ".join(piece)))

    print(f"  {notes:,} notes, {words:,} words")
    print(f"  -> {len(chunks):,} chunks of ~{CHUNK_WORDS} words "
          f"({OVERLAP_WORDS} overlapping)")
    # 384 dimensions, float32, is what a small model produces.
    index = len(chunks) * 384 * 4
    print(f"  -> index would be about {human(index)} on disk, "
          f"{human(index)} in memory while searching")
    return chunks


def runtime():
    """Whichever embedding runtime is already installed, if any."""
    for name, load in (
        ("fastembed", _fastembed),
        ("sentence_transformers", _sbert),
        ("llama_cpp", _llama),
    ):
        try:
            __import__(name)
        except ImportError:
            continue
        print(f"\n-- {name} is installed " + "-" * (41 - len(name)))
        return load
    print("\n-- no embedding runtime installed " + "-" * 28)
    print("  Nothing has been installed and nothing was downloaded.")
    print("  The numbers above are enough to judge whether it is worth it.")
    print("\n  If they look fine, the lightest thing to try is:")
    print("      ~/uOttawa-brightspace-scraper/.venv/bin/pip install fastembed")
    print("  then run this again. fastembed uses onnxruntime rather than")
    print("  PyTorch, which matters on a 1 GB machine -- torch alone is")
    print("  larger than this Pi's memory.")
    return None


def _fastembed():
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return lambda texts: list(model.embed(texts))


def _sbert():
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    return lambda texts: model.encode(texts)


def _llama():
    raise RuntimeError("llama_cpp needs a model file chosen by hand")


def measure(load, chunks, full):
    sample = chunks if full else chunks[:40]
    print(f"  embedding {len(sample):,} chunk(s)"
          + ("" if full else f" of {len(chunks):,} as a sample"))

    peak_before = _rss()
    t0 = time.time()
    try:
        embed = load()
    except Exception as e:
        print(f"  ! could not load the model: {e!r}")
        return
    loaded = time.time() - t0
    print(f"  model loaded in {loaded:.1f}s, "
          f"memory now {human(_rss())} (was {human(peak_before)})")

    t0 = time.time()
    try:
        vectors = embed([t for _, t in sample])
    except Exception as e:
        print(f"  ! embedding failed: {e!r}")
        return
    took = time.time() - t0

    per = took / max(1, len(sample))
    print(f"  {len(sample):,} chunks in {took:.1f}s  ({per*1000:.0f} ms each)")
    print(f"  memory peak {human(_rss())}")
    if not full:
        print(f"  -> the whole vault would take about "
              f"{per * len(chunks) / 60:.1f} minutes, once")
    try:
        dims = len(vectors[0])
        print(f"  vector size {dims} -- index {human(len(chunks) * dims * 4)}")
    except Exception:
        pass
    print("\n  A search once indexed is a few milliseconds: it compares "
          "numbers,\n  it does not run the model over the corpus again.")


def _rss():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def main():
    full = "--full" in sys.argv
    print("\nCan this machine search the vault by meaning?")
    machine()
    chunks = corpus()
    if not chunks:
        return
    load = runtime()
    if load:
        measure(load, chunks, full)
    print()


if __name__ == "__main__":
    main()
