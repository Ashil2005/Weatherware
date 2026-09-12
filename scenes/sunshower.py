"""Sunshower: the rare one (10%). Sun and rain at once -- the brightness
oscillates between the safe floor and ceiling while rain falls through a
golden haze.

This scene composes the other two rather than duplicating their artwork. It
turns their tints down and mutes their individual hardware requests, so the
two washes blend instead of stacking into mud and only one thing is driving
the display.

IT ALSO CARRIES THE EASTER EGG. In Kerala a sunshower is "the fox's
wedding", so this scene owns a WeddingCeremony (wedding.py) and plays it
out over the weather: fifty-three seconds of bells, a march, a ring, a kiss
and four rows of cheering crowd, drawn after the sun and the rain so it
reads clearly in front of them.

It is a SURPRISE, so it does not open with the event. The shower runs plain
for WEDDING_START_DELAY seconds and only then do the bells start -- the
ceremony is built and loaded in start(), because its assets decide how long
the event has to be, but it is held shut until update() reaches the delay.

The egg is visuals and audio only -- see the note at the top of wedding.py
-- but it does change three things about this scene while it runs:

  * the EVENT is stretched to outlast the delay AND the ceremony AND a tail
    (see _prepare_wedding), so even a 30-second --demo event plays the whole
    thing rather than cutting the bride off before the ring. The children
    are stretched with it: sun and rain run their own intensity envelope off
    their own duration, and leaving those at the un-stretched length is
    exactly what used to make the weather blank out part-way through the
    ceremony and leave the cast standing on a bare desktop;
  * the WEATHER is turned down but never off -- both tints drop so the cast
    reads in front of them, and the shower keeps falling at full envelope
    strength right through the ceremony and the tail after it;
  * the BRIGHTNESS SWEEP is narrowed and re-centred toward the bright end,
    because a full floor-to-ceiling sweep pulses the cast dark twice every
    eight seconds. It is still the same sweep between the same safe bounds,
    just a gentler one.

All three are requests this scene makes on the ceremony's behalf. Nothing in
wedding.py can reach the display, the power mode, the fan or the warm-up.

REAL effects: brightness sweeps, and Power Mode sits at Balanced -- neutral,
because this one is neither the quiet scene nor the loud one. No warm-up.
"""

import math

import config
from scenes.base import Scene
from scenes.rainy import RainyScene
from scenes.sunny import SunnyScene


