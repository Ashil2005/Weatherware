"""Rainy: a cold storm floats over the live desktop. Layered rain falls at
an angle, clouds drag across the top, lightning occasionally cracks -- and
the fan spins up for real, which is what the storm is actually made of.

Art direction: moody and cold. A light blue-grey tint plus a soft vignette,
a heavy storm mass across the top, three parallax rain layers (near drops
big, fast and blurred; far drops small, slow and faint), gusting wind, water
running down the foreground "glass", condensation the user has to wipe off,
and infrequent two-pulse lightning with a brief screen-shake. The tint stays
subtle -- it sits over the user's real work.

TWO DISRUPTORS, both deliberate:

  * Lightning also asks the Windows shell to redraw the DESKTOP, so the
    icons flicker in time with the flash. Tightly scoped -- one posted
    WM_COMMAND to the desktop's own SHELLDLL_DefView, never a synthesised
    keystroke and never a broadcast, so nothing the user is typing into is
    ever touched. See desktop.py.

  * Condensation fogs the whole screen and has to be WIPED with the mouse,
    and slowly returns. The overlay is click-through and cannot receive a
    mouse event, so the cursor is read with GetCursorPos rather than
    captured: clicks still land in the app underneath exactly as before.
    See CondensationFog in renderer.py.

REAL effects this scene asks for (all applied by effects.py):
  * brightness down to the safe floor
  * Windows Power Mode -> Best performance (Balanced on battery), so the fan
    audibly spins up

That is the OS power slider, not the fan itself: the firmware fan curve is
never modified and thermal protection stays intact. No warm-up here -- rain
is supposed to be the cool one. See the safety rule in config.py.
"""

import math
import random

import config
import desktop
from scenes.base import Scene


