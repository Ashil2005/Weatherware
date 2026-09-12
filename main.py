"""
Weatherware -- entry point.

Engine loop: idle for a random gap, roll a weather event, run it for
WEATHER_DURATION (2.5 minutes by default), then go quiet again.

THE OVERLAY
  In its real mode the app is a transparent, click-through, always-on-top
  window with no taskbar button that never takes focus. Weather floats over
  the live desktop and you keep working underneath it. Transparency is real
  per-pixel alpha via UpdateLayeredWindow -- see overlay.py for why a
  colour-keyed window could not do this job.

  Because the window is click-through and never focused, it cannot receive
  key presses. EVERY control is therefore a global hotkey or a tray item.

REAL HARDWARE EFFECTS (all via effects.py, all restored on exit)
  * screen brightness, clamped to a safe band
  * the Windows Power Mode overlay -- the user-facing power slider, which
    changes fan behaviour without touching the firmware fan curve
  * a gentle, time-boxed CPU warm-up during sunny only
  See the safety rule at the top of config.py.

SOUND (audio.py, deliberately outside the effects layer)
  One looping ambient bed per scene, started and stopped on the same scene
  edges the hardware effects use, plus thunder and gust one-shots fired by
  the scenes themselves at the moment the flash or the surge happens. Any
  audio failure degrades to silence and leaves the weather untouched.

Run:
    .venv\\Scripts\\python.exe main.py             (real: 2.5-minute events)
    .venv\\Scripts\\python.exe main.py --demo      (30s events, short gaps)
    .venv\\Scripts\\python.exe main.py --windowed  (opaque dev window)
    .venv\\Scripts\\python.exe main.py --selftest 5  (headless, no hardware)
    .venv\\Scripts\\python.exe main.py --no-admin  (skip the UAC prompt)
    dist\\Weatherware.exe                       (the packaged build, same flags)

Elevation: Lenovo fan control needs Administrator, so at startup the app
relaunches itself through the UAC prompt (config.AUTO_ELEVATE). Declining
is safe -- it keeps running unelevated and only the fan preset no-ops.

Controls, all global:
    Ctrl+Shift+Q  quit and restore everything
    Ctrl+Shift+R  restore brightness without quitting
    Ctrl+Shift+M  mute / unmute the weather, without stopping it
    Ctrl+Shift+1..5  force sunny / rainy / sunshower / windy / snowy
    Tray menu     force weather, toggle power mode, mute sound, quit
"""

import argparse
import ctypes
import os
import random
import subprocess
import sys
import threading
import time

import config

try:
    import keyboard
except Exception:                                   # pragma: no cover
    keyboard = None

IS_WINDOWS = sys.platform == "win32"


