"""
Audio layer: ambient weather loops and event-driven one-shots.

=============================================================================
 WHAT THIS MODULE IS ALLOWED TO DO
=============================================================================
 Play sound. That is the entire remit. It touches no hardware, no power
 mode, no fan, no brightness and no warm-up, and it must never be able to
 affect them: every entry point below is wrapped so that a missing file, a
 machine with no audio device, or a mixer that refuses to start degrades to
 silence rather than taking the weather down with it. See the safety rule at
 the top of config.py -- audio is not one of the real effects, and adding
 one here is forbidden.
=============================================================================

HOW IT IS WIRED

  * AMBIENT is one dedicated channel carrying one infinite loop. The engine
    starts it in start_weather() and stops it in end_weather(), next to the
    effects.idle() call, so silence and "hands off the machine" happen on
    exactly the same edge. Idle is silent by design.

    Clip length is irrelevant on purpose. Events are random and run from a
    few seconds to several minutes, so the ambient plays with loops=-1 and
    is stopped by the scene lifecycle, never by the end of the file. A 47s
    wind bed under a 5-minute front simply loops six times.

  * ONE-SHOTS are driven by the moments the SCENES already decide on --
    rainy's lightning strike and windy's gust rising edge -- not by a second
    set of timers in here. That keeps sound and picture in sync for free,
    and means the number of thunderclaps is however many times lightning
    randomly struck. Each cue owns its own channel, so thunder layers over
    rain instead of interrupting it, and a re-trigger fades the previous
    copy of the SAME cue rather than stacking a second one on top of it.

  * THUNDER_DELAY exists because light arrives before sound. The delay is
    counted down in update() off the engine's own dt rather than on a timer
    thread -- one less thread to have to shut down.

  * THE WEDDING (wedding.py, sunshower only) rides on the same two shapes
    and adds nothing new. Its march is a BED -- one dedicated music channel,
    loops=-1, started and stopped by the ceremony -- and its bells, vows and
    crowd cheer are one-shots on four more dedicated channels. Every one of
    those channels is reserved off the front of the pool alongside the
    weather's, so a thunderclap and a wedding bell can never land on the
    same channel and cut each other off. While the march plays, the ambient
    bed underneath it is DUCKED (WEDDING_RAIN_DUCK) rather than stopped, so
    the rain keeps falling audibly under the vows; the duck is lifted in
    stop_wedding(). Wedding levels live in AUDIO_CUE_VOLUME like everything
    else, which is what makes Ctrl+Shift+M reach them for free.

MUTE vs. DISABLE
  Muting drops every channel to zero volume but leaves the loop running, so
  Ctrl+Shift+M is instant and unmuting picks the storm back up mid-stride.
  Disabling (config.AUDIO_ENABLED, tray) actually stops playback.
"""

import sys
from pathlib import Path

import config


def _sound_dir():
    """Where the clips live, in dev and inside a frozen one-file build.

    PyInstaller unpacks the bundle to a temporary directory and points
    sys._MEIPASS at it, so config.SOUND_DIR -- which is derived from the
    source tree -- is the wrong answer there.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "assets" / "sounds"
    return config.SOUND_DIR


def _music_dir():
    """Same story as _sound_dir, for the one clip that is a music bed rather
    than a sound effect."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "assets" / "music"
    return config.MUSIC_DIR


