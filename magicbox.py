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
    def __init__(self, room):
        self.room = room
        self.clf = None
        self.is_running = True
        
        # Configure Roku settings
        self.roku_ip = os.getenv("ROKU_IP", "192.168.68.106")
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def play_sound(self, sound_type="success"):
        """Play a feedback sound through the Raspberry Pi headphone jack
        
        Args:
            sound_type (str): Type of sound to play ("success", "error", "info", or "scan")
        """
        try:
            # Sound configurations - different frequencies for different sounds
            sounds = {
                "success": {"freq": 880, "duration": 0.2},  # A5 note
                "error": {"freq": 220, "duration": 0.3},    # A3 note
                "info": {"freq": 440, "duration": 0.2},     # A4 note
                "scan": {"freq": 660, "duration": 0.1}      # E5 note
            }
            
            # Default to success sound if type not found
            config = sounds.get(sound_type, sounds["success"])
            
            # Sound parameters
            freq = config["freq"]  # Frequency in Hz
            duration = config["duration"]  # Duration in seconds
            sample_rate = 22050  # Sample rate in Hz (CD quality is 44100)
            amplitude = 0.5  # Volume (0.0 to 1.0)
            
            # Generate a sine wave (basic tone)
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            tone = np.sin(2 * np.pi * freq * t) * amplitude
            
            # Convert to 16-bit PCM
            audio = (tone * 32767).astype(np.int16)
            
            # Create a temporary WAV file
            fd, temp_file = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            
            # Save the audio data to the temporary file
            with open(temp_file, 'wb') as f:
                # Write WAV header (simple format)
                f.write(b'RIFF')
                f.write((36 + len(audio) * 2).to_bytes(4, 'little'))  # File size
                f.write(b'WAVE')
                # Format chunk
                f.write(b'fmt ')
                f.write((16).to_bytes(4, 'little'))  # Chunk size
                f.write((1).to_bytes(2, 'little'))   # PCM format
                f.write((1).to_bytes(2, 'little'))   # Mono
                f.write((sample_rate).to_bytes(4, 'little'))  # Sample rate
                f.write((sample_rate * 2).to_bytes(4, 'little'))  # Byte rate
                f.write((2).to_bytes(2, 'little'))   # Block align
                f.write((16).to_bytes(2, 'little'))  # Bits per sample
                # Data chunk
                f.write(b'data')
                f.write((len(audio) * 2).to_bytes(4, 'little'))  # Chunk size
                f.write(audio.tobytes())
            
            # Play the sound using aplay (common on Raspberry Pi)
            subprocess.run(['aplay', '-q', temp_file], 
                           stdout=subprocess.DEVNULL, 
                           stderr=subprocess.DEVNULL)
            
            # Clean up the temporary file
            os.unlink(temp_file)
            
        except Exception as e:
            self.logger.error(f"Failed to play sound: {e}")

    def run_sonos_command(self, *args):
        """Execute a Sonos command via soco-cli"""
        cmd = ["sonos", self.room] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def play_music(self, url, title=None, shuffle=False):
        """Play music from a streaming service URL"""
        try:
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
        """Handle basic playback controls"""
        commands = {
            "play": ("play", "▶️ Playing"),
            "stop": ("stop", "⏹️ Stopped"), 
            "next": ("next", "⏭️ Next track"),
            "prev": ("previous", "⏮️ Previous track"),
            "vol_up": lambda: self.adjust_volume(5),
            "vol_down": lambda: self.adjust_volume(-5)
        }

        if command not in commands:
            # Remove Roku control fallback
            self.play_sound("error")
            return False

        action = commands[command]
        if callable(action):
            action()
            self.play_sound("success")
        else:
            code, _, stderr = self.run_sonos_command(action[0])
            self.play_sound("success" if code == 0 else "error")
            print(action[1] if code == 0 else f"❌ Failed: {stderr}")

    def adjust_volume(self, delta):
        """Adjust volume up or down by delta"""
        code, volume, _ = self.run_sonos_command("volume")
        if code == 0:
            current = int(volume)
            new_volume = max(0, min(60, current + delta))
            self.run_sonos_command("volume", str(new_volume))
            print(f"{'🔊' if delta > 0 else '🔉'} {new_volume}%")

    def on_connect(self, tag):
        """Handle NFC tag connection"""
        try:
            # Play a scan sound when a tag is detected
            self.play_sound("scan")
            
            if not tag.ndef:
                print("❌ Not a valid NDEF tag")
                self.play_sound("error")
                return True
                
            # Track card settings
            card_name = None
            content_type = None
            content_id = None
            shuffle = False
            url = None
                
            # First scan for settings
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
                        elif identifier == "id":
                            content_id = content
                            
                elif record.type == "urn:nfc:wkt:U":
                    url = record.uri
            
            # Handle different content types
            if url and re.match(r'^https?://(open\.spotify\.com|music\.apple\.com|tidal\.com|www\.deezer\.com)/', url):
                result = self.play_music(url, card_name, shuffle)
                self.play_sound("success" if result else "error")
                return True
            elif not url:
                # Handle control commands
                for record in tag.ndef.records:
                    if record.type == "urn:nfc:wkt:T" and not ":" in record.text:
                        command = record.text.lower()
                        valid_commands = ["play", "stop", "next", "prev", "vol_up", "vol_down"]
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

    def handle_quit(self, signum, frame):
        """Clean shutdown on Ctrl+C/Z"""
        print("\n👋 Shutting down Magic Box...")
        self.is_running = False
        if self.clf:
            self.clf.close()
        # Play a goodbye sound
        self.play_sound("info")
        time.sleep(0.3)  # Wait for the sound to finish
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
        print(f"🔈 Speaker: {self.room}")
        print(f"📺 Roku TV: {self.roku_ip}")
        print("\nScan tag to begin... (Ctrl+C or Ctrl+Z to quit)")
        
        # Play startup sound
        self.play_sound("info")
        
        nfc_thread = threading.Thread(target=self.start_nfc_listener)
        nfc_thread.daemon = True
        nfc_thread.start()
        
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