class Engine:
    """Owns the state machine, the overlay window, and every shutdown path."""

    IDLE = "idle"
    WEATHER = "weather"

    def __init__(self, headless=False):
        self.headless = headless
        self.state = self.IDLE
        self.scene = None
        self.idle_remaining = self._roll_idle()
        self.forced = None
        self.running = True
        self.exit_reason = None
        self._exit_event = threading.Event()
        self._hotkeys = []

        # Window / overlay state.
        self.size = config.WINDOW_SIZE
        self.want_overlay = not config.WINDOWED_MODE
        self.overlay = None          # LayeredOverlay, or None when windowed
        self.hwnd = None
        self._topmost_due = 0.0

        # Imported late so --selftest can adjust config first.
        import effects
        self.effects = effects.Effects(log=self.log)

        # Sound is deliberately NOT part of the effects layer: it touches no
        # hardware, and keeping it separate makes that impossible to blur.
        # The mixer itself only comes up in setup_display, once pygame exists.
        import audio as audio_mod
        self.audio = audio_mod.Audio(log=self.log)

        self.pygame = None
        self.screen = None
        self.clock = None
        self.renderer = None
        self.panel = None

    # ======================================================================
    # Logging
    # ======================================================================
    def log(self, message):
        if config.DEBUG:
            print(f"[weatherware] {message}", flush=True)

    # ======================================================================
    # Setup
    # ======================================================================
    def setup_display(self):
        if self.headless:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

        # Must happen before the window exists, or a scaled display gives us
        # a window smaller than the screen we are trying to cover.
        if self.want_overlay:
            self._set_dpi_aware()

        import pygame
        self.pygame = pygame
        # pre_init has to land before pygame.init() brings the mixer up with
        # its own defaults; start() then loads the clips. Both are no-ops on
        # a machine with no audio device.
        self.audio.pre_init(pygame)
        pygame.init()
        self.audio.start()

        if self.want_overlay:
            size = self._desktop_size()
            flags = pygame.NOFRAME
        else:
            size = config.WINDOW_SIZE
            flags = 0

        self.screen = pygame.display.set_mode(size, flags)
        self.size = self.screen.get_size()
        pygame.display.set_caption(f"{config.APP_NAME} {config.VERSION}")
        self.clock = pygame.time.Clock()

        if self.want_overlay:
            self._init_hwnd()
            import overlay as overlay_mod
            # OVERLAY_WINDOW_ALPHA, not OVERLAY_ALPHA: the global dimming of
            # the WEATHER now happens in Renderer.flush_overlay, so that a
            # foreground layer (the wedding cast) can be drawn genuinely
            # opaque. See the note on OVERLAY_ALPHA in config.py.
            candidate = overlay_mod.LayeredOverlay(
                self.hwnd, self.size,
                alpha=config.OVERLAY_WINDOW_ALPHA, log=self.log)
            self.overlay = candidate if candidate.available else None

        import renderer
        self.renderer = renderer.Renderer(self.screen, overlay=self.overlay is not None)

        if self.overlay is not None:
            got = self.renderer.hint()
            if got is not None:
                # Premultiplied once: overlay.py composites it after the
                # artwork upscale so the text stays crisp.
                self.overlay.set_hint(got[0].premul_alpha())
                self.overlay.set_hint_position(got[1])

        mode = ("click-through overlay" if self.overlay is not None
                else "windowed" if not self.want_overlay
                else "plain window (overlay unavailable)")
        self.log(f"display: {self.size[0]}x{self.size[1]} {mode}"
                 f"{' (headless)' if self.headless else ''}")

    # -- Win32 overlay plumbing -------------------------------------------
    def _set_dpi_aware(self):
        if not IS_WINDOWS:
            return
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception as exc:
                self.log(f"dpi: could not set awareness ({exc})")

    def _desktop_size(self):
        """Primary monitor only -- a second screen is left uncovered."""
        if IS_WINDOWS:
            try:
                user32 = ctypes.windll.user32
                return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
            except Exception as exc:
                self.log(f"display: falling back to windowed size ({exc})")
        return config.WINDOW_SIZE

    def _init_hwnd(self):
        """Grab the SDL window handle. Absent under the dummy driver."""
        try:
            self.hwnd = self.pygame.display.get_wm_info().get("window")
        except Exception:
            self.hwnd = None
        if not self.hwnd:
            self.log("overlay: no window handle (headless?) -- "
                     "transparency and click-through are no-ops")

    def _keep_on_top(self):
        """Other apps going fullscreen can steal the top slot, so re-assert
        it on a timer rather than once at startup."""
        if self.overlay is None:
            return
        now = time.monotonic()
        if now >= self._topmost_due:
            self._topmost_due = now + config.TOPMOST_INTERVAL
            self.overlay.set_topmost()

    # -- hotkeys -----------------------------------------------------------
    def setup_hotkeys(self):
        """Everything is global: the overlay can never receive a key press."""
        if keyboard is None:
            self.log("hotkeys: 'keyboard' unavailable -- the tray menu is the "
                     "only way to control or quit the app")
            return
        bindings = [
            (config.KILL_SWITCH_HOTKEY,
             lambda: self.request_exit(f"hotkey {config.KILL_SWITCH_HOTKEY}")),
            (config.PANIC_RESTORE_HOTKEY, self.effects.restore_brightness),
            (config.MUTE_HOTKEY, self.audio.toggle_mute),
        ]
        for combo, name in config.FORCE_HOTKEYS.items():
            bindings.append((combo, (lambda n: lambda: self.force_weather(n))(name)))

        for combo, handler in bindings:
            try:
                self._hotkeys.append(keyboard.add_hotkey(combo, handler))
            except Exception as exc:
                self.log(f"hotkeys: could not bind {combo} ({exc})")

        forced = ", ".join(f"{k}={v}" for k, v in config.FORCE_HOTKEYS.items())
        self.log(f"hotkeys: {config.KILL_SWITCH_HOTKEY}=quit, "
                 f"{config.PANIC_RESTORE_HOTKEY}=restore brightness, "
                 f"{config.MUTE_HOTKEY}=mute, {forced}")

    def setup_tray(self):
        import control_panel
        self.panel = control_panel.ControlPanel(self)
        self.panel.start()

    # ======================================================================
    # State machine
    # ======================================================================
    @staticmethod
    def _roll_idle():
        return random.uniform(config.IDLE_MIN, config.IDLE_MAX)

    @staticmethod
    def roll_weather():
        names = list(config.WEATHER_WEIGHTS)
        weights = [config.WEATHER_WEIGHTS[n] for n in names]
        return random.choices(names, weights=weights, k=1)[0]

    def force_weather(self, name):
        """Queue a specific event. Global hotkeys and the tray use this."""
        self.forced = name
        self.log(f"forced: {name}")

    def start_weather(self, name):
        import scenes
        self.scene = scenes.make(name)
        self.state = self.WEATHER
        self.scene.start(self)
        # Sound follows the scene lifecycle, so it starts and stops on the
        # same edges the hardware effects do.
        self.audio.play_ambient_for_scene(name)
        self.log(f"event: {name} for {self.scene.duration:0.0f}s")

    def end_weather(self):
        if self.scene is not None:
            self.scene.stop(self)
            self.log(f"event: {self.scene.name} over")
        self.scene = None
        self.state = self.IDLE
        self.idle_remaining = self._roll_idle()
        # Hand the machine back: brightness, power mode, warm-up all released,
        # and the weather bed faded out. Idle is silent, so the last
        # thunderclap fades with the rain instead of over the next scene.
        self.audio.stop_ambient()
        self.audio.stop_oneshots()
        self.effects.idle()
        self.log(f"idle: next front in {self.idle_remaining:0.0f}s")

    def update(self, dt):
        if self.state == self.IDLE:
            if self.forced:
                name, self.forced = self.forced, None
                self.start_weather(name)
            else:
                self.idle_remaining -= dt
                if self.idle_remaining <= 0:
                    self.start_weather(self.roll_weather())
        elif self.state == self.WEATHER:
            self.scene.update(dt, self)
            if self.scene.finished or self.forced:
                self.end_weather()

        self.effects.update(dt)
        # Only advances the delayed one-shots (thunder trailing its flash);
        # the loops themselves run in the mixer's own thread.
        self.audio.update(dt)

    # ======================================================================
    # Drawing -- pure weather, plus one tiny hotkey reminder
    # ======================================================================
    def draw(self):
        r = self.renderer
        scene = self.scene
        shake = scene.shake_offset if scene is not None else (0, 0)

        r.begin_frame()
        if scene is not None:
            scene.draw(r.canvas, r)

        if self.overlay is not None:
            # The hint is composited by the overlay at full resolution, and
            # shake moves the window rather than the pixels. So is the
            # wedding cast, via draw_foreground, which keeps its edges crisp.
            self.overlay.present(r.canvas, shake, r.draw_foreground)
        else:
            r.draw_foreground(r.canvas)
            r.draw_escape_hint(r.canvas)
            r.present_windowed(shake)

    # ======================================================================
    # Events + exit
    # ======================================================================
    def pump_events(self):
        """The queue still has to be drained or Windows thinks we hung. The
        overlay never has focus, so no key handling lives here -- in
        --windowed dev mode ESC is honoured as a convenience."""
        pg = self.pygame
        for event in pg.event.get():
            if event.type == pg.QUIT:
                self.request_exit("window closed")
            elif event.type == pg.KEYDOWN and self.overlay is None:
                if event.key == pg.K_ESCAPE:
                    self.request_exit("escape (dev window)")
                elif pg.K_1 <= event.key <= pg.K_9:
                    # Same order as config.FORCE_HOTKEYS, so the dev window
                    # and the global hotkeys never drift apart.
                    names = list(config.FORCE_HOTKEYS.values())
                    index = event.key - pg.K_1
                    if index < len(names):
                        self.force_weather(names[index])

    def request_exit(self, reason="unspecified"):
        """Thread-safe. Called by hotkeys, the tray, and the window."""
        if self.running:
            self.exit_reason = reason
            self.log(f"exit requested: {reason}")
        self.running = False
        self._exit_event.set()

    def shutdown(self):
        """Idempotent teardown -- runs on every exit path, including crashes.
        Restores brightness AND the power mode, and stops the warm-up."""
        self.log("shutting down")
        if self.scene is not None:
            try:
                self.scene.stop(self)
            except Exception:
                pass
            self.scene = None

        for hk in self._hotkeys:
            try:
                keyboard.remove_hotkey(hk)
            except Exception:
                pass
        self._hotkeys.clear()

        # Silence first, for the same reason the overlay comes down next: the
        # two things the user can see and hear must not outlive the exit
        # request while brightness and the fan preset are still unwinding.
        try:
            self.audio.shutdown()
        except Exception as exc:
            print(f"[weatherware] audio shutdown error: {exc}", file=sys.stderr)

        # Get the overlay off the screen before the slower teardown below,
        # so a stuck window can never outlive the exit request.
        if self.overlay is not None:
            try:
                self.overlay.hide()
                self.overlay.destroy()
            except Exception:
                pass

        if self.panel is not None:
            self.panel.stop()

        try:
            self.effects.shutdown()   # warm-up off, power mode + brightness back
        except Exception as exc:
            print(f"[weatherware] effects shutdown error: {exc}", file=sys.stderr)

        if self.pygame is not None:
            try:
                self.pygame.quit()
            except Exception:
                pass
        self.log(f"clean exit ({self.exit_reason or 'end of run'})")

    # ======================================================================
    # Main loop
    # ======================================================================
    def run(self, max_seconds=None):
        started = time.monotonic()
        try:
            self.setup_display()
            if config.PROFILE:
                # Opt-in only (--profile); never imported otherwise.
                import profiler
                profiler.install(self)
            self.setup_hotkeys()
            self.setup_tray()
            self.effects.idle()
            # Say the active timing out loud: it is far too easy to run
            # --fast, see 6-second weather, and think the app is broken.
            self.log(timing_line())
            self.log(f"idle: first front in {self.idle_remaining:0.0f}s")

            while self.running:
                dt = self.clock.tick(config.FPS) / 1000.0
                dt = min(dt, 0.1)                  # survive a stalled frame
                self.pump_events()
                self._keep_on_top()
                self.update(dt)
                self.draw()
                if max_seconds is not None and time.monotonic() - started >= max_seconds:
                    self.request_exit(f"selftest limit {max_seconds}s")
        except KeyboardInterrupt:
            self.request_exit("ctrl+c")
        finally:
            self.shutdown()
        return 0


