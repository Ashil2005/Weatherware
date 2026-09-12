"""Sunny: a big breathing sun floats over the live desktop, dense god-rays
sweep across it, heat haze rises off the lower screen, and the machine goes
quiet and coasts warm.

Art direction: warm, oppressive, and always moving. Rain had motion and this
did not, so almost everything here now animates on its own clock:

  * a multi-layer bloom (wide amber corona -> warm mid -> white-hot core)
    that breathes on a slow cycle, each layer at its own rate
  * a broad soft hotspot around the sun, fading out with distance
  * dense god-rays that sweep and shimmer, white-hot at the disc and cooling
    to amber at the tips
  * drifting motes on three parallax depths, twinkling and catching the
    light more strongly near the sun
  * an occasional slow lens-flare bloom pulse along the sun's horizontal
    axis (a streak only -- the round ring ghosts read as a rendering
    artefact over a live desktop and were removed)
  * light, puffy fair-weather clouds drifting slowly across the top -- the
    same soft-brush sprites as the storm mass in rainy, cream instead of
    near-black, and much fainter
  * a heat mirage across the bottom of the screen: a faint warm haze that
    ripples continuously, strongest at the very bottom edge

Everything stays low-alpha on purpose -- it sits over the user's real work,
which has to stay readable. The ONE deliberate exception is the sun's own
disc and the hot core of its bloom, which are near-opaque: everything here
is translucent, and a translucent sun vanishes against a white desktop
because there is nothing dark underneath for it to tint. A solid white-hot
centre inside a saturated amber rim reads as a light source on a bright
background and a dark one alike.

WHAT THE MIRAGE IS NOT. It does not bend the desktop. The icons, the text
and the cursor belong to other processes and are composited below this
layered window, so their pixels are not ours to warp. The shimmer refracts
the overlay's OWN band down there instead -- see Renderer.apply_mirage.

REAL effects this scene asks for (all applied by effects.py):
  * brightness up to the safe ceiling
  * Lenovo smart fan -> Quiet (the preset that actually quiets the fan)
  * Windows Power Mode -> Best power efficiency (cross-vendor fallback)
  * the gentle, time-boxed warm-up, so the laptop actually feels warm

Nothing here touches the fan or the embedded controller directly: the Lenovo
call is the same three-way preset as Fn+Q, and the firmware fan curve is
never modified. See the safety rule in config.py.
"""

import math
import random

import config
from scenes.base import Scene


