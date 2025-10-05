# The Magic Box - Complete Installation & Setup Guide

A Raspberry Pi-powered NFC controller for Sonos speakers with TV control and video playback capabilities.

## Table of Contents
- [Overview](#overview)
- [What You'll Need](#what-youll-need)
- [Hardware Setup](#hardware-setup)
- [Software Installation](#software-installation)
- [Creating NFC Tags](#creating-nfc-tags)
- [Running the Magic Box](#running-the-magic-box)
- [Advanced Features](#advanced-features)
- [Troubleshooting](#troubleshooting)

---

## Overview

The Magic Box lets you control your home entertainment system by tapping NFC cards/tags:

- 🎵 **Play Music**: Tap a card to play albums/playlists from Spotify, Apple Music, Tidal, or Deezer
- 🎬 **Play Videos**: Stream content from Jellyfin or other sources to your TV
- 🎮 **Playback Controls**: Dedicated cards for play, stop, next/previous track, volume
- 📺 **TV Control**: Automatic TV power and input switching via HDMI-CEC
- 🔊 **Audio Feedback**: Built-in beep sounds for user feedback

### How It Works

1. Scan an NFC card with your Raspberry Pi
2. The Magic Box reads the card's content (music URL, video URL, or control command)
3. Executes the appropriate action (plays music on Sonos, plays video on TV, or sends control command)
4. Provides audio feedback through the Pi's headphone jack

---

## What You'll Need

### Hardware

**Required:**
- Raspberry Pi (3, 4, 5, or Zero 2W recommended)
- Waveshare PN532 NFC HAT
- MicroSD card (32GB+ recommended, Class 10/A2/U3)
- Power supply for your Pi
- NFC tags (NTAG213/215/216 recommended)

**For TV Control:**
- HDMI cable (standard or micro HDMI depending on your Pi)
- TV with HDMI-CEC support
- Optional: Speakers connected to Pi's headphone jack for audio feedback

### Network Requirements
- Sonos speakers on the same network
- Wi-Fi or Ethernet connection for Raspberry Pi
- (Optional) Access to streaming services: Spotify, Apple Music, Tidal, or Deezer

---

## Hardware Setup

### 1. PN532 NFC HAT Connection

The Waveshare PN532 can be connected via UART, I2C, or SPI. **UART is recommended** for ease of setup.

**UART Configuration:**
1. Set jumpers on the NFC HAT:
   - I0 → L (Low)
   - I1 → L (Low)

2. Configure DIP switches:
   ```
   SCK  MISO MOSI NSS  SCL  SDA  RX   TX
   OFF  OFF  OFF  OFF  OFF  OFF  ON   ON
   ```

3. Connect the HAT to your Raspberry Pi's 40-pin GPIO header

### 2. HDMI-CEC Setup

For TV control functionality:
1. Connect your Pi to your TV via HDMI
2. Enable HDMI-CEC in your TV settings (may be called "Anynet+" on Samsung, "Bravia Sync" on Sony, etc.)
3. On the Pi, enable CEC in `/boot/firmware/config.txt` (see Software Installation)

---

## Software Installation

### Option 1: Fresh Install (Recommended)

Start with a clean Raspberry Pi OS installation:

#### 1. Flash the SD Card

Download and install [Raspberry Pi Imager](https://www.raspberrypi.com/software/)

When flashing:
1. Choose: **Raspberry Pi OS Lite (64-bit)**
2. Click the **gear icon** for advanced settings:
   - ✅ Enable SSH
   - ✅ Set username (e.g., "pi") and password
   - ✅ Configure Wi-Fi network and password
   - ✅ Set locale settings
3. Write to SD card

#### 2. First Boot Setup

```bash
# SSH into your Pi
ssh pi@raspberrypi.local
# (Use the password you set during flashing)

# Change default password if needed
passwd

# Update system
sudo apt update
sudo apt upgrade -y
```

#### 3. Enable Required Interfaces

```bash
# Run Raspberry Pi configuration tool
sudo raspi-config

# Enable these interfaces:
# - Interface Options → Serial Port
#   - Login shell: NO
#   - Hardware serial: YES
# - Interface Options → I2C → Yes (for STEMMA speaker, optional)

# Reboot
sudo reboot
```

#### 4. Configure HDMI-CEC (for TV control)

```bash
# Edit boot config
sudo nano /boot/firmware/config.txt

# Add these lines at the end:
# HDMI CEC Configuration
hdmi_ignore_cec=0
hdmi_ignore_cec_init=0
hdmi_force_hotplug=1

# GPU memory for video playback
gpu_mem=256

# Save (Ctrl+O, Enter) and exit (Ctrl+X)
sudo reboot
```

#### 5. Install System Packages

```bash
# SSH back in after reboot
ssh pi@raspberrypi.local

# Install required packages
sudo apt install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    git \
    cec-utils \
    vlc \
    i2c-tools \
    libusb-dev \
    libpcsclite-dev \
    libtool \
    automake \
    autoconf

# Install numpy for audio feedback (optional)
sudo apt install -y python3-numpy
```

#### 6. Install nfc-tools (Optional - for testing)

```bash
# Clone and compile libnfc
cd ~
git clone https://github.com/nfc-tools/libnfc.git
cd libnfc
autoreconf -vis
./configure --prefix=/usr --sysconfdir=/etc
make
sudo make install

# Create NFC config
sudo mkdir -p /etc/nfc
sudo cp libnfc/libnfc.conf.sample /etc/nfc/libnfc.conf

# Edit config for UART
sudo nano /etc/nfc/libnfc.conf
# Uncomment and change the line to:
# device.connstring = "pn532_uart:/dev/ttyS0"

# Test NFC reader
nfc-list
# You should see "NFC device: pn532_uart:/dev/ttyS0 opened"
```

#### 7. Set Up Python Environment

```bash
# Create project directory
mkdir -p ~/magic_box
cd ~/magic_box

# Create virtual environment
python3 -m venv magicbox_env

# Activate environment
source magicbox_env/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install Python packages
pip install nfcpy soco soco-cli numpy
```

#### 8. Install Magic Box Code

```bash
# Still in ~/magic_box with virtual environment activated
# Create the main script
nano magic_box.py

# Copy and paste your Magic Box code
# (Use the "Best Version with Video" code from your documents)

# Save and exit (Ctrl+O, Enter, Ctrl+X)

# Make executable
chmod +x magic_box.py
```

#### 9. Create Configuration (Optional)

```bash
# Create config directory
mkdir -p ~/magic_box/config

# Create environment file for settings
nano ~/magic_box/config/env_vars

# Add your settings:
SONOS_ROOM=Kitchen
MAX_VOLUME=60
# (Optional) JELLYFIN_URL=http://your-jellyfin-server:8096

# Save and exit
```

### Option 2: Update Existing Installation

If you already have a Pi with Raspberry Pi OS:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install missing packages
sudo apt install -y python3-venv cec-utils vlc python3-numpy

# Enable UART in raspi-config
sudo raspi-config
# Interface Options → Serial Port → No to login shell, Yes to hardware

# Follow steps 4, 7, and 8 from Fresh Install above
```

---

## Creating NFC Tags

### Understanding Tag Format

Magic Box reads NDEF (NFC Data Exchange Format) records from tags. Each tag can contain multiple records.

### Tag Types

#### 1. Music Tags (Streaming Services)

Records needed:
- **URI Record**: Streaming service URL
- **Text Record** (optional): `name:Album Name`
- **Text Record** (optional): `mode:shuffle` to enable shuffle

Supported services:
- Spotify: `https://open.spotify.com/album/...` or `spotify:album:...`
- Apple Music: `https://music.apple.com/...`
- Tidal: `https://tidal.com/browse/album/...`
- Deezer: `https://www.deezer.com/...`

#### 2. Video Tags

Records needed:
- **URI Record**: Direct video URL (Jellyfin, etc.)
- **Text Record**: `type:video`
- **Text Record** (optional): `name:Movie Title`

Example Jellyfin URL:
```
http://192.168.1.100:8096/Items/abc123/Download?api_key=yourkey
```

#### 3. Control Tags

Record needed:
- **Text Record** with command name only (no prefix)

Available commands:
- `play` - Resume playback
- `stop` - Stop playback (works for both music and video)
- `next` - Next track
- `prev` - Previous track
- `vol_up` - Increase volume by 5
- `vol_down` - Decrease volume by 5
- `tv_on` - Turn on TV
- `tv_off` - Turn off TV

### Writing Tags

#### Option 1: NFC Tools App (Mobile - Easiest)

**Android:** [NFC Tools](https://play.google.com/store/apps/details?id=com.wakdev.wdnfc)  
**iOS:** [NFC Tools](https://apps.apple.com/app/nfc-tools/id1252962749)

**For Music Tags:**
1. Open NFC Tools app
2. Tap "Write"
3. Add records:
   - "Add a record" → "URL/URI" → paste streaming URL
   - "Add a record" → "Text" → enter `name:Your Album Name`
   - (Optional) "Add a record" → "Text" → enter `mode:shuffle`
4. Tap "Write" and scan your tag

**For Control Tags:**
1. Open NFC Tools app
2. Tap "Write"
3. Add records:
   - "Add a record" → "Text" → enter just the command (e.g., `play`)
4. Tap "Write" and scan your tag

#### Option 2: Python Script (Advanced)

```python
#!/usr/bin/env python3
import nfc
import ndef

def write_music_tag():
    """Write a music tag with shuffle mode"""
    records = [
        ndef.UriRecord("https://open.spotify.com/album/your_album_id"),
        ndef.TextRecord("name:My Favorite Album"),
        ndef.TextRecord("mode:shuffle")
    ]
    
    def on_connect(tag):
        if tag.ndef:
            tag.ndef.records = records
            print("✅ Music tag written successfully!")
            return True
        return False
    
    clf = nfc.ContactlessFrontend('tty:ttyS0:pn532')
    clf.connect(rdwr={'on-connect': on_connect})
    clf.close()

def write_control_tag(command):
    """Write a control command tag"""
    records = [ndef.TextRecord(command)]
    
    def on_connect(tag):
        if tag.ndef:
            tag.ndef.records = records
            print(f"✅ Control tag '{command}' written successfully!")
            return True
        return False
    
    clf = nfc.ContactlessFrontend('tty:ttyS0:pn532')
    clf.connect(rdwr={'on-connect': on_connect})
    clf.close()

if __name__ == "__main__":
    # Example: Write a control tag
    write_control_tag("play")
    
    # Example: Write a music tag
    # write_music_tag()
```

Save as `write_tag.py` and run:
```bash
source ~/magic_box/magicbox_env/bin/activate
python3 write_tag.py
```

---

## Running the Magic Box

### Manual Start

```bash
# Navigate to project directory
cd ~/magic_box

# Activate virtual environment
source magicbox_env/bin/activate

# Run Magic Box (replace "Kitchen" with your Sonos room name)
python3 magic_box.py Kitchen
```

You should see:
```
✨ Magic Box Ready
🔈 Sonos: Kitchen
📺 TV Control: Enabled (with smart detection)
🎬 Video Playback: Enabled
🎮 Universal Controls: stop, vol_up, vol_down

Scan tag to begin... (Ctrl+C or Ctrl+Z to quit)
```

### Test Your Setup

1. **Test NFC Reader:**
   ```bash
   nfc-list
   # Should show: "NFC device: pn532_uart:/dev/ttyS0 opened"
   ```

2. **Test Sonos Connection:**
   ```bash
   sonos Kitchen volume
   # Should return current volume level
   ```

3. **Test HDMI-CEC:**
   ```bash
   echo 'on 0' | cec-client -s -d 1
   # Your TV should turn on
   
   echo 'standby 0' | cec-client -s -d 1
   # Your TV should turn off
   ```

4. **Scan an NFC Card:**
   - Place a music or control tag on the reader
   - You should hear a beep
   - The action should execute (music plays, volume changes, etc.)

### Run on Boot (Optional)

Create a systemd service:

```bash
# Create service file
sudo nano /etc/systemd/system/magic-box.service
```

Add this content:
```ini
[Unit]
Description=Magic Box NFC Controller
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/magic_box
Environment="PATH=/home/pi/magic_box/magicbox_env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/pi/magic_box/magicbox_env/bin/python3 /home/pi/magic_box/magic_box.py Kitchen
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable magic-box.service

# Start service now
sudo systemctl start magic-box.service

# Check status
sudo systemctl status magic-box.service

# View logs
sudo journalctl -u magic-box.service -f
```

To stop the service:
```bash
sudo systemctl stop magic-box.service
```

---

## Advanced Features

### Audio Feedback Customization

The Magic Box plays beeps through the Pi's headphone jack:
- **Scan beep**: 660 Hz, 0.1s (tag detected)
- **Success beep**: 880 Hz, 0.2s (action succeeded)
- **Error beep**: 220 Hz, 0.3s (action failed)
- **Info beep**: 440 Hz, 0.2s (informational)

To adjust beep sounds, modify the `play_sound()` method in `magic_box.py`.

### TV Smart Detection

The Magic Box checks if your TV is already on before sending the power-on command, reducing the 3-4 second wait time when the TV is already powered on.

This works via HDMI-CEC's power status query.

### Multiple Sonos Rooms

To control different rooms, create multiple service instances:

```bash
# Kitchen instance
sudo cp /etc/systemd/system/magic-box.service /etc/systemd/system/magic-box-kitchen.service
sudo nano /etc/systemd/system/magic-box-kitchen.service
# Change: ExecStart line to use "Kitchen"

# Bedroom instance
sudo cp /etc/systemd/system/magic-box.service /etc/systemd/system/magic-box-bedroom.service
sudo nano /etc/systemd/system/magic-box-bedroom.service
# Change: ExecStart line to use "Bedroom"

# Enable both
sudo systemctl enable magic-box-kitchen.service
sudo systemctl enable magic-box-bedroom.service
```

### Debug Mode

Run with verbose logging:

```bash
# Edit magic_box.py
# Change:
logging.basicConfig(level=logging.INFO, ...)
# To:
logging.basicConfig(level=logging.DEBUG, ...)
```

---

## Troubleshooting

### NFC Reader Not Detected

**Symptoms:** `nfc-list` shows no devices, or script fails with "NFC setup failed"

**Solutions:**
1. Check jumper settings (I0=L, I1=L for UART)
2. Verify DIP switches (only RX and TX should be ON)
3. Check UART is enabled:
   ```bash
   ls -l /dev/ttyS0
   # Should show: crw-rw---- 1 root dialout
   ```
4. Add your user to dialout group:
   ```bash
   sudo usermod -a -G dialout pi
   # Logout and login again
   ```
5. Test with different device paths in code:
   ```python
   # Try these in order:
   'tty:ttyS0:pn532'    # Most common
   'tty:ttyAMA0:pn532'  # Alternate
   'usb:001'            # USB adapter
   ```

### Sonos Speaker Not Found

**Symptoms:** "Failed to find speaker" or timeout errors

**Solutions:**
1. Test Sonos CLI directly:
   ```bash
   sonos Kitchen volume
   ```
2. Verify speaker is on same network:
   ```bash
   sonos-discover
   # Should list all speakers with IPs
   ```
3. Use IP address instead of room name:
   ```bash
   python3 magic_box.py 192.168.1.50
   ```
4. Check firewall rules (if enabled):
   ```bash
   sudo ufw allow 1400:1499/tcp
   sudo ufw allow 54000:54099/tcp
   ```

### TV Control Not Working

**Symptoms:** TV doesn't respond to CEC commands

**Solutions:**
1. Verify HDMI-CEC is enabled on TV (check TV settings)
2. Test CEC manually:
   ```bash
   echo 'scan' | cec-client -s -d 1
   # Should show your TV
   ```
3. Check config.txt settings:
   ```bash
   grep hdmi /boot/firmware/config.txt
   # Should show:
   # hdmi_ignore_cec=0
   # hdmi_ignore_cec_init=0
   # hdmi_force_hotplug=1
   ```
4. Try different HDMI port on TV
5. Reboot both Pi and TV:
   ```bash
   sudo reboot
   ```

### Video Playback Issues

**Symptoms:** Video doesn't play, or VLC errors

**Solutions:**
1. Test VLC directly:
   ```bash
   cvlc --fullscreen http://your-video-url
   ```
2. Check GPU memory:
   ```bash
   vcgencmd get_mem gpu
   # Should be 256M or higher
   ```
3. Increase network caching (edit magic_box.py):
   ```python
   '--network-caching=5000',  # Increase from 3000
   ```
4. Check video URL is accessible:
   ```bash
   curl -I http://your-video-url
   # Should return 200 OK
   ```

### No Audio Feedback Beeps

**Symptoms:** No beep sounds when scanning tags

**Solutions:**
1. Check audio output:
   ```bash
   aplay -l
   # Should list audio devices
   ```
2. Test audio manually:
   ```bash
   speaker-test -t sine -f 1000 -c 2 -l 1
   ```
3. Set default audio output:
   ```bash
   sudo raspi-config
   # System Options → Audio → Choose headphones
   ```
4. Install numpy if missing:
   ```bash
   sudo apt install python3-numpy
   ```

### Tags Not Reading

**Symptoms:** Scanning tag does nothing

**Solutions:**
1. Verify tag format with mobile NFC app
2. Check tag is NDEF formatted
3. Ensure tag is placed correctly on reader (center of antenna)
4. Try different tag type (NTAG213, NTAG215, etc.)
5. Test tag with `nfc-list`:
   ```bash
   nfc-list
   # Place tag on reader, should detect it
   ```

### Script Crashes or Hangs

**Solutions:**
1. Check system logs:
   ```bash
   sudo journalctl -u magic-box.service -n 50
   ```
2. Run manually to see errors:
   ```bash
   cd ~/magic_box
   source magicbox_env/bin/activate
   python3 magic_box.py Kitchen
   ```
3. Update dependencies:
   ```bash
   source magicbox_env/bin/activate
   pip install --upgrade nfcpy soco soco-cli
   ```
4. Check for hardware issues:
   ```bash
   # Monitor system temperature
   vcgencmd measure_temp
   
   # Check for low voltage warnings
   vcgencmd get_throttled
   # 0x0 is good, anything else indicates power issues
   ```

---

## Getting Help

If you're still having issues:

1. **Check logs:** `sudo journalctl -u magic-box.service -f`
2. **Enable debug logging:** Set `logging.DEBUG` in code
3. **Test components individually:**
   - NFC: `nfc-list`
   - Sonos: `sonos Kitchen volume`
   - CEC: `echo 'scan' | cec-client -s -d 1`
   - Video: `cvlc --fullscreen test-video.mp4`

### Useful Commands

```bash
# Check service status
sudo systemctl status magic-box.service

# Restart service
sudo systemctl restart magic-box.service

# View real-time logs
sudo journalctl -u magic-box.service -f

# Test NFC reader
nfc-list

# Test Sonos
sonos Kitchen volume

# Test TV control
echo 'on 0' | cec-client -s -d 1

# Check system resources
htop

# Check network connectivity
ping 8.8.8.8
```

---

## What We've Built

The Magic Box represents a complete integration of:

1. **NFC Reading**: Waveshare PN532 HAT for tag detection
2. **Music Control**: Sonos speakers via SoCo-CLI
3. **TV Control**: HDMI-CEC for power and input switching
4. **Video Playback**: VLC for streaming video content
5. **Audio Feedback**: Raspberry Pi audio output for user feedback
6. **State Management**: Graceful handling of playback transitions

All controlled by simply tapping NFC cards!

---

## Credits & Resources

- [SoCo-CLI](https://github.com/avantrec/soco-cli) - Sonos command-line control
- [nfcpy](https://github.com/nfcpy/nfcpy) - Python NFC library
- [nfc-tools](http://nfc-tools.org/) - libnfc and utilities
- [Waveshare PN532 NFC HAT](https://www.waveshare.com/pn532-nfc-hat.htm)

---

## License

This project is for personal use. Trademarks belong to their respective owners:
- Sonos® is a registered trademark of Sonos, Inc.
- Spotify®, Apple Music®, Tidal®, Deezer® are trademarks of their respective companies
- HDMI® and HDMI-CEC® are trademarks of HDMI Licensing Administrator, Inc.
