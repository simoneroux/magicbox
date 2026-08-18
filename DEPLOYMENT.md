# Deployment Notes (Actual Setup)

This documents how the Magic Box is **actually deployed**, as opposed to the
generic instructions in the README. If something here disagrees with the
README, this file reflects reality on the running device.

> ⚠️ **Heads-up: the repo and the Pi have diverged.** The `magicbox.py` in this
> repository and the `~/magicbox/magicbox.py` running on the Pi are **not the
> same file** — the deployed version is more advanced (background Sonos warm-up,
> a tone cache with `play_sound(..., wait=...)`, in-process soco-cli, a 20 s
> timeout on CEC calls). Reconciling the two is tracked as an open item below.

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

## Known issues

- **CEC hangs for 20 s on TV-off.** `cec-client` times out
  (`TV off error: Command '['cec-client', '-s', '-d', '1']' timed out after 20
  seconds`) on every TV-off. Music playback is not blocked by this, but the TV
  never actually turns off. Needs CEC troubleshooting on the device.

## Open items

- [ ] Reconcile the deployed `~/magicbox/magicbox.py` with the repo (they have
      diverged; the Pi's copy is the source of truth for what actually runs).
- [ ] Confirm the purpose of `fast_magicbox.py`, `legacy_magicbox.py`,
      `dnlafinder.py`, and `start.sh`.
- [ ] Investigate the CEC 20 s timeout.
- [ ] Mouse controls: verify OS-level mouse detection and get mouse-control code
      onto the deployed script (see repo commit `070afe7`).