class SunshowerScene(Scene):
    name = "sunshower"
    label = "Sunshower"
    color = config.COLOR_SUNSHOWER

    power_intent = "balanced"
    lenovo_intent = "balanced"
    wants_warmup = False

    OSCILLATION_HZ = 0.25   # full bright/dim cycles per second

    def __init__(self):
        super().__init__()
        self.sun = SunnyScene()
        self.rain = RainyScene()
        self.wedding = None      # built in start(), and only if it has assets
        self.wedding_due = None  # seconds until the ceremony opens, or None

        # The composite drives the hardware itself, so mute the children.
        for child in (self.sun, self.rain):
            child.brightness_target = None
            child.power_intent = None
            child.lenovo_intent = None
            child.wants_warmup = False

        # Blend the two looks instead of stacking them at full strength.
        self.sun.tint_strength = 0.55
        self.rain.tint_strength = 0.45
        self.rain.vignette_strength = 0.6
        self.sun.cloud_strength = 0.5    # fair clouds under a storm mass
        # A thunderclap under a sunny sky reads as a bug, not a joke, and
        # with no strike the desktop refresh never fires either.
        self.rain.lightning_enabled = False
        self.rain.desktop_refresh = False
        # Two full scenes already draw every frame here. The mirage and the
        # condensation are the two most expensive things either of them
        # owns, and neither belongs in a bright golden shower anyway: a
        # fogged-over sunshower is just a grey screen.
        self.sun.mirage_enabled = False
        self.rain.fog_enabled = False

    def start(self, engine):
        super().start(engine)
        self.sun.start(engine)
        self.rain.start(engine)

        # The wedding first, because whether it loaded decides how long this
        # event runs and how the weather underneath it is dressed. Loading
        # is not starting: the ceremony is held shut until wedding_due.
        self._prepare_wedding(engine)

        # Thin both cloud decks by half. This is the only scene that draws
        # two complete scenes per frame, and a cloud is one full-sprite alpha
        # blit -- the most expensive thing either child owns. Halving them
        # also stops the two decks stacking into an overcast, which is the
        # same reason the tints above are turned down.
        stride = 2 * (config.WEDDING_CLOUD_THIN if self.wedding else 1)
        self.sun.clouds = self.sun.clouds[::stride]
        self.rain.clouds = self.rain.clouds[::stride]
        engine.log("sunshower: rare event -- sun and rain together")

    def _prepare_wedding(self, engine):
        """Build and load the ceremony -- or quietly do none of that.

        This does NOT open it. The whole point of WEDDING_START_DELAY is
        that the event begins as an ordinary sunshower, so all that happens
        here is that the assets are read -- which is what decides whether
        there is a ceremony at all -- and the event is made long enough to
        hold one.

        A missing sprite folder, a disabled flag or anything at all going
        wrong in here leaves self.wedding as None, and this scene is then
        the sunshower it has always been. That fallback is the whole reason
        the easter egg is allowed to live inside the heaviest scene in the
        app.
        """
        if not config.WEDDING_ENABLED:
            return
        try:
            import wedding
            ceremony = wedding.WeddingCeremony(log=engine.log)
            if not ceremony.load():
                return
        except Exception as exc:
            engine.log(f"sunshower: wedding unavailable ({exc}) -- "
                       "running the plain shower")
            return

        self.wedding = ceremony
        self.wedding_due = config.WEDDING_START_DELAY

        # Long enough for the quiet opening, the whole ceremony, and a tail
        # of plain shower afterwards -- or the scene's normal duration if
        # that is already longer. max(), never a plain assignment: this may
        # lengthen an event and must never shorten one.
        wanted = (config.WEDDING_START_DELAY + config.WEDDING_TOTAL
                  + config.WEDDING_EVENT_TAIL)
        if wanted > self.duration:
            engine.log(f"sunshower: event stretched {self.duration:0.0f}s -> "
                       f"{wanted:0.0f}s ({config.WEDDING_START_DELAY:0.0f}s "
                       f"quiet + {config.WEDDING_TOTAL:0.0f}s ceremony + "
                       f"{config.WEDDING_EVENT_TAIL:0.0f}s tail)")
            self.duration = wanted

        # And stretch the CHILDREN with it. Scene.intensity() is measured
        # against each scene's OWN duration, and the sun and the rain keep
        # theirs: leaving them at the un-stretched length means their
        # envelopes reach zero at the old end time and the weather blanks
        # out mid-ceremony, with the cast left standing on a bare desktop.
        # That is the bug this pair of lines fixes -- they now hold full
        # strength all the way through and fade out with the event.
        self.sun.duration = self.duration
        self.rain.duration = self.duration

        # Let the weather sit further back, so the cast reads in front of
        # it. Turned DOWN, never off: a sunshower with no shower in it is
        # not the joke.
        self.sun.tint_strength = config.WEDDING_SUN_TINT
        self.rain.tint_strength = config.WEDDING_RAIN_TINT

    @property
    def brightness_target(self):
        """Sweep between the configured floor and ceiling.

        While the ceremony runs the same sweep is narrowed and re-centred
        toward the bright end -- a full-band sweep pulses the wedding party
        dark twice every eight seconds. Both numbers are fractions of the
        SAME band, and the result is clamped into it, so this can only ever
        be gentler than the sweep above and can never leave the safe range.
        """
        span = config.BRIGHTNESS_CEILING - config.BRIGHTNESS_FLOOR
        wave = (math.sin(self.elapsed * math.tau * self.OSCILLATION_HZ) + 1) / 2
        if self.wedding is not None and self.wedding.active:
            wave = (config.WEDDING_BRIGHTNESS_CENTRE
                    + (wave - 0.5) * config.WEDDING_BRIGHTNESS_AMPLITUDE)
            wave = max(0.0, min(1.0, wave))
        return config.BRIGHTNESS_FLOOR + span * wave

    def update(self, dt, engine):
        # The children advance their own simulations; their push_intent is a
        # no-op because every intent above was muted.
        self.sun.update(dt, engine)
        self.rain.update(dt, engine)
        self._tick_wedding(dt, engine)
        super().update(dt, engine)

    def _tick_wedding(self, dt, engine):
        """Hold the ceremony shut for its delay, then open it and keep it
        ticking. Counted off the engine's dt like everything else here, so
        there is no second clock to keep in step."""
        if self.wedding is None:
            return
        if self.wedding_due is not None:
            self.wedding_due -= dt
            if self.wedding_due > 0:
                return              # still a perfectly ordinary sunshower
            self.wedding_due = None
            self.wedding.start(engine)
        self.wedding.update(dt, engine)

    def draw(self, surface, renderer):
        # Sun first so the rain falls in front of the glare. Each child uses
        # its own envelope, which matches ours since the durations match.
        self.sun.draw(surface, renderer)
        self.rain.draw(surface, renderer)
        # The wedding goes in front of both, in a pass of its own.
        if self.wedding is not None:
            self.wedding.draw(surface, renderer)

    def stop(self, engine):
        # The ceremony first: it is the thing making noise, and its stop()
        # both silences its cues and hands the rain's level back. Idempotent,
        # so the engine calling this again from shutdown() is harmless.
        if self.wedding is not None:
            self.wedding.stop(engine)
        self.sun.stop(engine)
        self.rain.stop(engine)