#: Which timing preset is active, for the startup banner. Set in main().
TIMING_PROFILE = "default"

#: What each flag does to WEATHER_DURATION, so the banner can name them all.
TIMING_PRESETS = {"default": 150, "demo": 30, "fast": 6}


def timing_line():
    """One unmistakable line about how long weather actually lasts."""
    others = ", ".join(f"--{name} for {secs}s"
                       for name, secs in TIMING_PRESETS.items()
                       if name != "default" and name != TIMING_PROFILE)
    active = f"events last {config.WEATHER_DURATION:0.0f}s"
    if TIMING_PROFILE != "default":
        active += f" (--{TIMING_PROFILE})"
    return (f"{active}, gaps {config.IDLE_MIN:0.0f}-{config.IDLE_MAX:0.0f}s"
            f"  (default is {TIMING_PRESETS['default']}s; run {others})")


#: Hidden flag the elevated copy is started with. Its presence means "you
#: ARE the relaunch": never try to elevate again, even if this copy somehow
#: still is not admin, so a failed elevation can never loop.
ELEVATED_FLAG = "--elevated-relaunch"

UNELEVATED_NOTE = ("running without Administrator -- Lenovo fan control is "
                   "disabled, everything else works")


def relaunch_as_admin(argv):
    """Re-run this program elevated via the UAC prompt, with the same
    arguments plus ELEVATED_FLAG. Returns True if the elevated copy was
    started, in which case this unelevated one should simply exit."""
    try:
        args = [a for a in argv if a not in ("--admin", ELEVATED_FLAG)]
        args.append(ELEVATED_FLAG)
        if not getattr(sys, "frozen", False):
            # From source, python.exe is the program and the script is its
            # first argument. Frozen, the .exe is both.
            args.insert(0, os.path.abspath(sys.argv[0]))
        # A one-file build must start a fresh copy that unpacks its own
        # bundle, not one that believes it is this process's child.
        os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, subprocess.list2cmdline(args),
            os.getcwd(), 1)
        # ShellExecuteW returns > 32 on success; declining UAC gives 5.
        if rc > 32:
            print("[weatherware] relaunching elevated; this process will exit",
                  flush=True)
            return True
        print(f"[weatherware] UAC declined or failed (code {rc}); "
              f"{UNELEVATED_NOTE}", flush=True)
    except Exception as exc:
        print(f"[weatherware] could not relaunch as admin ({exc}); "
              f"{UNELEVATED_NOTE}", flush=True)
    return False


