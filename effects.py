"""
Effects layer: the single place allowed to touch anything outside the window.

=============================================================================
 SAFETY -- what this module is allowed to do
=============================================================================
 Three real effects, and nothing else:

  1. Screen brightness. Every write goes through _apply_brightness(), which
     clamps to config.BRIGHTNESS_FLOOR..CEILING. The write itself is handed
     to _PanelWriter, a single daemon thread, because the driver takes
     100-250ms to answer and the render loop cannot afford to wait -- but
     the level is still chosen, and still clamped, in exactly one place.
     Restores do NOT go through the thread or the rate limit; see
     restore_brightness().

  2. The Windows Power Mode overlay, via power.py. That is the user-facing
     power slider; it does NOT touch the firmware fan curve, so the hardware
     keeps full authority over cooling.

  3. A gentle, time-boxed CPU warm-up during sunny, via warmup.py.

 There is deliberately NO fan, embedded-controller, or thermal-override code
 in this module or anywhere in the project, and none may be added.

 Everything is restored by shutdown(), which runs on every exit path
 including crashes: the brightness writer thread stopped and brightness put
 back to the captured level, power mode back to the user's own setting,
 warm-up threads stopped. shutdown() is idempotent. Returning to idle between events also restores brightness and
 power mode.
=============================================================================
"""

import threading
import time

import config
import lenovo as lenovo_api
import power as power_api
import warmup as warmup_api

# --- optional dependencies -------------------------------------------------
# Wrapped so the app still runs (and still exits cleanly) on a machine where
# a backend is unavailable -- important for a hackathon demo.
try:
    import screen_brightness_control as sbc
except Exception:                                   # pragma: no cover
    sbc = None


def clamp(value, low, high):
    return max(low, min(high, value))


class _PanelWriter:
    """The one thread allowed to call sbc.set_brightness().

    WHY THIS EXISTS. A set_brightness() on this panel is a 100-250ms
    round-trip through the display driver -- measured, not guessed. The
    sunshower sweep moves the rounded target about ten times a second, so
    the render loop was spending more than a second per second inside the
    driver and the whole scene ran at 9-16fps. That is what made the rain
    look laggy: the rain was fine, the frames were not.

    So the render loop no longer waits for the panel. It calls submit(),
    which drops a value in a one-slot mailbox and returns immediately; this
    thread picks it up and blocks on the driver instead. A value that is
    superseded before the thread gets to it is simply overwritten in the
    mailbox and never written -- which is correct, because it is stale.

    WHAT IT DOES NOT CHANGE. Nothing here decides a brightness level and
    nothing here can widen the safe band: values arrive already clamped to
    BRIGHTNESS_FLOOR..CEILING by _apply_brightness, which is still the only
    place a level is chosen.

    RESTORE ALWAYS WINS. force() is the restore path. It clears the mailbox
    and writes on the CALLING thread, synchronously, so a restore has
    finished by the time restore_brightness() returns -- which matters,
    because on shutdown the process may be gone a moment later. The
    sequence counter is what makes that safe against a write already in
    flight: every write takes a number, and a write whose number is older
    than the last one to reach the panel is dropped rather than applied.
    The panel's final value is therefore always the newest one asked for,
    and on every exit path that is the captured original.
    """

    def __init__(self, backend, log=print):
        self._backend = backend
        self.log = log
        self.failed = False        # set once the driver raises; latches off

        self._cv = threading.Condition()
        self._pending = None       # newest value not yet handed to the panel
        self._stop = False
        self._seq = 0              # ticket for the next write
        self._done = 0             # newest ticket that has reached the panel
        self._write_lock = threading.Lock()

        self._thread = threading.Thread(target=self._run, name="brightness",
                                        daemon=True)
        self._thread.start()

    # -- render-loop side (never blocks) ----------------------------------
    def submit(self, value):
        with self._cv:
            if self._stop:
                return
            self._pending = value
            self._cv.notify()

    # -- restore side (blocks, and has the last word) ----------------------
    def force(self, value):
        """Write now, on this thread, beating anything queued or in flight."""
        with self._cv:
            self._pending = None       # drop whatever the sweep wanted
            self._seq += 1
            ticket = self._seq
        self._write(value, ticket)

    def stop(self):
        """Retire the thread. Idempotent; force() still works afterwards, so
        shutdown can stop the sweep and then restore."""
        with self._cv:
            self._stop = True
            self._pending = None
            self._cv.notify_all()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            # Bounded: the only thing it can be doing is one driver call.
            thread.join(timeout=1.0)

    # -- the thread --------------------------------------------------------
    def _run(self):
        while True:
            with self._cv:
                while self._pending is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                value, self._pending = self._pending, None
                self._seq += 1
                ticket = self._seq
            self._write(value, ticket)

    def _write(self, value, ticket):
        with self._write_lock:
            # A restore that overtook us has already put the right number on
            # the panel; re-applying a stale sweep value over it is exactly
            # the bug this counter exists to prevent.
            if ticket <= self._done or self.failed:
                return
            try:
                self._backend.set_brightness(value)
                self._done = ticket
            except Exception as exc:
                self.failed = True
                self.log(f"brightness: write failed, disabling ({exc})")


