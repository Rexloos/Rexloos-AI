"""Bounded latest-frame preview from Android's hardware H.264 encoder."""
import shutil
import subprocess
import threading
import time

from PIL import Image


class PreviewStream:
    def __init__(self, adb, serial, size, env):
        self.adb, self.serial, self.size, self.env = adb, serial, size, env
        self.started = time.monotonic()
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.image = None
        self.received = 0
        self.error = None
        self.encoder = self.decoder = None
        self.remote_pid = None
        self.process_lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def frame(self, timeout=1.5):
        requested = time.monotonic()
        deadline = requested+timeout
        with self.condition:
            # Only return a frame decoded after this request, never a queued one.
            while self.received <= requested and not self.error and not self.stopped.is_set():
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Preview stream did not deliver a new frame in time.")
                self.condition.wait(remaining)
            if self.error or self.stopped.is_set() or self.image is None:
                raise RuntimeError(self.error or "Preview stream stopped.")
            return self.image.copy()

    def run(self):
        try:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("FFmpeg is unavailable for streaming preview.")
            width, height = self.size
            # Identify only our remote process so closing a static stream also
            # stops its encoder; closing the local ADB pipe alone is insufficient.
            command = ("echo BRAWL_PREVIEW_PID=$$; exec screenrecord --output-format=h264 "
                       f"--bit-rate=4000000 --size={width}x{height} --time-limit=120 -")
            self.encoder = subprocess.Popen([self.adb, "-s", self.serial, "exec-out", "sh", "-c", command],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.env, bufsize=0)
            marker = self.encoder.stdout.readline(64).strip()
            prefix = b"BRAWL_PREVIEW_PID="
            if not marker.startswith(prefix) or not marker[len(prefix):].isdigit():
                raise RuntimeError("Preview encoder did not report its process ID.")
            with self.process_lock:
                self.remote_pid = int(marker[len(prefix):])
            self.decoder = subprocess.Popen([ffmpeg, "-v", "error", "-probesize", "32", "-analyzeduration", "0",
                "-flags", "low_delay", "-f", "h264", "-i", "pipe:0", "-an", "-threads", "1",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], stdin=self.encoder.stdout,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.encoder.stdout.close()
            frame_bytes = width*height*3
            while not self.stopped.is_set():
                data = bytearray()
                while len(data) < frame_bytes and not self.stopped.is_set():
                    part = self.decoder.stdout.read(frame_bytes-len(data))
                    if not part:
                        raise RuntimeError("Preview video stream ended.")
                    data.extend(part)
                if self.stopped.is_set():
                    break
                image = Image.frombytes("RGB", self.size, bytes(data))
                with self.condition:
                    self.image = image
                    self.received = time.monotonic()
                    self.condition.notify_all()
        except Exception as error:
            with self.condition:
                self.error = str(error)
                self.condition.notify_all()
        finally:
            self.terminate()
            for process in (self.decoder, self.encoder):
                if process is not None:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()

    def terminate(self):
        with self.process_lock:
            pid, self.remote_pid = self.remote_pid, None
        if pid is not None and pid > 1:
            try:
                subprocess.run([self.adb, "-s", self.serial, "shell", "kill", "-INT", str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env, timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
        for process in (self.decoder, self.encoder):
            if process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1)
                except (OSError, subprocess.SubprocessError):
                    pass

    def close(self):
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()
        self.terminate()
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)
