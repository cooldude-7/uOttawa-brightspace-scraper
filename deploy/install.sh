#!/usr/bin/env bash
#
# Sets the Pi up to check Brightspace every 30 minutes and serve the card
# list. Run it on the Pi, from the repo folder, as your normal user:
#
#     bash deploy/install.sh
#
# Safe to run again after a git pull -- it replaces what it installed before.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${BRIGHTSPACE_DATA:-/mnt/data}"
MARKER="$DATA/.brightspace-data"
UNITS=(brightspace-web.service brightspace-update.service brightspace-update.timer)

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as your normal user, not with sudo.
It will ask for your password when it needs root."

# --------------------------------------------------------------- the stick

say "1/5  Checking the USB stick"

mountpoint -q "$DATA" || die "$DATA is not a mounted filesystem.

The database has to live on the USB stick, not the SD card. Work through
docs/pi-setup.md steps 1-5 first, then run this again."

if [ ! -e "$MARKER" ]; then
    touch "$MARKER"
    echo "  wrote $MARKER (proves the stick is mounted, not the folder under it)"
fi

# A file written by the stick's own filesystem, so a later boot with the
# stick missing fails loudly instead of writing to the SD card underneath.
df -h "$DATA" | tail -1 | sed 's/^/  /'

# ------------------------------------------------------------------ python

say "2/5  Python packages"

if [ ! -x "$REPO/.venv/bin/python" ]; then
    echo "  creating $REPO/.venv"
    python3 -m venv "$REPO/.venv"
fi

# The virtual environment stays on the SD card with the code, on purpose.
# It is written once and never again, so it costs the card nothing -- and if
# the stick ever fails to mount, the service still starts far enough to print
# a clear message about the stick instead of "no such file: python".
echo "  installing (several minutes on a 3B+; it is not stuck)"
"$REPO/.venv/bin/pip" install --quiet --upgrade pip
"$REPO/.venv/bin/pip" install --quiet -r "$REPO/scraper/requirements-pi.txt"
echo "  done"

# ----------------------------------------------------------------- secrets

say "3/5  Checking the files that cannot be copied automatically"

missing=0
for f in api_key.txt session.json; do
    if [ -s "$DATA/$f" ]; then
        echo "  found    $f"
    else
        echo "  MISSING  $f"
        missing=1
    fi
done
for f in google_client.json google_token.json me.json; do
    [ -s "$DATA/$f" ] && echo "  found    $f" || echo "  optional $f (not there yet)"
done

# Nobody but you should be able to read the session file: it is a bearer
# token for the whole uOttawa account, not just for course pages.
chmod 600 "$DATA"/*.json "$DATA"/api_key.txt 2>/dev/null || true

if [ "$missing" -eq 1 ]; then
    echo
    echo "  Copy the missing ones from your laptop's scraper folder, e.g. from"
    echo "  Windows PowerShell:"
    echo
    echo "      scp scraper\\api_key.txt  $USER@$(hostname):$DATA/"
    echo "      scp scraper\\session.json $USER@$(hostname):$DATA/"
    echo
    echo "  Then run this script again."
fi

# ----------------------------------------------------------------- systemd

say "4/5  Installing the services"

for u in "${UNITS[@]}"; do
    sed -e "s|__USER__|$USER|g" -e "s|__REPO__|$REPO|g" -e "s|__DATA__|$DATA|g" "$REPO/deploy/$u" \
        | sudo tee "/etc/systemd/system/$u" >/dev/null
    echo "  /etc/systemd/system/$u"
done

sudo systemctl daemon-reload
sudo systemctl enable --now brightspace-web.service
sudo systemctl enable --now brightspace-update.timer

# ------------------------------------------------------------------ report

say "5/5  Where things stand"

systemctl is-active --quiet brightspace-web.service \
    && echo "  web app   running   http://$(hostname -I | awk '{print $1}'):8000" \
    || echo "  web app   NOT running -- journalctl -u brightspace-web -n 30"

systemctl is-active --quiet brightspace-update.timer \
    && echo "  timer     armed" \
    || echo "  timer     NOT armed -- journalctl -u brightspace-update -n 30"

echo
systemctl list-timers brightspace-update.timer --no-pager | sed -n '1,2p' | sed 's/^/  /'

cat <<'TIP'

  Useful later:
    systemctl status brightspace-web            is the app up
    journalctl -u brightspace-update -n 50      what the last scrape did
    sudo systemctl start brightspace-update     scrape right now, do not wait
TIP
