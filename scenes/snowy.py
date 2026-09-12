"""Snowy: a cold, still snowfall. The screen dims to the safe floor, flakes
drift down through three depth layers, the glass fogs over, and snow slowly
piles up along the bottom edge.

Art direction: the opposite of rainy in every way that matters. Rain is fast,
angled and loud; snow is slow, nearly vertical, and quiet. The flakes fall at
a fraction of rain's speed, weave sideways instead of leaning, and are soft
round dots rather than streaks.

Three parts:

  * SNOWFALL on three parallax layers -- far flakes small, slow and faint;
    near flakes big, quicker and haloed. Each one weaves on its own sine, so
    the field never reads as a sheet moving as one.
  * CONDENSATION, the same wipeable fog rainy uses (see CondensationFog in
    renderer.py). Cold breath on the glass; the cursor wipes it, and it
    slowly returns. The overlay is click-through and cannot receive a mouse
    event, so the cursor is READ with GetCursorPos, never captured, and
    clicks still land in the app underneath.
  * ACCUMULATION along the bottom edge, growing over the event and capped by
    SNOW_PILE_MAX so it can never climb the screen.

REAL effects this scene asks for (all applied by effects.py):
  * brightness down to the safe floor -- the coldest, dimmest scene
  * Windows Power Mode -> Best performance
  * Lenovo smart fan -> Performance; cold weather, real cooling

No warm-up, for obvious reasons. Nothing here touches the fan or the
embedded controller directly: the Lenovo call is the same three-way preset as
Fn+Q, and the firmware fan curve is never modified. See the safety rule in
config.py.
"""

import math
import random

import config
from scenes.base import Scene


class SnowyScene(Scene):
    name = "snowy"
    label = "Snowy"
    color = config.COLOR_SNOWY

    brightness_target = config.BRIGHTNESS_FLOOR
    power_intent = "performance"
    lenovo_intent = "performance"   # Fn+Q Performance: cold means cooling
    wants_warmup = False

    # A pale blue-white wash: cold light, not a grey blanket.
    TINT_TOP = (176, 202, 232, 52)
    TINT_BOTTOM = (208, 224, 242, 26)
    VIGNETTE_COLOR = (24, 40, 62)

    def __init__(self):
        super().__init__()
        self.layers = []          # one list of flakes per SNOW_LAYERS spec
        self.fog_enabled = config.SNOW_FOG_ENABLED

    # -- setup -------------------------------------------------------------
    def start(self, engine):
        super().start(engine)
        w, h = self.size
        self.layers = [
            [self._spawn_flake(spec, w, h, seeded=True)
             for _ in range(spec["count"])]
            for spec in config.SNOW_LAYERS
        ]
        engine.log("snowy: dimming to the safe floor, power mode and fan to "
                   "performance")

    @staticmethod
    def _spawn_flake(spec, w, h, seeded=False):
        return {
            "x": random.uniform(-w * 0.05, w * 1.05),
            "y": random.uniform(0, h) if seeded else random.uniform(-h * 0.3, 0),
            "speed": random.uniform(*spec["speed"]),
            "radius": random.uniform(*spec["radius"]),
            # Sway is a horizontal VELOCITY amplitude, so a flake weaves
            # rather than being teleported along a sine each frame.
            "sway": random.uniform(*spec["sway"]),
            "rate": random.uniform(*spec["sway_hz"]) * math.tau,
            "phase": random.uniform(0.0, math.tau),
        }

    # -- simulation --------------------------------------------------------
    @property
    def pile_level(self):
        """0..1 of SNOW_PILE_MAX. Eased so it banks up early and then
        settles, which is how snow actually lies -- and it is keyed to the
        event's own progress, so a 30-second demo fills as convincingly as a
        two-and-a-half-minute event."""
        return self.progress ** max(0.05, config.SNOW_PILE_EASE)

    def update(self, dt, engine):
        super().update(dt, engine)
        w, h = self.size

        for flakes, spec in zip(self.layers, config.SNOW_LAYERS):
            drift = config.SNOW_WIND * spec["parallax"]
            for flake in flakes:
                flake["phase"] += flake["rate"] * dt
                flake["x"] += (drift + math.cos(flake["phase"]) * flake["sway"]) * dt
                flake["y"] += flake["speed"] * dt
                if flake["y"] > h + 10 or not (-w * 0.1 <= flake["x"] <= w * 1.1):
                    flake.update(self._spawn_flake(spec, w, h))

        if self.fog_enabled:
            renderer = getattr(engine, "renderer", None)
            if renderer is not None:
                renderer.fog().update(dt, config.SNOW_FOG_STRENGTH)

    def stop(self, engine):
        """Clear the condensation, so the next event opens on clean glass."""
        renderer = getattr(engine, "renderer", None)
        if renderer is not None:
            renderer.reset_fog()

    # -- artwork -----------------------------------------------------------
    def draw(self, surface, renderer):
        intensity = self.intensity()
        if intensity <= 0:
            return

        # Blits (tint, drift, fog, vignette) go on the composite layer; the
        # pygame.draw primitives (flakes) go on the art scratch, because
        # those overwrite alpha and would punch holes in the tint.
        layer = renderer.new_overlay()
        renderer.draw_tint(layer, self.TINT_TOP, self.TINT_BOTTOM, intensity)

        art = renderer.new_art()
        for flakes, spec in zip(self.layers, config.SNOW_LAYERS):
            renderer.draw_snow_layer(art, flakes, spec, intensity)
        renderer.merge_art(layer, art)

        # In front of the falling snow: the drift is the nearest thing on
        # screen, and flakes should disappear behind it, not into it.
        renderer.draw_snow_drift(layer, self.pile_level, intensity)

        if self.fog_enabled:
            renderer.fog().draw(layer, intensity, config.SNOW_FOG_STRENGTH)

        renderer.draw_vignette(layer, self.VIGNETTE_COLOR, intensity * 0.45)
        renderer.flush_overlay(surface, layer)
