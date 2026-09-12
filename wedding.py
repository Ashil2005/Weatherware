"""
The FOX-HEN WEDDING -- an easter egg that plays out during a sunshower.

In Kerala a sunshower is "കുറുക്കന്റെ കല്യാണം", the fox's wedding, so the
rare 10% event is where the joke belongs: while the sun and the rain are
both running, a small wedding party assembles in the lower half of the
screen under a decorated arch. Four bells, Mendelssohn, a ring, a blessing,
a kiss, and four rows of crowd on their feet. Fifty-three seconds, then it
fades and the shower carries on without it.

It does NOT start with the event. The shower runs plain for
WEDDING_START_DELAY first, so the bells arrive out of a perfectly ordinary
sunshower -- see SunshowerScene, which owns that delay.

=============================================================================
 WHAT THIS MODULE IS ALLOWED TO DO
=============================================================================
 Draw sprites and fire audio cues. That is the whole remit, and it is the
 same remit renderer.py and audio.py have. Nothing here touches brightness,
 the Power Mode overlay, the Lenovo fan preset, the warm-up or any exit
 path, and nothing here may ever be given a reason to -- see the safety rule
 at the top of config.py.

 The ceremony does ask TWO things of the scene that owns it, and both are
 requests handled in scenes/sunshower.py rather than actions taken here:
 that the event lasts long enough to finish, and that the scene's brightness
 sweep is gentle enough to keep the cast visible. The second one still lands
 inside BRIGHTNESS_FLOOR..BRIGHTNESS_CEILING; the ceremony can only ever
 narrow that sweep, never widen it.
=============================================================================

HOW IT IS WIRED

  * ITS OWN CLOCK. t=0 is the moment the ceremony starts, not the moment the
    event did, and every keyframe in config.WEDDING_TIMELINE is expressed
    against it. The engine's dt drives it, so there is no thread here and
    nothing to shut down.

  * SLOTS, NOT CHARACTERS. There are nine things that can be on screen --
    the arch, the priest, bride, groom, couple, and four crowd groups -- and
    the timeline just assigns a sprite to a slot. `couple` is the special
    one: while it holds a composite the separate bride and groom are hidden,
    and when it is cleared they come back. That hand-off is a cross-fade
    too, so couple_ring appearing does not make two characters vanish on one
    frame. A slot can also be MOVED by the timeline, which is how the bride
    and groom step together for the lone-kiss beat.

  * THE CAST IS OPAQUE. Everything else the app draws is translucent
    artwork over the user's real work, and the cast was being dimmed with
    it -- solid cartoon characters arriving on screen at 70% look like
    ghosts. The weather's global dimming now lives in
    Renderer.flush_overlay, so this module flushes its layer at full alpha
    and the party is genuinely solid. The fade-in, the fade-out and the
    cross-fades all still work; it is the HELD state that is opaque.

  * DEFENSIVE LOADING, EVERYWHERE. Every PNG, every clip and the font are
    optional. A missing one is logged once at load and then skipped for the
    life of the process; with the whole assets/images/wedding folder gone,
    start() reports that it is unavailable and the sunshower is exactly the
    sunshower it always was. This is deliberately the same contract audio.py
    already has for its clips.

  * SPRITES ARE CROPPED ON LOAD. The PNGs are subjects on a wide transparent
    canvas -- the fox occupies about a third of his -- so scaling one by its
    file height would draw a character a third of the size asked for, in the
    wrong place. Each is cropped to the bounding box of its GROUP (every fox
    pose shares one crop, every couple pose shares another), because
    cropping each pose to its own box would resize and re-centre the
    character every time it moved.

  * AUDIO IS audio.py's. The march is a looping bed on a dedicated music
    channel; the bells, the two lines of dialogue and the crowd are
    one-shots on four more. stop() takes them all down, and so does every
    path that reaches Audio.stop_all() -- return to idle, Ctrl+Shift+Q and
    shutdown alike. Nothing may still be playing after the bride has gone.
"""

import math
import random
import sys
from pathlib import Path

import pygame

import config


