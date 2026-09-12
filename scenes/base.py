"""Common shape for every weather scene.

A scene is a short-lived object: the engine builds one when an event starts,
ticks it every frame, and drops it when the event ends. Scenes never touch
hardware directly -- they publish an *intent* (brightness target, power-mode
name, whether the warm-up should run) and the effects layer decides what is
safe to actually do.

There is no on-screen text: no cards, no gauges, no HUD. Scenes draw weather
and nothing else.
"""

import config


class Scene:
    name = "base"
    label = "Unremarkable"
    color = config.COLOR_TEXT

    # What this scene *asks* for. effects.py clamps and resolves these.
    brightness_target = None    # None = leave the display alone
    power_intent = None         # 'efficiency' | 'performance' | 'balanced'
    lenovo_intent = None        # 'quiet' | 'balanced' | 'performance'
    wants_warmup = False        # gentle, time-boxed CPU warm-up (sunny only)

    def __init__(self):
        self.elapsed = 0.0
        self.duration = config.WEATHER_DURATION
        self.size = config.WINDOW_SIZE     # replaced with the real size in start()

    # -- lifecycle ---------------------------------------------------------
    def start(self, engine):
        """Called once when the event begins."""
        self.size = engine.size

    def update(self, dt, engine):
        """Advance the simulation by dt seconds."""
        self.elapsed += dt
        self.push_intent(engine)

    def push_intent(self, engine):
        """Hand this scene's hardware intent to the effects layer."""
        engine.effects.push_brightness(self.brightness_target)
        engine.effects.push_power_mode(self.power_intent)
        engine.effects.push_lenovo_mode(self.lenovo_intent)
        engine.effects.push_warmup(self.wants_warmup)

    def draw(self, surface, renderer):
        """Draw this scene's artwork onto surface."""

    def stop(self, engine):
        """Called once when the event ends, before the scene is discarded."""

    # -- helpers -----------------------------------------------------------
    @property
    def finished(self):
        return self.elapsed >= self.duration

    @property
    def progress(self):
        """0.0 at the start of the event, 1.0 at the end."""
        if self.duration <= 0:
            return 1.0
        return min(1.0, self.elapsed / self.duration)

    def intensity(self):
        """Eased 0..1..0 envelope: fade in, hold, fade out."""
        t, d = self.elapsed, self.duration
        if t < config.FADE_IN:
            return max(0.0, t / config.FADE_IN)
        if t > d - config.FADE_OUT:
            return max(0.0, (d - t) / config.FADE_OUT)
        return 1.0

    @property
    def shake_offset(self):
        """Screen-shake in pixels. Only the storm uses it."""
        return (0, 0)

    def __repr__(self):
        return f"<{type(self).__name__} {self.elapsed:.1f}/{self.duration:.1f}s>"