class Effects:
    """Owns brightness, power mode, and the warm-up."""

    def __init__(self, log=print):
        self.log = log

        # -- brightness --
        self.brightness_available = sbc is not None and config.BRIGHTNESS_ENABLED
        self.original_brightness = None
        self.current_brightness = None
        self.requested_brightness = None
        # Last value HANDED TO the panel; see _apply_brightness.
        self._applied_brightness = None
        # When that happened, and the thread that does the waiting for us.
        self._last_write_at = float("-inf")
        self._writer = None

        # -- power mode (cross-vendor fallback) --
        self.power = power_api.PowerMode(log=log)
        self.requested_power = None
        self.power_enabled = config.POWER_MODE_ENABLED

        # -- Lenovo smart-fan preset (what actually moves the fan on a LOQ) --
        self.lenovo = lenovo_api.LenovoThermal(log=log)
        self.requested_lenovo = None
        self.lenovo_enabled = config.LENOVO_FAN_ENABLED

        # -- warm-up --
        self.warmup = warmup_api.Warmup(log=log)
        self.requested_warmup = False
        # True once this event's warm-up budget is used up; see tick_warmup.
        self._warmup_spent = False

        self._capture_original_brightness()
        if self.power_enabled:
            self.power.capture_original()
        else:
            self.log("power: disabled by config")
        if self.lenovo_enabled:
            self.lenovo.capture_original()
        else:
            self.log("lenovo: disabled by config")

    # ======================================================================
    # Brightness
    # ======================================================================
    def _capture_original_brightness(self):
        if not self.brightness_available:
            self.log("brightness: backend unavailable, running in no-op mode")
            return
        try:
            levels = sbc.get_brightness()
            if levels:
                self.original_brightness = int(levels[0])
                self.current_brightness = float(self.original_brightness)
                self._applied_brightness = self.original_brightness
                self.log(f"brightness: captured original = {self.original_brightness}%")
        except Exception as exc:
            self.brightness_available = False
            self.log(f"brightness: disabled ({exc})")
            return
        if self.brightness_available:
            self._writer = _PanelWriter(sbc, log=self.log)
            self.log("brightness: writes throttled to one per "
                     f"{config.BRIGHTNESS_MIN_INTERVAL:0.2f}s and moved off "
                     "the render loop; restores bypass both")

    def push_brightness(self, target):
        """Record a scene's brightness intent. None means 'leave it alone'."""
        self.requested_brightness = target

    def tick_brightness(self, dt):
        """Step the display toward the requested level, clamped to safe bounds."""
        if not self.brightness_available or self.requested_brightness is None:
            return
        target = clamp(float(self.requested_brightness),
                       config.BRIGHTNESS_FLOOR, config.BRIGHTNESS_CEILING)
        if self.current_brightness is None:
            self.current_brightness = target

        step = config.BRIGHTNESS_STEP * max(dt * config.FPS, 1.0)
        delta = target - self.current_brightness
        if abs(delta) <= step:
            self.current_brightness = target
        else:
            self.current_brightness += step if delta > 0 else -step
        self._apply_brightness(self.current_brightness)

    def _apply_brightness(self, value):
        """The one and only write path to the display brightness.

        SAFETY FIRST, and unchanged: the clamp below is what keeps every
        value inside BRIGHTNESS_FLOOR..CEILING, and it happens before
        anything else in here. Nothing past this line can widen that band.

        PERFORMANCE is what the rest of this is. A set_brightness() on this
        panel is a 100-250ms driver round-trip -- measured; an earlier note
        here guessed ~24ms and was an order of magnitude out. The sunshower
        sweep changes the rounded target about ten times a second, so the
        loop was asking for more than a second of driver time per second of
        wall clock and the scene fell to 9-16fps. Three things stop that:

          1. Dedupe. An unchanged rounded value is never written. (Already
             here, and kept.)
          2. Rate limit. At most one new value every
             config.BRIGHTNESS_MIN_INTERVAL seconds. current_brightness goes
             on tracking the intended level between writes, so the next
             write is the level the sweep is at THEN rather than a stale one
             -- the sweep does not drift, it only steps more coarsely.
          3. Off-thread. Even a permitted write does not block the frame;
             _PanelWriter waits on the driver instead.

        WHAT THAT COSTS, measured on this machine: the panel and the overlay
        share one display driver, and they compete. Idle, a write takes
        ~110ms; while the overlay is pushing 1920x1080 frames at 30fps the
        SAME write takes 3-5s. So the writer thread gets through roughly one
        value every few seconds and the sweep is coarse -- it still moves
        across its whole band, just in bigger steps. That is the trade this
        is deliberately making: the eye forgives a stepped sweep and does
        not forgive stepped rain. Turning BRIGHTNESS_MIN_INTERVAL down does
        not buy finer steps, because the driver, not the throttle, is the
        limit; the throttle is there so the mailbox is not churned pointlessly.

        None of it touches restore_brightness(), which bypasses all three.
        """
        safe = int(round(clamp(value, config.BRIGHTNESS_FLOOR,
                               config.BRIGHTNESS_CEILING)))
        if safe == self._applied_brightness or self._writer is None:
            return
        now = time.monotonic()
        if now - self._last_write_at < config.BRIGHTNESS_MIN_INTERVAL:
            return                      # the sweep keeps moving; we catch up
        self._last_write_at = now
        self._applied_brightness = safe
        self._writer.submit(safe)
        if self._writer.failed:
            self.brightness_available = False

    def restore_brightness(self):
        """Put the display back exactly as we found it.

        The throttle and the writer thread are BYPASSED here on purpose.
        This is the safety path -- idle, Ctrl+Shift+R, Ctrl+Shift+Q,
        shutdown and the crash handler all end up in here -- so it writes
        the captured original synchronously, whatever the rate limit thinks,
        and _PanelWriter.force() makes sure no queued or in-flight sweep
        value can land on top of it afterwards. By the time this returns the
        panel is back where the user left it.
        """
        if not self.brightness_available or self.original_brightness is None:
            return
        if not config.BRIGHTNESS_RESTORE_ON_EXIT:
            return
        try:
            if self._writer is not None:
                self._writer.force(self.original_brightness)
            else:                                   # pragma: no cover
                sbc.set_brightness(self.original_brightness)
            self.current_brightness = float(self.original_brightness)
            self._applied_brightness = self.original_brightness
            # Reopen the throttle: a restore is itself a write, and the next
            # scene has to be free to move the panel again straight away.
            self._last_write_at = time.monotonic()
            self.log(f"brightness: restored to {self.original_brightness}%")
        except Exception as exc:
            self.log(f"brightness: restore failed ({exc})")

    # ======================================================================
    # Power mode
    # ======================================================================
    def push_power_mode(self, intent):
        """Record a scene's power-mode intent: 'efficiency', 'performance',
        'balanced', or None to hand the user's own setting back."""
        self.requested_power = intent

    def resolve_power_mode(self, intent):
        """Intent -> overlay GUID. 'performance' drops to balanced on
        battery rather than forcing a laptop to burn charge."""
        if intent == "efficiency":
            return power_api.BEST_EFFICIENCY
        if intent == "balanced":
            return power_api.BALANCED
        if intent == "performance":
            if self.power.on_ac_power() is False:
                return power_api.BALANCED
            return power_api.BEST_PERFORMANCE
        return None

    def tick_power_mode(self):
        """Apply the requested mode. set_mode() short-circuits when the mode
        is already active, so this is cheap to call every frame."""
        if not (self.power_enabled and self.power.available):
            return
        if self.requested_power is None:
            self.power.restore()
            return
        guid = self.resolve_power_mode(self.requested_power)
        if guid is not None:
            self.power.set_mode(guid)

    def restore_power_mode(self):
        if self.power.available:
            self.power.restore()

    def set_power_enabled(self, enabled):
        """Tray toggle. Turning it off restores the user's mode at once."""
        self.power_enabled = bool(enabled)
        config.POWER_MODE_ENABLED = self.power_enabled
        if not self.power_enabled:
            self.restore_power_mode()
        elif self.power.original is None:
            self.power.capture_original()
        self.log(f"power: effects {'on' if self.power_enabled else 'off'}")

    # ======================================================================
    # Lenovo smart-fan preset (see the safety rules in lenovo.py)
    # ======================================================================
    def push_lenovo_mode(self, intent):
        """Record a scene's fan intent: 'quiet', 'performance', 'balanced',
        or None to hand the user's own Fn+Q preset back."""
        self.requested_lenovo = intent

    def tick_lenovo(self):
        """set_mode() short-circuits when the preset is already active, so
        this is cheap to call every frame."""
        if not (self.lenovo_enabled and self.lenovo.available):
            return
        if self.requested_lenovo is None:
            self.lenovo.restore()
        else:
            self.lenovo.set_intent(self.requested_lenovo)

    def restore_lenovo(self):
        if self.lenovo.available:
            self.lenovo.restore()

    def set_lenovo_enabled(self, enabled):
        """Tray toggle. Turning it off restores the user's preset at once."""
        self.lenovo_enabled = bool(enabled)
        config.LENOVO_FAN_ENABLED = self.lenovo_enabled
        if not self.lenovo_enabled:
            self.restore_lenovo()
        elif self.lenovo.original is None:
            self.lenovo.capture_original()
        self.log(f"lenovo: fan effects {'on' if self.lenovo_enabled else 'off'}")

    # ======================================================================
    # Warm-up (sunny only; see the safety note in warmup.py)
    # ======================================================================
    def push_warmup(self, wanted):
        wanted = bool(wanted)
        # A fresh request (event start) re-arms the one allowed run. The
        # previous event's stop reason has to be cleared too, or tick_warmup
        # sees it and latches the budget off again before ever starting.
        if wanted and not self.requested_warmup:
            self._warmup_spent = False
            self.warmup.stopped_reason = None
        self.requested_warmup = wanted

    def tick_warmup(self):
        """Start the warm-up once per event, and honour its own caps.

        SAFETY: the warm-up stops itself when WARMUP_MAX_SECONDS or
        WARMUP_TEMP_CAP is reached. Without the _warmup_spent latch this
        method saw `running == False` on the very next frame and started it
        again, so a 150-second sunny event re-ran the "hard cap" over and
        over instead of stopping. The cap is a per-event budget, not a duty
        cycle -- once it is spent, no more warm-up until the next event.
        """
        if self.requested_warmup and config.WARMUP_ENABLED:
            if self.warmup.running or self._warmup_spent:
                return
            if self.warmup.stopped_reason is not None:
                # It stopped itself on a cap. Latch off for this event.
                self._warmup_spent = True
                self.warmup.stop()
                self.log("warmup: budget spent for this event, not restarting")
                return
            self.warmup.start()
        elif self.warmup.running:
            self.warmup.stop("no longer requested")

    # ======================================================================
    # Frame + shutdown
    # ======================================================================
    def update(self, dt):
        self.tick_brightness(dt)
        self.tick_power_mode()
        self.tick_lenovo()
        self.tick_warmup()

    def idle(self):
        """Between events: hand the machine back to the user."""
        self.push_brightness(self.original_brightness)
        self.push_power_mode(None)
        self.push_lenovo_mode(None)
        self.push_warmup(False)
        self.tick_warmup()
        self.restore_power_mode()
        self.restore_lenovo()

    def _stop_writer(self):
        if self._writer is not None:
            self._writer.stop()

    def shutdown(self):
        """Always safe to call twice. Runs on every exit path, including
        crashes. Each restore is wrapped so one failure cannot stop the
        others -- a stuck fan preset must never survive because brightness
        happened to raise."""
        for label, action in (("warmup", lambda: self.warmup.stop("shutdown")),
                              ("lenovo", self.restore_lenovo),
                              ("power", self.restore_power_mode),
                              # The writer thread goes down BEFORE the
                              # restore, so nothing the sweep queued is left
                              # sitting behind it. force() would win anyway;
                              # this leaves nothing to race with at all.
                              ("brightness writer", self._stop_writer),
                              ("brightness", self.restore_brightness)):
            try:
                action()
            except Exception as exc:
                self.log(f"{label}: restore failed during shutdown ({exc})")
