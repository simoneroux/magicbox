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
import array
import math
import wave
import shutil
import tempfile

class MagicBox:
    SOUNDS = {
        "success": {"freq": 880, "duration": 0.2},
        "error": {"freq": 220, "duration": 0.3},
        "info": {"freq": 440, "duration": 0.2},
        "scan": {"freq": 660, "duration": 0.1}
    }

    def __init__(self, room):
        self.room = room
        self.clf = None
        self.is_running = True
        self.vlc_process = None
        self.sound_dir = tempfile.mkdtemp(prefix='magicbox-')
        self.sound_files = {}

        # Universal playback controls, also used to validate scanned command tags
        self.commands = {
            "play": ("play", "▶️ Playing"),
            "stop": lambda: (self.stop_video(), self.run_sonos_command("stop")),
            "next": ("next", "⏭️ Next track"),
            "prev": ("previous", "⏮️ Previous track"),
            "vol_up": lambda: self.adjust_volume(5),
            "vol_down": lambda: self.adjust_volume(-5),
            "tv_on": self.tv_on,
            "tv_off": self.tv_off
        }

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def get_sound_file(self, sound_type):
        """Generate the tone for sound_type once and cache it on disk"""
        if sound_type not in self.sound_files:
            config = self.SOUNDS[sound_type]
            freq = config["freq"]
            duration = config["duration"]
            sample_rate = 22050
            amplitude = 0.5

            audio = array.array('h', (
                int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
                for i in range(int(sample_rate * duration))
            ))

            path = os.path.join(self.sound_dir, f'{sound_type}.wav')
            with wave.open(path, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(audio.tobytes())
            self.sound_files[sound_type] = path

        return self.sound_files[sound_type]

    def play_sound(self, sound_type="success"):
        """Play a feedback sound through the Raspberry Pi headphone jack"""
        try:
            if sound_type not in self.SOUNDS:
                sound_type = "success"
            subprocess.run(['aplay', '-q', self.get_sound_file(sound_type)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           timeout=5)
        except Exception as e:
            self.logger.error(f"Failed to play sound: {e}")

    def run_cec_command(self, command):
        """Send a command to the TV over HDMI-CEC"""
        return subprocess.run(
            ['cec-client', '-s', '-d', '1'],
            input=command + '\n',
            capture_output=True,
            text=True,
            timeout=20
        )

    def is_tv_on(self):
        """Check if TV is already on"""
        try:
            result = self.run_cec_command('pow 0')
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
                self.run_cec_command('as')
                time.sleep(1)  # Short wait for input switch
                print("✅ TV ready")
                return True

            # TV is off, need to turn it on
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
        """Turn off TV"""
        try:
            print("📺 Turning off TV...")
            self.run_cec_command('standby 0')
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
                except subprocess.TimeoutExpired:
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

    def run_sonos_command(self, *args):
        """Execute a Sonos command via soco-cli"""
        cmd = ["sonos", self.room] + list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            self.logger.error(f"Sonos command timed out: {' '.join(args)}")
            return 1, "", "command timed out"

    def play_music(self, url, title=None, shuffle=False):
        """Play music from a streaming service URL"""
        try:
            # Stop any video first
            self.stop_video()
            
            # Turn off TV
            self.tv_off()
            
            # Clear queue and play
            self.run_sonos_command("clear_queue")
            code, _, stderr = self.run_sonos_command("play_sharelink", url)
            
            if code == 0:
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

    def handle_control(self, command):
        """Handle basic playback controls - universal for both music and video"""
        if command not in self.commands:
            self.play_sound("error")
            return False

        action = self.commands[command]
        if callable(action):
            action()
            self.play_sound("success")
            print(f"✅ {command}")
            return True

        code, _, stderr = self.run_sonos_command(action[0])
        self.play_sound("success" if code == 0 else "error")
        print(action[1] if code == 0 else f"❌ Failed: {stderr}")
        return code == 0

    def adjust_volume(self, delta):
        """Adjust volume up or down by delta - universal control"""
        code, volume, _ = self.run_sonos_command("volume")
        if code != 0:
            return
        try:
            current = int(volume)
        except ValueError:
            self.logger.error(f"Unexpected volume output: {volume!r}")
            return
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
                        if command in self.commands:
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
        shutil.rmtree(self.sound_dir, ignore_errors=True)
        sys.exit(0)

    def start(self):
        """Start the Magic Box"""
        if not self.setup_nfc():
            print("❌ NFC setup failed")
            self.play_sound("error")
            return

        signal.signal(signal.SIGINT, self.handle_quit)
        signal.signal(signal.SIGTSTP, self.handle_quit)
        
        print("\n✨ Magic Box Ready")
        print(f"🔈 Sonos: {self.room}")
        print(f"📺 TV Control: Enabled (with smart detection)")
        print(f"🎬 Video Playback: Enabled")
        print(f"🎮 Universal Controls: stop, vol_up, vol_down")
        print("\nScan tag to begin... (Ctrl+C or Ctrl+Z to quit)")
        
        self.play_sound("info")
        
        nfc_thread = threading.Thread(target=self.start_nfc_listener)
        nfc_thread.daemon = True
        nfc_thread.start()
        
        while self.is_running:
            time.sleep(1)

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 magicbox.py ROOM_NAME")
        print("Example: python3 magicbox.py Kitchen")
        sys.exit(1)

    box = MagicBox(sys.argv[1])
    box.start()

if __name__ == "__main__":
    main()
