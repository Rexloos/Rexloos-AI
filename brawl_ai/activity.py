"""Bounded session diagnostics, independent of Tk and model data."""
from collections import deque
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import sys
import time
import threading


class ActivityLog:
    def __init__(self, folder):
        self.path = folder/"activity.log"
        self.lines = deque(maxlen=500)
        self.last = {}
        self.lock = threading.RLock()
        self.logger = logging.Logger(f"brawl-activity-{id(self)}", logging.INFO)
        self.failure = None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(self.path, maxBytes=2*1024**2, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            # Logging failures must not interrupt learning or print on every tick.
            handler.handleError = lambda record: self.failed()
            handler.addFilter(lambda record: not self.failure)
            self.logger.addHandler(handler)
        except OSError:
            self.failed()
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(console)

    def failed(self):
        self.failure = "File logging unavailable; activity remains visible in this session."

    def snapshot(self):
        with self.lock:
            return list(self.lines)

    def add(self, source, message, interval=0, repeat=False):
        with self.lock:
            self._add(source, message, interval, repeat)

    def _add(self, source, message, interval, repeat):
        now = time.monotonic()
        previous = self.last.get(source)
        message = str(message).replace("\n", " | ")[:4000]
        if previous and ((message == previous[1] and not repeat) or now-previous[0] < interval):
            return
        self.last[source] = (now, message)
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} [{source}] {message}"
        self.lines.append(line)
        self.logger.info(line)

    def close(self):
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
