"""Non-overlapping preview capture scheduling with measured processing cost."""
import math
import time


def capture_interval(value):
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Enter a capture interval between 0.2 and 60 seconds.") from error
    if not math.isfinite(value) or not 0.2 <= value <= 60:
        raise ValueError("Capture interval must be between 0.2 and 60 seconds.")
    return value


class CaptureLoop:
    def __init__(self, root, capture, blocked, report, clock=time.monotonic):
        self.root, self.capture, self.blocked, self.report = root, capture, blocked, report
        self.clock = clock
        self.enabled = False
        self.job = None
        self.generation = 0
        self.seconds = 2.0
        self.adaptive = True
        self.average = None

    def start(self, seconds, adaptive):
        seconds = capture_interval(seconds)
        self.stop()
        self.seconds, self.adaptive, self.enabled = seconds, adaptive, True
        self.average = None
        self.job = self.root.after(0, self.tick)

    def stop(self):
        self.enabled = False
        self.generation += 1
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None

    def tick(self):
        self.job = None
        if not self.enabled:
            return
        reason = self.blocked()
        if reason:
            self.report(f"Auto capture paused: {reason}")
            self.job = self.root.after(250, self.tick)
            return
        generation = self.generation
        started = self.clock()
        self.report("Auto capture: capturing and recognizing...")

        def complete():
            # A result arriving after stop/reconfigure must not restart an old loop.
            if not self.enabled or generation != self.generation:
                return
            elapsed = max(0, self.clock() - started)
            self.average = elapsed if self.average is None else .7*self.average + .3*elapsed
            target = max(self.seconds, 2*self.average) if self.adaptive else self.seconds
            # Measure start-to-start cadence; always leave time for user interaction.
            delay = max(.1, target - elapsed)
            self.report(f"Auto capture: {'adaptive' if self.adaptive else 'fixed'} | "
                        f"target {target:.2f}s | processing {elapsed:.2f}s")
            self.job = self.root.after(round(delay*1000), self.tick)

        self.capture(complete)
