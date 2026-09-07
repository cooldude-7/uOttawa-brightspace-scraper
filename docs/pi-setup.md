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