class Audio:
    """Owns the mixer, the loaded clips, and every channel the app uses.

    Safe to construct, call and shut down on a machine with no sound card:
    `available` stays False and every method below turns into a no-op.
    """

    def __init__(self, log=print):
        self.log = log
        self.available = False
        self.enabled = config.AUDIO_ENABLED
        self.muted = False

        self._pygame = None
        self._sounds = {}          # cue name -> Sound, only for clips that loaded
        self._missing = []         # cue names whose file was absent or unreadable

        self._ambient_channel = None
        self._oneshot_channels = {}   # cue name -> Channel; weather AND wedding
        self._music_channel = None    # the wedding march, and nothing else
        self._music_cue = None        # what that channel is carrying, or None

        # What the ambient channel is currently carrying, so a repeated
        # play_ambient() for the scene already playing is a no-op rather
        # than a restart from the top of the clip.
        self._ambient_cue = None
        self._ambient_gain = 1.0
        # Multiplied into the ambient level on top of _ambient_gain, so the
        # wedding can quieten the rain without touching the scene's own
        # balance -- and lifting the duck restores exactly what was there.
        self._ambient_duck = 1.0

        # cue -> seconds remaining before it fires; see update().
        self._pending = {}

    # ======================================================================
    # Setup
    # ======================================================================
    def pre_init(self, pygame):
        """Configure the mixer BEFORE pygame.init() brings it up.

        pygame.init() initialises the mixer with whatever defaults are in
        force at that moment, and re-initialising it afterwards means
        tearing down a device we just opened. pre_init is the supported way
        to ask for a smaller buffer -- the default is large enough that a
        thunderclap can land audibly late behind its own flash.
        """
        self._pygame = pygame
        try:
            pygame.mixer.pre_init(
                frequency=config.AUDIO_FREQUENCY,
                size=-16,
                channels=2,
                buffer=config.AUDIO_BUFFER,
            )
        except Exception as exc:
            self.log(f"audio: pre_init failed ({exc}); continuing with defaults")

    def start(self):
        """Bring up the mixer and load every clip. Never raises."""
        if not config.AUDIO_ENABLED:
            self.enabled = False
            self.log("audio: disabled by config")
            return
        pygame = self._pygame
        if pygame is None:
            self.log("audio: pre_init was never called -- running silent")
            return

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.set_num_channels(config.AUDIO_CHANNELS)
            # Reserve ours off the front of the pool so nothing that calls a
            # bare Sound.play() can ever be handed the ambient channel and
            # cut the weather bed off mid-event.
            #
            # The wedding's four one-shots and its march sit in the same
            # reserved block, each on a channel of its own. That is the
            # point: weather audio and wedding audio overlap by design (rain
            # keeps falling all the way through the ceremony), so sharing a
            # channel between them would mean a bell silencing the rain, or
            # a gust cutting the vows.
            cues = tuple(config.ONESHOT_CUES) + tuple(config.WEDDING_ONESHOT_CUES)
            pygame.mixer.set_reserved(2 + len(cues))
            self._ambient_channel = pygame.mixer.Channel(0)
            self._oneshot_channels = {
                cue: pygame.mixer.Channel(1 + i) for i, cue in enumerate(cues)
            }
            self._music_channel = pygame.mixer.Channel(1 + len(cues))
            self.available = True
        except Exception as exc:
            # No device, no driver, or a driver that refuses the format.
            # Logged exactly once, here, and then never mentioned again.
            self.available = False
            self.log(f"audio: unavailable, running silent ({exc})")
            return

        self._load_all()
        got = self._pygame.mixer.get_init()
        missing = f", missing: {', '.join(self._missing)}" if self._missing else ""
        self.log(f"audio: mixer {got[0]}Hz {abs(got[2])}ch, "
                 f"{config.AUDIO_CHANNELS} channels, "
                 f"{len(self._sounds)} clip(s) loaded{missing}")

    def _load_all(self):
        """Load every configured cue once. A missing file is a warning and
        an absent cue, not an error: the app has to run with zero, some, or
        all of the clips present."""
        sounds = _sound_dir()
        for cue, filename in config.AUDIO_FILES.items():
            self._load_one(cue, sounds / filename)
        # The wedding cues, on the same terms. The march lives in
        # assets/music rather than assets/sounds because it is a long piece
        # of music and not a sound effect; it is otherwise an ordinary clip.
        for cue, filename in config.WEDDING_AUDIO_FILES.items():
            self._load_one(cue, sounds / filename)
        self._load_one(config.WEDDING_MUSIC_CUE,
                       _music_dir() / config.WEDDING_MUSIC_FILE)

    def _load_one(self, cue, path):
        """One clip, and never an exception. Absent or unreadable simply
        means that cue is silent for the life of the process."""
        if not path.is_file():
            self._missing.append(cue)
            self.log(f"audio: {cue} missing ({path.name}) -- that cue is silent")
            return
        try:
            self._sounds[cue] = self._pygame.mixer.Sound(str(path))
        except Exception as exc:
            self._missing.append(cue)
            self.log(f"audio: {cue} failed to load ({exc}) -- that cue is silent")

    # ======================================================================
    # Volume
    # ======================================================================
    def _volume(self, cue, gain=1.0):
        """Master x per-cue balance x caller's gain, or zero when silenced."""
        if self.muted or not self.enabled:
            return 0.0
        level = (config.AUDIO_MASTER_VOLUME
                 * config.AUDIO_CUE_VOLUME.get(cue, 1.0)
                 * gain)
        return max(0.0, min(1.0, level))

    def _refresh_volumes(self):
        """Re-apply volumes in place, so mute takes effect on a running loop
        without restarting it."""
        if not self.available:
            return
        try:
            if self._ambient_cue is not None:
                self._ambient_channel.set_volume(self._ambient_level())
            for cue, channel in self._oneshot_channels.items():
                if channel.get_busy():
                    channel.set_volume(self._volume(cue))
            # The march is a loop, so unlike a one-shot it is still playing
            # long after it started and MUST be re-levelled here -- this is
            # the line that makes Ctrl+Shift+M silence the wedding music.
            if self._music_cue is not None and self._music_channel is not None:
                self._music_channel.set_volume(self._volume(self._music_cue))
        except Exception as exc:
            self.log(f"audio: volume update failed ({exc})")

    def _ambient_level(self):
        """The bed's level, including whatever is currently ducking it."""
        return self._volume(self._ambient_cue,
                            self._ambient_gain * self._ambient_duck)

    # ======================================================================
    # Ambient loops
    # ======================================================================
    def play_ambient(self, cue, gain=1.0):
        """Start `cue` looping forever, fading in.

        loops=-1 is the whole point: the event decides when this stops, the
        length of the clip never does.
        """
        if not (self.available and self.enabled):
            return
        if cue is None or cue not in self._sounds:
            # No mapping, or the file was absent -- this scene is just quiet.
            self.stop_ambient()
            return
        if cue == self._ambient_cue and self._ambient_channel.get_busy():
            # Already running: only the balance can have changed.
            self._ambient_gain = gain
            self._refresh_volumes()
            return
        try:
            self._ambient_cue = cue
            self._ambient_gain = gain
            # stop() before set_volume() is load-bearing, not tidiness.
            # SDL_mixer implements fadeout by ramping the channel volume down
            # and restoring the pre-fade level when the ramp expires -- and
            # that restore lands on whatever is playing by then. Setting a
            # new volume while an old fadeout is still in flight therefore
            # gets silently overwritten a second later, which showed up as
            # every scene playing at the PREVIOUS scene's level (sunshower at
            # full rain, most visibly). Halting first forces the restore to
            # happen now, so the level set below is the one that survives.
            self._ambient_channel.stop()
            self._ambient_channel.set_volume(self._ambient_level())
            self._ambient_channel.play(
                self._sounds[cue], loops=-1, fade_ms=config.AUDIO_FADE_MS)
            trim = f" at {gain:.2f} gain" if gain != 1.0 else ""
            self.log(f"audio: ambient {cue} looping{trim}")
        except Exception as exc:
            self._ambient_cue = None
            self.log(f"audio: could not start ambient {cue} ({exc})")

    def play_ambient_for_scene(self, scene_name):
        """Resolve a scene to its bed and start it. Unmapped scenes go quiet."""
        cue = config.AMBIENT_BY_SCENE.get(scene_name)
        gain = config.AMBIENT_SCENE_GAIN.get(scene_name, 1.0)
        self.play_ambient(cue, gain)

    def stop_ambient(self):
        """Fade the bed out. Called on every return to idle and every exit."""
        if not self.available or self._ambient_cue is None:
            self._ambient_cue = None
            return
        try:
            self._ambient_channel.fadeout(config.AUDIO_FADE_MS)
            self.log(f"audio: ambient {self._ambient_cue} fading out")
        except Exception as exc:
            self.log(f"audio: could not stop ambient ({exc})")
        self._ambient_cue = None
        self._ambient_gain = 1.0
        self._ambient_duck = 1.0

    def duck_ambient(self, factor):
        """Hold the weather bed down while something else is talking.

        The wedding uses this so the vows are audible over the rain instead
        of stopping the rain, which would be a much bigger tell than a
        quieter shower. It is a separate multiplier from _ambient_gain on
        purpose: lifting the duck restores the scene's own balance exactly,
        with no need to remember what that balance was.
        """
        factor = max(0.0, min(1.0, float(factor)))
        if factor == self._ambient_duck:
            return
        self._ambient_duck = factor
        self._refresh_volumes()
        self.log(f"audio: ambient ducked to {factor:.2f}"
                 if factor < 1.0 else "audio: ambient duck lifted")

    def stop_oneshots(self):
        """Let the last clap roll away with the rain rather than across the
        next scene.

        maxtime keeps a one-shot to about four seconds, which is easily long
        enough to outlive the event that fired it: forcing rainy -> sunshower
        mid-clap put a thunderclap over a sunny sky, which is exactly the
        thing sunshower disables lightning to avoid. Anything still queued
        behind THUNDER_DELAY is dropped outright -- the strike it belonged to
        is already over.
        """
        self._pending.clear()
        if not self.available:
            return
        try:
            for channel in self._oneshot_channels.values():
                if channel.get_busy():
                    channel.fadeout(config.AUDIO_FADE_MS)
        except Exception as exc:
            self.log(f"audio: could not stop one-shots ({exc})")

    # ======================================================================
    # One-shots
    # ======================================================================
    def play_oneshot(self, cue):
        """Fire a one-shot, honouring its configured delay.

        Called from the scenes at the exact moment the visual event happens,
        so the count and the timing are whatever the scene's own randomness
        produced.
        """
        if not (self.available and self.enabled):
            return
        if cue not in self._sounds:
            return
        delay = config.ONESHOT_DELAY.get(cue, 0.0)
        if delay > 0:
            # A second strike inside the delay window replaces the pending
            # one rather than queueing a double clap.
            self._pending[cue] = delay
            return
        self._fire(cue)

    def _fire(self, cue):
        """Actually put the clip on its own channel.

        thunder.mp3 and gust.mp3 are 11-12s, which is long for a one-shot,
        so two things keep them tidy: each cue owns a channel (a re-trigger
        replaces its own previous copy, and only its own), and maxtime clips
        playback to the punchy front of the clip.

        A re-trigger halts the previous copy and fades the NEW one in over
        ONESHOT_CUT_FADE_MS, rather than fading the old one out and starting
        the new one on top. Fading the old one out reads better on paper but
        does nothing here -- play() on the same channel kills it in the same
        frame anyway -- and it leaves a pending volume restore in flight
        that clobbers the level we set. See the note in play_ambient; the
        symptom there was thunder still audible after Ctrl+Shift+M.
        """
        channel = self._oneshot_channels.get(cue)
        sound = self._sounds.get(cue)
        if channel is None or sound is None:
            return
        try:
            replacing = channel.get_busy()
            channel.stop()
            channel.set_volume(self._volume(cue))
            channel.play(sound, loops=0,
                         maxtime=int(config.ONESHOT_MAXTIME_MS.get(cue, 0)),
                         fade_ms=config.ONESHOT_CUT_FADE_MS if replacing else 0)
            self.log(f"audio: one-shot {cue}")
        except Exception as exc:
            self.log(f"audio: one-shot {cue} failed ({exc})")

    def update(self, dt):
        """Advance pending delayed one-shots. Driven by the engine's dt so
        there is no timer thread to leak or to shut down."""
        if not self._pending:
            return
        for cue in list(self._pending):
            self._pending[cue] -= dt
            if self._pending[cue] <= 0:
                del self._pending[cue]
                self._fire(cue)

    # ======================================================================
    # The wedding easter egg (wedding.py). Sound only, like everything here.
    # ======================================================================
    def play_wedding_music(self):
        """Start the march looping on its own channel.

        loops=-1 for the same reason the weather beds use it: the ceremony
        decides when this stops, and the length of the clip never does. It
        does not touch the ambient channel, so the shower keeps falling
        underneath -- the ceremony ducks that separately.
        """
        if not (self.available and self.enabled):
            return
        cue = config.WEDDING_MUSIC_CUE
        sound = self._sounds.get(cue)
        if sound is None or self._music_channel is None:
            return
        try:
            self._music_cue = cue
            # stop() before set_volume(), for the reason spelled out at
            # length in play_ambient: a fadeout still in flight restores the
            # pre-fade level a second later and clobbers whatever we set.
            self._music_channel.stop()
            self._music_channel.set_volume(self._volume(cue))
            self._music_channel.play(
                sound, loops=-1, fade_ms=config.WEDDING_MUSIC_FADE_IN_MS)
            self.log("audio: wedding march looping")
        except Exception as exc:
            self._music_cue = None
            self.log(f"audio: could not start the wedding march ({exc})")

    def fade_wedding_music(self, fade_ms=None):
        """Ride the march out under the finale, rather than cutting it."""
        if not self.available or self._music_cue is None:
            self._music_cue = None
            return
        try:
            self._music_channel.fadeout(
                int(config.WEDDING_MUSIC_FADE_OUT_MS if fade_ms is None else fade_ms))
            self.log("audio: wedding march fading out")
        except Exception as exc:
            self.log(f"audio: could not fade the wedding march ({exc})")
        self._music_cue = None

    def stop_wedding(self):
        """Everything the ceremony started, silent NOW, and the duck lifted.

        Called from WeddingCeremony.stop(), which the sunshower scene calls
        from its own stop() -- so this runs on the end of the event, on a
        forced scene change, on Ctrl+Shift+Q and on shutdown. stop_all()
        covers the same ground with a blunter instrument; this one exists so
        the ceremony can end cleanly without silencing the weather too.
        """
        self._ambient_duck = 1.0
        if not self.available:
            self._music_cue = None
            return
        try:
            if self._music_channel is not None:
                self._music_channel.stop()
            for cue in config.WEDDING_ONESHOT_CUES:
                self._pending.pop(cue, None)
                channel = self._oneshot_channels.get(cue)
                if channel is not None:
                    channel.stop()
        except Exception as exc:
            self.log(f"audio: could not stop the wedding ({exc})")
        self._music_cue = None
        self._refresh_volumes()
        self.log("audio: wedding cues stopped")

    # ======================================================================
    # Runtime switches -- hotkey and tray
    # ======================================================================
    def toggle_mute(self):
        self.set_mute(not self.muted)
        return self.muted

    def set_mute(self, muted):
        """Instant silence that keeps the loop running underneath, so
        unmuting during a Q&A drops straight back into the weather."""
        self.muted = bool(muted)
        self._refresh_volumes()
        self.log(f"audio: {'muted' if self.muted else 'unmuted'}")

    def set_enabled(self, enabled):
        """Harder than mute: stops playback outright. The caller restarts
        the bed for whatever is on screen when it comes back on."""
        self.enabled = bool(enabled)
        config.AUDIO_ENABLED = self.enabled
        if not self.enabled:
            self.stop_all()
        self.log(f"audio: sound effects {'on' if self.enabled else 'off'}")

    def resume_scene(self, scene_name):
        """Re-start the bed for a scene already in progress -- used when
        sound is switched back on mid-event."""
        if self.enabled and scene_name is not None:
            self.play_ambient_for_scene(scene_name)

    # ======================================================================
    # Teardown
    # ======================================================================
    def stop_all(self):
        """Everything quiet, immediately -- weather AND wedding.

        mixer.stop() halts every channel in the pool, the reserved ones
        included, so the march and the crowd cheer die here along with the
        rain. This is the path Ctrl+Shift+Q takes (request_exit -> shutdown
        -> audio.shutdown -> here), which is why nothing can outlive a quit.
        """
        self._pending.clear()
        self._ambient_cue = None
        self._ambient_duck = 1.0
        self._music_cue = None
        if not self.available:
            return
        try:
            self._pygame.mixer.stop()
        except Exception as exc:
            self.log(f"audio: stop_all failed ({exc})")

    def shutdown(self):
        """Idempotent, and runs on every exit path. Nothing may still be
        playing after the window is gone."""
        self.stop_all()
        if not self.available:
            return
        try:
            self._pygame.mixer.quit()
        except Exception as exc:
            self.log(f"audio: mixer quit failed ({exc})")
        self.available = False
        self._sounds.clear()
        self.log("audio: stopped")
