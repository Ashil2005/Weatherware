"""
Rendering helpers. Scenes describe what they want; this module draws it.

Nothing here touches hardware -- it is pixels only.

There is almost no text on screen by design: no cards, no gauges, no HUD.
The only chrome is a small low-opacity reminder of the global stop hotkey,
which matters because the overlay is click-through and can never be focused
or closed by hand.

Five things worth knowing before editing:

1. Everything draws into `self.canvas`, not straight to the display. The
   canvas is blitted to the screen in present(), which is what makes the
   lightning screen-shake a one-line offset instead of a rewrite.

2. In overlay mode the canvas carries TRUE per-pixel alpha and is presented
   by overlay.py via UpdateLayeredWindow, so it is cleared to fully
   transparent each frame and only painted where there is weather. It also
   lives at ARTWORK resolution (half, at 1080p): the single upscale happens
   once at present time, which is what keeps the premultiply cheap. In
   --windowed dev mode the canvas is instead a normal opaque full-size
   surface blitted to the display.

3. pygame.draw.* OVERWRITES RGBA on a per-pixel-alpha surface -- it does not
   blend. Only blit() blends. So primitives must never be drawn straight
   onto a layer that already holds a tint: they punch transparent holes in
   it. Hence two scratch surfaces: `new_overlay()` is the composite layer,
   built with blits; `new_art()` is where primitives are drawn, then
   merge_art() blends the whole thing in one go.

4. Never call set_alpha(None) on a per-pixel-alpha surface. SDL2 reads that
   as "no blending" and the blit becomes a raw copy. Use set_alpha(255).

5. Translucent artwork batches into ONE scratch overlay rather than one
   full-screen blit per element, and at 1080p that overlay renders at HALF
   resolution and is scaled up on flush. Gradients, vignettes, the corona
   and the cloud sprites are built once small and cached.
"""

import math
import random

import pygame

import config
import desktop


def clamp01(value):
    return max(0.0, min(1.0, value))


