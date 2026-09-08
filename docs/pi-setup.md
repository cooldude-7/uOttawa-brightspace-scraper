# Pi 3B+ storage setup

Formatting the 256 GB USB stick as ext4 and mounting it permanently at `/mnt/data`,
driven from a Windows machine over SSH.

---

## Step 0 — Get a terminal on the Pi

Everything below runs **on the Pi**, not on Windows. From Windows `cmd` or PowerShell:

```
ssh yourusername@raspberrypi.local
```

Use the username set when the SD card was imaged — the old `pi` / `raspberry` default was
removed from Raspberry Pi OS in 2022.

**If `raspberrypi.local` doesn't resolve:** get the Pi's IP from your router's connected-devices
list and use `ssh yourusername@192.168.1.x` instead.

**If the Pi isn't imaged yet:** use Raspberry Pi Imager on Windows. Pick **Raspberry Pi OS Lite
(64-bit)** — no desktop (the 1 GB of RAM is better spent elsewhere), and 64-bit because arm64
Python wheels are widely prebuilt while 32-bit armhf increasingly are not, which matters when
installing this project's dependencies. Before writing, open the gear/settings icon and set
hostname, username, password, Wi-Fi, and **enable SSH**. That avoids all the boot-partition
fiddling afterward.

---

## Step 1 — Identify the stick

Plug the USB stick into the Pi, then:

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,MODEL
```

Expect something like:

```
NAME        SIZE FSTYPE MOUNTPOINT      MODEL
mmcblk0    59.5G
├─mmcblk0p1 512M vfat   /boot/firmware
└─mmcblk0p2  59G ext4   /
sda        229G  exfat                  Ultra
```

- `mmcblk0` is the **SD card — the operating system.** Never target this.
- `sda` is the USB stick. It reads ~229–239 G rather than 256 because manufacturers count in
  GB (10^9) and `lsblk` counts in GiB (2^30).

Confirm the size and MODEL match your stick before continuing. Every command below is
destructive if pointed at the wrong device.

---

## Step 2 — Unmount if the system auto-mounted it

```bash
sudo umount /dev/sda1 2>/dev/null; sudo umount /dev/sda 2>/dev/null; true
```

No output is the expected result.

---

## Step 3 — Wipe, partition, format

**This erases the stick.**

```bash
sudo wipefs -a /dev/sda
sudo parted /dev/sda --script mklabel gpt mkpart primary ext4 0% 100%
sudo partprobe /dev/sda
sudo mkfs.ext4 -L BSDATA /dev/sda1
```

`mkfs.ext4` prints a few lines about inode tables and takes 10–30 seconds. `partprobe` forces
the kernel to re-read the new partition table so `/dev/sda1` exists for the next command.

Verify:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL /dev/sda
```

`sda1` should now show `ext4` and label `BSDATA`.

---

## Step 4 — Mount it permanently at /mnt/data

```bash
sudo mkdir -p /mnt/data
UUID=$(sudo blkid -s UUID -o value /dev/sda1)
echo "UUID=$UUID  /mnt/data  ext4  defaults,noatime,nofail  0  2" | sudo tee -a /etc/fstab
sudo mount -a
sudo chown -R $USER:$USER /mnt/data
df -h /mnt/data
```

`df` should report ~225 G available on `/mnt/data`.

Three details in that fstab line matter:

- **`UUID=`** — a serial number baked into the filesystem, so the stick mounts correctly
  regardless of which USB port it's in. Using `/dev/sda` instead would be positional: move the
  stick to another port, and the Pi mounts nothing while the app happily writes to `/mnt/data`
  **on the SD card** — a silently empty database with no error anywhere.
- **`nofail`** — without it, a Pi that boots with the stick missing or failed drops to
  emergency mode and never comes up on the network. On a headless machine that means physically
  attaching a monitor and keyboard to recover.
- **`noatime`** — stops Linux writing a timestamp every time a file is merely *read*. Pointless
  write wear otherwise.

---

## Step 5 — Verify it survives a reboot

Do this now, before anything is built on top of it.

```bash
touch /mnt/data/hello && ls -la /mnt/data && rm /mnt/data/hello
sudo reboot
```

Wait ~30 seconds, reconnect, and confirm:

```bash
df -h /mnt/data
```

If it still shows ~225 G on `/dev/sda1`, the mount is permanent and the storage layer is done.
If it shows the SD card's size instead, the fstab entry didn't take — stop and fix that before
continuing.

---

## Layout once running

```
/                    ← 64 GB SD card (OS only)
└── mnt/
    └── data/        ← 256 GB USB stick
        ├── brightspace.db
        ├── vault/
        └── _originals/
```

Linux has no drive letters. Every device is grafted onto a folder in one tree, so anything
written below `/mnt/data` lands on the stick and everything else lands on the SD card. Programs
can't tell the difference, which is why the app just needs this one path.

SQLite runs here in WAL mode (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL`) — see
`PLAN.md` §4 for why.

---

# Part 2 — Putting the scraper on the Pi

Part 1 gave you storage. This part gets the code running on it, checking
Brightspace every 30 minutes on its own.

Do Part 1 first. Every command below assumes `df -h /mnt/data` reports the
stick, not the SD card.

---

## Step 6 — Get the code onto the Pi

Still in the SSH session:

```bash
sudo apt update
sudo apt install -y git python3-venv
git clone https://github.com/cooldude-7/uottawa-brightspace-scraper.git
cd uottawa-brightspace-scraper
```

If the repo is private, GitHub will ask for a username and password, and the
password it wants is a personal access token rather than your GitHub password.
Easier alternative: copy the folder from Windows instead, leaving out the
secrets and the database.

```powershell
scp -r C:\path\to\uOttawa-brightspace-scraper yourusername@raspberrypi.local:~/
```

---

## Step 7 — Hand over what cannot travel through git

Everything secret or changing is gitignored, so it has to be copied by hand —
once. Do it from the laptop: logging in needs a browser, and the Pi has none.

Stop `web.py` on the laptop first, so nothing has the database open. Then, in
Windows PowerShell, from the `scraper` folder:

```powershell
scp api_key.txt session.json me.json yourusername@raspberrypi.local:/mnt/data/
scp brightspace.db yourusername@raspberrypi.local:/mnt/data/
```

**Bring the database.** It is tempting to let the Pi start clean, and it is the
wrong call. `brightspace.db` holds every accept and dismiss you have made, the
ids of the calendar events already created, and the record of which documents
have been read. Starting empty means every deadline you dismissed comes back,
and every document is sent to the API again — the whole initial extraction
cost, paid twice, for nothing.

`me.json` carries your lab section. Without it the Pi shows you all five
sections' deadlines rather than your own.

If you have Google Calendar working on the laptop:

```powershell
scp google_client.json google_token.json yourusername@raspberrypi.local:/mnt/data/
```

Then the two folders, which spare the Pi re-downloading every document from
Brightspace — `download.py` skips any file it already has:

```powershell
scp -r extracted   yourusername@raspberrypi.local:/mnt/data/
scp -r _originals  yourusername@raspberrypi.local:/mnt/data/
```

`extracted` is small. `_originals` is a semester of slide decks and may be a
gigabyte or more, so leave it running. It is the least important of these —
without it the Pi simply fetches the files again on its first run.

Note they go to `/mnt/data/`, not into the code folder. Everything that is
either secret or changing lives on the stick; the code folder holds only code.

**`session.json` is worth being careful with.** It is not just a Brightspace
pass — it carries the uOttawa sign-on cookies too, so anyone who reads it can
be you on email and OneDrive, not only on course pages. The installer sets it
to owner-only. The other half of that is a Pi with a password worth having
and no ports forwarded to it from the internet.

---

## Step 8 — Install

```bash
cd ~/uottawa-brightspace-scraper
bash deploy/install.sh
```

It checks the stick is really mounted, builds a Python environment, checks the
files from Step 7 arrived, and installs two services:

| | |
|---|---|
| `brightspace-web` | the card list, always running, on port 8000 |
| `brightspace-update` | one scrape, started every 30 minutes by a timer |

Installing the Python packages takes several minutes on a 3B+. It is not stuck.

The script is safe to run again — after a `git pull`, run it again and it
replaces what it installed before.

---

## Step 9 — Check it actually works

Do not wait 30 minutes to find out. Run a scrape immediately and watch it:

```bash
sudo systemctl start brightspace-update
journalctl -u brightspace-update -f
```

`Ctrl+C` stops watching (it does not stop the scrape). What you want to see is
it finding your courses and reporting either new deadlines or nothing new.

Then open the web app from your phone or laptop browser, at the address the
installer printed — `http://raspberrypi.local:8000`, or the numeric one if
that name does not resolve.

Confirm the database is on the stick and not the card:

```bash
ls -lh /mnt/data/brightspace.db
```

Then reboot once and check it all comes back by itself:

```bash
sudo reboot
```

Wait a minute, reconnect, and:

```bash
systemctl is-active brightspace-web      # expect: active
systemctl list-timers brightspace-update # expect: a time in the next 30 min
```

---

## When the session expires

The Pi renews its own Brightspace session as long as the uOttawa sign-on
cookies are still good — that is the whole point of §2 in `PLAN.md`. When those
expire as well, after a few weeks, the Pi cannot fix it: logging in needs a
browser and a tap on your phone.

You will see it as scrapes failing in the log:

```bash
journalctl -u brightspace-update -n 30
```

The fix is Step 7 again — `python update.py` on the laptop to log in properly,
then copy the refreshed `session.json` across:

```powershell
scp scraper\session.json yourusername@raspberrypi.local:/mnt/data/
```

Until push notifications are built (Phase 5), nothing tells you this has
happened, so it is worth glancing at the card list every few days.

---

## Everyday commands

```bash
systemctl status brightspace-web            is the app up
journalctl -u brightspace-update -n 50      what the last scrape did
sudo systemctl start brightspace-update     scrape now, do not wait for the timer
systemctl list-timers brightspace-update    when the next one is due
sudo systemctl restart brightspace-web      after changing anything
```

## Still to come

Not part of this setup, and not needed for it to be useful:

- **cloudflared** — HTTPS and an address that works away from your home wifi.
  Right now the app is reachable only from your own network. Web push *requires*
  HTTPS, so this comes before notifications.
- **Push notifications** — including the one that says the session expired,
  instead of you noticing.
- **A heartbeat** — something that tells you when no scrape has succeeded in
  three hours, rather than the app quietly going stale.

---

# Part 3 — Reaching it from anywhere

Part 2 left the card list working on your own network. This gets it onto your
phone wherever you are, and onto the home screen as an app.

`PLAN.md` originally specified cloudflared here. It says Tailscale now, and §4
records why — the short version is that the web app has no password, so a
private network beats a public address.

---

## Step 10 — Tailscale on the Pi

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

`tailscale up` prints a login URL and waits. Open it in a browser, sign in, and
note which account you used — the phone needs the same one.

```bash
tailscale ip -4
```

That address, starting `100.`, works from anywhere. There is usually a plain
name too, so try `http://lucapi:8000` before resorting to digits.

Tailscale starts itself on boot, so this is a one-time step.

A useful side effect: SSH works from anywhere now, not just your own wifi.

---

## Step 11 — The phone

Install **Tailscale** from the App Store or Play Store, sign in with the same
account, and switch it on. iOS shows a VPN indicator while it runs; that is how
the private network works and it does not route your ordinary browsing anywhere.

Then open `http://100.x.y.z:8000` — **in Safari on iOS**, not Chrome. The
home-screen behaviour comes from Apple-specific tags that only Safari reads.

Share button → **Add to Home Screen**.

You get a dark tile with a white check, named *Deadlines*, that opens full
screen with no address bar.

This needs no HTTPS. Safari honours those tags over plain `http`, which is why
it works without a tunnel or a certificate.

**Test it properly:** turn wifi off, leave cell data on, and tap the icon. If
the cards load, it works from campus.

---

## What is still missing

- **Push notifications.** Nothing tells you a new deadline appeared — you have
  to open the app and look. Web push needs a secure context; Tailscale can issue
  real certificates for `*.ts.net` names, so test that before buying a domain
  for cloudflared.
- **A heartbeat.** Nothing tells you the Pi has stopped scraping either, which
  matters more the more you rely on it. Until it exists, `journalctl -u
  brightspace-update -n 20` is the manual version.
