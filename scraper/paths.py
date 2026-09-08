r"""
Where the data lives.

On the laptop: right here, next to the code, exactly as before.

On the Pi: on the USB stick, because a database that commits writes every
30 minutes for a year is what kills SD cards. Set BRIGHTSPACE_DATA=/mnt/data
and everything -- database, downloads, session, keys -- moves there while the
code stays where it is.

The marker file is the important part. /mnt/data still exists as an ordinary
empty folder on the SD card when the stick fails to mount, so "the folder is
there" proves nothing. A file written onto the stick itself does: no marker
means no stick, and we stop instead of quietly starting a fresh empty
database on the card.
"""

import os
from pathlib import Path

HERE = Path(__file__).parent
MARKER = ".brightspace-data"


def _resolve():
    told = os.environ.get("BRIGHTSPACE_DATA")
    if not told:
        return HERE

    where = Path(told).expanduser()
    if not where.is_dir():
        raise SystemExit(
            f"\nBRIGHTSPACE_DATA points at {where}, which does not exist.\n"
            f"If that is the USB stick, it is not mounted. Check with:  df -h {where}\n"
        )
    if not (where / MARKER).exists():
        raise SystemExit(
            f"\n{where} has no {MARKER} file in it.\n\n"
            "That almost always means the USB stick did not mount and this is\n"
            "the empty folder on the SD card underneath it. Writing here would\n"
            "start a fresh empty database and lose nothing visibly.\n\n"
            f"Check the stick is mounted:  df -h {where}\n"
            f"If it is, and this is a first run:  touch {where}/{MARKER}\n"
        )
    return where


DATA = _resolve()

# The database and everything alongside it.
DB = DATA / "brightspace.db"
PREFS = DATA / "me.json"
SESSION = DATA / "session.json"
COLLECTED = DATA / "collected.json"
FOUND = DATA / "found_dates.json"
ORIGINALS = DATA / "_originals"
# The Obsidian vault, and the private profile the study skill reads. The
# profile is never inside the vault: the vault is meant to be synced, and a
# psychoeducational report is not a thing to sync anywhere by accident.
VAULT = DATA / "vault"
PROFILE = DATA / "profile"
EXTRACTED = DATA / "extracted"

# Secrets. Kept with the data rather than the code so that nothing secret is
# ever inside the git checkout on the Pi.
API_KEY = DATA / "api_key.txt"
GOOGLE_CLIENT = DATA / "google_client.json"
GOOGLE_TOKEN = DATA / "google_token.json"