class SunnyScene(Scene):
    name = "sunny"
    label = "Sunny"
    color = config.COLOR_SUNNY

    brightness_target = config.BRIGHTNESS_CEILING
    power_intent = "efficiency"
    lenovo_intent = "quiet"      # Fn+Q Quiet: the fan winds down
    wants_warmup = True

    # Warm amber wash: heavier at the top, thin toward the bottom. Alphas
    # are low because this is a transparent overlay over real work.
    TINT_TOP = (255, 190, 88, 68)
    TINT_BOTTOM = (255, 134, 48, 26)

    def __init__(self):
        super().__init__()
        self.phase = random.uniform(0.0, math.tau)
        self.rays = []
        self.motes = []
        self.clouds = []
        self.sun_pos = (0, 0)
        self.sun_radius = 40

        # Occasional lens-flare bloom, rather than a constant one.
        self.flare_timer = random.uniform(*config.FLARE_GAP)
        self.flare_elapsed = None

        # Sunshower turns these down so the two looks do not stack up.
        self.tint_strength = 1.0
        self.cloud_strength = 1.0
        self.mirage_enabled = True

    # -- setup -------------------------------------------------------------
    def start(self, engine):
        super().start(engine)
        w, h = self.size
        self.sun_pos = (int(w * config.SUN_POS[0]), int(h * config.SUN_POS[1]))
        self.sun_radius = max(12, int(h * config.SUN_RADIUS))

        self.rays = [{
            "angle": random.uniform(0.0, math.tau),
            "spin": random.uniform(0.028, 0.068) * random.choice((-1, 1)),
            "pulse": random.uniform(0.28, 0.72),
            "shimmer": random.uniform(0.9, 1.9),    # second, faster beat
            "offset": random.uniform(0.0, math.tau),
            "length": random.uniform(0.55, 1.0),
            "width": random.uniform(0.008, 0.036),  # half-angle, radians
            # Each ray is nine nested wedges drawn with pygame.draw, which
            # overwrites rather than accumulates -- so this is the alpha of
            # the brightest core, not a value that stacks up.
            "alpha": random.uniform(44, 92),
        } for _ in range(config.SUN_RAY_COUNT)]

        self.motes = [self._spawn_mote(w, h, seeded=True)
                      for _ in range(config.MOTE_COUNT)]

        # Fair-weather clouds: high, slow, and spread across the top. Half
        # drift each way so they cross one another instead of marching.
        #
        # Spaced evenly across the width and then jittered, rather than
        # placed at random: random positions clump, and the gaps between the
        # clumps leave bare sky where there should be cloud.
        span = w * 1.4
        self.clouds = [{
            "x": -w * 0.4 + span * (i / config.FAIR_CLOUD_COUNT)
                 + random.uniform(-w * 0.05, w * 0.05),
            "y": random.uniform(-h * 0.05, h * 0.15),
            "speed": random.uniform(*config.FAIR_CLOUD_SPEED)
                     * random.choice((-1, 1)),
            "sprite": random.randrange(4),
        } for i in range(config.FAIR_CLOUD_COUNT)]

        engine.log("sunny: glare up, fan to quiet, warm-up on")

    @staticmethod
    def _spawn_mote(w, h, seeded=False):
        # Three depth layers: far motes are small, slow and faint.
        depth = random.choice((0.45, 0.72, 1.0))
        return {
            "x": random.uniform(0, w),
            "y": random.uniform(0, h) if seeded else random.uniform(h, h * 1.12),
            "depth": depth,
            "radius": random.uniform(1.0, 2.8) * depth,
            "rise": random.uniform(8.0, 26.0) * depth,
            "drift": random.uniform(-16.0, 16.0) * depth,
            "alpha": random.uniform(60, 150) * depth,
            "twinkle": random.uniform(0.0, math.tau),
            "twinkle_rate": random.uniform(0.8, 2.4),
        }

    # -- simulation --------------------------------------------------------
    def _update_flare(self, dt):
        """A slow bloom that swells and fades, then waits a while."""
        if self.flare_elapsed is None:
            self.flare_timer -= dt
            if self.flare_timer <= 0:
                self.flare_elapsed = 0.0
        else:
            self.flare_elapsed += dt
            if self.flare_elapsed >= config.FLARE_LENGTH:
                self.flare_elapsed = None
                self.flare_timer = random.uniform(*config.FLARE_GAP)

    @property
    def flare_boost(self):
        """0..1 extra flare on top of the always-on baseline."""
        if self.flare_elapsed is None:
            return 0.0
        return math.sin(math.pi * self.flare_elapsed / config.FLARE_LENGTH)

    def update(self, dt, engine):
        super().update(dt, engine)
        self.phase += dt
        self._update_flare(dt)

        w, h = self.size
        for mote in self.motes:
            mote["y"] -= mote["rise"] * dt
            mote["x"] += mote["drift"] * dt
            mote["twinkle"] += mote["twinkle_rate"] * dt
            if mote["y"] < -10 or not (-20 <= mote["x"] <= w + 20):
                mote.update(self._spawn_mote(w, h))

        # Wrap rather than respawn: a cloud is one long sprite, so it has to
        # reappear whole on the far side instead of fading in mid-screen.
        for cloud in self.clouds:
            cloud["x"] += cloud["speed"] * dt
            if cloud["x"] < -w * 0.6:
                cloud["x"] = w
            elif cloud["x"] > w:
                cloud["x"] = -w * 0.6


    # -- artwork -----------------------------------------------------------
    def draw(self, surface, renderer):
        intensity = self.intensity()
        if intensity <= 0:
            return

        # Blits (tint, bloom, hotspot) go on the composite layer; pygame.draw
        # primitives go on the art scratch, because those overwrite alpha and
        # would punch holes through the tint.
        pulse = 0.5 + 0.5 * math.sin(self.phase * math.tau * config.SUN_PULSE_HZ)

        layer = renderer.new_overlay()
        renderer.draw_tint(layer, self.TINT_TOP, self.TINT_BOTTOM,
                           intensity * self.tint_strength)
        renderer.draw_heat_haze(layer, intensity * self.tint_strength)
        # Behind the glare: the sun burns through the clouds, not the other
        # way round.
        renderer.draw_clouds(layer, self.clouds, intensity, kind="fair",
                             strength=config.FAIR_CLOUD_ALPHA
                             * self.cloud_strength)
        renderer.draw_hotspot(layer, self.sun_pos,
                              self.sun_radius * config.SUN_HOTSPOT,
                              intensity * self.tint_strength)
        renderer.draw_sun_bloom(layer, self.sun_pos, self.sun_radius,
                                pulse, intensity)

        # Order matters here: primitives overwrite, so the flare streak has
        # to go down before the disc or it cuts a dull band across the sun.
        art = renderer.new_art()
        renderer.draw_god_rays(art, self.sun_pos, self.rays, self.phase,
                               intensity, (255, 244, 214))
        renderer.draw_lens_flare(art, self.sun_pos,
                                 intensity * (0.55 + 0.85 * self.flare_boost),
                                 (255, 214, 150))
        renderer.draw_sun_disc(art, self.sun_pos,
                               self.sun_radius * (1.0 + 0.09 * pulse),
                               (255, 232, 168), 255 * intensity)
        renderer.draw_motes(art, self.motes, intensity, (255, 236, 190),
                            sun_pos=self.sun_pos)
        renderer.merge_art(layer, art)

        # Last, so the shimmer refracts everything painted in that band --
        # the haze, the tint, the motes -- and not just the haze alone.
        if self.mirage_enabled:
            renderer.apply_mirage(layer, self.phase, intensity)

        renderer.flush_overlay(surface, layer)
