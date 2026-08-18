#!/usr/bin/env python3
"""
Magic Box — an NFC-powered jukebox for Raspberry Pi.

How it works, in one paragraph:
A PN532 NFC reader (connected over the Pi's serial port) waits for a tag.
Each tag stores small "NDEF records" written with an app like NFC Tools:
a URI record holding a streaming link, plus optional text records like
"name:Frozen" or "mode:shuffle". When a tag is scanned, this script reads
those records and decides what to do — send a music link to a Sonos
speaker (via the `sonos` command from soco-cli), play a video on the TV
(via VLC + HDMI-CEC to wake the TV), or run a control command like
"vol_up". Audible beeps (played through the Pi's headphone jack) give
feedback since the box has no screen.

A connected mouse can also drive playback (optional): scroll wheel for
volume, the side buttons for next/previous track, middle click to
play/stop. This needs the `python-evdev` package; without it (or without
a mouse) the box just runs NFC-only as before.

External tools this script drives:
  - soco-cli                 -> controls the Sonos speaker (called via
                                its Python API for speed, with the
                                `sonos` command as fallback)
  - cec-client  (libcec)     -> talks to the TV over the HDMI cable
  - cvlc        (VLC)        -> plays videos fullscreen, no GUI
  - aplay       (ALSA)       -> plays the feedback beeps
"""

import nfc          # nfcpy: talks to the PN532 NFC reader
import subprocess   # used to run all the external programs listed above
import sys
import signal       # lets us catch Ctrl+C and shut down cleanly
import logging
import threading    # the NFC listener runs in its own thread
import time
import re           # regex, used to recognize music streaming URLs
import os
import array        # compact array of raw audio samples (16-bit ints)
import math         # math.sin generates the beep waveforms
import wave         # stdlib writer for .wav files
import shutil       # rmtree, to clean up the cached beep files on exit
import tempfile     # gives us a throwaway directory for those beeps

# soco-cli's Python API lets us run Sonos commands inside this process,
# which is much faster than shelling out to the `sonos` command: no new
# Python interpreter per command, and the speaker lookup is cached after
# the first call instead of doing network discovery every time. If the
# import fails for some reason we fall back to the `sonos` CLI.
try:
    from soco_cli import api as sonos_api
except ImportError:
    sonos_api = None


