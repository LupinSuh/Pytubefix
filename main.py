import os
import platform
import re
import subprocess
from time import sleep
from tqdm import tqdm
from pytubefix import YouTube, Playlist
from pytubefix.cli import on_progress
import io
import shutil

import tempfile
import threading

class YouTubeDownloader:
    def __init__(self):
        self.path = os.path.expanduser('~/Downloads')
        os.makedirs(self.path, exist_ok=True)
        self.mode = 'S'  # Default mode is Sound

    def _sanitize_filename(self, title: str) -> str:
        """Removes illegal characters from a string so it can be a valid filename."""
        return re.sub(r'[\\/*?:"<>|#]', "", title)

    def _check_dependencies(self):
        # Check for ffmpeg
        if not shutil.which("ffmpeg"):
            print("\nError: ffmpeg is not installed or not in your PATH.")
            print("Please install ffmpeg to use this script.")
            print("  - On macOS (with Homebrew): brew install ffmpeg")
            print("  - On Windows (with Chocolatey): choco install ffmpeg")
            print("  - Or download from https://ffmpeg.org/download.html and add it to your system's PATH.")
            if input("Do you want to exit? (y/n): ").lower() == 'y':
                exit()

        # Check for pytubefix and tqdm
        missing_packages = []
        try:
            import pytubefix
        except ImportError:
            missing_packages.append('pytubefix')
        try:
            import tqdm
        except ImportError:
            missing_packages.append('tqdm')

        if missing_packages:
            print(f"\nError: The following Python packages are not installed: {', '.join(missing_packages)}")
            print(f"Please install them using pip: pip install {' '.join(missing_packages)}")
            if input("Do you want to exit? (y/n): ").lower() == 'y':
                exit()

    def _download_video(self, url: str, lang: str = 'ko'):
        """Downloads a single video or converts audio directly to MP3."""
        try:
            yt = YouTube(url, on_progress_callback=on_progress)
            title = self._sanitize_filename(yt.title)
            print(f"\nProcessing: {yt.title}")
            print(f"Sanitized Title: {title}")

            # Caption download logic remains the same
            try:
                if lang in yt.captions:
                    caption = yt.captions[lang]
                    caption.download(title=title, output_path=self.path)
                    print("Captions downloaded.")
                else:
                    print(f"No captions available for language code: {lang}")
            except Exception as e:
                print(f"Could not download captions: {e}")

            if self.mode == 'V':
                # Get the highest resolution video stream (may be adaptive)
                video_stream = yt.streams.filter(only_video=True).order_by('resolution').desc().first()
                # Get the audio-only stream
                audio_stream = yt.streams.get_audio_only()

                if not video_stream:
                    print("No video stream found.")
                    return
                if not audio_stream:
                    print("No audio stream found.")
                    return

                temp_dir = os.path.join(self.path, '.temp')
                os.makedirs(temp_dir, exist_ok=True)

                print(f"Downloading video stream ({video_stream.resolution})...")
                video_filepath = video_stream.download(output_path=temp_dir, filename=f"{title}_video.mp4")
                print("Video stream download complete.")

                print("Downloading audio stream...")
                audio_filepath = audio_stream.download(output_path=temp_dir, filename=f"{title}_audio.mp4")
                print("Audio stream download complete.")

                output_filepath = os.path.join(self.path, f"{title}.mp4")

                print("Merging video and audio streams with ffmpeg...")
                progress_file = tempfile.NamedTemporaryFile(delete=False, mode='w+', encoding='utf-8')
                video_codec = 'h264_videotoolbox' if platform.system() == 'Darwin' else 'libx264'
                command = [
                    'ffmpeg',
                    '-y', # Overwrite output file if it exists
                    '-nostdin',
                    '-progress', progress_file.name,
                    '-i', video_filepath,
                    '-i', audio_filepath,
                    '-c:v', video_codec,
                    '-pix_fmt', 'yuv420p', # Set pixel format for broader compatibility
                    '-c:a', 'aac', # Re-encode audio to aac for broader compatibility
                    '-strict', 'experimental', # Needed for aac encoding
                    output_filepath
                ]

                total_duration = yt.length # Get total video duration in seconds

                try:
                    with tqdm(total=total_duration, unit="s", desc="FFmpeg Progress") as pbar:
                        progress_thread = threading.Thread(target=self._update_ffmpeg_progress, args=(pbar, progress_file.name))
                        progress_thread.start()

                        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        process.wait()

                        progress_thread.join()

                    if process.returncode == 0:
                        print(f"Successfully merged and saved to {output_filepath}")
                    else:
                        print(f"Error during ffmpeg merging: {process.stderr.read().decode('utf-8', errors='ignore')}")

                except Exception as e:
                    print(f"An error occurred during ffmpeg process: {e}")
                finally:
                    progress_file.close()
                    os.unlink(progress_file.name)
                    # Clean up temporary files
                    if os.path.exists(video_filepath):
                        os.remove(video_filepath)
                    if os.path.exists(audio_filepath):
                        os.remove(audio_filepath)
                    print("Temporary files cleaned up.")
            
            elif self.mode == 'S':
                stream = yt.streams.get_audio_only()
                print("Streaming audio to ffmpeg for MP3 conversion...")
                
                audio_buffer = io.BytesIO()
                stream.stream_to_buffer(buffer=audio_buffer)
                audio_buffer.seek(0) # Rewind buffer to the beginning

                mp3_file_path = os.path.join(self.path, f"{title}.mp3")

                command = [
                    'ffmpeg',
                    '-i', 'pipe:0',      # Input from stdin
                    '-vn',               # No video
                    '-b:a', '192k',      # Audio bitrate
                    '-f', 'mp3',         # Output format
                    mp3_file_path
                ]
                
                total_duration = yt.length # Get total video duration in seconds

                try:
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    stdout, stderr = process.communicate(input=audio_buffer.read())

                    if process.returncode == 0:
                        print(f"Successfully created {title}.mp3")
                    else:
                        print(f"Error during ffmpeg conversion: {stderr.decode('utf-8', errors='ignore')}")

                except Exception as e:
                    print(f"An error occurred during ffmpeg conversion: {e}")
        except Exception as err:
            print(f"An error occurred during video processing: {err}")

    

    

    def _update_ffmpeg_progress(self, pbar, progress_file_name):
        with open(progress_file_name, 'r') as f:
            while True:
                line = f.readline()
                if not line:
                    sleep(0.1)
                    continue
                if "progress=end" in line:
                    break
                if "out_time_ms=" in line:
                    parts = line.split("=")
                    if len(parts) > 1:
                        try:
                            time_ms = int(parts[1])
                            current_time_seconds = time_ms / 1_000_000
                            pbar.update(current_time_seconds - pbar.n)
                        except ValueError:
                            pass # Ignore lines that don't have a valid time

    def _download_playlist(self, url: str):
        """Downloads all videos from a YouTube playlist."""
        try:
            playlist_id = re.search(r"list=([a-zA-Z0-9_-]+)", url).group(1)
            print("playlist_id: " + playlist_id)
            playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
            pl = Playlist(playlist_url)
            print(f"\nFound playlist: {pl.title}")
            print(f"Downloading {len(pl.video_urls)} videos...")

            for video_url in tqdm(pl.video_urls, desc="Playlist Progress"):
                self._download_video(video_url)
                sleep(1)

            print("\nPlaylist download finished.")
        except Exception as err:
            print(f"Error processing playlist: {err}")

    def _print_instructions(self):
        print("\nCommands:")
        print("  - Enter a YouTube URL to download.")
        print("  - '/video' to switch to video download mode.")
        print("  - '/sound' to switch to sound download mode (default). ")
        print("  - '/exit' to quit the program.")
        print(f"\nDefault mode is Sound. Current mode: {'Video' if self.mode == 'V' else 'Sound'}")

    def run(self):
        """Runs the main loop to get user input and start downloads."""
        self._check_dependencies()
        self._print_instructions()

        while True:
            user_input = input('\nEnter YouTube URL or command: ').strip()

            if user_input.lower() == '/exit':
                print("Exiting program.")
                break
            elif user_input.lower() == '/video':
                self.mode = 'V'
                print("Mode changed to Video.")
                continue
            elif user_input.lower() == '/sound':
                self.mode = 'S'
                print("Mode changed to Sound.")
                continue
            elif not user_input:
                continue

            link = user_input
            self._download_video(link)

            print(f"\nCurrent mode: {'Video' if self.mode == 'V' else 'Sound'}")

            

            

if __name__ == "__main__":
    downloader = YouTubeDownloader()
    downloader.run()
