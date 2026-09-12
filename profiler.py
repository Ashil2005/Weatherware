"""
Opt-in frame profiler -- `main.py --profile` (or config.PROFILE = True).

OFF BY DEFAULT, and when it is off this module is never even imported: the
engine calls install() behind the flag and nowhere else, so a normal run
pays nothing for it.

When it is on, it wraps -- for this process only, at the class level -- the
handful of calls that make up a frame, times each one EXCLUSIVELY of the
other wrapped calls inside it, and prints an averaged breakdown about once a
second, plus a per-scene summary at exit. Every wrapper is time-in, call,
time-out: no argument and no return value is touched, and nothing here
decides anything about the picture, the timeline, the audio or the hardware.

What the rows mean:
  <scene>.update / .draw   that scene's own work, children excluded
  wedding.update / .draw   the ceremony (sunshower only)
  fog.update / .draw       the condensation layer (rainy/snowy; off in sunshower)
  present                  premultiply + upscale + UpdateLayeredWindow
  effects                  Effects.update on the render thread (brightness tick,
                           power mode, fan preset, warm-up)
  [bracketed]              helpers inside that row: composite = layer clear /
                           merge / flush blits, smoothscale / scale = pygame
                           transform calls (with a call count), ulw =
                           UpdateLayeredWindow
  frame                    the per-frame canvas clear (Renderer.begin_frame)
  other                    whatever is left: the loop, events, timeline bookkeeping
"""

import atexit
import functools
import gc
import threading
import time
from collections import defaultdict

_now = time.perf_counter

#: Renderer methods that clear, merge or flush the shared scratch layers.
_COMPOSITE = ("begin_frame", "new_overlay", "new_art", "merge_art", "flush_overlay")

#: Top-level rows, in print order. Anything else that turns up is appended.
_ORDER = ("sunny.update", "rainy.update", "sunshower.update", "wedding.update",
          "fog.update", "sunny.draw", "rainy.draw", "fog.draw", "wedding.draw",
          "sunshower.draw", "frame", "present", "effects", "audio", "events")


class _Window:
    """Accumulated numbers for a run of frames under one label."""

    def __init__(self):
        self.frames = 0
        self.periods = []
        self.wait = 0.0
        self.excl = defaultdict(float)
        self.calls = defaultdict(int)
        self.busy_frames = 0
        self.busy_period = 0.0
        self.ulw_idle = []
        self.ulw_busy = []
        self.panel_main = 0
        self.panel_other = []
        self.gc = []

    def absorb(self, other):
        self.frames += other.frames
        self.periods += other.periods
        self.wait += other.wait
        for k, v in other.excl.items():
            self.excl[k] += v
        for k, v in other.calls.items():
            self.calls[k] += v
        self.busy_frames += other.busy_frames
        self.busy_period += other.busy_period
        self.ulw_idle += other.ulw_idle
        self.ulw_busy += other.ulw_busy
        self.panel_main += other.panel_main
        self.panel_other += other.panel_other
        self.gc += other.gc


class _ClockProxy:
    """Stands in for pygame's Clock so the frame edge and the sleep inside
    tick() can be timed without touching the main loop."""

    def __init__(self, clock, prof):
        self._clock = clock
        self._prof = prof

    def tick(self, *args):
        start = _now()
        result = self._clock.tick(*args)
        self._prof.end_frame(wait=_now() - start)
        return result

    def __getattr__(self, name):
        return getattr(self._clock, name)


class _User32Proxy:
    """Forwards everything to user32 except UpdateLayeredWindow, which is
    timed. Installed on the overlay instance only."""

    def __init__(self, user32, ulw):
        self._user32 = user32
        self.UpdateLayeredWindow = ulw

    def __getattr__(self, name):
        return getattr(self._user32, name)