class MagicBox:
    # The four feedback beeps. Higher frequency = higher pitch, so
    # "success" is a high chirp and "error" is a low buzz. Durations are
    # in seconds. These are turned into real .wav files on first use
    # (see get_sound_file).
    SOUNDS = {
        "success": {"freq": 880, "duration": 0.2},
        "error": {"freq": 220, "duration": 0.3},
        "info": {"freq": 440, "duration": 0.2},
        "scan": {"freq": 660, "duration": 0.1}
    }

    # Volume control. WHEEL_STEP is the change per scroll-wheel notch —
    # small, so the wheel is a fine-grained knob. CARD_STEP is the
    # coarser change for a vol_up/vol_down NFC card. VOLUME_CAP is a hard
    # ceiling that protects ears and neighbours from a runaway delta.
    VOLUME_CAP = 50
    WHEEL_STEP = 2
    CARD_STEP = 5

    def __init__(self, room):
        # Name of the Sonos room/speaker to control, e.g. "Kitchen".
        # Passed to every `sonos` command.
        self.room = room

        # The NFC reader object (nfcpy ContactlessFrontend). Stays None
        # until setup_nfc() manages to open the device.
        self.clf = None

        # Main-loop flag. Both the main thread and the NFC listener
        # thread keep going while this is True; handle_quit() flips it.
        self.is_running = True

        # Handle to the currently running VLC process (subprocess.Popen),
        # or None when no video is playing. Kept so stop_video() can
        # terminate it later.
        self.vlc_process = None

        # Temp directory where generated beep .wav files are cached, and
        # the cache itself: sound name -> file path. Generating each tone
        # once and reusing the file keeps scan feedback snappy on a Pi.
        self.sound_dir = tempfile.mkdtemp(prefix='magicbox-')
        self.sound_files = {}

        # Optional mouse control. mouse_devices holds every evdev device
        # setup_mouse() decides is worth listening to — a single mouse can
        # expose more than one node (e.g. Logitech receivers put the scroll
        # wheel on one and the side buttons on another). is_playing is a
        # best-effort flag so the middle-click button can toggle play/stop.
        self.mouse_devices = []
        self.is_playing = False

        # Last volume we know the speaker is at, cached so a scroll-wheel
        # notch is a single "set" call instead of a read-then-write.
        # Seeded by warm_up_sonos() and kept current by adjust_volume().
        self.current_volume = None

        # Every control command a tag (or a mouse button) can trigger,
        # defined once here. Two kinds of values:
        #   - a tuple ("sonos-subcommand", "message to print") for simple
        #     Sonos commands, executed by handle_control()
        #   - a callable (method or lambda) for anything more involved
        # on_connect() also uses the keys of this dict to check whether
        # a scanned text record is a valid command.
        self.commands = {
            "play": ("play", "▶️ Playing"),
            "stop": lambda: (self.stop_video(), self.run_sonos_command("stop")),
            "next": ("next", "⏭️ Next track"),
            "prev": ("previous", "⏮️ Previous track"),
            "vol_up": lambda: self.adjust_volume(self.CARD_STEP),
            "vol_down": lambda: self.adjust_volume(-self.CARD_STEP),
            "tv_on": self.tv_on,
            "tv_off": self.tv_off
        }

        # Errors go through the logger (with timestamps); normal user
        # feedback uses plain print() with emoji.
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

        # soco-cli logs every speaker lookup and command at INFO ("Trying
        # direct cache lookup", "Return value: ...") — far too chatty when
        # a scroll wheel fires commands rapidly. Quiet it to warnings.
        for noisy in ("soco_cli", "soco"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    # ------------------------------------------------------------------
    # Feedback beeps
    # ------------------------------------------------------------------

    def get_sound_file(self, sound_type):
        """Return the path to the .wav file for a beep, generating it on
        first use and caching it for the rest of the session."""
        if sound_type not in self.sound_files:
            config = self.SOUNDS[sound_type]
            freq = config["freq"]
            duration = config["duration"]
            sample_rate = 22050   # samples per second; plenty for a beep
            amplitude = 0.5       # half of maximum volume, easy on the ears

            # Build the waveform sample by sample. Each sample is the
            # sine wave's height at that instant, scaled to a signed
            # 16-bit integer (range -32767..32767), which is the format
            # WAV files expect. 'h' = array of 16-bit signed ints.
            audio = array.array('h', (
                int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
                for i in range(int(sample_rate * duration))
            ))

            # Wrap the raw samples in a proper WAV file: 1 channel
            # (mono), 2 bytes per sample (16-bit), at our sample rate.
            path = os.path.join(self.sound_dir, f'{sound_type}.wav')
            with wave.open(path, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(audio.tobytes())
            self.sound_files[sound_type] = path

        return self.sound_files[sound_type]

    def play_sound(self, sound_type="success", wait=False):
        """Play a feedback beep through the Pi's headphone jack.

        By default this is fire-and-forget: aplay is started in the
        background and we return immediately, so a beep never delays
        the tag handling that follows it. Pass wait=True when the beep
        must finish before moving on (only needed at shutdown, where we
        delete the sound files right after).
        """
        try:
            # Unknown sound names fall back to the success beep rather
            # than crashing.
            if sound_type not in self.SOUNDS:
                sound_type = "success"
            # aplay is ALSA's command-line player; -q keeps it quiet on
            # stdout.
            cmd = ['aplay', '-q', self.get_sound_file(sound_type)]
            if wait:
                # The timeout guards against a wedged audio device.
                subprocess.run(cmd,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               timeout=5)
            else:
                subprocess.Popen(cmd,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception as e:
            # A broken beep should never take down the box — log and move on.
            self.logger.error(f"Failed to play sound: {e}")

    # ------------------------------------------------------------------
    # TV control over HDMI-CEC
    # ------------------------------------------------------------------

    def run_cec_command(self, command):
        """Send one command to the TV over HDMI-CEC.

        CEC is a protocol that runs over a wire inside the HDMI cable and
        lets devices control each other. cec-client's flags: -s means
        "single command" mode (send and exit), -d 1 limits log output to
        errors. The command itself is fed in on stdin. Commands used here:
          'pow 0'     -> ask device 0 (the TV) for its power status
          'on 0'      -> turn the TV on
          'standby 0' -> put the TV in standby (off)
          'as'        -> "active source": make the Pi the displayed input
        """
        return subprocess.run(
            ['cec-client', '-s', '-d', '1'],
            input=command + '\n',
            capture_output=True,
            text=True,      # send/receive strings instead of bytes
            timeout=20      # don't hang forever if the CEC bus is stuck
        )

    def is_tv_on(self):
        """Ask the TV for its power status; True if it reports 'on'."""
        try:
            result = self.run_cec_command('pow 0')
            # cec-client prints something like "power status: on" —
            # we just look for that phrase in its output.
            return 'power status: on' in result.stdout.lower()
        except Exception as e:
            self.logger.error(f"TV status check error: {e}")
            # If we can't tell, assume off — worst case we send a
            # harmless extra power-on command.
            return False

    def tv_on(self):
        """Turn on the TV and switch it to the Pi's HDMI input.

        Checks the current power state first: if the TV is already on we
        skip the power-on command and its 3-second warm-up wait, so
        back-to-back video cards start faster.
        """
        try:
            if self.is_tv_on():
                print("📺 TV already on, switching input...")
                self.run_cec_command('as')
                time.sleep(1)  # give the TV a moment to switch inputs
                print("✅ TV ready")
                return True

            # TV is off: power it on, wait for it to boot enough to
            # accept the input-switch command, then switch.
            print("📺 Turning on TV...")
            self.run_cec_command('on 0')

            print("📺 Waiting 3 seconds...")
            time.sleep(3)

            print("📺 Switching to Pi input...")
            self.run_cec_command('as')
            time.sleep(1)

            print("✅ TV ready")
            return True

        except Exception as e:
            self.logger.error(f"TV on error: {e}")
            return False

    def tv_off(self):
        """Put the TV into standby."""
        try:
            print("📺 Turning off TV...")
            self.run_cec_command('standby 0')
            return True
        except Exception as e:
            self.logger.error(f"TV off error: {e}")
            return False

    # ------------------------------------------------------------------
    # Video playback (VLC)
    # ------------------------------------------------------------------

    def stop_video(self):
        """Stop any running video."""
        try:
            if self.vlc_process:
                print("⏹️ Stopping video...")
                # terminate() asks politely (SIGTERM); if VLC hasn't
                # exited after 2 seconds, kill() forces it (SIGKILL).
                self.vlc_process.terminate()
                try:
                    self.vlc_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.vlc_process.kill()
                self.vlc_process = None

                # Belt and braces: make sure nothing VLC-related
                # survived the kill. Only done when a video was actually
                # playing — strays from a previous crashed run are
                # cleaned up once at startup instead, which keeps this
                # method fast on the music path.
                subprocess.run(['pkill', 'vlc'], capture_output=True)

        except Exception as e:
            self.logger.error(f"Stop video error: {e}")

    def play_video(self, url, title=None):
        """Play a video URL (e.g. a Jellyfin stream) fullscreen on the TV.

        Non-blocking: VLC is started with Popen and left running in the
        background, so the box immediately goes back to listening for
        the next tag (which is how "stop" cards can interrupt a video).
        """
        try:
            print(f"🎬 Playing video: {title or url}")

            # Only one thing should play at a time: stop any current
            # video and any Sonos music, then make sure the TV is on
            # and showing the Pi.
            self.stop_video()
            self.run_sonos_command("stop")
            self.tv_on()

            # cvlc = VLC without its GUI. --network-caching=3000 buffers
            # 3 seconds of stream to ride out Wi-Fi hiccups, and
            # --play-and-exit makes VLC quit when the video ends instead
            # of sitting on a black screen.
            self.vlc_process = subprocess.Popen([
                'cvlc',
                '--fullscreen',
                '--network-caching=3000',
                '--play-and-exit',
                url
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Something is now playing, so a middle-click should stop it.
            self.is_playing = True

            print("✅ Video playing (scan another card to control)")
            return True

        except Exception as e:
            self.logger.error(f"Video playback error: {e}")
            return False

    # ------------------------------------------------------------------
    # Music playback (Sonos via soco-cli)
    # ------------------------------------------------------------------

    def run_sonos_command(self, *args):
        """Run one soco-cli command against our Sonos room.

        Example: run_sonos_command("volume", "30") does the same as the
        shell command `sonos Kitchen volume 30`. Returns an (exit_code,
        stdout, stderr) tuple; exit code 0 means success, anything else
        is an error with details in stderr.

        Fast path: soco-cli's Python API, called directly inside this
        process. The first call discovers the speaker on the network
        (slow, a second or two — which is why start() warms it up in
        the background), every call after that reuses the cached
        connection and takes milliseconds.

        Slow path (only if the soco_cli module isn't importable): shell
        out to the `sonos` command, which pays interpreter startup and
        speaker discovery on every single call.
        """
        if sonos_api is not None:
            try:
                code, output, error = sonos_api.run_command(self.room, *args)
                return code, str(output).strip(), str(error).strip()
            except Exception as e:
                self.logger.error(f"Sonos API error on '{' '.join(args)}': {e}")
                return 1, "", str(e)

        cmd = ["sonos", self.room] + list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            # An unreachable speaker shouldn't freeze the whole box;
            # report it like any other failed command.
            self.logger.error(f"Sonos command timed out: {' '.join(args)}")
            return 1, "", "command timed out"

    def warm_up_sonos(self):
        """Resolve the Sonos speaker once, so the first card is fast.

        Run in a background thread at startup: it issues a harmless
        query (read the volume) whose only purpose is to trigger
        speaker discovery now, while nobody is waiting, instead of on
        the first scanned card.
        """
        code, volume, error = self.run_sonos_command("volume")
        if code == 0:
            self.logger.info(f"Sonos speaker '{self.room}' found and cached")
            # Seed the volume cache so the first scroll notch is a single
            # "set" call rather than a read-then-write.
            try:
                self.current_volume = int(volume)
            except ValueError:
                pass
        else:
            self.logger.warning(f"Sonos warm-up failed: {error}")

    def play_music(self, url, title=None, shuffle=False):
        """Send a streaming-service share link to the Sonos speaker."""
        try:
            # Music means the TV isn't needed: stop any video and turn
            # the TV off. The TV command goes to a background thread
            # because cec-client takes several seconds to register on
            # the HDMI bus — there's no reason the music should wait
            # for that.
            self.stop_video()
            threading.Thread(target=self.tv_off, daemon=True).start()

            # Start fresh: empty the speaker's queue, then hand it the
            # share link. soco-cli's play_sharelink understands public
            # Spotify/Apple Music/Tidal/Deezer URLs and queues their
            # contents (a track, album, or playlist).
            self.run_sonos_command("clear_queue")
            code, _, stderr = self.run_sonos_command("play_sharelink", url)

            if code == 0:
                # Something is now playing, so a middle-click should stop it.
                self.is_playing = True
                # Shuffle is a speaker-level toggle that would otherwise
                # persist from the previous card, so set it explicitly
                # either way.
                if shuffle:
                    self.run_sonos_command("shuffle", "on")
                    print(f"🔀 Playing shuffled: {title or 'Music from tag'}")
                else:
                    self.run_sonos_command("shuffle", "off")
                    print(f"▶️ Playing: {title or 'Music from tag'}")
                return True
            else:
                print(f"❌ Failed: {stderr}")
                return False

        except Exception as e:
            self.logger.error(f"Error playing URL: {e}")
            print("❌ Failed to play music from tag")
            return False

    # ------------------------------------------------------------------
    # Control commands (the self.commands table)
    # ------------------------------------------------------------------

    def handle_control(self, command, beep=True):
        """Run one control command from the self.commands table.

        Works for both music and video: e.g. "stop" halts VLC and the
        Sonos speaker at the same time. Returns True on success.

        beep=False suppresses the feedback beep — used by the mouse
        remote, where beeping on every click gets annoying (the tap of
        an NFC card still beeps).
        """
        if command not in self.commands:
            if beep:
                self.play_sound("error")
            return False

        # Keep the middle-click toggle roughly in sync with reality.
        if command == "play":
            self.is_playing = True
        elif command == "stop":
            self.is_playing = False

        action = self.commands[command]

        # Callable entries (lambdas/methods) just get called; they
        # handle their own logic and printing.
        if callable(action):
            action()
            if beep:
                self.play_sound("success")
            print(f"✅ {command}")
            return True

        # Tuple entries are (sonos subcommand, success message).
        code, _, stderr = self.run_sonos_command(action[0])
        if beep:
            self.play_sound("success" if code == 0 else "error")
        print(action[1] if code == 0 else f"❌ Failed: {stderr}")
        return code == 0

    def adjust_volume(self, delta):
        """Change Sonos volume by delta (e.g. +2 or -2), capped at VOLUME_CAP.

        Sonos has no relative-volume command, so we work out the target
        level and write it. To keep a fast scroll responsive we base the
        change on the cached level (self.current_volume) and only read
        the speaker when we don't have one yet — halving the number of
        Sonos calls per notch from two (read+write) to one.
        """
        if self.current_volume is None:
            code, volume, _ = self.run_sonos_command("volume")
            if code != 0:
                return
            try:
                self.current_volume = int(volume)
            except ValueError:
                # soco-cli printed something unexpected; better to do
                # nothing than to set a wild volume.
                self.logger.error(f"Unexpected volume output: {volume!r}")
                return

        # Clamp between 0 and VOLUME_CAP; the ceiling protects ears and
        # neighbours from a misbehaving delta.
        new_volume = max(0, min(self.VOLUME_CAP, self.current_volume + delta))
        if new_volume == self.current_volume:
            # Already at the floor/ceiling — nothing to send.
            return
        self.run_sonos_command("volume", str(new_volume))
        self.current_volume = new_volume
        print(f"{'🔊' if delta > 0 else '🔉'} {new_volume}%")

    # ------------------------------------------------------------------
    # NFC: reading tags and deciding what to do
    # ------------------------------------------------------------------

    def on_connect(self, tag):
        """Called by nfcpy every time a tag touches the reader.

        This is the heart of the box. A tag carries NDEF records of two
        kinds we care about:
          - text records ("urn:nfc:wkt:T"), used two ways:
              with a colon  -> metadata: "name:Frozen", "mode:shuffle",
                               "type:video"
              without colon -> a control command: "stop", "vol_up", ...
          - one URI record ("urn:nfc:wkt:U") holding the content link

        Returning True tells nfcpy we're done with this tag and it can
        go back to waiting for the next one.
        """
        try:
            # Immediate beep so you know the scan registered, even
            # before we've figured out what the tag wants.
            self.play_sound("scan")

            # NDEF is the standard format for data on NFC tags; a tag
            # without it (blank card, bank card...) is no use to us.
            if not tag.ndef:
                print("❌ Not a valid NDEF tag")
                self.play_sound("error")
                return True

            # What we hope to learn from the records:
            card_name = None      # display name, from "name:..."
            content_type = None   # "video" forces VLC, from "type:..."
            shuffle = False       # from "mode:shuffle"
            url = None            # the content link, from the URI record

            # Pass 1: collect metadata and the URL from all records.
            for record in tag.ndef.records:
                if record.type == "urn:nfc:wkt:T":
                    if ":" in record.text:
                        # Split "name:Frozen" into "name" and "Frozen".
                        # Only the identifier is lowercased — the
                        # content keeps its capitalization for display.
                        identifier, content = record.text.split(":", 1)
                        identifier = identifier.lower()
                        if identifier == "name":
                            card_name = content
                            print(f"\n💳 Card: {card_name}")
                        elif identifier == "mode" and content.lower() == "shuffle":
                            shuffle = True
                        elif identifier == "type":
                            content_type = content.lower()

                elif record.type == "urn:nfc:wkt:U":
                    url = record.uri

            # Decide what kind of card this is, in priority order:

            # 1. Explicitly marked as video -> VLC on the TV.
            if content_type == "video" and url:
                result = self.play_video(url, card_name)
                self.play_sound("success" if result else "error")
                return True

            # 2. A link from a known streaming service -> Sonos.
            elif url and re.match(r'^https?://(open\.spotify\.com|music\.apple\.com|tidal\.com|www\.deezer\.com)/', url):
                result = self.play_music(url, card_name, shuffle)
                self.play_sound("success" if result else "error")
                return True

            # 3. No URL at all -> maybe it's a control card. Look for a
            #    plain text record (no colon) matching a known command.
            elif not url:
                for record in tag.ndef.records:
                    if record.type == "urn:nfc:wkt:T" and ":" not in record.text:
                        command = record.text.lower()
                        if command in self.commands:
                            self.handle_control(command)
                            return True

            # Nothing matched: unknown URL scheme, or a tag with
            # records we don't understand.
            print("❌ No supported content found on tag")
            self.play_sound("error")
            return True

        except Exception as e:
            self.logger.error(f"Error handling tag: {e}")
            self.play_sound("error")
            return False

    def setup_nfc(self):
        """Open the PN532 NFC reader on the Pi's serial port.

        The device path differs between Pi models/configs (ttyS0 is the
        mini UART, ttyAMA0 the full UART), so try both and keep the
        first one that answers.
        """
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
        """Endless scan loop; runs in a background thread.

        clf.connect() blocks until a tag is presented, calls on_connect
        with it, then returns — so we loop to wait for the next tag.
        If the reader hiccups, wait a second and try again rather than
        letting the thread die.
        """
        while self.is_running:
            try:
                self.clf.connect(rdwr={'on-connect': self.on_connect})
            except Exception as e:
                self.logger.error(f"NFC error: {e}")
                time.sleep(1)

    # ------------------------------------------------------------------
    # Mouse control (optional)
    # ------------------------------------------------------------------

    def setup_mouse(self):
        """Find a connected mouse to use for playback control.

        Optional: uses python-evdev. If evdev isn't installed or no
        mouse is present, mouse control is simply disabled and the NFC
        reader keeps working exactly as before. Returns True if a mouse
        was found.
        """
        try:
            import evdev
        except ImportError:
            self.logger.info("python-evdev not installed; mouse control disabled")
            return False

        try:
            self.mouse_devices = self._find_mouse_devices(evdev)
        except Exception as e:
            self.logger.error(f"Mouse detection error: {e}")
            return False

        if self.mouse_devices:
            names = ", ".join(d.name for d in self.mouse_devices)
            self.logger.info(f"Mouse control enabled: {names}")
            return True

        self.logger.info("No mouse found; mouse control disabled")
        return False

    def _find_mouse_devices(self, evdev):
        """Return every input device we should listen to for mouse control.

        We keep any device that reports a scroll wheel (REL_WHEEL), mouse
        buttons (BTN_MOUSE), or one of the side/media buttons we map to a
        control. That last case matters: a single physical mouse often
        splits its inputs across several /dev/input nodes — the pointer
        and wheel on one, the "forward/back" side buttons on another —
        so listening to only the first node silently drops those buttons.
        Reading /dev/input requires membership in the 'input' group.
        """
        from evdev import ecodes
        wanted_keys = set(self._button_actions(ecodes))
        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except Exception:
                # Skip devices we can't open (permissions, disappeared).
                continue
            caps = dev.capabilities()
            rel_axes = caps.get(ecodes.EV_REL, [])
            keys = caps.get(ecodes.EV_KEY, [])
            if (ecodes.REL_WHEEL in rel_axes
                    or ecodes.BTN_MOUSE in keys
                    or wanted_keys.intersection(keys)):
                devices.append(dev)
        return devices

    def _toggle_play_stop(self):
        """Middle-click: pause if the speaker is playing, else resume.

        Uses pause (not stop) so playback resumes where it left off
        instead of restarting the track, and reads the speaker's real
        transport state so the toggle stays correct even if our
        is_playing flag has drifted. Falls back to the flag if the
        state query fails. Silent — no feedback beep.
        """
        code, state, _ = self.run_sonos_command("state")
        playing = ("PLAYING" in state.upper()) if code == 0 and state else self.is_playing
        if playing:
            self.run_sonos_command("pause")
            self.is_playing = False
            print("⏸️ Paused")
        else:
            self.run_sonos_command("play")
            self.is_playing = True
            print("▶️ Playing")

    def _button_actions(self, ecodes):
        """Map evdev key codes to control commands for mouse buttons.

        This mouse reports only left/right/middle/wheel (no separate
        forward/back side buttons), so left and right clicks drive
        previous/next. The BTN_SIDE/EXTRA and media KEY_* codes are kept
        as well, harmlessly, for mice that do have real side buttons.
        "toggle" is play/pause; the rest are handle_control names.
        """
        return {
            ecodes.BTN_LEFT: "prev",
            ecodes.BTN_RIGHT: "next",
            ecodes.BTN_FORWARD: "next",
            ecodes.BTN_EXTRA: "next",
            ecodes.KEY_NEXTSONG: "next",
            ecodes.KEY_FORWARD: "next",
            ecodes.BTN_BACK: "prev",
            ecodes.BTN_SIDE: "prev",
            ecodes.KEY_PREVIOUSSONG: "prev",
            ecodes.KEY_BACK: "prev",
            ecodes.BTN_MIDDLE: "toggle",
            ecodes.KEY_PLAYPAUSE: "toggle",
        }

    def start_mouse_listener(self):
        """Map mouse events to playback controls; runs in its own thread.

        Scroll wheel        -> volume up/down (silent; no per-notch beep)
        Left / right click  -> previous / next track
        Middle click        -> play/pause toggle

        Reads from every device setup_mouse() found, because one mouse can
        spread the wheel and the side buttons across separate input nodes.
        A selector lets us watch them all at once while still checking
        is_running (so shutdown doesn't hang on a quiet mouse).
        """
        from evdev import ecodes
        from selectors import DefaultSelector, EVENT_READ

        actions = self._button_actions(ecodes)
        selector = DefaultSelector()
        for dev in self.mouse_devices:
            selector.register(dev, EVENT_READ)

        try:
            while self.is_running and selector.get_map():
                for key, _ in selector.select(timeout=1):
                    dev = key.fileobj
                    try:
                        events = list(dev.read())
                    except OSError:
                        # Device unplugged: stop watching it, keep the rest.
                        selector.unregister(dev)
                        continue
                    for event in events:
                        self._handle_mouse_event(event, ecodes, actions)
        except Exception as e:
            # A mouse unplug or read error shouldn't crash the box; the
            # NFC reader keeps working.
            self.logger.error(f"Mouse listener error: {e}")

    def _handle_mouse_event(self, event, ecodes, actions):
        """Turn one evdev event into a control command."""
        # Scroll wheel: adjust volume directly (not via handle_control) so
        # scrolling doesn't fire the success beep on every single notch.
        # Small step per notch for fine control.
        if event.type == ecodes.EV_REL and event.code == ecodes.REL_WHEEL:
            if event.value > 0:
                self.adjust_volume(self.WHEEL_STEP)
            elif event.value < 0:
                self.adjust_volume(-self.WHEEL_STEP)
            return

        # Buttons: act on the press (value == 1) only, so we ignore the
        # release event and any auto-repeat.
        if event.type == ecodes.EV_KEY and event.value == 1:
            command = actions.get(event.code)
            if command == "toggle":
                self._toggle_play_stop()
            elif command:
                # beep=False: the mouse is a quiet remote, so next/prev
                # don't chirp the way an NFC card tap does.
                self.handle_control(command, beep=False)
            else:
                # Unmapped press — log the code so an unrecognised side
                # button can be identified and added to _button_actions.
                name = ecodes.keys.get(event.code, event.code)
                self.logger.info(f"Unmapped mouse button: {name} ({event.code})")

    # ------------------------------------------------------------------
    # Startup and shutdown
    # ------------------------------------------------------------------

    def handle_quit(self, signum, frame):
        """Clean shutdown, triggered by Ctrl+C or Ctrl+Z.

        signum/frame are passed by Python's signal machinery; we don't
        need them, but the signature is required for a signal handler.
        """
        print("\n👋 Shutting down Magic Box...")
        self.is_running = False

        # Leave the room quiet and dark: stop video, stop music, TV off.
        self.stop_video()
        self.run_sonos_command("stop")
        self.tv_off()

        # Release the NFC reader so the next run can open it.
        if self.clf:
            self.clf.close()

        # Goodbye beep — wait=True so the beep finishes before we
        # delete the very file aplay is reading from.
        self.play_sound("info", wait=True)
        shutil.rmtree(self.sound_dir, ignore_errors=True)
        sys.exit(0)

    def start(self):
        """Wire everything up and run until interrupted."""
        if not self.setup_nfc():
            print("❌ NFC setup failed")
            self.play_sound("error")
            return

        # Clean up any VLC left over from a previous crashed run, once,
        # here — so stop_video() doesn't have to do it on every card.
        subprocess.run(['pkill', 'vlc'], capture_output=True)

        # Find the Sonos speaker now, in the background, so the first
        # scanned card doesn't pay the discovery delay.
        threading.Thread(target=self.warm_up_sonos, daemon=True).start()

        # Optional: find a mouse for playback control (scroll = volume,
        # side buttons = track, middle = play/stop).
        mouse_enabled = self.setup_mouse()

        # Route Ctrl+C (SIGINT) and Ctrl+Z (SIGTSTP) to our clean
        # shutdown instead of their default behavior.
        signal.signal(signal.SIGINT, self.handle_quit)
        signal.signal(signal.SIGTSTP, self.handle_quit)

        print("\n✨ Magic Box Ready")
        print(f"🔈 Sonos: {self.room}")
        print(f"📺 TV Control: Enabled (with smart detection)")
        print(f"🎬 Video Playback: Enabled")
        print(f"🎮 Universal Controls: stop, vol_up, vol_down")
        if mouse_enabled:
            print(f"🖱️  Mouse: scroll=volume, left/right=prev/next, middle=play/pause")
        print("\nScan tag to begin... (Ctrl+C or Ctrl+Z to quit)")

        self.play_sound("info")

        # The scan loop runs in a daemon thread; "daemon" means Python
        # won't wait for it when the process exits.
        nfc_thread = threading.Thread(target=self.start_nfc_listener)
        nfc_thread.daemon = True
        nfc_thread.start()

        # The mouse listener also runs in its own daemon thread, when a
        # mouse was found.
        if mouse_enabled:
            mouse_thread = threading.Thread(target=self.start_mouse_listener)
            mouse_thread.daemon = True
            mouse_thread.start()

        # The main thread just idles, staying alive to receive signals
        # (signal handlers only run on the main thread in Python).
        while self.is_running:
            time.sleep(1)


def main():
    # Exactly one argument expected: the Sonos room name.
    if len(sys.argv) != 2:
        print("Usage: python3 magicbox.py ROOM_NAME")
        print("Example: python3 magicbox.py Kitchen")
        sys.exit(1)

    box = MagicBox(sys.argv[1])
    box.start()


if __name__ == "__main__":
    main()