class RainyScene(Scene):
    name = "rainy"
    label = "Rainy"
    color = config.COLOR_RAINY

    brightness_target = config.BRIGHTNESS_FLOOR
    power_intent = "performance"
    lenovo_intent = "performance"   # Fn+Q Performance: the fan spins up
    wants_warmup = False

    TINT_TOP = (78, 104, 142, 54)
    TINT_BOTTOM = (22, 32, 50, 24)
    VIGNETTE_COLOR = (6, 10, 20)

    def __init__(self):
        super().__init__()
        self.layers = []          # one list of drops per RAIN_LAYERS spec
        self.clouds = []
        self.streaks = []

        self.wind = config.WIND_BASE
        self.gust_timer = random.uniform(*config.GUST_GAP)
        self.gust_elapsed = None

        self.flash_timer = random.uniform(*config.LIGHTNING_GAP)
        self.flash_elapsed = None
        self.lightning_enabled = config.LIGHTNING_ENABLED

        # Sunshower turns these down (or off) so the two looks do not stack.
        self.tint_strength = 1.0
        self.vignette_strength = 1.0
        self.fog_enabled = True
        self.desktop_refresh = True

    # -- setup -------------------------------------------------------------
    def start(self, engine):
        super().start(engine)
        w, h = self.size

        self.layers = [
            [self._spawn_drop(spec, w, h, seeded=True) for _ in range(spec["count"])]
            for spec in config.RAIN_LAYERS
        ]

        # Spaced evenly across the width and then jittered -- random
        # positions clump, and a clump leaves clear sky beside it, which is
        # the one thing a storm mass must not have. The vertical spread is
        # deeper than the sprite height alone gives, so the sprites read as
        # one ragged mass rather than as a single row.
        span = w * 1.35
        self.clouds = [{
            "x": -w * 0.35 + span * (i / config.CLOUD_COUNT)
                 + random.uniform(-w * 0.04, w * 0.04),
            "y": random.uniform(-h * 0.08, h * 0.16),
            "speed": random.uniform(*config.CLOUD_SPEED) * random.choice((-1, 1)),
            "sprite": random.randrange(4),
        } for i in range(config.CLOUD_COUNT)]

        self.streaks = [self._spawn_streak(w, h, seeded=True)
                        for _ in range(config.GLASS_STREAKS)]

        engine.log("rainy: dimming to the safe floor, power mode to performance")

    @staticmethod
    def _spawn_drop(spec, w, h, seeded=False):
        return {
            "x": random.uniform(-w * 0.15, w * 1.15),
            "y": random.uniform(0, h) if seeded else random.uniform(-h * 0.35, 0),
            "speed": random.uniform(*spec["speed"]),
            "length": random.uniform(*spec["length"]),
        }

    @staticmethod
    def _spawn_streak(w, h, seeded=False):
        return {
            "x": random.uniform(0, w),
            "y": random.uniform(0, h) if seeded else random.uniform(-h * 0.2, 0),
            "length": random.uniform(30, 130),
            "speed": random.uniform(30, 120),
            "alpha": random.uniform(40, 105),
            "phase": random.uniform(0.0, math.tau),
        }

    # -- simulation --------------------------------------------------------
    def _update_wind(self, dt):
        """A steady base wind with occasional swelling gusts."""
        if self.gust_elapsed is None:
            self.gust_timer -= dt
            if self.gust_timer <= 0:
                self.gust_elapsed = 0.0
        else:
            self.gust_elapsed += dt
            if self.gust_elapsed >= config.GUST_LENGTH:
                self.gust_elapsed = None
                self.gust_timer = random.uniform(*config.GUST_GAP)

        gust = 0.0
        if self.gust_elapsed is not None:
            gust = math.sin(math.pi * self.gust_elapsed / config.GUST_LENGTH)
        self.wind = config.WIND_BASE + config.WIND_GUST * gust

    def _update_lightning(self, dt, engine=None):
        if not self.lightning_enabled:
            return
        if self.flash_elapsed is None:
            self.flash_timer -= dt
            if self.flash_timer <= 0:
                self.flash_elapsed = 0.0
                self._strike(engine)
        else:
            self.flash_elapsed += dt
            if self.flash_elapsed >= max(config.LIGHTNING_LENGTH, config.SHAKE_LENGTH):
                self.flash_elapsed = None
                self.flash_timer = random.uniform(*config.LIGHTNING_GAP)

    def _strike(self, engine):
        """Fired once at the front of a flash, never once per pulse.

        The thunder rides on this same edge rather than on a timer of its
        own, so the clap count is exactly the strike count and the two can
        never drift apart. It goes ABOVE the desktop-refresh guard below:
        sunshower turns the refresh off but would still get its thunder from
        here if it ever struck, and a scene that skips the icon flicker
        should not also lose its sound.

        The refresh is posted to the desktop's own shell view and nowhere
        else, and desktop.py rate-limits it on top of this, so a strike
        landing moments after the last one simply does not get a second
        refresh. Anything that fails in there is swallowed: a missing
        desktop window must never take the storm down with it.
        """
        audio = getattr(engine, "audio", None)
        if audio is not None:
            # Delayed by THUNDER_DELAY inside the audio layer -- light first.
            audio.play_oneshot("thunder")

        if not (self.desktop_refresh and config.DESKTOP_REFRESH_ON_LIGHTNING):
            return
        log = engine.log if engine is not None else None
        if desktop.refresh_desktop(log=log) and engine is not None:
            engine.log("lightning: desktop icons refreshed")

    def update(self, dt, engine):
        super().update(dt, engine)
        w, h = self.size
        self._update_wind(dt)
        self._update_lightning(dt, engine)
        self._update_fog(dt, engine)

        for drops, spec in zip(self.layers, config.RAIN_LAYERS):
            drift = self.wind * spec["parallax"] * dt
            for drop in drops:
                drop["y"] += drop["speed"] * dt
                drop["x"] += drift
                if drop["y"] > h or not (-w * 0.2 <= drop["x"] <= w * 1.2):
                    drop.update(self._spawn_drop(spec, w, h))

        for cloud in self.clouds:
            cloud["x"] += cloud["speed"] * dt
            if cloud["x"] < -w * 0.6:
                cloud["x"] = w
            elif cloud["x"] > w:
                cloud["x"] = -w * 0.6

        for streak in self.streaks:
            streak["y"] += streak["speed"] * dt
            if streak["y"] - streak["length"] > h:
                streak.update(self._spawn_streak(w, h))

    # -- condensation ------------------------------------------------------
    @property
    def fog_active(self):
        return config.FOG_ENABLED and self.fog_enabled

    def _update_fog(self, dt, engine):
        """Fog up a little, then wipe wherever the cursor went this frame.

        The renderer owns the mask -- it is the thing that knows the display
        geometry -- so this reaches it through the engine rather than
        keeping a second copy of that geometry here.
        """
        if not self.fog_active:
            return
        renderer = getattr(engine, "renderer", None)
        if renderer is not None:
            renderer.fog().update(dt)

    def stop(self, engine):
        """Clear the condensation, so the next storm opens on clean glass."""
        renderer = getattr(engine, "renderer", None)
        if renderer is not None:
            renderer.reset_fog()

    # -- lightning envelope ------------------------------------------------
    @property
    def flash_alpha(self):
        """Two soft pulses then a decay. Deliberately not a strobe."""
        t = self.flash_elapsed
        if t is None:
            return 0.0
        peak = config.LIGHTNING_ALPHA
        if t < 0.055:
            return peak
        if t < 0.115:
            return peak * 0.22
        if t < 0.195:
            return peak * 0.72
        if t < config.LIGHTNING_LENGTH:
            fade = 1.0 - (t - 0.195) / max(1e-6, config.LIGHTNING_LENGTH - 0.195)
            return peak * 0.55 * max(0.0, fade)
        return 0.0

    @property
    def shake_offset(self):
        t = self.flash_elapsed
        if t is None or t >= config.SHAKE_LENGTH:
            return (0, 0)
        decay = 1.0 - t / config.SHAKE_LENGTH
        amp = config.SHAKE_AMPLITUDE * decay * self.intensity()
        return (int(math.sin(t * 58.0) * amp), int(math.cos(t * 47.0) * amp * 0.6))

    # -- artwork -----------------------------------------------------------
    def draw(self, surface, renderer):
        intensity = self.intensity()
        if intensity <= 0:
            return

        # Blits (tint, clouds, vignette) go on the composite layer;
        # pygame.draw primitives (rain, streaks) go on the art scratch,
        # because those overwrite alpha and would punch holes in the tint.
        layer = renderer.new_overlay()
        renderer.draw_tint(layer, self.TINT_TOP, self.TINT_BOTTOM,
                           intensity * self.tint_strength)
        # The band first, then the sprites on top of it: the band is what
        # ties the separate blobs into one continuous storm mass.
        renderer.draw_storm_band(layer, intensity * self.tint_strength)
        renderer.draw_clouds(layer, self.clouds, intensity)

        art = renderer.new_art()
        for drops, spec in zip(self.layers, config.RAIN_LAYERS):
            renderer.draw_rain_layer(art, drops, spec, self.wind, intensity)
        renderer.draw_glass_streaks(art, self.streaks, intensity)
        renderer.merge_art(layer, art)

        # Over the rain, under the vignette: condensation sits on the glass
        # in front of the storm, not behind it.
        if self.fog_active:
            renderer.fog().draw(layer, intensity)

        renderer.draw_vignette(layer, self.VIGNETTE_COLOR,
                               intensity * 0.6 * self.vignette_strength)
        renderer.flush_overlay(surface, layer)

        # Drawn last, but before the escape hint -- the way out stays legible.
        renderer.draw_lightning(surface, self.flash_alpha * intensity)
