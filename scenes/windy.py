"""Windy: a blustery overcast tears across the desktop in one direction,
carrying autumn leaves with it, while the fan spins up to be the sound of it.

Art direction: fast, one-way, and gusty. Everything obeys a single wind
direction picked when the event starts -- clouds streaming across the top an
order of magnitude faster than rainy's drift, tumbling leaves crossing the
whole screen, and faint streaks of moving air behind them. Mixed directions
read as a bug rather than as weather, so the direction lives on the scene and
every moving thing multiplies by it.

Three things carry the effect:

  * LEAVES. Wind itself is invisible; what you actually see is what it
    carries. These are the signature of the scene and everything else is
    supporting texture. They tumble in two axes at once -- see
    Renderer.draw_leaves for why that matters.
  * GUSTS. A steady speed reads as a conveyor belt. The same swelling gust
    envelope rainy uses drives every speed here, so the whole screen surges
    and eases together.
  * STREAKS. Thin smears of air with a speck of debris at the head, fast and
    faint, for the space between leaves.

REAL effects this scene asks for (all applied by effects.py):
  * Windows Power Mode -> Best performance
  * Lenovo smart fan -> Performance; the fan IS the wind
  * brightness left roughly neutral -- wind is not a story about light

No warm-up. Nothing here touches the fan or the embedded controller
directly: the Lenovo call is the same three-way preset as Fn+Q. See the
safety rule in config.py.
"""

import math
import random

import config
from scenes.base import Scene