def _image_dir():
    """Where the sprites live, in dev and inside a frozen one-file build.

    Same reasoning as audio._sound_dir: PyInstaller unpacks to a temporary
    directory and points sys._MEIPASS at it, so a path derived from the
    source tree is the wrong answer there.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "assets" / "images" / "wedding"
    return config.IMAGE_DIR / "wedding"


def _group(key):
    """Which crop a sprite key shares: everything before the underscore."""
    return key.split("_", 1)[0]


class WeddingCeremony:
    """One run of the ceremony, owned by the sunshower scene.

    Built once per event, ticked every frame, drawn after the weather, and
    stopped when the event ends. Safe to build and run with no assets at
    all: `available` stays False and update/draw become no-ops.
    """

    def __init__(self, log=print):
        self.log = log
        self.available = False
        self.active = False
        self.t = 0.0

        self._sprites = {}        # sprite key -> cropped Surface
        self._missing = []        # sprite keys whose file was absent
        self._cursor = 0          # next timeline entry to fire

        # slot -> the sprite key currently assigned, or None.
        self._slots = {name: None for name in config.WEDDING_SLOTS}
        # slot -> (previous key, seconds of cross-fade left).
        self._fading = {}
        # 0..1 hand-off between "bride and groom, separately" and "the
        # couple composite". See _draw_cast.
        self._couple_vis = 0.0

        self._hearts = []
        self._hearts_on = False
        self._heart_debt = 0.0
        self._heart_sprite = None   # optional hearts.png; drawn if absent

        # slot -> position override set by a timeline "move", or absent.
        self._moves = {}

        self._audio = None
        self._rng = random.Random()

    # ======================================================================
    # Loading -- every piece of it optional
    # ======================================================================
    def load(self):
        """Read the sprites once. Never raises; logs what it could not find.

        Returns True if there is enough here to be worth running, which
        means at least one sprite: a ceremony with a priest and no couple is
        odd but harmless, a ceremony with nothing at all is just a silent
        cost every frame.
        """
        directory = _image_dir()
        raw = {}
        for key, filename in config.WEDDING_SPRITES.items():
            path = directory / filename
            if not path.is_file():
                self._missing.append(key)
                continue
            try:
                raw[key] = pygame.image.load(str(path)).convert_alpha()
            except Exception as exc:
                self._missing.append(key)
                self.log(f"wedding: {key} failed to load ({exc}) -- skipped")

        self._sprites = self._crop_groups(raw, self._missing, self.log)
        self.available = bool(self._sprites)
        self._heart_sprite = self._load_hearts(directory)

        if self._missing:
            self.log(f"wedding: {len(self._missing)} sprite(s) missing "
                     f"({', '.join(sorted(self._missing))}) -- those poses are skipped")
        if self.available:
            self.log(f"wedding: {len(self._sprites)} sprite(s) loaded from "
                     f"{directory.name}/")
        else:
            self.log(f"wedding: no sprites in {directory} -- easter egg disabled")
        return self.available

    def _load_hearts(self, directory):
        """The optional hearts.png. Absent is the normal case and not worth
        a warning: draw_hearts falls back to primitives, which is what this
        has always looked like."""
        path = directory / config.WEDDING_HEART_SPRITE
        if not path.is_file():
            return None
        try:
            sprite = pygame.image.load(str(path)).convert_alpha()
        except Exception as exc:
            self.log(f"wedding: {path.name} failed to load ({exc}) -- "
                     "drawing the hearts instead")
            return None
        box = sprite.get_bounding_rect(min_alpha=8)
        if box.width <= 0 or box.height <= 0:
            return None
        self.log(f"wedding: using {path.name} for the hearts")
        return sprite.subsurface(box).copy()

    @staticmethod
    def _crop_groups(raw, missing, log):
        """Trim the transparent margins, one shared box per character group.

        The art is drawn on a wide canvas with the subject somewhere in the
        middle of it, so an uncropped sprite scaled to 20% of screen height
        puts a 7%-tall fox at an arbitrary offset. Cropping fixes both --
        but it has to be done per GROUP, not per file: fox_stand's own
        bounding box is wider than fox_plain's (he is holding a ring), and
        cropping each to itself would make the fox visibly grow and shift
        sideways at the exact moment he picks it up.
        """
        boxes = {}
        blank = []
        for key, surf in raw.items():
            box = surf.get_bounding_rect(min_alpha=8)
            if box.width <= 0 or box.height <= 0:
                # A PNG that loaded fine and contains nothing but
                # transparency. It has to be caught HERE rather than left to
                # draw as nothing, because its group's crop comes from its
                # siblings, so it would sail through as a perfectly valid
                # sprite that happens to be invisible -- and a pose swap
                # into it would blank that character out mid-ceremony.
                blank.append(key)
                continue
            group = _group(key)
            boxes[group] = box if group not in boxes else boxes[group].union(box)

        if blank:
            missing.extend(blank)
            log(f"wedding: {', '.join(sorted(blank))} "
                f"{'is' if len(blank) == 1 else 'are'} fully transparent -- "
                "treating as missing, so whoever holds that slot keeps the "
                "pose they are already in")

        cropped = {}
        for key, surf in raw.items():
            box = boxes.get(_group(key)) if key not in blank else None
            if box is None:
                continue
            cropped[key] = surf.subsurface(box).copy()
        return cropped

    # ======================================================================
    # Lifecycle
    # ======================================================================
    def start(self, engine):
        """Open the ceremony at t=0. Called from SunshowerScene.start."""
        if not self.available:
            return
        self._audio = getattr(engine, "audio", None)
        self.t = 0.0
        self._cursor = 0
        self._slots = {name: None for name in config.WEDDING_SLOTS}
        self._fading.clear()
        self._couple_vis = 0.0
        self._hearts.clear()
        self._hearts_on = False
        self._heart_debt = 0.0
        self._moves.clear()
        self.active = True
        self.log(f"wedding: ceremony begins -- {config.WEDDING_TOTAL:0.0f}s, "
                 f"{len(config.WEDDING_TIMELINE)} keyframes")

    def stop(self, engine=None):
        """Take everything down. Runs on the end of the event, on a forced
        scene change, on Ctrl+Shift+Q and on shutdown, because the scene's
        own stop() is on all four of those paths.

        Audio.stop_all() would cover the cues anyway -- this exists so the
        ceremony can end WITHOUT silencing the weather it was playing over,
        and so the rain's level is handed back rather than left ducked.
        """
        if not self.active and self._audio is None:
            return
        was_active = self.active
        self.active = False
        self._hearts.clear()
        self._hearts_on = False
        if self._audio is not None:
            try:
                self._audio.stop_wedding()
            except Exception as exc:
                self.log(f"wedding: could not stop the audio ({exc})")
        self._audio = None
        if was_active:
            self.log(f"wedding: ceremony over at t={self.t:0.1f}s")

    # ======================================================================
    # Simulation
    # ======================================================================
    def update(self, dt, engine):
        if not (self.available and self.active):
            return
        self.t += dt
        self._fire_keyframes()
        self._advance_crossfades(dt)
        self._update_hearts(dt)

    def _fire_keyframes(self):
        """Walk the timeline forwards, once each, in order.

        A cursor rather than a search: entries fire in order and never
        re-fire, and a frame that swallows two of them (a stalled frame, or
        the 18.0/18.3 pair at 60fps) still plays both in the right order.
        """
        timeline = config.WEDDING_TIMELINE
        while self._cursor < len(timeline) and timeline[self._cursor]["t"] <= self.t:
            entry = timeline[self._cursor]
            self._cursor += 1
            self._apply(entry)

    def _apply(self, entry):
        for slot, key in entry.get("set", {}).items():
            self._set_slot(slot, key)
        if "move" in entry:
            # Only one override is implemented, and config asserts that: the
            # bride and groom stepping together for the lone-kiss beat.
            # Anything else clears the overrides and puts everyone back on
            # their WEDDING_LAYOUT marks.
            self._moves = (dict(config.WEDDING_LAYOUT_KISS)
                           if entry["move"] == "kiss" else {})
        if "duck" in entry:
            self._duck(entry["duck"])
        cue = entry.get("audio")
        if cue is not None:
            self._play(cue)
        music = entry.get("music")
        if music == "start":
            self._start_music()
        elif music == "fade":
            self._fade_music()
        if entry.get("hearts"):
            self._hearts_on = config.WEDDING_HEARTS_ENABLED
        if entry.get("fade"):
            self._hearts_on = False
        self.log(f"wedding: t={entry['t']:0.1f}s "
                 f"{self._describe(entry)}")

    @staticmethod
    def _describe(entry):
        """One readable line per keyframe, so a run can be checked against
        the score in the log rather than by eye."""
        bits = []
        for slot, key in entry.get("set", {}).items():
            bits.append(f"{slot}={key or 'none'}")
        if "move" in entry:
            bits.append(f"move:{entry['move'] or 'default'}")
        if "duck" in entry:
            bits.append(f"duck:{entry['duck']:.2f}")
        if "audio" in entry:
            bits.append(f"cue:{entry['audio']}")
        if "music" in entry:
            bits.append(f"march:{entry['music']}")
        if entry.get("hearts"):
            bits.append("hearts")
        if entry.get("fade"):
            bits.append("fade-out")
        return " ".join(bits) or "(nothing)"

    def _set_slot(self, slot, key):
        """Assign a sprite to a slot, cross-fading out of the old pose.

        A swap to a pose that never loaded is IGNORED rather than applied.
        Storing it would blank that character out for the rest of the
        ceremony, and one seated row among three cheering ones is a far
        better failure than a hole in the front row. Clearing a slot with an
        explicit None is a different thing and still works: that is how the
        couple composite hands back to the separate bride and groom.
        """
        if slot not in self._slots:
            return
        if key is not None and key not in self._sprites:
            self.log(f"wedding: no sprite for {key}, so {slot} holds "
                     f"{self._slots[slot] or 'nothing'}")
            return
        previous = self._slots[slot]
        if previous == key:
            return
        self._slots[slot] = key
        self._fading[slot] = [previous, config.WEDDING_CROSSFADE]

    def _advance_crossfades(self, dt):
        for slot in list(self._fading):
            self._fading[slot][1] -= dt
            if self._fading[slot][1] <= 0:
                del self._fading[slot]

        # The bride/groom <-> couple hand-off, on the same clock. Eased
        # rather than switched so neither pair pops in or out on one frame.
        # Keyed off whether the composite actually LOADED, not merely off
        # whether the timeline assigned one: if couple_ring.png is missing,
        # hiding the bride and groom for it would leave an empty aisle, so
        # in that case they simply stay where they are.
        step = dt / max(1e-6, config.WEDDING_CROSSFADE)
        target = 1.0 if self._slots.get("couple") in self._sprites else 0.0
        if self._couple_vis < target:
            self._couple_vis = min(target, self._couple_vis + step)
        elif self._couple_vis > target:
            self._couple_vis = max(target, self._couple_vis - step)

    def _update_hearts(self, dt):
        if not (self._hearts or self._hearts_on):
            return
        alive = []
        for heart in self._hearts:
            heart["age"] += dt
            if heart["age"] >= heart["life"]:
                continue
            heart["y"] -= heart["rise"] * dt
            heart["phase"] += heart["sway_hz"] * dt
            heart["x"] = heart["x0"] + math.sin(heart["phase"]) * heart["sway"]
            # Fade in fast, then out over the back half of the life.
            p = heart["age"] / heart["life"]
            envelope = min(1.0, p / 0.18) * min(1.0, (1.0 - p) / 0.45)
            heart["alpha"] = config.WEDDING_HEART_ALPHA * envelope
            alive.append(heart)
        self._hearts = alive

        if not self._hearts_on:
            return
        self._heart_debt += config.WEDDING_HEART_RATE * dt
        while self._heart_debt >= 1.0:
            self._heart_debt -= 1.0
            if len(self._hearts) >= config.WEDDING_HEART_MAX:
                break
            self._hearts.append(self._spawn_heart())

    def _spawn_heart(self):
        rnd = self._rng
        ox, oy = config.WEDDING_HEART_ORIGIN
        x0 = ox + rnd.uniform(-1.0, 1.0) * config.WEDDING_HEART_SPREAD
        return {
            "x": x0, "x0": x0,
            "y": oy + rnd.uniform(-0.02, 0.02),
            "rise": rnd.uniform(*config.WEDDING_HEART_RISE),
            "size": rnd.uniform(*config.WEDDING_HEART_SIZE),
            "sway": rnd.uniform(*config.WEDDING_HEART_SWAY),
            "sway_hz": rnd.uniform(1.1, 2.4),
            "phase": rnd.uniform(0.0, math.tau),
            "life": rnd.uniform(*config.WEDDING_HEART_LIFE),
            "age": 0.0,
            "alpha": 0.0,
        }

    # ======================================================================
    # Audio -- all of it through audio.py, none of it owned here
    # ======================================================================
    def _play(self, cue):
        if self._audio is None:
            return
        try:
            self._audio.play_oneshot(cue)
        except Exception as exc:
            self.log(f"wedding: cue {cue} failed ({exc})")

    def _start_music(self):
        if self._audio is None:
            return
        try:
            self._audio.play_wedding_music()
        except Exception as exc:
            self.log(f"wedding: could not start the march ({exc})")

    def _duck(self, factor):
        """Hold the shower down, or hand it back.

        Both edges are timeline entries rather than side effects of the
        march starting and stopping, because the rain has to come back UP
        at the finale -- while the ceremony is still on screen and the event
        still has its tail to run -- and not merely when the scene is torn
        down. The bed itself is never stopped: the shower keeps falling
        right through the vows, just far enough under them to hear the goat.
        """
        if self._audio is None:
            return
        try:
            self._audio.duck_ambient(factor)
        except Exception as exc:
            self.log(f"wedding: could not duck the weather bed ({exc})")

    def _fade_music(self):
        if self._audio is None:
            return
        try:
            self._audio.fade_wedding_music()
        except Exception as exc:
            self.log(f"wedding: could not fade the march ({exc})")

    # ======================================================================
    # The envelope
    # ======================================================================
    @property
    def cast_alpha(self):
        """0..1 for the whole cast together: up over
        WEDDING_FADE_IN at the start, held, down over WEDDING_FADE_OUT at
        the finale."""
        t = self.t
        held = config.WEDDING_OPACITY
        if t < config.WEDDING_FADE_IN:
            return held * max(0.0, t / max(1e-6, config.WEDDING_FADE_IN))
        if t >= config.WEDDING_FADE_OUT_AT:
            gone = (t - config.WEDDING_FADE_OUT_AT) / max(1e-6, config.WEDDING_FADE_OUT)
            return held * max(0.0, 1.0 - gone)
        return held

    @property
    def finished(self):
        return self.t >= config.WEDDING_TOTAL

    # ======================================================================
    # Artwork
    # ======================================================================
    def draw(self, surface, renderer):
        """Drawn AFTER the sun and the rain, in a pass of its own.

        The cast never goes on the half-res artwork layer. It is queued on
        the renderer's full-resolution foreground, which is composited after
        the weather has been upscaled -- see Renderer.new_foreground. Drawn
        into the artwork layer, the sprites were smoothscaled to half size
        and then blown back up 2x nearest-neighbour with the rain, which is
        what made their edges soft and blocky.

        Nothing here is dimmed by config.OVERLAY_ALPHA either: that is the
        weather's global dimming, applied in flush_overlay, and the cast is
        NOT weather. That is what keeps the characters solid instead of
        see-through.
        """
        if not (self.available and self.active):
            return
        alpha = self.cast_alpha
        if alpha <= 0.004:
            return

        # Back to front: the arch is scenery behind everyone, the priest
        # stands under it, the couple in front of him, and the four crowd
        # groups are the congregation nearest the camera.
        layer = renderer.new_foreground()
        self._draw_slot(layer, renderer, "arch", alpha)
        self._draw_slot(layer, renderer, "priest", alpha)
        self._draw_cast(layer, renderer, alpha)
        self._draw_crowds(layer, renderer, alpha)

        if self._hearts:
            if self._heart_sprite is not None:
                self._draw_heart_sprites(layer, renderer, alpha)
            else:
                # Primitives, so they are drawn on the art scratch -- see
                # note 3 at the top of renderer.py -- and that corner of it
                # is queued in front of the cast.
                art = renderer.new_art()
                area = renderer.draw_hearts(art, self._hearts, alpha)
                renderer.queue_art(layer, art, area)

    def _draw_heart_sprites(self, layer, renderer, alpha):
        """hearts.png, one blit each. The size cache is keyed by the heart's
        rounded size so a handful of variants are reused rather than a new
        scaled copy per heart per frame."""
        for heart in self._hearts:
            size = round(heart["size"], 3)
            renderer.draw_sprite(layer, f"hearts@{size}", self._heart_sprite,
                                 (heart["x"], heart["y"]), size,
                                 alpha=heart["alpha"] * alpha)

    def _draw_cast(self, layer, renderer, alpha):
        """The couple, one way or the other.

        _couple_vis eases 0..1 as the composite takes over, so the separate
        bride and groom fade out exactly as it fades in instead of all three
        being on screen at full strength for a frame.
        """
        separate = alpha * (1.0 - self._couple_vis)
        together = alpha * self._couple_vis
        if separate > 0.004:
            self._draw_slot(layer, renderer, "bride", separate)
            self._draw_slot(layer, renderer, "groom", separate)
        if together > 0.004:
            self._draw_slot(layer, renderer, "couple", together)

    def _draw_crowds(self, layer, renderer, alpha):
        """All four groups, bobbing while they are cheering.

        The bob is what turns a still picture of people mid-cheer into
        people jumping, and it is keyed off the sprite name rather than off
        a flag so it can never disagree with what is on screen. Each group
        gets its own phase from config: four rows leaving the ground on the
        same frame read as one sprite drawn four times, not as a crowd.
        """
        for slot in config.WEDDING_CROWD_SLOTS:
            key = self._slots.get(slot)
            offset = (0.0, 0.0)
            if key and key.endswith("_cheer"):
                phase = (self.t * config.WEDDING_CROWD_BOB_HZ * math.tau
                         + config.WEDDING_CROWD_BOB_PHASE.get(slot, 0.0))
                offset = (0.0, -abs(math.sin(phase)) * config.WEDDING_CROWD_BOB)
            self._draw_slot(layer, renderer, slot, alpha, offset=offset)

    def _draw_slot(self, layer, renderer, slot, alpha, offset=(0.0, 0.0)):
        """One slot, cross-fading out of whatever pose it was holding."""
        # A timeline "move" wins over the slot's standing mark -- that is
        # how the bride and groom step together for the lone-kiss beat.
        pos = self._moves.get(slot) or config.WEDDING_LAYOUT[slot]
        height = config.WEDDING_HEIGHT[slot]
        anchor = config.WEDDING_ANCHOR.get(slot, "center")

        fade = self._fading.get(slot)
        if fade is None:
            self._blit(layer, renderer, self._slots.get(slot), pos, height,
                       alpha * 255, anchor, offset)
            return
        previous, remaining = fade
        mix = 1.0 - max(0.0, remaining) / max(1e-6, config.WEDDING_CROSSFADE)
        self._blit(layer, renderer, previous, pos, height,
                   alpha * 255 * (1.0 - mix), anchor, offset)
        self._blit(layer, renderer, self._slots.get(slot), pos, height,
                   alpha * 255 * mix, anchor, offset)

    def _blit(self, layer, renderer, key, pos, height, alpha, anchor, offset):
        """A key with no sprite behind it -- a PNG that was missing at load
        -- simply draws nothing, which is the whole of the fallback."""
        sprite = self._sprites.get(key) if key else None
        if sprite is None:
            return
        renderer.draw_sprite(layer, key, sprite, pos, height,
                             alpha=alpha, anchor=anchor, offset=offset)
