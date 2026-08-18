#!/usr/bin/env python3
import nfc
import subprocess
import sys
import signal
import logging
import threading
import time
import re
import os
import numpy as np
import tempfile

class MagicBox:
    # Fixed feedback tones — generated once and replayed, never rebuilt per scan.
    SOUNDS = {
        "success": {"freq": 880, "duration": 0.2},
        "error": {"freq": 220, "duration": 0.3},
        "info": {"freq": 440, "duration": 0.2},
        "scan": {"freq": 660, "duration": 0.1},
    }

    def __init__(self, room):
        self.room = room
        self.speaker_ip = None  # Cached once, reused to skip per-command discovery
        self.sound_files = {}   # sound_type -> pre-generated WAV path
        self.clf = None
        self.is_running = True
        self.vlc_process = None
        self.mouse_device = None  # evdev device, if a mouse is connected
        self.is_playing = False   # best-effort state for the middle-click toggle
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def _write_wav(self, path, freq, duration):
        """Render a single sine-wave tone to a mono 16-bit WAV file."""
        sample_rate = 22050
        amplitude = 0.5

        t = np.linspace(0, duration, int(sample_rate * duration), False)
        tone = np.sin(2 * np.pi * freq * t) * amplitude
        audio = (tone * 32767).astype(np.int16)

        with open(path, 'wb') as f:
            f.write(b'RIFF')
            f.write((36 + len(audio) * 2).to_bytes(4, 'little'))
            f.write(b'WAVE')
            f.write(b'fmt ')
            f.write((16).to_bytes(4, 'little'))
            f.write((1).to_bytes(2, 'little'))
            f.write((1).to_bytes(2, 'little'))
            f.write((sample_rate).to_bytes(4, 'little'))
            f.write((sample_rate * 2).to_bytes(4, 'little'))
            f.write((2).to_bytes(2, 'little'))
            f.write((16).to_bytes(2, 'little'))
            f.write(b'data')
            f.write((len(audio) * 2).to_bytes(4, 'little'))
            f.write(audio.tobytes())

    def prepare_sounds(self):
        """Pre-generate every feedback tone once so scans just replay a file."""
        for name, cfg in self.SOUNDS.items():
            try:
                fd, path = tempfile.mkstemp(suffix='.wav', prefix=f'magicbox_{name}_')
                os.close(fd)
                self._write_wav(path, cfg["freq"], cfg["duration"])
                self.sound_files[name] = path
            except Exception as e:
                self.logger.error(f"Failed to prepare sound '{name}': {e}")

    def play_sound(self, sound_type="success"):
        """Play a pre-generated feedback tone without blocking the caller."""
        try:
            path = self.sound_files.get(sound_type) or self.sound_files.get("success")
            if not path:
                # Sounds weren't prepared (e.g. play_sound called very early);
                # skip rather than rebuilding a WAV on the hot path.
                return
            # Non-blocking: the beep plays while we get on with parsing the tag
            # and issuing the Sonos command.
            subprocess.Popen(['aplay', '-q', path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception as e:
            self.logger.error(f"Failed to play sound: {e}")

    def is_tv_on(self):
        """Check if TV is already on"""
        try:
            result = subprocess.run(
                ['cec-client', '-s', '-d', '1'],
                input='pow 0\n',  # Changed from b'pow 0\n' to regular string
                capture_output=True,
                text=True  # This tells subprocess to handle text encoding
            )
            # Check if response contains "power status: on"
            return 'power status: on' in result.stdout.lower()
        except Exception as e:
            self.logger.error(f"TV status check error: {e}")
            return False

    def tv_on(self):
        """Turn on TV and switch to Pi input - optimized with status check"""
        try:
            # Check if TV is already on
            if self.is_tv_on():
                print("📺 TV already on, switching input...")
                # Just switch input
                subprocess.run(
                    ['cec-client', '-s', '-d', '1'],
                    input='as\n',  # Changed from b'as\n'
                    capture_output=True,
                    text=True
                )
                time.sleep(1)  # Short wait for input switch
                print("✅ TV ready")
                return True
            
            # TV is off, need to turn it on
            print("📺 Turning on TV...")
            
            # Turn on
            subprocess.run(
                ['cec-client', '-s', '-d', '1'],
                input='on 0\n',  # Changed from b'on 0\n'
                capture_output=True,
                text=True
            )
            
            # Reduced wait from 4 to 3 seconds
            print("📺 Waiting 3 seconds...")
            time.sleep(3)
            
            # Switch input
            print("📺 Switching to Pi input...")
            subprocess.run(
                ['cec-client', '-s', '-d', '1'],
                input='as\n',  # Changed from b'as\n'
                capture_output=True,
                text=True
            )
            
            # Reduced wait from 2 to 1 second
            time.sleep(1)
            
            print("✅ TV ready")
            return True
            
        except Exception as e:
            self.logger.error(f"TV on error: {e}")
            return False

    def tv_off(self):
        """Turn off TV"""
        try:
            print("📺 Turning off TV...")
            subprocess.run(
                ['cec-client', '-s', '-d', '1'],
                input='standby 0\n',  # Changed from b'standby 0\n'
                capture_output=True,
                text=True
            )
            return True
        except Exception as e:
            self.logger.error(f"TV off error: {e}")
            return False

    def stop_video(self):
        """Stop any running video"""
        try:
            if self.vlc_process:
                print("⏹️ Stopping video...")
                self.vlc_process.terminate()
                try:
                    self.vlc_process.wait(timeout=2)
                except:
                    self.vlc_process.kill()
                self.vlc_process = None
            
            # Also kill any stray VLC processes
            subprocess.run(['pkill', 'vlc'], capture_output=True)
            
        except Exception as e:
            self.logger.error(f"Stop video error: {e}")

    def play_video(self, url, title=None):
        """Play video from URL (Jellyfin, etc) - NON-BLOCKING"""
        try:
            print(f"🎬 Playing video: {title or url}")
            
            # Stop any current video
            self.stop_video()
            
            # Stop any music
            self.run_sonos_command("stop")
            
            # Turn on TV and switch input (optimized)
            self.tv_on()
            
            # Start video in background
            self.vlc_process = subprocess.Popen([
                'cvlc',
                '--fullscreen',
                '--network-caching=3000',
                '--play-and-exit',
                url
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            print("✅ Video playing (scan another card to control)")
            return True
            
        except Exception as e:
            self.logger.error(f"Video playback error: {e}")
            return False

    def resolve_speaker_ip(self):
        """Resolve the room name to a speaker IP once and cache it.

        soco-cli re-runs a full network discovery on every `sonos <RoomName> ...`
        call. Passing an IP address instead lets it connect directly and skip
        discovery, so we pay that cost a single time here and reuse the result
        for every subsequent command.
        """
        if self.speaker_ip:
            return self.speaker_ip

        # If the user already started us with an IP address, use it as-is.
        if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', self.room):
            self.speaker_ip = self.room
            return self.speaker_ip

        try:
            import soco
            device = soco.discovery.by_name(self.room)
            if device is None:
                # Fall back to a full scan and match the player name case-insensitively.
                for candidate in (soco.discovery.discover() or []):
                    if candidate.player_name.lower() == self.room.lower():
                        device = candidate
                        break
            if device is not None:
                self.speaker_ip = device.ip_address
                self.logger.info(f"Resolved '{self.room}' to {self.speaker_ip}")
        except Exception as e:
            # Not fatal: we fall back to addressing the speaker by room name,
            # which still works, just with per-command discovery.
            self.logger.debug(f"Speaker IP resolution failed: {e}")

        return self.speaker_ip

    def _sonos_target(self):
        """Prefer the cached IP (no discovery); fall back to the room name."""
        return self.speaker_ip or self.room

    def run_sonos_command(self, *args):
        """Execute a single Sonos command via soco-cli"""
        cmd = ["sonos", self._sonos_target()] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def run_sonos_sequence(self, *commands):
        """Execute several Sonos commands in a single soco-cli invocation.

        soco-cli runs multiple commands separated by ':' in one process, so this
        collapses N connections into one. Each element of `commands` is a tuple
        of args, e.g. ("clear_queue",), ("play_sharelink", url).
        """
        target = self._sonos_target()
        cmd = ["sonos"]
        for i, command in enumerate(commands):
            if i > 0:
                cmd.append(":")
            cmd.append(target)
            cmd.extend(command)
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def play_music(self, url, title=None, shuffle=False):
        """Play music from a streaming service URL"""
        try:
            # Video stop and TV-off don't affect whether the music starts, so run
            # them off the critical path — the play command fires immediately
            # instead of waiting on a CEC round-trip and VLC teardown.
            threading.Thread(target=self._pre_music_cleanup, daemon=True).start()

            # Clear the queue, load the share link, and set shuffle in a SINGLE
            # soco-cli invocation (one connection instead of three).
            shuffle_state = "on" if shuffle else "off"
            code, _, stderr = self.run_sonos_sequence(
                ("clear_queue",),
                ("play_sharelink", url),
                ("shuffle", shuffle_state),
            )

            if code == 0:
                self.is_playing = True
                if shuffle:
                    print(f"🔀 Playing shuffled: {title or 'Music from tag'}")
                else:
                    print(f"▶️ Playing: {title or 'Music from tag'}")
                return True
            else:
                print(f"❌ Failed: {stderr}")
                return False

        except Exception as e:
            self.logger.error(f"Error playing URL: {e}")
            print("❌ Failed to play music from tag")
            return False

    def _pre_music_cleanup(self):
        """Stop video playback and turn the TV off (runs off the critical path)."""
        try:
            self.stop_video()
            self.tv_off()
        except Exception as e:
            self.logger.error(f"Pre-music cleanup error: {e}")

    def handle_control(self, command):
        """Handle basic playback controls - universal for both music and video"""
        commands = {
            "play": ("play", "▶️ Playing"),
            "stop": lambda: (self.stop_video(), self.run_sonos_command("stop")),
            "next": ("next", "⏭️ Next track"),
            "prev": ("previous", "⏮️ Previous track"),
            "vol_up": lambda: self.adjust_volume(5),
            "vol_down": lambda: self.adjust_volume(-5),
            "tv_on": lambda: self.tv_on(),
            "tv_off": lambda: self.tv_off()
        }

        if command not in commands:
            self.play_sound("error")
            return False

        # Keep the middle-click toggle roughly in sync with reality.
        if command == "play":
            self.is_playing = True
        elif command == "stop":
            self.is_playing = False

        action = commands[command]
        if callable(action):
            action()
            self.play_sound("success")
            print(f"✅ {command}")
        else:
            code, _, stderr = self.run_sonos_command(action[0])
            self.play_sound("success" if code == 0 else "error")
            print(action[1] if code == 0 else f"❌ Failed: {stderr}")

    def adjust_volume(self, delta):
        """Adjust volume up or down by delta - universal control"""
        code, volume, _ = self.run_sonos_command("volume")
        if code == 0:
            current = int(volume)
            new_volume = max(0, min(60, current + delta))
            self.run_sonos_command("volume", str(new_volume))
            print(f"{'🔊' if delta > 0 else '🔉'} {new_volume}%")

    def on_connect(self, tag):
        """Handle NFC tag connection"""
        try:
            self.play_sound("scan")
            
            if not tag.ndef:
                print("❌ Not a valid NDEF tag")
                self.play_sound("error")
                return True
                
            # Track card settings
            card_name = None
            content_type = None
            shuffle = False
            url = None
                
            # Parse tag
            for record in tag.ndef.records:
                if record.type == "urn:nfc:wkt:T":
                    if ":" in record.text:
                        identifier, content = record.text.lower().split(":", 1)
                        if identifier == "name":
                            card_name = record.text.split(":", 1)[1]
                            print(f"\n💳 Card: {card_name}")
                        elif identifier == "mode" and content == "shuffle":
                            shuffle = True
                        elif identifier == "type":
                            content_type = content
                            
                elif record.type == "urn:nfc:wkt:U":
                    url = record.uri
            
            # Handle different content types
            if content_type == "video" and url:
                # Play video (Jellyfin, direct URLs)
                result = self.play_video(url, card_name)
                self.play_sound("success" if result else "error")
                return True
                
            elif url and re.match(r'^https?://(open\.spotify\.com|music\.apple\.com|tidal\.com|www\.deezer\.com)/', url):
                # Play music
                result = self.play_music(url, card_name, shuffle)
                self.play_sound("success" if result else "error")
                return True
                
            elif not url:
                # Handle control commands (universal for music and video)
                for record in tag.ndef.records:
                    if record.type == "urn:nfc:wkt:T" and ":" not in record.text:
                        command = record.text.lower()
                        valid_commands = ["play", "stop", "next", "prev", "vol_up", "vol_down", "tv_on", "tv_off"]
                        if command in valid_commands:
                            self.handle_control(command)
                            return True
            
            print("❌ No supported content found on tag")
            self.play_sound("error")
            return True

        except Exception as e:
            self.logger.error(f"Error handling tag: {e}")
            self.play_sound("error")
            return False

    def setup_nfc(self):
        """Initialize NFC reader"""
        try:
            for path in ['tty:ttyS0:pn532', 'tty:ttyAMA0:pn532']:
                try:
                    self.clf = nfc.ContactlessFrontend(path)
                    if self.clf:
                        return True
                except Exception as e:
                    self.logger.debug(f"NFC init failed {path}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"NFC setup error: {e}")
            return False

    def start_nfc_listener(self):
        """Start NFC tag listener loop"""
        while self.is_running:
            try:
                self.clf.connect(rdwr={'on-connect': self.on_connect})
            except Exception as e:
                self.logger.error(f"NFC error: {e}")
                time.sleep(1)

    def setup_mouse(self):
        """Find a connected mouse to use for playback control (optional).

        Uses python-evdev. If it isn't installed or no mouse is present, mouse
        control is simply disabled and the NFC reader keeps working as before.
        """
        try:
            import evdev
        except ImportError:
            self.logger.info("python-evdev not installed; mouse control disabled")
            return False

        try:
            self.mouse_device = self._find_mouse_device(evdev)
        except Exception as e:
            self.logger.error(f"Mouse detection error: {e}")
            return False

        if self.mouse_device:
            self.logger.info(f"Mouse control enabled: {self.mouse_device.name}")
            return True

        self.logger.info("No mouse found; mouse control disabled")
        return False

    def _find_mouse_device(self, evdev):
        """Return the first input device that looks like a mouse, or None."""
        from evdev import ecodes
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except Exception:
                continue
            caps = dev.capabilities()
            rel_axes = caps.get(ecodes.EV_REL, [])
            keys = caps.get(ecodes.EV_KEY, [])
            # A mouse reports relative axes (movement/wheel) and mouse buttons.
            if ecodes.REL_WHEEL in rel_axes or ecodes.BTN_MOUSE in keys:
                return dev
        return None

    def _toggle_play_stop(self):
        """Middle-click: stop if we think we're playing, otherwise resume."""
        self.handle_control("stop" if self.is_playing else "play")

    def start_mouse_listener(self):
        """Map mouse events to playback controls.

        Scroll wheel -> volume, side buttons -> next/previous track,
        middle click -> play/stop toggle.
        """
        from evdev import ecodes
        try:
            for event in self.mouse_device.read_loop():
                if not self.is_running:
                    break

                if event.type == ecodes.EV_REL and event.code == ecodes.REL_WHEEL:
                    if event.value > 0:
                        self.handle_control("vol_up")
                    elif event.value < 0:
                        self.handle_control("vol_down")

                # Buttons: act on press only (value == 1), not release/autorepeat.
                elif event.type == ecodes.EV_KEY and event.value == 1:
                    if event.code in (ecodes.BTN_FORWARD, ecodes.BTN_EXTRA):
                        self.handle_control("next")
                    elif event.code in (ecodes.BTN_BACK, ecodes.BTN_SIDE):
                        self.handle_control("prev")
                    elif event.code == ecodes.BTN_MIDDLE:
                        self._toggle_play_stop()

        except Exception as e:
            self.logger.error(f"Mouse listener error: {e}")

    def handle_quit(self, signum, frame):
        """Clean shutdown on Ctrl+C/Z"""
        print("\n👋 Shutting down Magic Box...")
        self.is_running = False
        
        # Stop everything
        self.stop_video()
        self.run_sonos_command("stop")
        self.tv_off()
        
        if self.clf:
            self.clf.close()
        
        self.play_sound("info")
        time.sleep(0.3)

        # Remove the pre-generated tone files.
        for path in self.sound_files.values():
            try:
                os.unlink(path)
            except OSError:
                pass

        sys.exit(0)

    def start(self):
        """Start the Magic Box"""
        # Generate the feedback tones once, up front, so every later beep is
        # just a file replay (and so the setup-failure beep below works).
        self.prepare_sounds()

        if not self.setup_nfc():
            print("❌ NFC setup failed")
            self.play_sound("error")
            return

        signal.signal(signal.SIGINT, self.handle_quit)
        signal.signal(signal.SIGTSTP, self.handle_quit)

        # Resolve and cache the speaker IP up front so the first scan doesn't
        # pay for discovery. Done in the background so startup isn't blocked.
        threading.Thread(target=self.resolve_speaker_ip, daemon=True).start()

        # Optional mouse control (scroll = volume, side buttons = track).
        mouse_enabled = self.setup_mouse()

        print("\n✨ Magic Box Ready")
        print(f"🔈 Sonos: {self.room}")
        print(f"📺 TV Control: Enabled (with smart detection)")
        print(f"🎬 Video Playback: Enabled")
        print(f"🎮 Universal Controls: stop, vol_up, vol_down")
        if mouse_enabled:
            print(f"🖱️  Mouse: scroll=volume, side buttons=track, middle=play/stop")
        print("\nScan tag to begin... (Ctrl+C or Ctrl+Z to quit)")

        self.play_sound("info")

        nfc_thread = threading.Thread(target=self.start_nfc_listener)
        nfc_thread.daemon = True
        nfc_thread.start()

        if mouse_enabled:
            mouse_thread = threading.Thread(target=self.start_mouse_listener)
            mouse_thread.daemon = True
            mouse_thread.start()
        
        while self.is_running:
            time.sleep(1)

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 magic_box.py ROOM_NAME")
        print("Example: python3 magic_box.py Kitchen")
        sys.exit(1)

    box = MagicBox(sys.argv[1])
    box.start()

if __name__ == "__main__":
    main()