class WindyScene(Scene):
    name = "windy"
    label = "Windy"
    color = config.COLOR_WINDY

    brightness_target = config.WINDY_BRIGHTNESS
    power_intent = "performance"
    lenovo_intent = "performance"   # Fn+Q Performance: the fan spins up
    wants_warmup = False

    # Cold overcast wash: grey-blue and light, as always -- real work is
    # underneath it.
    TINT_TOP = (150, 172, 196, 56)
    TINT_BOTTOM = (104, 128, 156, 24)
    VIGNETTE_COLOR = (12, 18, 28)
    BAND_COLOR = (96, 116, 142)

    def __init__(self):
        super().__init__()
        self.direction = 1        # +1 blows right, -1 blows left
        self.clouds = []
        self.leaves = []
        self.streaks = []

        self.gust_timer = random.uniform(*config.WIND_GUST_GAP)
        self.gust_elapsed = None
        self.gust = 0.0           # 0..1, the swell of the current gust

    # -- setup -------------------------------------------------------------
    def start(self, engine):
        super().start(engine)
        w, h = self.size
        self.direction = random.choice((-1, 1))

        # Spread evenly across the width and then jittered, rather than
        # placed at random: pure random leaves gaps, and a gap in an
        # overcast reads as a hole in the sky.
        span = w * 1.5
        self.clouds = [{
            "x": -w * 0.25 + span * (i / config.WIND_CLOUD_COUNT)
                 + random.uniform(-w * 0.05, w * 0.05),
            "y": random.uniform(-h * 0.10, h * 0.20),
            "speed": random.uniform(*config.WIND_CLOUD_SPEED),
            "sprite": random.randrange(4),
        } for i in range(config.WIND_CLOUD_COUNT)]

        self.leaves = [self._spawn_leaf(w, h, seeded=True)
                       for _ in range(config.LEAF_COUNT)]
        self.streaks = [self._spawn_streak(w, h, seeded=True)
                        for _ in range(config.WIND_STREAK_COUNT)]

        engine.log(f"windy: blowing {'right' if self.direction > 0 else 'left'}, "
                   f"power mode and fan to performance")

    @staticmethod
    def _leaf_colors():
        color = random.choice(config.LEAF_COLORS)
        # The midrib is the same colour, darkened -- a second hue would read
        # as a stripe painted on rather than as part of the leaf.
        return color, tuple(int(c * 0.62) for c in color)

    def _spawn_leaf(self, w, h, seeded=False):
        """A leaf entering from upwind, or anywhere on screen at start."""
        color, vein = self._leaf_colors()
        return {
            "x": (random.uniform(0, w) if seeded
                  else self._upwind_edge(w, random.uniform(60, w * 0.4))),
            "y": random.uniform(-h * 0.05, h * 1.02),
            "size": random.uniform(*config.LEAF_SIZE),
            "speed": random.uniform(*config.LEAF_SPEED),
            "rise": random.uniform(*config.LEAF_RISE) * random.choice((-1, 1)),
            "angle": random.uniform(0.0, math.tau),
            "spin": random.uniform(*config.LEAF_SPIN) * random.choice((-1, 1)),
            "tumble": random.uniform(0.0, math.tau),
            "tumble_rate": random.uniform(*config.LEAF_TUMBLE),
            "bob": random.uniform(0.0, math.tau),
            "bob_rate": random.uniform(0.5, 1.9),
            "alpha": random.uniform(*config.LEAF_ALPHA),
            "color": color,
            "vein": vein,
        }

    def _spawn_streak(self, w, h, seeded=False):
        return {
            "x": (random.uniform(0, w) if seeded
                  else self._upwind_edge(w, random.uniform(20, w * 0.3))),
            "y": random.uniform(0, h),
            "speed": random.uniform(*config.WIND_STREAK_SPEED),
            "length": random.uniform(*config.WIND_STREAK_LENGTH),
            "alpha": random.uniform(config.WIND_STREAK_ALPHA * 0.4,
                                    config.WIND_STREAK_ALPHA),
            "speck": random.random() < 0.35,
            "dir": self.direction,
        }

    def _upwind_edge(self, w, back):
        """An x just off the side the wind is coming from."""
        return -back if self.direction > 0 else w + back

    def _blown_off(self, x, w, margin):
        """True once something has left downwind."""
        return x > w + margin if self.direction > 0 else x < -margin

    # -- simulation --------------------------------------------------------
    def _update_gust(self, dt, engine=None):
        """A steady wind with frequent swelling gusts -- the same envelope
        rainy uses, run faster, because gusts are the whole point here.

        The gust sound fires on the RISING EDGE only -- the frame the
        envelope leaves None -- not on every frame the wind happens to be
        high. That is what makes the number of gust sounds exactly the
        number of times the screen actually surged.
        """
        if self.gust_elapsed is None:
            self.gust_timer -= dt
            if self.gust_timer <= 0:
                self.gust_elapsed = 0.0
                audio = getattr(engine, "audio", None)
                if audio is not None:
                    audio.play_oneshot("gust")
        else:
            self.gust_elapsed += dt
            if self.gust_elapsed >= config.WIND_GUST_LENGTH:
                self.gust_elapsed = None
                self.gust_timer = random.uniform(*config.WIND_GUST_GAP)

        self.gust = (0.0 if self.gust_elapsed is None else
                     math.sin(math.pi * self.gust_elapsed / config.WIND_GUST_LENGTH))

    @property
    def speed_scale(self):
        """One multiplier for every moving thing, so the screen surges as a
        single body of air rather than as unrelated layers."""
        return 1.0 + config.WIND_GUST_BOOST * self.gust

    def update(self, dt, engine):
        super().update(dt, engine)
        w, h = self.size
        self._update_gust(dt, engine)
        push = self.direction * self.speed_scale * dt

        for cloud in self.clouds:
            cloud["x"] += cloud["speed"] * push
            if self._blown_off(cloud["x"], w, w * 0.75):
                cloud["x"] = self._upwind_edge(w, w * 0.75)

        for leaf in self.leaves:
            leaf["x"] += leaf["speed"] * push
            leaf["bob"] += leaf["bob_rate"] * dt
            leaf["y"] += math.sin(leaf["bob"]) * leaf["rise"] * dt
            leaf["angle"] += leaf["spin"] * dt
            leaf["tumble"] += leaf["tumble_rate"] * dt
            if self._blown_off(leaf["x"], w, 80) or not (-h * 0.1 <= leaf["y"] <= h * 1.1):
                leaf.update(self._spawn_leaf(w, h))

        for streak in self.streaks:
            streak["x"] += streak["speed"] * push
            if self._blown_off(streak["x"], w, streak["length"] + 20):
                streak.update(self._spawn_streak(w, h))

    # -- artwork -----------------------------------------------------------
    def draw(self, surface, renderer):
        intensity = self.intensity()
        if intensity <= 0:
            return

        # Blits (tint, clouds, vignette) go on the composite layer; the
        # pygame.draw primitives (streaks, leaves) go on the art scratch,
        # because those overwrite alpha and would punch holes in the tint.
        layer = renderer.new_overlay()
        renderer.draw_tint(layer, self.TINT_TOP, self.TINT_BOTTOM, intensity)
        # The band first, then the sprites over it: on its own a handful of
        # fast sprites reads as scraps, not as an overcast.
        renderer.draw_top_band(layer, self.BAND_COLOR, config.WIND_BAND_ALPHA,
                               config.WIND_BAND_HEIGHT, intensity)
        renderer.draw_clouds(layer, self.clouds, intensity, kind="wind",
                             strength=config.WIND_CLOUD_ALPHA)

        art = renderer.new_art()
        # Streaks first: leaves are the foreground and must land on top of
        # the air, not under it.
        renderer.draw_wind_streaks(art, self.streaks, intensity)
        renderer.draw_leaves(art, self.leaves, intensity)
        renderer.merge_art(layer, art)

        renderer.draw_vignette(layer, self.VIGNETTE_COLOR, intensity * 0.28)
        renderer.flush_overlay(surface, layer)
