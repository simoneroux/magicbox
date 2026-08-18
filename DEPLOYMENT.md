# Deployment Notes (Actual Setup)

This documents how the Magic Box is **actually deployed**, as opposed to the
generic instructions in the README. If something here disagrees with the
README, this file reflects reality on the running device.

> ✅ **Repo and Pi are now reconciled.** As of the mouse-control change, the
> repo's `magicbox.py` is the deployed version (background Sonos warm-up, tone
> cache with `play_sound(..., wait=...)`, in-process soco-cli, 20 s CEC timeout)
> **plus** optional mouse control. Deploy by copying the repo's `magicbox.py`
> onto the Pi (see "Deploying an update" below). The Pi remains the source of
> truth for anything not yet committed.

## Host

| | |
|---|---|
| Hostname | `magicbox` (reachable as `magicbox.local`) |
| OS | Raspberry Pi OS (Debian 12 / Bookworm, kernel 6.12, aarch64) |
| Login user | `sroux` |
| Project directory | `/home/sroux/magicbox` |
| Virtualenv | `/home/sroux/magicbox/magicbox_env` |
| Sonos room argument | `Salon` (the speaker's real name is "Sonos Salon"; soco-cli matches on the partial name) |

### Files in the project directory

| File | Purpose |
|---|---|
| `magicbox.py` | The deployed controller (the one the service runs). |
| `fast_magicbox.py` | Alternate/experimental variant — *purpose to confirm*. |
| `legacy_magicbox.py` | Older version, kept for reference — *purpose to confirm*. |
| `dnlafinder.py` | Helper script — *purpose to confirm*. |
| `start.sh` | Startup wrapper — *contents to confirm*. |
| `magic.png` | Image asset. |
| `magicbox_env/` | Python virtualenv. |
| `libnfc/` | Local libnfc build/checkout. |

## Running as a service

The unit is `magicbox.service`. It does **not** run Python directly — it
launches the app inside a **detached `screen` session named `magicbox`**, so the
app's console output goes to that screen session, **not** to `journalctl`.

`/etc/systemd/system/magicbox.service` (secrets redacted):

```ini
[Unit]
Description=Magic Box NFC Controller
After=network.target sound.target

[Service]
Type=forking
User=sroux
WorkingDirectory=/home/sroux/magicbox
ExecStart=/usr/bin/screen -dmS magicbox /home/sroux/magicbox/magicbox_env/bin/python3 /home/sroux/magicbox/magicbox.py Salon
ExecStop=/usr/bin/screen -S magicbox -X quit
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment variables
Environment="PATH=/home/sroux/magicbox/magicbox_env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="VIRTUAL_ENV=/home/sroux/magicbox/magicbox_env"
Environment="ROKU_IP=<your-roku-ip>"
Environment="PLEX_TOKEN=<your-plex-token>"

[Install]
WantedBy=multi-user.target
```

> 🔐 Keep the real `PLEX_TOKEN` and `ROKU_IP` values only in the unit file on the
> Pi — never commit them. If a token is ever exposed, rotate it in Plex.

### Common operations

```bash
# Restart / stop / status
sudo systemctl restart magicbox.service
sudo systemctl stop magicbox.service
sudo systemctl status magicbox.service

# View the APP's live output (it's in the screen session, not the journal):
sudo -u sroux screen -r magicbox
#   detach again with:  Ctrl+A then d
#   do NOT press Ctrl+C — that stops the app

# The journal only shows systemd start/stop lines, plus anything the app
# writes to stderr after screen exits:
sudo journalctl -u magicbox.service -f
```

## Installing Python packages into the deployed venv

Always target the venv the service uses. Either activate it, or call its `pip`
directly:

```bash
/home/sroux/magicbox/magicbox_env/bin/pip install <package>
sudo systemctl restart magicbox.service
```

## Deploying an update

The service runs `/home/sroux/magicbox/magicbox.py`. To ship a new version from
this repo onto the Pi:

```bash
# On the Pi, in the project dir, back up the current file first:
cd /home/sroux/magicbox
cp magicbox.py magicbox.py.bak

# Pull the new magicbox.py in (git pull, scp, or paste), then restart:
sudo systemctl restart magicbox.service

# Watch the app come up (output is in the screen session, not the journal):
sudo -u sroux screen -r magicbox
#   detach with Ctrl+A then d
```

### Mouse control

Mouse control is optional and auto-enabled when a mouse is detected. It needs
the `evdev` package in the service venv, and the service user in the `input`
group:

```bash
/home/sroux/magicbox/magicbox_env/bin/pip install evdev   # already installed (1.9.3)
groups sroux                                               # confirm 'input' is present
# if not: sudo usermod -a -G input sroux   (then restart the service)
```

On startup you'll see `Mouse control enabled: <name>` in the screen session.
Mappings: scroll wheel = volume, side buttons = next/previous, middle = play/stop.

## Known issues

- **CEC hangs for 20 s on TV-off.** `cec-client` times out
  (`TV off error: Command '['cec-client', '-s', '-d', '1']' timed out after 20
  seconds`) on every TV-off. Music playback is not blocked by this, but the TV
  never actually turns off. Needs CEC troubleshooting on the device.

## Open items

- [x] Reconcile the deployed `~/magicbox/magicbox.py` with the repo — the repo's
      `magicbox.py` now matches the deployed version plus mouse control.
- [x] Mouse hardware verified: `Logitech Wireless Mouse` on `/dev/input/event3`,
      `evdev` 1.9.3 in the venv, `sroux` can read the device. Remaining step is
      to deploy the new `magicbox.py` (above) and restart.
- [ ] Confirm the purpose of `fast_magicbox.py`, `legacy_magicbox.py`,
      `dnlafinder.py`, and `start.sh` (none are in the repo yet).
- [ ] Investigate the CEC 20 s timeout on TV-off.