def ensure_elevated(args, argv):
    """Returns True if this process should exit because an elevated copy
    has taken over. Every failure path returns False and the app runs on."""
    if not IS_WINDOWS or args.selftest or args.no_admin:
        return False
    if not (config.AUTO_ELEVATE or args.admin):
        return False
    import lenovo
    if lenovo.is_admin():
        if config.DEBUG:
            print("[weatherware] running as Administrator", flush=True)
        return False
    if args.elevated_relaunch:
        print(f"[weatherware] relaunched but still not elevated; "
              f"{UNELEVATED_NOTE}", flush=True)
        return False
    return relaunch_as_admin(argv)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=f"{config.APP_NAME} -- {config.APP_TAGLINE}")
    p.add_argument("--demo", action="store_true",
                   help="30-second events and short gaps, for quick iteration")
    p.add_argument("--fast", action="store_true",
                   help="very short events and gaps, for smoke tests")
    p.add_argument("--selftest", type=float, metavar="SECONDS",
                   help="run headless for N seconds with hardware effects off, then exit")
    p.add_argument("--windowed", action="store_true",
                   help="opaque development window instead of the overlay")
    p.add_argument("--admin", action="store_true",
                   help="relaunch elevated via UAC even when config.AUTO_ELEVATE "
                        "is off (Lenovo fan control needs it)")
    p.add_argument("--no-admin", action="store_true",
                   help="skip the UAC prompt and run unelevated (Lenovo fan "
                        "control no-ops); for development")
    p.add_argument(ELEVATED_FLAG, action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--profile", action="store_true",
                   help="print a per-frame timing breakdown about once a second")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # Lenovo fan control needs elevation, so ask for it up front. Declined
    # or failed, the app carries on unelevated; --no-admin skips the prompt.
    if ensure_elevated(args, list(sys.argv[1:] if argv is None else argv)):
        return 0

    global TIMING_PROFILE
    if args.selftest:
        TIMING_PROFILE = "fast"
    elif args.demo:
        TIMING_PROFILE = "demo"
    elif args.fast:
        TIMING_PROFILE = "fast"

    if args.windowed:
        config.WINDOWED_MODE = True

    if args.profile:
        config.PROFILE = True

    if args.selftest:
        # Verification must never touch real hardware -- including the fan,
        # in case a self-test is ever run from an elevated shell.
        config.BRIGHTNESS_ENABLED = False
        config.POWER_MODE_ENABLED = False
        config.LENOVO_FAN_ENABLED = False
        config.WARMUP_ENABLED = False

    if args.demo:
        config.WEATHER_DURATION = 30.0
        config.IDLE_MIN, config.IDLE_MAX = 4.0, 10.0
        config.FADE_IN, config.FADE_OUT = 2.0, 2.5

    if args.fast or args.selftest:
        config.WEATHER_DURATION = 6.0
        config.IDLE_MIN, config.IDLE_MAX = 1.0, 2.0
        config.FADE_IN, config.FADE_OUT = 0.5, 0.5

    engine = Engine(headless=bool(args.selftest))
    return engine.run(max_seconds=args.selftest)


if __name__ == "__main__":
    sys.exit(main())