class Profiler:
    def __init__(self, engine, interval=1.0):
        self.engine = engine
        self.interval = interval
        self._main = threading.get_ident()
        self._lock = threading.Lock()
        self._stack = []                 # [key, start, child seconds, owner]

        self._label = None
        self._win = _Window()
        self._win_start = _now()
        self._last_edge = None
        self._runs = {}                  # label -> _Window, for the exit summary

        self._panel_busy = 0             # set_brightness calls in flight
        self._panel_touched = False      # a write overlapped this frame
        self._gc_start = None

    # ======================================================================
    # Installing the wrappers
    # ======================================================================
    def wrap(self, fn, name, phase):
        prof = self
        main = self._main

        @functools.wraps(fn)
        def timed(*args, **kwargs):
            if threading.get_ident() != main:
                return fn(*args, **kwargs)
            stack = prof._stack
            if phase:
                key = owner = name
            else:
                owner = stack[-1][3] if stack else "frame"
                key = f"{owner}/{name}"
            entry = [key, _now(), 0.0, owner]
            stack.append(entry)
            try:
                return fn(*args, **kwargs)
            finally:
                total = _now() - entry[1]
                stack.pop()
                prof._win.excl[key] += total - entry[2]
                prof._win.calls[key] += 1
                if stack:
                    stack[-1][2] += total
        return timed

    def _patch(self, owner, attr, name, phase):
        fn = owner.__dict__.get(attr) if isinstance(owner, type) else getattr(owner, attr, None)
        if fn is None:
            return
        setattr(owner, attr, self.wrap(fn, name, phase))

    def install(self):
        import pygame

        import audio
        import effects
        import overlay
        import renderer
        import scenes
        import wedding

        engine = self.engine

        # Scenes: each class's OWN update/draw, so sunshower's children show
        # up as sunny.* / rainy.* rows and sunshower's own rows are ~0.
        for cls in scenes.SCENES.values():
            self._patch(cls, "update", f"{cls.name}.update", True)
            self._patch(cls, "draw", f"{cls.name}.draw", True)
        self._patch(wedding.WeddingCeremony, "update", "wedding.update", True)
        self._patch(wedding.WeddingCeremony, "draw", "wedding.draw", True)
        self._patch(renderer.CondensationFog, "update", "fog.update", True)
        self._patch(renderer.CondensationFog, "draw", "fog.draw", True)

        # Renderer helpers, reported inside whichever row called them.
        for attr, value in list(vars(renderer.Renderer).items()):
            if not callable(value):
                continue
            if attr in _COMPOSITE:
                self._patch(renderer.Renderer, attr, "composite", False)
            elif attr == "draw_sprite":
                self._patch(renderer.Renderer, attr, "sprites", False)
            elif attr.startswith("draw_") or attr == "apply_mirage":
                self._patch(renderer.Renderer, attr, attr.replace("draw_", ""), False)

        # Every scale, wherever it is called from -- a sprite rescale inside
        # wedding.draw shows up as wedding.draw/smoothscale with its count.
        pygame.transform.scale = self.wrap(pygame.transform.scale, "scale", False)
        pygame.transform.smoothscale = self.wrap(pygame.transform.smoothscale,
                                                 "smoothscale", False)

        self._patch(overlay.LayeredOverlay, "present", "present", True)
        if engine.overlay is not None and engine.overlay._user32 is not None:
            real = engine.overlay._user32
            ulw = self.wrap(self._ulw(real.UpdateLayeredWindow), "ulw", False)
            engine.overlay._user32 = _User32Proxy(real, ulw)

        self._patch(effects.Effects, "update", "effects", True)
        self._patch(audio.Audio, "update", "audio", True)
        self._patch(type(engine), "pump_events", "events", True)
        self._patch(type(engine), "_keep_on_top", "events", True)

        self._wrap_panel(effects)
        gc.callbacks.append(self._on_gc)
        engine.clock = _ClockProxy(engine.clock, self)
        atexit.register(self.summary)
        print("[profile] on -- per-second breakdown below, ms per frame, "
              "exclusive of nested rows", flush=True)

    def _ulw(self, fn):
        prof = self

        def update_layered_window(*args):
            start = _now()
            try:
                return fn(*args)
            finally:
                took = _now() - start
                (prof._win.ulw_busy if prof._panel_busy else prof._win.ulw_idle).append(took)
        return update_layered_window

    def _wrap_panel(self, effects):
        """Count every set/get_brightness, and on which thread. The render
        thread should never make one -- that is what the writer is for."""
        sbc = getattr(effects, "sbc", None)
        if sbc is None:
            return
        prof = self
        for attr in ("set_brightness", "get_brightness"):
            fn = getattr(sbc, attr, None)
            if fn is None:
                continue

            def counted(*args, _fn=fn, **kwargs):
                on_main = threading.get_ident() == prof._main
                with prof._lock:
                    prof._panel_busy += 1
                    prof._panel_touched = True
                start = _now()
                try:
                    return _fn(*args, **kwargs)
                finally:
                    took = _now() - start
                    with prof._lock:
                        prof._panel_busy -= 1
                        if on_main:
                            prof._win.panel_main += 1
                        else:
                            prof._win.panel_other.append(took)
            setattr(sbc, attr, counted)

    def _on_gc(self, phase, info):
        if threading.get_ident() != self._main:
            return
        if phase == "start":
            self._gc_start = _now()
        elif self._gc_start is not None:
            self._win.gc.append(_now() - self._gc_start)
            self._gc_start = None

    # ======================================================================
    # Per frame
    # ======================================================================
    def _current_label(self):
        scene = self.engine.scene
        if scene is None:
            return "idle"
        wed = getattr(scene, "wedding", None)
        if wed is not None:
            if wed.active:
                return f"{scene.name}+wedding"
            if getattr(scene, "wedding_due", None) is not None:
                return f"{scene.name} (pre-wedding)"
        return scene.name

    def end_frame(self, wait):
        now = _now()
        if self._last_edge is None:
            self._last_edge = now
            self._win = _Window()
            self._win_start = now
            return
        period = now - self._last_edge
        self._last_edge = now

        win = self._win
        win.frames += 1
        win.periods.append(period)
        win.wait += wait
        with self._lock:
            busy = self._panel_touched or self._panel_busy > 0
            self._panel_touched = self._panel_busy > 0
        if busy:
            win.busy_frames += 1
            win.busy_period += period

        label = self._current_label()
        if self._label is None:
            self._label = label
        if label != self._label or now - self._win_start >= self.interval:
            self._flush(now)
            self._label = label

    def _flush(self, now):
        win, self._win = self._win, _Window()
        span, self._win_start = now - self._win_start, now
        if win.frames == 0:
            return
        run = self._runs.setdefault(self._label, _Window())
        run.absorb(win)
        print(self._format(self._label, win, span, self._extra()), flush=True)

    def _extra(self):
        scene = self.engine.scene
        wed = getattr(scene, "wedding", None) if scene is not None else None
        if wed is not None and wed.active:
            return f"ceremony t={wed.t:5.1f}s"
        return ""

    # ======================================================================
    # Reporting
    # ======================================================================
    @staticmethod
    def _pct(values, q):
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    def _format(self, label, win, span, extra=""):
        n = win.frames
        ms = lambda seconds: 1000.0 * seconds / n
        period = sum(win.periods)
        fps = n / period if period > 0 else 0.0
        hitches = sum(1 for p in win.periods if p > 0.025)
        lines = [
            f"[profile] {label:<26} {extra:<16} {fps:5.1f} fps  "
            f"frame {1000 * period / n:5.1f}ms "
            f"(p95 {1000 * self._pct(win.periods, 0.95):5.1f}, "
            f"max {1000 * max(win.periods):5.1f}, >25ms {hitches}/{n})  "
            f"work {ms(period - win.wait):5.1f}  wait {ms(win.wait):5.1f}"
        ]

        rows = [k for k in _ORDER if any(key.split("/")[0] == k for key in win.excl)]
        rows += sorted({k.split("/")[0] for k in win.excl} - set(rows))
        accounted = 0.0
        for row in rows:
            own = win.excl.get(row, 0.0)
            helpers = {k.split("/", 1)[1]: v for k, v in win.excl.items()
                       if k.startswith(row + "/")}
            total = own + sum(helpers.values())
            accounted += total
            if 1000 * total / n < 0.005:
                continue
            parts = []
            for name, secs in sorted(helpers.items(), key=lambda kv: -kv[1]):
                if 1000 * secs / n < 0.05:
                    continue
                count = win.calls.get(f"{row}/{name}", 0)
                tag = f"x{count / n:.1f}" if name in ("scale", "smoothscale") else ""
                parts.append(f"{name} {ms(secs):.2f}{tag}")
            detail = f"  [{', '.join(parts)}]" if parts else ""
            lines.append(f"    {row:<16} {ms(total):6.2f}{detail}")
        other = period - win.wait - accounted
        lines.append(f"    {'other':<16} {ms(other):6.2f}")

        sprite_rescales = win.calls.get("wedding.draw/smoothscale", 0) + \
            win.calls.get("wedding.draw/scale", 0)
        busy = (f"{100 * win.busy_frames / n:3.0f}% of frames overlap a panel write"
                f" (their avg frame {1000 * win.busy_period / max(1, win.busy_frames):.1f}ms)")
        writes = (f"{len(win.panel_other)} panel write(s) on writer thread"
                  + (f" avg {1000 * sum(win.panel_other) / len(win.panel_other):.0f}ms"
                     if win.panel_other else ""))
        ulw = ""
        if win.ulw_idle or win.ulw_busy:
            avg = lambda xs: 1000 * sum(xs) / len(xs) if xs else float("nan")
            ulw = (f"; ULW {avg(win.ulw_idle):.1f}ms idle / "
                   f"{avg(win.ulw_busy):.1f}ms during a write")
        gcs = f"; gc {len(win.gc)}x {1000 * sum(win.gc):.1f}ms" if win.gc else ""
        lines.append(f"    checks: wedding sprite rescales {sprite_rescales}; "
                     f"sbc on render thread {win.panel_main}; {writes}; {busy}{ulw}{gcs}")
        return "\n".join(lines)

    def summary(self):
        if self._win.frames:
            self._flush(_now())
        if not self._runs:
            return
        cache = getattr(getattr(self.engine, "renderer", None), "_sprites", None)
        print("\n[profile] ===== whole-run summary, per scene =====", flush=True)
        for label, win in self._runs.items():
            if label == "idle":
                continue
            print(self._format(label, win, sum(win.periods)), flush=True)
        if cache is not None:
            print(f"[profile] renderer sprite cache: {len(cache)} scaled surfaces "
                  "(built once, reused)", flush=True)


def install(engine, interval=1.0):
    prof = Profiler(engine, interval)
    prof.install()
    return prof
