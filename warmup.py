"""
Gentle warm-up: the one part of Weatherware that intentionally makes heat.

Read this before changing anything here.

WHAT IT DOES
  During a sunny event only, a couple of daemon threads do short bursts of
  arithmetic with sleeps in between, so the machine idles warm rather than
  cool. That is enough for the fan to change note under Best power
  efficiency, which is the joke.

WHY IT IS SAFE
  * Partial load, not a pin. At most cpu_count // 4 workers, each running a
    short burst then sleeping -- roughly a third of one core per worker.
    These are threads doing pure-Python arithmetic, so the GIL serialises
    them: total load cannot exceed about one core no matter how many cores
    the machine has. Measured on a 16-core laptop it added ~4% total CPU
    over idle. Do not "fix" this by moving to multiprocessing.
  * Self-limiting. The whole thing stops after WARMUP_MAX_SECONDS per event
    regardless of how long sunny runs.
  * It stops on event end, kill switch, tray quit, and any crash, because
    stop() is called from the same shutdown path as the brightness restore.
  * If a CPU temperature is readable it stops immediately above
    WARMUP_TEMP_CAP. On most Windows laptops psutil exposes no temperature
    sensor at all, so treat the cap as a bonus, not the primary guard.
  * The primary guard is that the load is light and time-boxed, and the
    firmware fan curve is never touched -- the embedded controller keeps
    full authority over cooling and will always protect the chip.

WHAT MUST NEVER GO IN HERE
  Full-core saturation, unbounded runtime, GPU load, or anything that
  removes the time cap. It may only ever make the laptop mildly warm.
"""

import os
import sys
import threading
import time

import config

#: While the warm-up runs, Python's GIL switch interval is lowered from the
#: 5ms default. The render thread makes hundreds of short GIL-releasing
#: pygame.draw calls per frame; against CPU-bound worker threads at a 5ms
#: interval it gets caught in a convoy and starves -- measured at 26 fps
#: versus 51 fps with a shorter interval, and far worse once the frame
#: limiter's sleep is in the mix. This does not change how much work the
#: warm-up does, only how promptly the foreground can preempt it.
SWITCH_INTERVAL_WHILE_WARM = 0.0005

try:
    import psutil
except Exception:                                   # pragma: no cover
    psutil = None


def read_cpu_temp():
    """Highest core temperature in C, or None if unreadable.

    Windows very often has no accessible sensor here -- that is expected and
    is why the caller must not depend on this value.
    """
    if psutil is None or not hasattr(psutil, "sensors_temperatures"):
        return None
    try:
        readings = psutil.sensors_temperatures()
    except Exception:
        return None
    hottest = None
    for entries in (readings or {}).values():
        for entry in entries:
            value = getattr(entry, "current", None)
            if value and (hottest is None or value > hottest):
                hottest = value
    return hottest


class Warmup:
    """A light, time-boxed CPU load. Safe to start/stop repeatedly."""

    BURST = 0.05        # seconds of arithmetic
    REST = 0.11         # seconds asleep afterwards -> ~30% duty per worker
    TEMP_POLL = 2.0     # seconds between temperature checks

    def __init__(self, log=print):
        self.log = log
        self._stop = threading.Event()
        self._threads = []
        self._started_at = None
        self._temp_thread = None
        self.stopped_reason = None
        self._switch_interval = None

    # -- state -------------------------------------------------------------
    @property
    def running(self):
        return bool(self._threads) and not self._stop.is_set()

    @property
    def elapsed(self):
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    def worker_count(self):
        return max(1, (os.cpu_count() or 4) // 4)

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if not config.WARMUP_ENABLED or self.running:
            return
        self.stop()                     # clear out any finished threads
        self._stop = threading.Event()
        self._started_at = time.monotonic()
        self.stopped_reason = None

        # Keep the foreground responsive while these threads run.
        if self._switch_interval is None:
            self._switch_interval = sys.getswitchinterval()
            try:
                sys.setswitchinterval(SWITCH_INTERVAL_WHILE_WARM)
            except (ValueError, AttributeError):
                self._switch_interval = None

        count = self.worker_count()
        for i in range(count):
            thread = threading.Thread(target=self._work, name=f"warmup-{i}",
                                      daemon=True)
            thread.start()
            self._threads.append(thread)

        self._temp_thread = threading.Thread(target=self._watch, name="warmup-watch",
                                             daemon=True)
        self._temp_thread.start()

        self.log(f"warmup: {count} light worker(s), hard cap "
                 f"{config.WARMUP_MAX_SECONDS:0.0f}s, temp cap "
                 f"{config.WARMUP_TEMP_CAP:0.0f}C")

    def stop(self, reason=None):
        """Idempotent. Signals the threads and waits briefly for them."""
        if self._threads or self._temp_thread:
            self._stop.set()
            for thread in self._threads:
                thread.join(timeout=0.5)
            if self._temp_thread is not None:
                self._temp_thread.join(timeout=0.5)
            if reason and self.stopped_reason is None:
                self.stopped_reason = reason
                self.log(f"warmup: stopped ({reason})")
        self._threads = []
        self._temp_thread = None
        self._started_at = None

        if self._switch_interval is not None:
            try:
                sys.setswitchinterval(self._switch_interval)
            except (ValueError, AttributeError):
                pass
            self._switch_interval = None

    # -- internals ---------------------------------------------------------
    def _work(self):
        """Short bursts of arithmetic with rests in between."""
        value = 1.000001
        while not self._stop.is_set():
            deadline = time.monotonic() + self.BURST
            while time.monotonic() < deadline:
                # A few thousand float ops, then re-check the clock.
                for _ in range(2000):
                    value = value * 1.0000001 + 0.0000001
                if value > 1e6:
                    value = 1.000001
            if self._stop.wait(self.REST):
                return

    def _watch(self):
        """Enforces the time cap, and the temperature cap when readable."""
        while not self._stop.is_set():
            if self.elapsed >= config.WARMUP_MAX_SECONDS:
                self.stopped_reason = "time cap reached"
                self.log(f"warmup: stopped (time cap "
                         f"{config.WARMUP_MAX_SECONDS:0.0f}s reached)")
                self._stop.set()
                return
            temp = read_cpu_temp()
            if temp is not None and temp > config.WARMUP_TEMP_CAP:
                self.stopped_reason = f"temperature {temp:0.0f}C over cap"
                self.log(f"warmup: stopped (temperature {temp:0.0f}C over "
                         f"{config.WARMUP_TEMP_CAP:0.0f}C cap)")
                self._stop.set()
                return
            if self._stop.wait(self.TEMP_POLL):
                return