class Renderer:
    #: Above this many pixels, artwork renders at half resolution.
    HALF_RES_ABOVE = 1_400_000

    def __init__(self, surface, overlay=True):
        self.surface = surface
        self.overlay = overlay        # per-pixel-alpha overlay vs opaque window
        self.width, self.height = surface.get_size()

        # The shared artwork layer, possibly at half resolution. Screen
        # coordinates are converted with self.k inside the draw helpers, so
        # scenes never need to know about this.
        self.art_div = 2 if self.width * self.height > self.HALF_RES_ABOVE else 1
        self.k = 1.0 / self.art_div
        self.art_w = self.width // self.art_div
        self.art_h = self.height // self.art_div
        self._layer = pygame.Surface((self.art_w, self.art_h), pygame.SRCALPHA)
        # Separate scratch for pygame.draw primitives -- see note 3 above.
        self._art = pygame.Surface((self.art_w, self.art_h), pygame.SRCALPHA)

        if self.overlay:
            # Per-pixel alpha at artwork resolution; overlay.py upscales it
            # once while pushing it to the layered window.
            self.canvas = pygame.Surface((self.art_w, self.art_h), pygame.SRCALPHA)
            self._layer_full = None
        else:
            self.canvas = pygame.Surface((self.width, self.height)).convert()
            self._layer_full = (
                pygame.Surface((self.width, self.height), pygame.SRCALPHA)
                if self.art_div > 1 else None)
        self.canvas_size = self.canvas.get_size()

        self.scale = max(0.78, self.height / 720.0)
        self.font_hint = pygame.font.SysFont("Segoe UI", self._pt(15))

        # Lazily built, then reused forever.
        self._gradients = {}
        self._vignettes = {}
        self._glow_bases = {}
        self._glow_scaled = {}
        self._flats = {}
        self._bands = {}
        self._clouds = {}
        self._mirage_band = None
        self._drift = None
        self._fog = None
        self._hint = None
        # (sprite key, w, h) -> scaled copy. See draw_sprite: rescaling a
        # 400px PNG every frame is the one thing that would make the wedding
        # easter egg cost real time.
        self._sprites = {}
        # This frame's full-resolution foreground -- the wedding cast -- as
        # (premultiplied surface, screen pos, alpha). See new_foreground.
        self._foreground = []
        # size -> reusable scratch for drawing a fading sprite. See
        # draw_foreground.
        self._faded = {}

    def _pt(self, size):
        return max(11, int(size * self.scale))

    def px(self, value):
        """Scale a screen-space pixel measurement for this display."""
        return int(value * self.scale)

    def apx(self, value):
        """Same, but for artwork drawn into the (possibly half-res) layer."""
        return max(1, int(value * self.scale * self.k))

    def _ap(self, point):
        """Screen coordinates -> artwork-layer coordinates."""
        return (point[0] * self.k, point[1] * self.k)

    # ======================================================================
    # Frame pipeline
    # ======================================================================
    def begin_frame(self):
        """Clear to fully transparent, so anything we do not paint this frame
        shows the live desktop straight through."""
        self._foreground.clear()
        if self.overlay:
            self.canvas.fill((0, 0, 0, 0))
        else:
            self.canvas.fill(config.COLOR_BG)

    def present_windowed(self, shake=(0, 0)):
        """Dev-window path only. The overlay is presented by overlay.py."""
        dx, dy = int(shake[0]), int(shake[1])
        if dx or dy:
            self.surface.fill(config.COLOR_BG)
        self.surface.blit(self.canvas, (dx, dy))
        pygame.display.flip()

    # -- batched translucent artwork ---------------------------------------
    def new_overlay(self):
        """Clear and hand back the composite layer. Build this one with
        blits (tints, corona, clouds, vignette) -- they blend correctly."""
        self._layer.set_alpha(255)      # 255, never None -- see note 4
        self._layer.fill((0, 0, 0, 0))
        return self._layer

    def new_art(self):
        """Clear and hand back the primitives scratch. Everything drawn with
        pygame.draw goes here, because those calls overwrite alpha rather
        than blending and would punch holes in a tinted layer."""
        self._art.set_alpha(255)
        self._art.fill((0, 0, 0, 0))
        return self._art

    def merge_art(self, layer, art, area=None):
        """Blend the primitives scratch onto the composite layer.

        `area` limits the blend to one rect of the scratch, and is only
        correct when everything drawn since new_art() lies inside it -- see
        draw_hearts, which returns exactly that rect.
        """
        if area is None:
            layer.blit(art, (0, 0))
        else:
            layer.blit(art, area.topleft, area)

    def flush_overlay(self, surface, layer, alpha=None, areas=None):
        """Composite the artwork layer onto the canvas. In overlay mode the
        canvas is already at artwork resolution, so this is a plain blit and
        the single upscale happens later, in overlay.present().

        alpha=None means "this is weather" and applies config.OVERLAY_ALPHA,
        which is where the overlay's global dimming now lives. It used to be
        the layered window's SourceConstantAlpha, and moving it here is what
        lets a FOREGROUND layer -- the wedding cast -- pass alpha=255 and
        actually arrive on screen opaque instead of at 70%.

        Dimming per flush rather than once at the very end is not quite the
        same arithmetic where two layers stack, so this was measured against
        the old scheme frame by frame. Every scene that flushes ONCE --
        rainy, sunny, windy, snowy -- comes out identical to within a single
        0-255 rounding step. Sunshower flushes twice (it draws two whole
        scenes) and is the one place the two schemes disagree: its layers
        occlude each other slightly less, so the picture is up to 63/255
        more solid exactly where two near-opaque things overlap -- the sun's
        disc behind a storm cloud -- and about 4/255 more solid on average.
        That is a shower that reads a shade stronger through its own cloud,
        which is a fair trade for a wedding party that is not a ghost.

        Dimming the whole canvas once instead would be exact, but a
        full-canvas BLEND_RGBA_MULT measures 4.0ms against the 1.8ms these
        two extra alpha-modded flushes cost, and sunshower is the scene that
        can least afford it.

        `areas`, if given, is a list of rects that between them cover
        EVERYTHING drawn on the layer since new_overlay() cleared it, and
        only those parts are blended. That is exact, not an approximation:
        the rest of the layer is fully transparent, and blending a fully
        transparent pixel leaves the canvas exactly as it was. The rects may
        overlap; they are turned into disjoint bands first, because blending
        one region twice would not be a no-op. Nothing passes it at the
        moment: the wedding cast it was written for is now composited on the
        full-resolution foreground instead (see new_foreground). The
        windowed upscale path ignores it.
        """
        if alpha is None:
            # Dev-window mode never had a layered window to dim it, so it
            # keeps the look it had rather than gaining one.
            alpha = config.OVERLAY_ALPHA if self.overlay else 255
        out = layer
        if surface.get_size() != layer.get_size() and self._layer_full is not None:
            grow = (pygame.transform.smoothscale if config.SMOOTH_ART_UPSCALE
                    else pygame.transform.scale)
            grow(layer, self._layer_full.get_size(), self._layer_full)
            out = self._layer_full
        out.set_alpha(max(0, min(255, int(alpha))))
        if areas is None or out is not layer:
            surface.blit(out, (0, 0))
            return
        for band in self._disjoint_bands(areas):
            surface.blit(out, band.topleft, band)

    @staticmethod
    def _disjoint_bands(rects):
        """Possibly-overlapping rects -> horizontal bands that cover every
        one of them and never overlap each other. A band spans the whole
        x-extent of the rects crossing it, so it can include transparent
        gaps -- harmless, they blend to nothing -- but no pixel is ever in
        two bands."""
        rects = [r for r in rects if r is not None and r.w > 0 and r.h > 0]
        edges = sorted({r.top for r in rects} | {r.bottom for r in rects})
        bands = []
        for top, bottom in zip(edges, edges[1:]):
            crossing = [r for r in rects if r.top < bottom and r.bottom > top]
            if not crossing:
                continue
            left = min(r.left for r in crossing)
            right = max(r.right for r in crossing)
            last = bands[-1] if bands else None
            if (last is not None and last.bottom == top
                    and last.left == left and last.right == right):
                last.height += bottom - top
            else:
                bands.append(pygame.Rect(left, top, right - left, bottom - top))
        return bands

    # ======================================================================
    # Cached full-screen effects
    # ======================================================================
    def _flat(self, color):
        """A plain opaque canvas-sized colour, blended via set_alpha."""
        key = tuple(color)
        cached = self._flats.get(key)
        if cached is None:
            cached = pygame.Surface(self.canvas_size).convert()
            cached.fill(color)
            self._flats[key] = cached
        return cached

    def _gradient(self, top_rgba, bottom_rgba):
        """Vertical gradient, built tiny and scaled to the ARTWORK size."""
        key = (tuple(top_rgba), tuple(bottom_rgba))
        cached = self._gradients.get(key)
        if cached is None:
            steps = 64
            small = pygame.Surface((8, steps), pygame.SRCALPHA)
            for i in range(steps):
                t = i / (steps - 1)
                color = tuple(int(top_rgba[c] + (bottom_rgba[c] - top_rgba[c]) * t)
                              for c in range(4))
                pygame.draw.line(small, color, (0, i), (7, i))
            cached = pygame.transform.smoothscale(small, (self.art_w, self.art_h))
            self._gradients[key] = cached
        return cached

    def draw_tint(self, layer, top_rgba, bottom_rgba, intensity):
        """A light translucent wash, into the composite layer. Kept subtle:
        on the overlay this sits over the user's actual work."""
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        grade = self._gradient(top_rgba, bottom_rgba)
        grade.set_alpha(int(255 * intensity))
        layer.blit(grade, (0, 0))

    def _vignette(self, color):
        """Darkened edges. Built at 96x54 and smoothscaled to the artwork
        size, so it is smooth and costs nothing after the first frame."""
        key = tuple(color)
        cached = self._vignettes.get(key)
        if cached is None:
            sw, sh = 96, 54
            small = pygame.Surface((sw, sh), pygame.SRCALPHA)
            cx, cy = (sw - 1) / 2.0, (sh - 1) / 2.0
            longest = math.hypot(cx, cy)
            for y in range(sh):
                for x in range(sw):
                    d = math.hypot(x - cx, y - cy) / longest
                    a = int(255 * max(0.0, (d - 0.34) / 0.66) ** 1.7)
                    small.set_at((x, y), (*color, min(255, a)))
            cached = pygame.transform.smoothscale(small, (self.art_w, self.art_h))
            self._vignettes[key] = cached
        return cached

    def _band(self, height, top_rgba, bottom_rgba):
        """A full-width gradient strip, built tiny once and cached.

        _gradient() covers the whole canvas top to bottom; this is the same
        trick for a strip of it -- the storm mass at the top, the heat haze
        at the bottom -- so those do not need a three-stop full-screen
        gradient each.
        """
        height = max(2, int(height))
        key = (tuple(top_rgba), tuple(bottom_rgba), height)
        cached = self._bands.get(key)
        if cached is None:
            steps = 48
            small = pygame.Surface((8, steps), pygame.SRCALPHA)
            for i in range(steps):
                t = i / (steps - 1)
                color = tuple(int(top_rgba[c] + (bottom_rgba[c] - top_rgba[c]) * t)
                              for c in range(4))
                pygame.draw.line(small, color, (0, i), (7, i))
            cached = pygame.transform.smoothscale(small, (self.art_w, height))
            self._bands[key] = cached
        return cached

    def draw_vignette(self, layer, color, intensity):
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        vig = self._vignette(tuple(color))
        vig.set_alpha(int(255 * intensity))
        layer.blit(vig, (0, 0))

    def _glow_base(self, color):
        """A soft radial falloff, computed per-pixel once at 128x128."""
        key = tuple(color)
        cached = self._glow_bases.get(key)
        if cached is None:
            n = 128
            surf = pygame.Surface((n, n), pygame.SRCALPHA)
            c = (n - 1) / 2.0
            for y in range(n):
                for x in range(n):
                    d = math.hypot(x - c, y - c) / c
                    a = 0 if d >= 1.0 else int(255 * (1.0 - d) ** 2.4)
                    surf.set_at((x, y), (*color, a))
            self._glow_bases[key] = cached = surf
        return cached

    def draw_radial_glow(self, layer, center, radius, color, alpha, bucket=32):
        """Corona/bloom, into the composite layer.

        Scaled sizes are cached, and `bucket` is the size quantum. That
        quantum is visible motion: a radius that breathes across a coarse
        bucket makes the glow POP between discrete diameters instead of
        swelling. So big layers are drawn at a fixed radius and breathe via
        alpha instead (perfectly continuous, one cache entry), and only the
        small inner layers breathe in size, with a fine bucket they can
        afford. See draw_sun_bloom.
        """
        if radius <= 2 or alpha <= 1:
            return
        cx, cy = self._ap(center)
        bucket = max(2, int(bucket))
        d = max(bucket, (int(radius * 2 * self.k) // bucket) * bucket)
        key = (tuple(color), d)
        scaled = self._glow_scaled.get(key)
        if scaled is None:
            scaled = pygame.transform.smoothscale(
                self._glow_base(tuple(color)), (d, d))
            self._glow_scaled[key] = scaled
        scaled.set_alpha(max(0, min(255, int(alpha))))
        layer.blit(scaled, (int(cx - d / 2), int(cy - d / 2)))

    # ======================================================================
    # Sunny artwork  (drawn into the primitives scratch)
    # ======================================================================
    #: Concentric steps from the amber limb in to the white-hot centre:
    #: (radius fraction, colour, alpha fraction of the disc's own alpha).
    #:
    #: The alpha climbs to effectively opaque at the middle, which is the
    #: whole point. Every other layer of this scene is deliberately
    #: translucent, and a translucent amber disc simply DISAPPEARS against a
    #: white desktop -- there is nothing dark underneath for it to tint. A
    #: solid near-white centre inside a saturated rim reads as a light
    #: source on any background, bright or dark, and the corona around it
    #: stays as faint as it always was.
    DISC_STEPS = (
        (0.88, (255, 228, 164), 0.90),
        (0.72, (255, 240, 202), 0.96),
        (0.56, (255, 249, 230), 0.99),
        (0.40, (255, 253, 246), 1.00),
        (0.24, (255, 255, 255), 1.00),
    )

    #: The crisp rim at the limb. Saturated and dense, because over a white
    #: desktop the white core has no edge of its own -- this is what draws
    #: it. Nothing else on this scene is this saturated, on purpose: with
    #: the layered window compositing at OVERLAY_ALPHA the interior can
    #: never be more than about 70% opaque, so on a white background a pale
    #: cream disc is only a disc BECAUSE of this ring.
    DISC_RIM = (255, 152, 46)

    #: The rim, outside in: (radius multiple, alpha fraction). Four steps a
    #: pixel apart at artwork resolution, which is an antialiased edge in
    #: all but name. A single hard circle stair-steps badly once the
    #: artwork is upscaled, and an actual gfxdraw.aacircle over it came out
    #: as a dashed ring -- its per-pixel coverage blend does not survive a
    #: nearest-neighbour 2x upscale.
    RIM_STEPS = ((1.060, 0.26), (1.042, 0.54), (1.024, 0.80), (1.008, 1.00))

    def draw_sun_disc(self, art, center, radius, color, alpha):
        """A saturated amber rim around a near-opaque white-hot core.

        Drawn on the primitives scratch, so each circle OVERWRITES the one
        before it rather than blending -- the steps are a ramp, not a stack,
        and the ray wedges under the disc are covered rather than tinted.
        Largest first, so every step lands inside the last.
        """
        cx, cy = self._ap(center)
        cx, cy = int(cx), int(cy)
        r = max(2, int(radius * self.k))
        base = max(0, min(255, int(alpha)))

        for mult, fade in self.RIM_STEPS:
            pygame.draw.circle(art, (*self.DISC_RIM, int(base * fade)),
                               (cx, cy), max(2, int(r * mult)))
        pygame.draw.circle(art, (*color, base), (cx, cy), r)
        for frac, shade, fade in self.DISC_STEPS:
            pygame.draw.circle(art, (*shade, int(base * fade)),
                               (cx, cy), max(1, int(r * frac)))

    #: (radius multiple, colour, base alpha, alpha breathe, size breathe, bucket)
    #:
    #: The two big layers breathe in ALPHA at a fixed radius: alpha is
    #: continuous, so they swell smoothly and cost one cached surface each.
    #: Breathing their radius instead made them jump between bucketed
    #: diameters, which is what read as the sun stuttering. The two small
    #: layers do breathe in size, with a fine bucket they can afford because
    #: the surfaces are tiny.
    #:
    #: The three inner layers stack into a genuinely hot core: blits
    #: accumulate, so 168 over 214 over 244 is very close to opaque at the
    #: centre and still fades to the same faint amber corona at the rim.
    #: That is what stops the sun washing out on a white desktop. The extra
    #: innermost layer breathes in alpha only (size breathe 0.00), so it
    #: cannot reintroduce the bucket-popping the comment above describes.
    BLOOM_LAYERS = (
        (1.35, (255, 176, 96), 74, 0.34, 0.00, 32),
        (0.72, (255, 206, 132), 112, 0.26, 0.00, 32),
        (0.34, (255, 236, 188), 168, 0.16, 0.10, 8),
        (0.20, (255, 248, 224), 214, 0.10, 0.00, 4),
        (0.13, (255, 254, 244), 244, 0.07, 0.16, 4),
    )

    def draw_sun_bloom(self, layer, center, radius, pulse, intensity):
        """Multi-layer bloom that breathes: a wide soft amber corona, a warm
        mid glow, and a tight white-hot core."""
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        for mult, color, alpha, a_breathe, s_breathe, bucket in self.BLOOM_LAYERS:
            r = radius * config.SUN_CORONA * mult
            if s_breathe:
                r *= 1.0 - s_breathe + s_breathe * 2.0 * pulse
            a = alpha * (1.0 - a_breathe + a_breathe * 2.0 * pulse) * intensity
            self.draw_radial_glow(layer, center, r, color, int(a), bucket=bucket)

    def draw_hotspot(self, layer, center, radius, intensity):
        """A broad, very soft bright patch around the sun that fades out with
        distance -- the local 'this corner of the screen is baking' feel."""
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        self.draw_radial_glow(layer, center, radius, (255, 214, 150),
                              int(52 * intensity))

    #: (length fraction, alpha fraction, colour blend toward the tip) --
    #: shafts fade AND cool as they travel, white-hot at the disc to amber
    #: at the tip, which is what makes them read as volumetric light.
    #: Polygon budget matters on the live path: every wedge is a Python-level
    #: pygame.draw call, and during sunny the warm-up's GIL-bound threads are
    #: also running, which roughly doubles frame time. Two spans x five nests
    #: keeps the smooth cross-width gradient (which is what kills the visible
    #: stepping) while costing well under half of three-by-six.
    RAY_SPANS = ((1.00, 0.30, 1.00), (0.46, 0.80, 0.30))
    #: (width fraction, alpha fraction) -- nesting a narrow bright wedge
    #: inside a wide faint one gives the shaft a soft edge. A single flat
    #: polygon reads as a hard-edged wedge of paint, not as light.
    #:
    #: The step COUNT is what makes the sweep smooth. Ray width changes very
    #: slowly, so with only three steps a whole step's worth of pixels
    #: appears at once every half-second or so and the ray visibly jerks.
    #: With a finer ramp each edge is a small fraction of the brightness, so
    #: the same sub-pixel drift reads as a continuous swell.
    RAY_NEST = tuple(
        (1.00 - i * (1.00 - 0.18) / 4.0,          # width: 1.00 -> 0.18
         0.22 + (1.0 - 0.22) * (i / 4.0) ** 1.6)  # alpha: 0.22 -> 1.00, eased
        for i in range(5)
    )

    #: Ray tip colour; the core colour is passed in per call.
    RAY_TIP = (255, 168, 84)

    def draw_god_rays(self, art, center, rays, phase, intensity, color):
        """Long tapered shafts sweeping out of the sun.

        Three length segments x three nested widths per ray, with the colour
        interpolated from the hot core colour toward RAY_TIP along the
        length. Costs more polygons than a flat wedge and looks far more
        like light for it.
        """
        intensity = clamp01(intensity)
        if intensity <= 0.01 or not rays:
            return
        cx, cy = self._ap(center)
        hot = tuple(color)
        tip = self.RAY_TIP
        reach = math.hypot(self.art_w, self.art_h) * 1.3
        for ray in rays:
            angle = ray["angle"] + phase * ray["spin"]
            # Two beating frequencies so the sweep never looks metronomic.
            pulse = (0.55 + 0.30 * math.sin(phase * ray["pulse"] + ray["offset"])
                     + 0.15 * math.sin(phase * ray["shimmer"] + ray["offset"] * 2))
            length = reach * ray["length"] * (0.72 + 0.38 * pulse)
            half = ray["width"] * (0.55 + 0.65 * pulse)
            base = ray["alpha"] * intensity * max(0.0, pulse)

            for span, span_fade, mix in self.RAY_SPANS:
                seg = length * span
                shade = tuple(int(hot[i] + (tip[i] - hot[i]) * mix) for i in range(3))
                for widen, width_fade in self.RAY_NEST:
                    a = int(base * span_fade * width_fade)
                    if a <= 1:
                        continue
                    edge = half * widen
                    pygame.draw.polygon(art, (*shade, min(255, a)), [
                        (cx, cy),
                        (cx + math.cos(angle - edge) * seg,
                         cy + math.sin(angle - edge) * seg),
                        (cx + math.cos(angle + edge) * seg,
                         cy + math.sin(angle + edge) * seg),
                    ])

    def draw_lens_flare(self, art, center, intensity, color):
        """A soft horizontal streak of light through the sun.

        There are deliberately no ring "ghosts" along the sun axis: as hard
        round outlines floating over the desktop they read as a rendering
        artefact rather than as lens flare, so they were removed.
        """
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        cx, cy = self._ap(center)
        cx, cy = int(cx), int(cy)
        reach = int(self.art_w * 0.46)
        for width, alpha in ((2, 46), (6, 16), (16, 7)):
            a = int(alpha * intensity)
            if a <= 1:
                continue
            pygame.draw.line(art, (255, 240, 205, a),
                             (cx - reach, cy), (cx + reach, cy), self.apx(width))

    def draw_motes(self, art, motes, intensity, color, sun_pos=None):
        """Dust and pollen drifting through the light.

        Motes near the sun catch more of it, which both sells the light
        source and gives the depth layers something to read against.
        """
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        color = tuple(color)
        sun = self._ap(sun_pos) if sun_pos is not None else None
        near = max(1.0, math.hypot(self.art_w, self.art_h) * 0.42)
        for mote in motes:
            twinkle = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(mote["twinkle"]))
            a = mote["alpha"] * intensity * twinkle
            x, y = self._ap((mote["x"], mote["y"]))
            if sun is not None:
                # Brighten toward the sun, up to about double.
                lit = 1.0 - min(1.0, math.hypot(x - sun[0], y - sun[1]) / near)
                a *= 1.0 + 0.95 * lit * lit
            a = int(a)
            if a <= 2:
                continue
            pos = (int(x), int(y))
            r = max(1, int(mote["radius"] * self.k * self.scale))
            pygame.draw.circle(art, (*color, min(46, a)), pos, r * 3)
            pygame.draw.circle(art, (255, 250, 232, min(215, a)), pos, r)

    # -- heat mirage -------------------------------------------------------
    def _mirage_geometry(self):
        """(band height, top y) in artwork pixels, or None if it is off."""
        if not config.MIRAGE_ENABLED:
            return None
        h = max(4, int(self.art_h * config.MIRAGE_HEIGHT))
        return h, self.art_h - h

    def _haze_texture(self, height):
        """The warm air the mirage bends, as soft VERTICAL ribbons.

        This is the whole trick, and it is worth the paragraph. The
        displacement in apply_mirage is horizontal, so anything without
        horizontal variation is invisible when it moves: shifting a flat
        wash, or a purely vertical gradient, changes not one pixel. A real
        mirage is only visible because the scene behind it has upright
        structure -- poles, kerbs, cars -- for the hot air to bend.

        We cannot bend the desktop's upright structure, so the haze brings
        its own: columns of very slightly different warmth, which shear into
        wavy ribbons the moment each row is offset by a different amount.

        Ribbon WIDTH is the other half of it: a ribbon far wider than the
        displacement barely changes when it moves, so these are tuned to
        land around 90-100 screen px, which a 12px shear visibly bends.

        Built at 144x24 and smoothscaled up, which is what makes the columns
        soft-edged for free, and cached forever after.
        """
        height = max(4, int(height))
        key = ("haze", height)
        cached = self._bands.get(key)
        if cached is None:
            sw, sh = 144, 24
            small = pygame.Surface((sw, sh), pygame.SRCALPHA)
            peak = config.MIRAGE_HAZE_ALPHA
            for x in range(sw):
                # Three beating frequencies: ribbons of uneven width, with
                # no repeat you can pick out along the screen.
                ribbon = (0.50
                          + 0.32 * math.sin(x * 0.90)
                          + 0.13 * math.sin(x * 2.13 + 1.9)
                          + 0.09 * math.sin(x * 3.31 + 0.4))
                for y in range(sh):
                    depth = (y / (sh - 1)) ** 1.5      # nothing at the top
                    a = int(peak * depth * max(0.0, min(1.35, ribbon)))
                    small.set_at((x, y), (255, 192 - int(18 * ribbon),
                                          124, max(0, min(255, a))))
            cached = pygame.transform.smoothscale(small, (self.art_w, height))
            self._bands[key] = cached
        return cached

    def draw_heat_haze(self, layer, intensity):
        """The warm haze over the bottom band, which apply_mirage then
        bends. Faint on its own -- it is the ripple that sells it, not the
        colour."""
        intensity = clamp01(intensity)
        got = self._mirage_geometry()
        if got is None or intensity <= 0.01 or config.MIRAGE_HAZE_ALPHA <= 0:
            return
        h, y0 = got
        haze = self._haze_texture(h)
        haze.set_alpha(int(255 * intensity))
        layer.blit(haze, (0, y0))

    def apply_mirage(self, layer, phase, intensity):
        """Bend the bottom of the composite layer like air over hot tarmac.

        WHAT THIS CANNOT DO, and why it is built this way: the overlay
        cannot warp the real desktop. Those icons, that text and the cursor
        belong to other processes and are composited by the DWM *below* our
        layered window -- we never see their pixels and nothing we draw can
        displace them. So this refracts the only thing we own: our own band
        of weather. The haze, the tint and any artwork down there are
        re-blitted as horizontal slabs with a sine-driven horizontal offset,
        which is exactly the shear a real mirage applies to what is behind
        it, and against the still desktop underneath it reads as heat.

        The band is copied out, cleared, and blitted back slab by slab. A
        shifted slab would otherwise leave a transparent sliver at the
        screen edge -- a hard flickering vertical line -- so the exposed
        columns are refilled from the nearest edge of the source, which is
        an edge clamp and invisible at these amplitudes.

        Amplitude eases in as the CUBE of the depth into the band, so it is
        strongest at the very bottom edge and has died to nothing well
        before mid-screen. Cubic rather than quadratic for two reasons: it
        piles the shimmer into the bottom strip, where hot tarmac actually
        puts it, and it lifts the no-displacement line below, which is
        real frames saved. Two beating frequencies keep the ripple from
        looking like a single clean sine.

        COST. Each slab is a Python-level blit, so the count is the price.
        Only the part of the band where the eased amplitude can still round
        to a whole pixel is touched at all -- above that line the ripple
        would displace by zero anyway, so those rows are left exactly where
        they already are rather than being cleared and copied back.
        """
        intensity = clamp01(intensity)
        got = self._mirage_geometry()
        if got is None or intensity <= 0.02:
            return
        h, y0 = got
        w = self.art_w

        amp = config.MIRAGE_AMPLITUDE * self.scale * self.k * intensity
        if amp < 0.5:
            return
        # Depth at which the eased amplitude first reaches half a pixel;
        # everything shallower rounds to no displacement at all. 1.42 is the
        # largest the two sines can sum to.
        top = min(h - 2, int(h * min(1.0, (0.5 / (amp * 1.42)) ** (1.0 / 3.0))))
        band = h - top
        if band < 2:
            return

        buf = self._mirage_band
        if buf is None or buf.get_size() != (w, h):
            buf = self._mirage_band = pygame.Surface((w, h), pygame.SRCALPHA)
        buf.fill((0, 0, 0, 0), (0, 0, w, band))
        buf.blit(layer, (0, 0), (0, y0 + top, w, band))
        layer.fill((0, 0, 0, 0), (0, y0 + top, w, band))

        step = max(1, self.apx(config.MIRAGE_SLAB))
        wave = math.tau / max(2.0, config.MIRAGE_WAVELENGTH * self.scale * self.k)
        travel = phase * config.MIRAGE_SPEED
        for sy in range(0, band, step):
            rows = min(step, band - sy)
            row = top + sy
            depth = row / h                    # 0 at the band top, 1 at the floor
            dx = int(round(amp * depth * depth * depth * (
                math.sin(row * wave + travel)
                + 0.42 * math.sin(row * wave * 2.3 - travel * 1.7))))
            dy = y0 + row
            if dx == 0:
                layer.blit(buf, (0, dy), (0, sy, w, rows))
            elif dx > 0:
                layer.blit(buf, (dx, dy), (0, sy, w - dx, rows))
                layer.blit(buf, (0, dy), (0, sy, dx, rows))          # edge clamp
            else:
                layer.blit(buf, (0, dy), (-dx, sy, w + dx, rows))
                layer.blit(buf, (w + dx, dy), (w + dx, sy, -dx, rows))

    # ======================================================================
    # Rainy artwork
    # ======================================================================
    #: Soft-brush cloud recipes, keyed by kind. All three use the same
    #: technique and differ only in size, weight and colour: "storm" is the
    #: near-black mass the rain falls out of, "fair" is the light puffy
    #: sunny-day cloud, and "wind" is the long flat overcast that streams
    #: across a blustery sky.
    #:
    #: (sprite size in screen px, lumps, radius band, per-lump alpha, seed)
    CLOUD_KINDS = {
        "storm": ((580, 220), 22, (0.28, 0.52), (95, 150), 20260903),
        "fair": ((620, 150), 18, (0.30, 0.56), (52, 92), 20260904),
        "wind": ((760, 130), 16, (0.30, 0.48), (74, 128), 20260905),
    }
    #: Fair clouds are cream rather than pure white, which glares on a dark
    #: desktop; wind cloud is a colder, flatter grey-white.
    CLOUD_COLORS = {"fair": (255, 251, 240), "wind": (234, 240, 248)}

    #: (colour, alpha scale, vertical offset as a fraction of sprite height)
    #: for a second pass painted BEFORE the body, offset up or down.
    #:
    #: This is the one trick that makes a cloud survive an arbitrary
    #: desktop, and it goes both ways:
    #:
    #:   fair/wind  a cool grey base offset DOWN (positive). A cream cloud
    #:              over a WHITE desktop is nothing at all -- there is no
    #:              dark underneath for it to lighten -- so it brings its
    #:              own shadowed underside.
    #:   storm      a lit grey top offset UP (negative). Symmetrically, a
    #:              near-black cloud over a BLACK desktop is nothing either;
    #:              it cannot darken what is already dark. The body still
    #:              does the work on a light desktop, and these lit upper
    #:              edges are what give it a shape on a dark one.
    CLOUD_SHADE = {
        "fair": ((150, 168, 196), 0.72, 0.20),
        "wind": ((128, 146, 172), 0.62, 0.22),
        "storm": ((104, 120, 146), 0.80, -0.16),
    }

    def _cloud_sprites(self, kind="storm"):
        """Blobby soft-edged clouds, built once per kind."""
        cached = self._clouds.get(kind)
        if cached is None:
            size, lumps, r_band, a_band, seed = self.CLOUD_KINDS[kind]
            color = self.CLOUD_COLORS.get(kind, tuple(config.COLOR_STORM))
            rnd = random.Random(seed)
            w, h = self.apx(size[0]), self.apx(size[1])
            # Lumps are soft radial brushes, not hard circles, and each is
            # blitted so the overlaps build up. Hard circles showed their
            # own outlines through the cloud, and drawing them straight onto
            # the sprite would overwrite rather than accumulate.
            #
            # Every lump also sits fully inside the sprite: a brush clipped
            # by the edge leaves a straight cut, which showed up as a
            # visible horizontal seam across the desktop.
            base = self._glow_base(color)
            shade = self.CLOUD_SHADE.get(kind)
            shade_base = self._glow_base(shade[0]) if shade else None
            cached = []
            for _ in range(4):
                surf = pygame.Surface((w, h), pygame.SRCALPHA)
                lumped = []
                for _ in range(lumps):
                    r = rnd.randint(max(3, int(h * r_band[0])),
                                    max(4, int(h * r_band[1])))
                    lumped.append((
                        r,
                        rnd.randint(r, max(r + 1, w - r)),
                        rnd.randint(r, max(r + 1, h - r)),
                        rnd.randint(*a_band),
                    ))
                if shade is not None:
                    drop = int(h * shade[2])
                    for r, cx, cy, alpha in lumped:
                        brush = pygame.transform.smoothscale(shade_base,
                                                             (r * 2, r * 2))
                        brush.set_alpha(int(alpha * shade[1]))
                        surf.blit(brush, (cx - r, cy - r + drop))
                for r, cx, cy, alpha in lumped:
                    brush = pygame.transform.smoothscale(base, (r * 2, r * 2))
                    brush.set_alpha(alpha)
                    surf.blit(brush, (cx - r, cy - r))
                cached.append(surf.convert_alpha())
            self._clouds[kind] = cached
        return cached

    def draw_clouds(self, layer, clouds, intensity, kind="storm", strength=1.0):
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        sprites = self._cloud_sprites(kind)
        alpha = int(255 * intensity * clamp01(strength))
        if alpha <= 2:
            return
        for cloud in clouds:
            sprite = sprites[cloud["sprite"] % len(sprites)]
            sprite.set_alpha(alpha)
            x, y = self._ap((cloud["x"], cloud["y"]))
            layer.blit(sprite, (int(x), int(y)))

    def draw_top_band(self, layer, color, alpha, height, intensity):
        """A soft strip along the very top, fading out downward.

        Cloud sprites read as separate blobs however many of them there are;
        this is what ties them into one continuous mass. One cached blit,
        which is far cheaper than the extra dozen sprites it saves.
        """
        intensity = clamp01(intensity)
        if intensity <= 0.01 or alpha <= 0:
            return
        color = tuple(color)
        band = self._band(self.art_h * height, (*color, int(alpha)), (*color, 0))
        band.set_alpha(int(255 * intensity))
        layer.blit(band, (0, 0))

    def draw_storm_band(self, layer, intensity):
        """The rainy storm mass. Still translucent -- the desktop underneath
        is dimmed, never hidden."""
        self.draw_top_band(layer, config.COLOR_STORM, config.STORM_BAND_ALPHA,
                           config.STORM_BAND_HEIGHT, intensity)

    def draw_rain_layer(self, art, drops, spec, wind, intensity):
        """One parallax layer. Near drops get a wide faint pass under the
        bright core, which reads as motion blur without a real blur."""
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        alpha = int(spec["alpha"] * intensity)
        if alpha <= 2:
            return
        k = self.k
        width = self.apx(spec["width"])
        # Lean is per-drop-length, so a gust visibly tilts the whole storm.
        lean = wind * config.RAIN_LEAN * spec["parallax"] * k
        blur = spec["blur"]
        blur_width = width + self.apx(3)
        blur_alpha = max(1, alpha // 4)
        for drop in drops:
            x = drop["x"] * k
            y = drop["y"] * k
            end = (x + lean, y + drop["length"] * k)
            if blur:
                pygame.draw.line(art, (176, 202, 232, blur_alpha),
                                 (x, y), end, blur_width)
            pygame.draw.line(art, (206, 228, 255, alpha), (x, y), end, width)

    def draw_glass_streaks(self, art, streaks, intensity):
        """Water running down the foreground, as if the screen were glass."""
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        k = self.k
        for streak in streaks:
            alpha = int(streak["alpha"] * intensity)
            if alpha <= 2:
                continue
            x, y = streak["x"] * k, streak["y"] * k
            length = streak["length"] * k
            wobble = math.sin(streak["y"] * 0.02 + streak["phase"]) * self.apx(3)
            pygame.draw.line(art, (198, 220, 244, max(1, alpha // 3)),
                             (x + wobble, y - length), (x, y), self.apx(3))
            pygame.draw.circle(art, (222, 238, 255, alpha),
                               (int(x), int(y)), self.apx(3))

    def draw_lightning(self, surface, alpha):
        """A brief white flash. Capped by config.LIGHTNING_ALPHA."""
        alpha = int(max(0, min(config.LIGHTNING_ALPHA, alpha)))
        if alpha <= 1:
            return
        flash = self._flat((255, 255, 255))
        flash.set_alpha(alpha)
        surface.blit(flash, (0, 0))

    # ======================================================================
    # Windy artwork
    # ======================================================================
    #: A leaf in unit space, tip at +x. Six points is plenty: anything finer
    #: is detail nobody can see at fifteen pixels long.
    LEAF_SHAPE = ((1.00, 0.00), (0.36, 0.44), (-0.48, 0.32),
                  (-1.00, 0.00), (-0.48, -0.32), (0.36, -0.44))

    def draw_leaves(self, art, leaves, intensity):
        """Autumn leaves tumbling downwind -- the signature of this scene.

        TWO rotations, not one. A flat 2D spin on its own reads as a sticker
        being turned in the plane of the screen. A real leaf also flips
        edge-on, so the shape is additionally squashed across its short axis
        by |cos(tumble)|, which costs one multiply and is most of what makes
        these look like they are turning over IN the air rather than
        sliding across the glass.

        Drawn on the primitives scratch: the spine is a line laid over the
        polygon, which works precisely because pygame.draw overwrites.
        """
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        k = self.k
        shape = self.LEAF_SHAPE
        for leaf in leaves:
            a = int(leaf["alpha"] * intensity)
            if a <= 3:
                continue
            cos_a = math.cos(leaf["angle"])
            sin_a = math.sin(leaf["angle"])
            sx = leaf["size"] * self.scale * k
            sy = sx * (0.20 + 0.80 * abs(math.cos(leaf["tumble"])))
            x, y = leaf["x"] * k, leaf["y"] * k
            pts = [(x + px * sx * cos_a - py * sy * sin_a,
                    y + px * sx * sin_a + py * sy * cos_a)
                   for px, py in shape]
            pygame.draw.polygon(art, (*leaf["color"], a), pts)
            if sx >= 5:
                # A midrib, drawn tip to tail. Small, but it is the
                # difference between a leaf and a coloured blob.
                pygame.draw.line(art, (*leaf["vein"], a), pts[0], pts[3], 1)

    def draw_wind_streaks(self, art, streaks, intensity):
        """Thin smears of moving air, some with a brighter speck at the head.

        Air is invisible; what sells it is the debris it carries and the
        trail that debris leaves at speed. One line each, so the whole layer
        costs less than a tenth of the rain.
        """
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        k = self.k
        width = self.apx(1)
        for streak in streaks:
            a = int(streak["alpha"] * intensity)
            if a <= 2:
                continue
            x, y = streak["x"] * k, streak["y"] * k
            tail = x - streak["dir"] * streak["length"] * k
            pygame.draw.line(art, (226, 238, 250, a), (tail, y), (x, y), width)
            if streak["speck"]:
                pygame.draw.circle(art, (244, 250, 255, min(255, a * 3)),
                                   (int(x), int(y)), width)

    # ======================================================================
    # Snowy artwork
    # ======================================================================
    def draw_snow_layer(self, art, flakes, spec, intensity):
        """One parallax layer of snowfall.

        Near flakes get a faint wide halo under the bright core, which reads
        as out of focus -- the same cheap trick the near rain layer uses for
        motion blur, and what stops a big flake looking like a hard dot.
        """
        intensity = clamp01(intensity)
        if intensity <= 0.01:
            return
        alpha = int(spec["alpha"] * intensity)
        if alpha <= 2:
            return
        halo = spec["halo"]
        halo_alpha = max(1, alpha // 4)
        k, sc = self.k, self.scale
        for flake in flakes:
            x, y = int(flake["x"] * k), int(flake["y"] * k)
            r = max(1, int(flake["radius"] * sc * k))
            if halo:
                # A COOL halo, not a white one. It glows on a dark desktop
                # and darkens a light one, so a white flake still has an
                # edge over white -- the same trick as the cloud shading.
                pygame.draw.circle(art, (146, 178, 214, halo_alpha), (x, y), r * 3)
            pygame.draw.circle(art, (255, 255, 255, alpha), (x, y), r)

    def _snow_drift(self):
        """The accumulation sprite: a wavy, feathered white bank.

        Built once at 240x48 and smoothscaled to full width, which softens
        both the crests and the top edge for free. It is drawn with its TOP
        at the current snow depth and everything past the bottom of the
        screen is clipped by pygame, so "growing" costs nothing but a
        different y -- no per-frame geometry, no rebuilds.

        Returned as (front, back); the back copy is mirrored so the two
        banks do not share a silhouette.
        """
        if self._drift is None:
            h = max(6, int(self.art_h * config.SNOW_PILE_MAX))
            sw, sh = 240, 48
            feather, shade = 5, 5
            peak = config.SNOW_PILE_ALPHA
            cool = tuple(config.SNOW_PILE_SHADE)
            bumps = max(1, int(config.SNOW_PILE_BUMPS))
            small = pygame.Surface((sw, sh), pygame.SRCALPHA)
            for x in range(sw):
                t = x / sw * math.tau
                # Two harmonics only, and gently: three made narrow pointed
                # crests, and the drift came out as a row of white triangles
                # rather than as snow. Whole multiples of one period, so the
                # edge still meets itself at the screen's far side.
                crest = (0.44
                         + 0.15 * math.sin(t * bumps)
                         + 0.06 * math.sin(t * bumps * 2 + 1.1))
                top = max(shade, min(sh - feather - 2, int(sh * crest)))
                # A cool shaded lip ABOVE the white. White snow over a white
                # desktop is nothing at all -- this is the edge that draws
                # the drift on a light background, and it reads as the shaded
                # side of the bank on a dark one.
                for i in range(shade):
                    a = int(config.SNOW_PILE_SHADE_ALPHA * (i + 1) / shade)
                    small.set_at((x, top - shade + i), (*cool, a))
                for i in range(feather):
                    a = int(peak * (i + 1) / feather * 0.9)
                    small.set_at((x, top + i), (255, 255, 255, a))
                pygame.draw.line(small, (255, 255, 255, peak),
                                 (x, top + feather), (x, sh - 1))
            front = pygame.transform.smoothscale(small, (self.art_w, h))
            back = pygame.transform.flip(front, True, False)
            self._drift = (front.convert_alpha(), back.convert_alpha())
        return self._drift

    def draw_snow_drift(self, layer, level, intensity):
        """Snow settling along the bottom edge. `level` is 0..1 of the cap."""
        intensity = clamp01(intensity)
        if not config.SNOW_PILE_ENABLED or intensity <= 0.01:
            return
        front, back = self._snow_drift()
        depth = int(front.get_height() * clamp01(level))
        if depth < 2:
            return
        alpha = int(255 * intensity)
        # The mirrored bank sits lower and fainter: two silhouettes read as
        # a drift with depth, one reads as a painted stripe.
        back.set_alpha(int(alpha * 0.55))
        layer.blit(back, (0, self.art_h - int(depth * 0.74)))
        front.set_alpha(alpha)
        layer.blit(front, (0, self.art_h - depth))

    # -- condensation fog --------------------------------------------------
    def fog(self):
        """The wipeable condensation layer, built on first use."""
        if self._fog is None:
            self._fog = CondensationFog(self)
        return self._fog

    def reset_fog(self):
        """Clear the fog completely. Called when a rainy event ends, so the
        next one does not open on somebody else's smeared window."""
        if self._fog is not None:
            self._fog.reset()

    # ======================================================================
    # Sprites -- the wedding easter egg (wedding.py) and nothing else yet
    # ======================================================================
    def sprite_size(self, sprite, height_frac):
        """Artwork-layer (w, h) for a sprite drawn `height_frac` tall.

        Width follows the sprite's own aspect ratio, so the crowds come out
        wide and the goat comes out narrow from one number each.
        """
        sw, sh = sprite.get_size()
        if sh <= 0 or sw <= 0:
            return (0, 0)
        h = max(2, int(self.art_h * height_frac))
        return (max(2, int(sw * h / sh)), h)

    def draw_sprite(self, layer, key, sprite, pos, height_frac,
                    alpha=255, anchor="center", offset=(0.0, 0.0)):
        """Queue an RGBA sprite on the full-resolution foreground.

        `layer` is the list new_foreground() handed back, not a surface:
        the sprite is composited by draw_foreground AFTER the half-res
        artwork has been upscaled, so its edges are the smoothscale's own
        and never go through the canvas's 2x nearest-neighbour.

        `pos` and `offset` are fractions of the SCREEN (width, height), and
        `height_frac` is the on-screen height as a fraction of screen
        height, so a caller never has to know that the artwork layer is at
        half resolution. `anchor` is "center" (pos is the middle) or
        "bottom" (pos is the sprite's feet) -- see WEDDING_LAYOUT for why
        the standing cast uses the latter.

        Size and position are still measured on the artwork grid and then
        multiplied out by art_div, so every sprite covers exactly the screen
        pixels it covered when it was drawn into the half-res layer; only
        what is inside that box got sharper.

        The scaled copy is smoothscaled ONCE, from the cropped source PNG
        straight to that final screen size, premultiplied for
        draw_foreground, and cached per (key, size) forever -- which is why
        `key` is a stable name rather than the surface itself.
        """
        if sprite is None:
            return
        alpha = int(max(0, min(255, alpha)))
        if alpha <= 2:
            return
        w, h = self.sprite_size(sprite, height_frac)
        if w <= 0:
            return
        d = self.art_div
        cache_key = (key, w, h)
        scaled = self._sprites.get(cache_key)
        if scaled is None:
            scaled = pygame.transform.smoothscale(
                sprite, (w * d, h * d)).convert_alpha().premul_alpha()
            self._sprites[cache_key] = scaled
        x = (pos[0] + offset[0]) * self.art_w - w / 2
        y = (pos[1] + offset[1]) * self.art_h
        y = y - h if anchor == "bottom" else y - h / 2
        layer.append((scaled, (int(x) * d, int(y) * d), alpha))

    def new_foreground(self):
        """Clear and hand back this frame's full-resolution foreground.

        Only the wedding cast uses it. It is a list, not a surface: the
        sprites are composited straight into the final full-size bitmap by
        draw_foreground, after the artwork's upscale, the same way the
        escape hint is. That is what keeps their edges crisp -- drawn into
        the half-res layer they were smoothscaled to half size and then
        blown back up 2x nearest-neighbour along with the rain.
        """
        self._foreground.clear()
        return self._foreground

    def queue_art(self, layer, art, area):
        """Put one corner of the primitives scratch on the foreground.

        For the drawn hearts, which have to stay in front of the cast now
        that the cast is composited after the upscale. They are still drawn
        at artwork resolution and blown up nearest-neighbour -- the same
        upscale the canvas gets in overlay.present -- so they look exactly
        as they did; they simply land on top of the sharp sprites.
        """
        area = area.clip(art.get_rect())
        if area.w <= 0 or area.h <= 0:
            return
        # copy() first: premul_alpha() on a subsurface returns transparent
        # pixels in pygame 2.6.
        piece = art.subsurface(area).copy().premul_alpha()
        d = self.art_div
        if d > 1:
            piece = pygame.transform.scale(piece, (area.w * d, area.h * d))
        layer.append((piece, (area.x * d, area.y * d), 255))

    def draw_foreground(self, surface):
        """Composite the queued foreground onto a FULL-RESOLUTION surface:
        overlay.present's bitmap right after its upscale, or the canvas in
        --windowed mode.

        Premultiplied blend, because the overlay's bitmap is premultiplied
        and a straight-alpha blit onto it writes the wrong colour wherever
        the desktop shows through (pygame copies the source colour outright
        onto a fully transparent destination). The same blend is exact over
        the opaque windowed canvas too. BLEND_PREMULTIPLIED ignores
        set_alpha, so a sprite that is fading is first multiplied by its
        alpha into a reusable scratch of its size -- only during the
        fade-in, the fade-out and the pose cross-fades; a held sprite is one
        blit. Filling the scratch and MULT-blitting the sprite into it is
        byte-identical to copy() + a MULT fill, and about 6x cheaper: the
        blit is SIMD and the blended fill is not.
        """
        for surf, pos, alpha in self._foreground:
            if alpha < 255:
                size = surf.get_size()
                faded = self._faded.get(size)
                if faded is None:
                    faded = self._faded[size] = pygame.Surface(size, pygame.SRCALPHA)
                faded.fill((alpha, alpha, alpha, alpha))
                faded.blit(surf, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                surf = faded
            surface.blit(surf, pos, special_flags=pygame.BLEND_PREMULTIPLIED)

    def draw_hearts(self, art, hearts, alpha_scale=1.0):
        """Soft hearts rising off the couple, as primitives.

        Three shapes each -- two lobes and a point -- drawn on the
        PRIMITIVES scratch, because pygame.draw overwrites alpha and would
        punch holes in the composite layer the cast is standing on.

        Returns a rect covering every pixel it changed (zero-size if none),
        which is what lets merge_art blend just that corner of the scratch.
        """
        drawn = []
        color = tuple(config.WEDDING_HEART_COLOR)
        for heart in hearts:
            alpha = int(heart["alpha"] * clamp01(alpha_scale))
            if alpha <= 3:
                continue
            r = max(1, int(heart["size"] * self.art_h * 0.5))
            cx = int(heart["x"] * self.art_w)
            cy = int(heart["y"] * self.art_h)
            rgba = (*color, alpha)
            lobe = max(1, int(r * 0.58))
            lift = int(lobe * 0.45)
            drawn.append(pygame.draw.circle(art, rgba, (cx - lobe, cy - lift), lobe))
            drawn.append(pygame.draw.circle(art, rgba, (cx + lobe, cy - lift), lobe))
            drawn.append(pygame.draw.polygon(art, rgba, (
                (cx - lobe * 2, cy - lift),
                (cx + lobe * 2, cy - lift),
                (cx, cy + int(r * 1.1)),
            )))
        return drawn[0].unionall(drawn[1:]) if drawn else pygame.Rect(0, 0, 0, 0)

    # ======================================================================
    # The only chrome: the global stop hotkey.
    # ======================================================================
    def _build_hint(self):
        """Small and low-opacity. The overlay is click-through and never
        focused, so this is the user's only reminder of the way out.

        Built at full screen resolution: in overlay mode the canvas is
        half-res, so compositing this here instead would visibly soften the
        one piece of text on screen. overlay.py blits it after the upscale.
        """
        img = self.font_hint.render(config.ESCAPE_HINT, True, (236, 240, 248))
        pad_x, pad_y = self.px(13), self.px(7)
        w = img.get_width() + pad_x * 2
        h = img.get_height() + pad_y * 2
        pill = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(pill, (12, 14, 20, 150), pill.get_rect(),
                         border_radius=h // 2)
        pill.blit(img, (pad_x, pad_y))
        # Bake the low opacity in, so no set_alpha is needed downstream.
        #
        # OVERLAY_ALPHA goes in here too. The hint is composited by
        # overlay.py straight into the bitmap, so it never passes through
        # flush_overlay -- and with the window's own constant now at 255 it
        # would otherwise have got brighter the day that moved. This keeps
        # it at exactly the opacity it has always had.
        faded = config.ESCAPE_HINT_ALPHA
        if self.overlay:
            faded = int(faded * config.OVERLAY_ALPHA / 255)
        pill.fill((255, 255, 255, max(1, faded)),
                  special_flags=pygame.BLEND_RGBA_MULT)
        return pill

    def hint(self):
        """(surface, top-left position) at full resolution, or None."""
        if not config.SHOW_ESCAPE_HINT:
            return None
        if self._hint is None:
            self._hint = self._build_hint()
        return (self._hint,
                (self.width - self._hint.get_width() - self.px(22),
                 self.height - self._hint.get_height() - self.px(18)))

    def draw_escape_hint(self, surface):
        """Dev-window path: the canvas is already full resolution there."""
        got = self.hint()
        if got is not None:
            surface.blit(got[0], got[1])


class CondensationFog:
    """Rain fogging up the screen, wiped clear by the cursor.

    HOW IT IS WIPED WITHOUT TAKING INPUT
      The overlay is WS_EX_TRANSPARENT, so it never receives a mouse event
      -- clicks, drags and hovers all go to the app underneath, and that has
      to stay true. So the cursor is READ instead, once per frame, with
      GetCursorPos (desktop.cursor_pos), which sees through a click-through
      layered window. The user works normally; the fog simply notices where
      the pointer went. Nothing is captured, swallowed or synthesised.

      Wiping follows the SEGMENT from the previous position to the current
      one, not just the current point, or a fast flick would leave a dotted
      line of holes at 60fps instead of a streak.

    HOW IT IS AFFORDABLE
      The fog is a mask at 1/FOG_MASK_DIV of screen resolution whose ALPHA
      is the density -- 480x270 on a 1080p display. Everything is a surface
      operation, because there is no numpy here:

        re-fog   fill(BLEND_RGBA_ADD) with (0, 0, 0, n), which raises alpha
                 and leaves the colour alone
        wipe     blit a soft brush with BLEND_RGBA_SUB, which subtracts
                 alpha to zero at the middle and feathers out at the edge

      Alpha is an integer, and a gentle re-fog is well under one unit per
      frame, so the increment is accumulated and applied only once it has
      built up -- otherwise int() truncates it to nothing and the fog never
      comes back. It is applied in whole REFOG_STEPs rather than single
      units because that fill is a surprisingly expensive full-mask
      operation (~1ms at 480x270) and a step of four is 1.5% of the density,
      which nobody can see arriving eight times a second.

      For the same reason the upscale runs on every other frame and the
      result is reused in between: a 16ms lag on the wipe trail is
      invisible, and smoothscale is the single most expensive thing here.

      Density runs the full 0..255 and the cap is applied at blit time with
      set_alpha(FOG_OPACITY), so a fully-fogged patch is still a haze you
      can read through, and a wiped patch takes FOG_REFOG_SECONDS to climb
      back to it.
    """

    #: Density units per re-fog fill. See the note above about cost.
    REFOG_STEP = 4

    def __init__(self, renderer):
        self.r = renderer
        div = max(1, int(config.FOG_MASK_DIV))
        self.size = (max(8, renderer.width // div), max(8, renderer.height // div))
        self.mask = pygame.Surface(self.size, pygame.SRCALPHA)
        # Scaled up on the way out; allocated once and reused.
        self._scaled = pygame.Surface((renderer.art_w, renderer.art_h),
                                      pygame.SRCALPHA)
        self._brush = self._make_brush(
            max(2, int(config.FOG_WIPE_RADIUS * renderer.scale / div)))
        self._last = None        # previous cursor position, in mask pixels
        self._carry = 0.0        # sub-step re-fog accumulator
        self._stale = True       # the scaled copy needs rebuilding
        self._ticks = 0          # frame parity, to halve the upscale rate
        self.reset()

    # ------------------------------------------------------------------
    @staticmethod
    def _make_brush(radius):
        """A flat-cored, feathered-edged eraser.

        The radial brush used elsewhere falls off from the very centre,
        which wipes a smudge rather than a clear patch. This one is solid
        across the middle and only feathers over the outer half, so the
        cursor leaves a genuinely clear trail with a soft edge.
        """
        n = 96
        stamp = pygame.Surface((n, n), pygame.SRCALPHA)
        c = (n - 1) / 2.0
        for y in range(n):
            for x in range(n):
                d = math.hypot(x - c, y - c) / c
                if d <= 0.46:
                    a = 255
                elif d >= 1.0:
                    a = 0
                else:
                    a = int(255 * (1.0 - (d - 0.46) / 0.54) ** 1.5)
                stamp.set_at((x, y), (0, 0, 0, a))
        return pygame.transform.smoothscale(stamp, (radius * 2, radius * 2))

    def reset(self):
        """Back to clear glass, and forget where the cursor was."""
        self.mask.fill((*config.FOG_COLOR, 0))
        self._scaled.fill((*config.FOG_COLOR, 0))
        self._last = None
        self._carry = 0.0
        self._stale = False

    # ------------------------------------------------------------------
    def update(self, dt, strength=1.0):
        """Fog up a little, then wipe along wherever the cursor went."""
        if not config.FOG_ENABLED:
            return
        self._refog(dt * clamp01(strength))
        self._wipe()

    def _refog(self, dt):
        rate = 255.0 / max(0.5, config.FOG_REFOG_SECONDS)
        self._carry += rate * dt
        if self._carry < self.REFOG_STEP:
            return
        step = int(self._carry)
        self._carry -= step
        # Alpha only: the colour channels are already the fog colour.
        self.mask.fill((0, 0, 0, min(255, step)),
                       special_flags=pygame.BLEND_RGBA_ADD)
        self._stale = True

    def _wipe(self):
        pos = desktop.cursor_pos()
        if pos is None:
            self._last = None
            return
        sx = self.size[0] / max(1, self.r.width)
        sy = self.size[1] / max(1, self.r.height)
        now = (pos[0] * sx, pos[1] * sy)

        brush = self._brush
        radius = brush.get_width() / 2.0
        points = [now]
        if self._last is not None:
            dx, dy = now[0] - self._last[0], now[1] - self._last[1]
            dist = math.hypot(dx, dy)
            # Stamps every half-radius fill the gap; the cap keeps a cursor
            # that jumped monitors or reappeared from a cheap stamp count.
            steps = min(48, int(dist / max(1.0, radius * 0.5)))
            points = [(self._last[0] + dx * i / steps,
                       self._last[1] + dy * i / steps)
                      for i in range(1, steps + 1)] or points
        for px_, py_ in points:
            self.mask.blit(brush, (int(px_ - radius), int(py_ - radius)),
                           special_flags=pygame.BLEND_RGBA_SUB)
            self._stale = True
        self._last = now

    # ------------------------------------------------------------------
    def draw(self, layer, intensity, strength=1.0):
        """Blit the mask over the weather, scaled up and softened."""
        if not config.FOG_ENABLED:
            return
        alpha = int(config.FOG_OPACITY * clamp01(intensity) * clamp01(strength))
        if alpha <= 2:
            return
        # At most every other frame: a moving cursor dirties the mask on
        # every single one, and this upscale is the expensive part.
        self._ticks += 1
        if self._stale and not self._ticks & 1:
            pygame.transform.smoothscale(self.mask, self._scaled.get_size(),
                                         self._scaled)
            self._stale = False
        self._scaled.set_alpha(alpha)
        layer.blit(self._scaled, (0, 0))
