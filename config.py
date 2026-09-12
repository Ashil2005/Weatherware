"""
Weatherware — central configuration.

Every tunable in the project lives here. Nothing else should hardcode a
duration, probability, or colour.

=============================================================================
 SAFETY RULE  (project-wide, revised 2026-09-03)
=============================================================================
 ALLOWED real effects, and nothing beyond this list:

  1. Screen brightness, bounded by BRIGHTNESS_FLOOR / BRIGHTNESS_CEILING and
     restored on every exit path.

  2. The Windows Power Mode overlay (power.py) -- the same user-facing slider
     as Settings > System > Power & battery. No admin rights, and it does NOT
     disable or reprogram the firmware fan curve, so the embedded controller
     keeps full authority over cooling and thermal protection stays intact.
     The original mode is captured at startup and restored when returning to
     idle AND on every exit path.

  2b. The Lenovo smart-fan PRESET (lenovo.py), via SetSmartFanMode with a
     value of 1, 2 or 3 only -- the same selector as pressing Fn+Q. This is
     what actually moves the fan on a LOQ. It is temporary, non-persistent,
     captured at startup and restored on idle and on every exit path. Custom
     fan curves, custom RPMs, full-speed values, fan unlocks and BIOS writes
     are forbidden; every preset leaves Lenovo's own fan curve in charge.

  3. A gentle, time-boxed CPU warm-up during sunny only (warmup.py): a
     partial load on at most cpu_count // 4 threads, hard-stopped after
     WARMUP_MAX_SECONDS, and stopped immediately above WARMUP_TEMP_CAP where
     a temperature is readable. It may only ever make the laptop mildly
     warm. Off with WARMUP_ENABLED = False.

 STILL FORBIDDEN, permanently:
  * Direct fan or embedded-controller access, custom fan curves, custom RPMs,
    full-speed/fan-unlock values, and vendor fan-control drivers.
  * Disabling, overriding or reprogramming thermal protection or fan curves,
    and any BIOS/firmware write.
  * Saturating the CPU/GPU, or any heat load without a hard time cap.
  * Anything that stops the machine from cooling itself.

 Previous versions of this file banned CPU load outright and faked all heat.
 Items 2 and 3 above replace that; the fake heat gauge is gone.
=============================================================================
"""

import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
# Every asset path is derived from ROOT_DIR. A PyInstaller one-file build
# unpacks itself to a temporary directory and points sys._MEIPASS at it, so
# resolve against that when frozen and against the source tree otherwise.
# (audio._sound_dir and wedding._image_dir make the same check, so both
# routes land in the same place.)
ROOT_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
ASSETS_DIR = ROOT_DIR / "assets"
IMAGE_DIR = ASSETS_DIR / "images"
SOUND_DIR = ASSETS_DIR / "sounds"
MUSIC_DIR = ASSETS_DIR / "music"

# --------------------------------------------------------------------------
# App identity
# --------------------------------------------------------------------------
APP_NAME = "Weatherware"
APP_TAGLINE = "Local forecast for your desktop. Accuracy not included."
VERSION = "0.3.0"

# --------------------------------------------------------------------------
# Timing (seconds). --demo and --fast override these at startup.
# --------------------------------------------------------------------------
IDLE_MIN = 20.0            # shortest quiet gap between weather events
IDLE_MAX = 60.0            # longest quiet gap between weather events
WEATHER_DURATION = 150.0   # 2.5 minutes of weather per event

FADE_IN = 4.0              # ramp-up at the start of an event
FADE_OUT = 5.0             # ramp-down at the end of an event

# --------------------------------------------------------------------------
# Weather probabilities (must sum to 1.0 -- asserted at import)
# --------------------------------------------------------------------------
SUNNY = 0.30
RAINY = 0.30
WINDY = 0.15
SNOWY = 0.15
SUNSHOWER = 0.10

WEATHER_WEIGHTS = {
    "sunny": SUNNY,
    "rainy": RAINY,
    "windy": WINDY,
    "snowy": SNOWY,
    "sunshower": SUNSHOWER,
}

# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------
# False = transparent, click-through, always-on-top overlay (the real thing).
# True  = a normal opaque 900x600 window for development and screenshots.
WINDOWED_MODE = False

WINDOW_SIZE = (900, 600)   # used only when WINDOWED_MODE is True
FPS = 60

# --------------------------------------------------------------------------
# TRANSPARENT CLICK-THROUGH OVERLAY
# --------------------------------------------------------------------------
# The overlay uses UpdateLayeredWindow with TRUE per-pixel alpha, not a
# colour key. A colour-keyed window (LWA_COLORKEY + LWA_ALPHA) was built and
# measured first and could not meet the goal: that API gives only two states
# per pixel, so at alpha 180 every painted pixel cuts the desktop underneath
# to 29% brightness no matter how faint the art is. A "light" tint became a
# grey blanket over real work, and every soft edge -- corona, clouds,
# vignette -- ended in a hard ring. See the long note in overlay.py.
#
# With per-pixel alpha a 20%-alpha tint really is 20%, so the tints below can
# stay genuinely subtle.
# The global multiplier on top of per-pixel alpha for WEATHER artwork.
#
# This used to be the layered window's own SourceConstantAlpha, which meant
# it capped every pixel the app could ever draw -- and that is why the
# wedding cast (renderer.draw_sprite, flushed by wedding.py) looked like a
# ghost: 100% opaque art still arrived on screen at 70%. It is now applied
# by Renderer.flush_overlay instead, so the weather is dimmed exactly as
# before while a foreground layer can ask for full opacity. The window
# composites at OVERLAY_WINDOW_ALPHA, which is why that is 255.
OVERLAY_ALPHA = 180
OVERLAY_WINDOW_ALPHA = 255   # leave at 255; see OVERLAY_ALPHA above
TOPMOST_INTERVAL = 2.0    # seconds between re-asserting always-on-top

COLOR_BG = (14, 17, 24)   # windowed dev mode only
COLOR_TEXT = (232, 236, 244)
COLOR_MUTED = (128, 138, 158)
COLOR_SUNNY = (255, 204, 92)
COLOR_RAINY = (108, 158, 214)
COLOR_SUNSHOWER = (186, 148, 226)
COLOR_WINDY = (176, 198, 212)
COLOR_SNOWY = (200, 222, 242)

# --------------------------------------------------------------------------
# On-screen text: there is almost none by design. The overlay is pure
# weather. The single exception is a small, low-opacity reminder of the
# global stop hotkey -- a safety affordance, since the window is
# click-through and can never be focused or closed by hand.
# --------------------------------------------------------------------------
SHOW_ESCAPE_HINT = True
ESCAPE_HINT = "Ctrl+Shift+Q to clear the weather"
ESCAPE_HINT_ALPHA = 96

# --------------------------------------------------------------------------
# Global hotkeys. The overlay never takes focus, so EVERY control has to be
# a global hotkey or a tray menu item.
# --------------------------------------------------------------------------
KILL_SWITCH_HOTKEY = "ctrl+shift+q"    # always exits cleanly
PANIC_RESTORE_HOTKEY = "ctrl+shift+r"  # restore brightness without quitting
MUTE_HOTKEY = "ctrl+shift+m"           # instant silence, without stopping the weather
FORCE_HOTKEYS = {
    "ctrl+shift+1": "sunny",
    "ctrl+shift+2": "rainy",
    "ctrl+shift+3": "sunshower",
    "ctrl+shift+4": "windy",
    "ctrl+shift+5": "snowy",
}

# --------------------------------------------------------------------------
# Brightness -- a real hardware effect. Bounded so the screen never becomes
# unusable, and restored on every exit path.
# --------------------------------------------------------------------------
BRIGHTNESS_ENABLED = True
BRIGHTNESS_FLOOR = 25      # never dim below this (%)
BRIGHTNESS_CEILING = 100   # never raise above this (%)
BRIGHTNESS_STEP = 4        # % change per tick, keeps transitions smooth
BRIGHTNESS_RESTORE_ON_EXIT = True

# The panel is SLOW. A single screen_brightness_control.set_brightness() on
# this machine is a 100-250ms round-trip through the display driver -- not
# the ~24ms an earlier note here guessed. The sunshower sweep changes the
# rounded target about ten times a second, so unthrottled it asked for more
# than a second of panel writes per second of wall clock and the render loop
# simply stopped: 9-16fps, which is what made the rain look laggy.
#
# Two things fix that, and effects.py does both:
#
#   1. This rate limit. At most one NEW value is handed to the panel every
#      BRIGHTNESS_MIN_INTERVAL seconds.
#   2. The write itself happens on a dedicated writer thread, so even a
#      250ms round-trip never blocks a frame.
#
# The floor on how fine the sweep can be is the driver, not this number:
# the panel and the overlay contend, so a write that takes ~110ms with
# nothing drawing takes 3-5s while the overlay is pushing 1920x1080 at
# 30fps, and the sweep ends up stepping every few seconds however low this
# is set. That is the intended trade -- a stepped sweep reads as weather,
# stepped rain reads as a broken app -- and 0.15s is simply low enough that
# the throttle is never the thing holding a value back.
#
# NEITHER weakens the safety contract. Every value still goes through the
# BRIGHTNESS_FLOOR..CEILING clamp before it leaves, and every restore path
# (idle, Ctrl+Shift+R, Ctrl+Shift+Q, shutdown, crash) bypasses both the rate
# limit and the thread and writes the captured original synchronously, with
# the last word on the panel. See effects.py.
BRIGHTNESS_MIN_INTERVAL = 0.15   # seconds between panel writes; 0 disables

# --------------------------------------------------------------------------
# Power Mode -- a real hardware effect. See safety item 2 and power.py.
# Sunny goes quiet and coasts warm; rain spins the fan up, which is the
# sound the storm is really made of.
# --------------------------------------------------------------------------
POWER_MODE_ENABLED = True
# Per-scene intent, resolved in effects.py. "performance" falls back to
# balanced on battery so we are not forcing a laptop to burn charge.
POWER_MODE_BY_SCENE = {
    "sunny": "efficiency",
    "rainy": "performance",
    "windy": "performance",
    "snowy": "performance",
    "sunshower": "balanced",
}

# --------------------------------------------------------------------------
# Lenovo thermal mode -- a real hardware effect. See safety item 2b and
# lenovo.py. On a Lenovo LOQ the fan follows Lenovo's own Fn+Q preset, not
# the Windows slider, so this is the one that actually moves the fan. Needs
# Administrator (see AUTO_ELEVATE); it no-ops with a warning otherwise.
# --------------------------------------------------------------------------
LENOVO_FAN_ENABLED = True

# Relaunch through the standard UAC prompt at startup when not already
# elevated (main.py). Declining the prompt is fine: the app carries on
# unelevated and only the Lenovo preset no-ops. Set False, or pass
# --no-admin, to skip the prompt during development; --selftest never
# elevates.
AUTO_ELEVATE = True
LENOVO_MODE_BY_SCENE = {
    "sunny": "quiet",
    "rainy": "performance",
    "windy": "performance",     # the fan IS the wind
    "snowy": "performance",     # cold weather, real cooling
    "sunshower": "balanced",
}

# --------------------------------------------------------------------------
# Gentle warm-up -- see safety item 3 and warmup.py. Sunny only.
# --------------------------------------------------------------------------
WARMUP_ENABLED = True
WARMUP_MAX_SECONDS = 60.0  # hard cap per event, even if sunny runs longer
WARMUP_TEMP_CAP = 75.0     # stop above this if a temperature is readable

# --------------------------------------------------------------------------
# Audio -- see audio.py.
# --------------------------------------------------------------------------
# NOT a hardware effect and not covered by the safety rule above: this is
# pygame.mixer writing to the default output device and nothing else. It is
# listed here rather than folded into the effects layer precisely so it stays
# obvious that no audio path can reach brightness, power mode, the fan or the
# warm-up. If any of it fails, the weather runs on in silence.
AUDIO_ENABLED = True
AUDIO_MASTER_VOLUME = 0.7   # 0.0-1.0, applied on top of every per-cue level

# A small buffer keeps a thunderclap tight against its own flash; the mixer
# default is big enough to hear the gap. Drop to 1024 if audio crackles.
AUDIO_FREQUENCY = 44100
AUDIO_BUFFER = 512
# Ambient + one per one-shot cue are reserved off the front of this pool,
# and the wedding easter egg reserves its own music channel plus one per
# wedding one-shot on top of that -- see WEDDING_ONESHOT_CUES. Reserving
# rather than sharing is what stops a thunderclap and a wedding bell landing
# on the same channel and cutting each other off.
AUDIO_CHANNELS = 16
AUDIO_FADE_MS = 1000        # ambient fade in and out, so scene changes glide

# cue name -> filename in assets/sounds. Any file that is absent simply
# makes that cue silent; the app runs with none, some or all of them.
AUDIO_FILES = {
    "rain": "rain.mp3",     # 50s   ambient loop
    "wind": "wind.mp3",     # 47s   ambient loop
    "snow": "snow.mp3",     # 2min  ambient loop
    "sunny": "sunny.mp3",   # 50s   ambient loop
    "thunder": "thunder.mp3",   # 11s one-shot
    "gust": "gust.mp3",         # 12s one-shot
}

# Per-cue balance, multiplied by AUDIO_MASTER_VOLUME. This is the dial to
# turn when one bed sits louder than the others.
AUDIO_CUE_VOLUME = {
    "rain": 1.0,
    "wind": 0.9,
    "snow": 0.85,
    "sunny": 0.7,      # birdsong under everything else; it should not lead
    "thunder": 1.0,
    "gust": 0.85,
}

# Scene -> ambient bed. A scene missing from here plays nothing, which is
# also what happens when its file is absent.
AMBIENT_BY_SCENE = {
    "sunny": "sunny",
    "rainy": "rain",
    "windy": "wind",
    "snowy": "snow",
    "sunshower": "rain",
}

# Sunshower borrows rainy's bed, so it needs its own level: this is the calm
# rare one, and rain at full strength under a sunny sky reads as a storm.
SUNSHOWER_AMBIENT_VOLUME = 0.5
AMBIENT_SCENE_GAIN = {
    "sunshower": SUNSHOWER_AMBIENT_VOLUME,
}

# One-shots. These are fired by the SCENES at the moments they already
# decide on -- rainy's lightning strike, windy's gust rising edge -- so
# there is deliberately no gap/interval tunable here. How often thunder
# rolls is LIGHTNING_GAP; how often the wind surges is WIND_GUST_GAP.
ONESHOT_CUES = ("thunder", "gust")

# Light before sound. 0 fires the clap on the same frame as the flash.
THUNDER_DELAY = 0.3
ONESHOT_DELAY = {
    "thunder": THUNDER_DELAY,
    "gust": 0.0,
}

# thunder.mp3 and gust.mp3 are 11-12s, which is long for a one-shot: an
# 11s roll still playing when the next bolt lands turns into mud. Cut to
# the punchy front of the clip. 0 plays the whole file.
ONESHOT_MAXTIME_MS = {
    "thunder": 4000,
    "gust": 4000,
}
# When a cue re-triggers while its own previous copy is still playing, the
# replacement fades in over this instead of starting on a hard edge.
ONESHOT_CUT_FADE_MS = 180

# --------------------------------------------------------------------------
# Artwork
# --------------------------------------------------------------------------
# Weather art renders at half resolution above ~1.4MP and is scaled up.
# Smoothing that upscale is slightly cleaner on thin rain streaks but costs
# roughly 5ms/frame at 1080p.
SMOOTH_ART_UPSCALE = False

# --- Sunny: warm, oppressive, and always moving ----------------------------
SUN_RAY_COUNT = 22         # dense, but every ray costs 10 polygons/frame
SUN_POS = (0.74, 0.19)     # fraction of screen width/height
SUN_RADIUS = 0.072         # fraction of screen height (a bigger sun)
SUN_CORONA = 5.6           # bloom diameter as a multiple of SUN_RADIUS
SUN_HOTSPOT = 13.0         # broad soft bright patch, multiple of SUN_RADIUS
SUN_PULSE_HZ = 0.15        # slow breathing of the bloom
MOTE_COUNT = 84            # drifting dust/pollen specks, on 3 depth layers

# Occasional lens-flare bloom pulse, rather than a constant flare.
FLARE_GAP = (9.0, 20.0)    # seconds between bloom pulses
FLARE_LENGTH = 3.2         # seconds for one pulse to swell and fade

# Fair-weather clouds drifting across the top of a sunny sky. These are the
# light, puffy counterpart to the storm mass in rainy: same soft-brush
# sprites, cream instead of near-black, and much fainter, because they pass
# straight over the user's work.
FAIR_CLOUD_COUNT = 11
FAIR_CLOUD_SPEED = (4.0, 12.0)   # px/sec; far slower than the storm drift
FAIR_CLOUD_ALPHA = 0.85          # multiplier on top of the sprite's own alpha

# Heat mirage -- a refractive shimmer across the bottom of the screen.
#
# The overlay canNOT bend the real desktop: those icons and that text are
# other applications' pixels, composited by the DWM below our layered
# window, and nothing we draw can displace them. So this warps OUR OWN
# band instead -- a faint warm haze plus whatever weather is painted down
# there -- by re-blitting the bottom of the composite layer as horizontal
# slabs with a sine-driven horizontal offset. Strongest at the very bottom
# edge and eased to nothing by mid-screen, so it reads as air shimmering
# over hot tarmac rather than as a wobbling rectangle.
MIRAGE_ENABLED = True
MIRAGE_HEIGHT = 0.46       # fraction of screen height, measured up from the bottom
MIRAGE_AMPLITUDE = 15.0    # px of horizontal displacement at the very bottom
MIRAGE_WAVELENGTH = 74.0   # px between wave crests, measured vertically
MIRAGE_SPEED = 2.0         # radians/sec the ripple crawls upward
MIRAGE_SLAB = 4            # screen px per displaced slab; bigger = cheaper
MIRAGE_HAZE_ALPHA = 56     # peak alpha of the warm ribbons that ripple

# --- Rainy: moody and cold ------------------------------------------------
# Parallax layers, far to near: near drops are big, fast and blurred.
RAIN_LAYERS = (
    {"count": 150, "speed": (300, 460),   "length": (6, 15),  "width": 1,
     "alpha": 60,  "parallax": 0.45, "blur": False},
    {"count": 110, "speed": (560, 780),   "length": (14, 27), "width": 2,
     "alpha": 115, "parallax": 0.75, "blur": False},
    {"count": 52,  "speed": (900, 1280),  "length": (30, 58), "width": 3,
     "alpha": 165, "parallax": 1.0,  "blur": True},
)

RAIN_LEAN = 0.075          # how hard the wind tilts each streak
WIND_BASE = -150.0         # px/sec; negative blows the rain leftward
WIND_GUST = -270.0         # extra push at the peak of a gust
GUST_GAP = (4.0, 9.5)      # seconds between gusts
GUST_LENGTH = 1.8          # seconds a gust takes to swell and fade

# Smaller sprites over a soft band, rather than a few big ones: the same
# screen coverage, a raggeder mass, and cheaper per frame -- every cloud is
# one full alpha blit, and sprite AREA is what that costs. Spawn positions
# are spread evenly across the width (see RainyScene.start), so the whole
# top band is covered rather than a few patches of it.
CLOUD_COUNT = 24
CLOUD_SPEED = (14.0, 34.0) # px/sec drift across the top

# The cloud BODY. Near-black on purpose -- but note that a near-black cloud
# over a near-black desktop is nothing at all, which is why the storm sprite
# also gets a lit top edge (Renderer.CLOUD_SHADE). The body darkens a light
# desktop; the lit edge gives it a shape on a dark one.
COLOR_STORM = (7, 10, 18)

# A soft dark band under the clouds, so the top of the screen reads as one
# continuous storm mass rather than as separate blobs. Still translucent:
# the desktop has to stay faintly visible through it.
STORM_BAND_ALPHA = 74
STORM_BAND_HEIGHT = 0.40   # fraction of screen height it fades out over

GLASS_STREAKS = 16         # rain running down the "glass" in the foreground

# Lightning. Deliberately infrequent, two soft pulses, never a strobe.
LIGHTNING_ENABLED = True
LIGHTNING_GAP = (7.0, 16.0)   # seconds between strikes
LIGHTNING_ALPHA = 165         # peak white-flash alpha (capped well under 255)
LIGHTNING_LENGTH = 0.45       # seconds for the whole flash envelope
SHAKE_AMPLITUDE = 7           # pixels of screen-shake at the strike
SHAKE_LENGTH = 0.40           # seconds for the shake to settle

# When lightning strikes, ask the Windows shell to redraw the DESKTOP, so
# the icons flicker in time with the flash. Deliberately disruptive -- but
# tightly scoped: see desktop.py. One WM_COMMAND is posted to the desktop's
# own SHELLDLL_DefView and nothing else on the machine is touched. No global
# keystroke is ever synthesised, so whatever the user is typing into is
# never disturbed.
DESKTOP_REFRESH_ON_LIGHTNING = True
DESKTOP_REFRESH_MIN_INTERVAL = 3.0   # seconds; a double-pulse strike gets one

# --------------------------------------------------------------------------
# Condensation fog -- rain fogging up the "glass", wiped clear by the cursor.
# --------------------------------------------------------------------------
# The overlay is click-through (WS_EX_TRANSPARENT), so it can never receive
# a mouse event. The cursor is instead READ each frame with GetCursorPos,
# which works fine through a layered click-through window, and the fog is
# wiped along the segment the cursor travelled. Clicks still go to the app
# underneath exactly as before -- nothing here captures input.
#
# The mask lives at 1/FOG_MASK_DIV of screen resolution and is smoothscaled
# up on the way out, which is what keeps a full-screen per-pixel effect
# affordable.
FOG_ENABLED = True
FOG_OPACITY = 130          # peak alpha of the haze; a mist, never opaque
FOG_COLOR = (223, 233, 243)
FOG_WIPE_RADIUS = 104      # screen px; the clear patch the cursor drags
FOG_REFOG_SECONDS = 8.0    # seconds for a fully wiped patch to fog back up
FOG_MASK_DIV = 4           # mask resolution divisor (4 = quarter res)

# --------------------------------------------------------------------------
# --- Windy: a blustery, fast-moving overcast ------------------------------
# --------------------------------------------------------------------------
# One direction, chosen at the start of each event, and EVERYTHING obeys it:
# clouds, leaves, streaks and the leaves' own sway all travel the same way.
# Mixed directions read as a bug, not as weather.
WINDY_BRIGHTNESS = 62       # neutral-ish; wind is not a story about light

WIND_CLOUD_COUNT = 16
WIND_CLOUD_SPEED = (150.0, 360.0)  # px/sec -- an order up from rainy's drift
WIND_BAND_ALPHA = 40               # overcast band tying the sprites together
WIND_BAND_HEIGHT = 0.34
WIND_CLOUD_ALPHA = 0.95

# Leaves are the whole tell. Everything else here is supporting texture.
LEAF_COUNT = 54
LEAF_SPEED = (250.0, 640.0)     # px/sec along the wind
LEAF_SIZE = (7.0, 18.0)         # screen px, half-length of the leaf
LEAF_SPIN = (1.4, 6.0)          # rad/sec, the flat 2D rotation
LEAF_TUMBLE = (1.8, 6.4)        # rad/sec of the edge-on flip -- see draw_leaves
LEAF_RISE = (18.0, 70.0)        # px/sec of vertical wander
LEAF_ALPHA = (150, 225)
LEAF_COLORS = (
    (196, 96, 38), (172, 120, 44), (148, 66, 32),
    (208, 148, 56), (126, 90, 38), (120, 132, 60),
)

# Faint horizontal streaks and specks: air itself, given something to catch
# the light on. Cheap -- one line each.
WIND_STREAK_COUNT = 34
WIND_STREAK_SPEED = (700.0, 1600.0)
WIND_STREAK_LENGTH = (40.0, 200.0)
WIND_STREAK_ALPHA = 52

WIND_GUST_GAP = (2.4, 6.0)  # seconds between gusts -- far busier than rain
WIND_GUST_LENGTH = 1.6
WIND_GUST_BOOST = 0.9       # extra speed multiplier at the peak of a gust

# --------------------------------------------------------------------------
# --- Snowy: cold, still, and slowly burying the bottom of the screen ------
# --------------------------------------------------------------------------
# The heaviest scene in the app: flakes plus the condensation fog plus the
# drift. Every part of it is tunable here so it can be dialled back.
SNOW_LAYERS = (
    {"count": 112, "speed": (24, 44),  "radius": (1.0, 2.1), "alpha": 108,
     "sway": (6, 18),  "sway_hz": (0.16, 0.44), "parallax": 0.45, "halo": False},
    {"count": 74,  "speed": (46, 84),  "radius": (1.8, 3.2), "alpha": 168,
     "sway": (12, 32), "sway_hz": (0.14, 0.36), "parallax": 0.75, "halo": True},
    {"count": 40,  "speed": (84, 140), "radius": (2.8, 5.0), "alpha": 218,
     "sway": (18, 46), "sway_hz": (0.10, 0.30), "parallax": 1.0,  "halo": True},
)
SNOW_WIND = -22.0          # px/sec; a gentle, steady sideways drift

# Accumulation along the bottom edge. The drift is one cached sprite with a
# wavy top, blitted with its top edge at the current depth -- everything
# below the screen is clipped, so "growing" is just moving it down.
SNOW_PILE_ENABLED = True
SNOW_PILE_MAX = 0.13       # fraction of screen height; it may never eat more
SNOW_PILE_ALPHA = 178
SNOW_PILE_BUMPS = 6        # crests across the drift's top edge
# The shaded lip along the crest. Snow is white, and white over a white
# desktop is invisible, so the drift carries its own cool shadow -- the same
# problem, and the same answer, as the cloud sprites.
SNOW_PILE_SHADE = (128, 152, 186)
SNOW_PILE_SHADE_ALPHA = 112
SNOW_PILE_EASE = 0.7       # <1 piles up quickly at first, then settles

SNOW_FOG_ENABLED = True    # the condensation layer, shared with rainy
SNOW_FOG_STRENGTH = 0.85   # multiplier on FOG_OPACITY and the re-fog rate

# ==========================================================================
# THE FOX-HEN WEDDING -- easter egg, sunshower only
# ==========================================================================
# A small wedding party plays out in the lower half of the screen while the
# rare sunshower runs: bells, a march, a ring, a blessing, a kiss and four
# rows of crowd on their feet. In Kerala a sunshower is the fox's wedding,
# which is the whole joke, so this rides on the scene that already means it
# rather than on a hotkey of its own.
#
# It is also a SURPRISE. The shower plays normally for WEDDING_START_DELAY
# seconds first, so the event opens as an ordinary sunshower and the bells
# arrive out of nowhere.
#
# VISUALS AND AUDIO ONLY. Nothing in wedding.py can reach brightness, the
# power mode, the fan preset or the warm-up; see the safety rule at the top
# of this file. The only two things it asks of the machine are that the
# sunshower event lasts long enough to finish (the delay, the ceremony and
# the tail, all added up) and that the scene's brightness sweep stays gentle
# so the cast stays visible -- and that sweep is still bounded by the same
# safe floor/ceiling as before.
#
# Every asset is optional. A missing PNG, clip or crowd row is logged once
# and skipped; with the whole assets/images/wedding folder gone the
# sunshower is exactly the sunshower it was before.
# --------------------------------------------------------------------------
WEDDING_ENABLED = True

# The shower runs plain for this long before the ceremony starts, so the
# wedding lands as a surprise rather than as part of the weather. The event
# is stretched by this much on top of the ceremony, so nothing is lost.
WEDDING_START_DELAY = 10.0

# Sprites: sprite key -> filename in assets/images/wedding.
#
# Each PNG is a subject on a wide transparent canvas, so they are cropped to
# their content on load. The crop is shared across every pose in a GROUP
# (the part of the key before the first underscore: fox, hen, goat, couple,
# crowd, crowd2, crowd3, crowd4), because cropping each pose to its own
# bounding box would resize and re-centre the character every single time it
# changed pose. `wedding_arch` is its own group of one.
WEDDING_SPRITES = {
    "fox_stand": "fox_stand.png",         # fox holding the ring
    "fox_plain": "fox_plain.png",         # fox, no ring
    "fox_kiss": "fox_kiss.png",           # leaning in
    "hen_stand": "hen_stand.png",
    "hen_kiss": "hen_kiss.png",
    "couple_hold": "couple_hold.png",
    "couple_ring": "couple_ring.png",
    "couple_kiss": "couple_kiss.png",
    "couple_celebrate": "couple_celebrate.png",
    "goat_priest": "goat_priest.png",
    "goat_bless": "goat_bless.png",
    "goat_cheer": "goat_cheer.png",
    "crowd_sit": "crowd_sit.png",
    "crowd_cheer": "crowd_cheer.png",
    "crowd2_sit": "crowd2_sit.png",
    "crowd2_cheer": "crowd2_cheer.png",
    "crowd3_sit": "crowd3_sit.png",
    "crowd3_cheer": "crowd3_cheer.png",
    "crowd4_sit": "crowd4_sit.png",
    "crowd4_cheer": "crowd4_cheer.png",
    # The decorated arch, with the title already lettered into the artwork.
    # It replaces the drawn text banner an earlier version had: pygame can
    # only lay Malayalam out glyph by glyph, and a picture of the title is
    # both prettier and one less font to go missing.
    "wedding_arch": "wedding_arch.png",
}

# Everything that can be on screen at once. `couple` is the odd one: while
# it holds a composite the separate bride and groom are hidden and it is
# shown instead, and when it is cleared they come back.
WEDDING_SLOTS = (
    "arch", "priest", "bride", "groom", "couple",
    "crowd_left", "crowd_mid_left", "crowd_mid_right", "crowd_right",
)

#: The four crowd groups, in the order they are drawn and bobbed.
WEDDING_CROWD_SLOTS = ("crowd_left", "crowd_mid_left",
                       "crowd_mid_right", "crowd_right")

# --- The front row --------------------------------------------------------
# The four crowd groups are wide sprites (their cropped artwork runs between
# 2.0 and 2.4 times as wide as it is tall), and width is not something the
# layout gets to choose: renderer.sprite_size derives it from the sprite's
# own aspect ratio, so the only lever here is the shared height.
#
# On a 16:9 screen a sprite drawn h tall comes out
#
#     w = h * aspect * (screen_h / screen_w) = h * aspect * 0.5625
#
# of the screen WIDE, so the four of them together want about 4.82 * h of
# the width. Whatever is left is split into five equal parts -- a margin at
# each end and three gaps between -- and the x-centres in WEDDING_LAYOUT are
# just the running total of that. Measured with the renderer's own integer
# sizing on the real sprites:
#
#     h       row width   each space        verdict
#     0.16    0.771       0.046 (~88px)     tidy, but small next to the couple
#     0.19    0.908       0.018 (~35px)     <- this: proportionate, still clear
#     0.20    0.963       0.008 (~14px)     cramped against the screen edges
#     0.21    1.006       none              they overlap
#
# 0.19 also keeps the top of the row at 0.781, just under the couple's 0.78
# ground line, so the seated congregation never covers the bride's hem.
# Recompute the centres if the artwork is redrawn or this height changes:
#
#     aspect  crowd 1.986 | crowd3 2.195 | crowd4 1.984 | crowd2 2.404
#     width   0.212       | 0.234        | 0.212        | 0.256
#     centre  0.124       | 0.363        | 0.603        | 0.854
WEDDING_CROWD_H = 0.19        # shared on-screen height of all four groups

# One shared ground line, so they stand on the same floor rather than in two
# stepped ranks. Every crowd slot is anchored "bottom", so this is where
# their feet go; just off the bottom edge, close to the camera.
WEDDING_CROWD_GROUND = 0.97

# Where each slot sits, as fractions of screen width/height. The party is
# stacked up the lower middle of the screen under the arch: four crowd
# groups filling the bottom edge, the couple above them, the priest above
# the couple, the arch framing the lot.
#
# The y value is a GROUND LINE for every slot anchored "bottom" below -- the
# sprite's feet, not its middle. That is what keeps the cast standing still
# when a pose swap changes a sprite's height: the couple composites are
# taller than the separate bride and groom, so centring them would jog the
# whole group up and down on every swap. bride, groom and couple therefore
# share one ground line on purpose.
#
# The arch is the exception and is anchored on its centre, because it is a
# piece of scenery hanging over the scene rather than something standing on
# the floor.
WEDDING_LAYOUT = {
    "arch":            (0.50, 0.500),
    "priest":          (0.50, 0.57),
    "bride":           (0.435, 0.78),   # faces right, so she stands LEFT of centre
    "groom":           (0.565, 0.78),   # faces left, so he stands RIGHT of centre
    "couple":          (0.50, 0.78),
    # ONE FRONT ROW. The four groups used to be stepped across two ground
    # lines (0.965 / 1.000) at two different heights, which read as four
    # unrelated pictures rather than as a congregation, and the wide sprites
    # ran into each other besides. They now share WEDDING_CROWD_GROUND and
    # WEDDING_CROWD_H, and the x-centres below are COMPUTED from each
    # group's real cropped aspect ratio so the gaps between them -- and the
    # margins at either end -- all come out the same. See the block above
    # WEDDING_CROWD_GROUND for the arithmetic.
    "crowd_left":      (0.124, WEDDING_CROWD_GROUND),
    "crowd_mid_left":  (0.363, WEDDING_CROWD_GROUND),
    "crowd_mid_right": (0.603, WEDDING_CROWD_GROUND),
    "crowd_right":     (0.854, WEDDING_CROWD_GROUND),
}
WEDDING_ANCHOR = {
    "arch": "center", "priest": "bottom", "bride": "bottom", "groom": "bottom",
    "couple": "bottom", "crowd_left": "bottom", "crowd_mid_left": "bottom",
    "crowd_mid_right": "bottom", "crowd_right": "bottom",
}

# Where the bride and groom stand for the LONE-KISS beat at t=28, when the
# couple composite is off and the two of them are leaning in separately.
# Their normal positions leave a visible gap between two figures that are
# supposed to be about to kiss, and the gap reads badly against the
# composites either side of the beat -- so for those two and a half seconds
# they step in until they nearly touch. Slot -> position, applied as an
# override by a "move" entry in the timeline and cleared by a None.
# NEAR, NOT TOUCHING. The first numbers here (0.470 / 0.530) put the two of
# them a clear 4% of the screen INSIDE one another: hen_kiss fills its whole
# crop box, and fox_kiss's own artwork starts about a tenth of a sprite width
# in from the left edge of the shared fox crop, so the drawn beaks overlapped
# by roughly 80px at 1920 wide and the beat read as one smudged figure.
#
# At 0.443 / 0.557 their visible edges stop about 0.011 of the screen apart
# (~20px at 1920) -- down from the ~0.031 they stand at normally, so it still
# reads as the two of them stepping in, but there is daylight between them.
# The pair also sits close to the width couple_kiss occupies a beat later, so
# the hand-off to the composite is a lean rather than a jump.
WEDDING_LAYOUT_KISS = {
    "bride": (0.443, 0.78),
    "groom": (0.557, 0.78),
}

# On-screen height of each slot, as a fraction of screen height. Width
# follows from the cropped sprite's own aspect ratio, which is why the
# crowds come out wide from these numbers.
#
# Everything is deliberately larger than the first cut: the cast read as
# small and remote against a full-screen shower. The crowds and the priest
# went up the most, because they were the ones that looked tiny standing
# next to the couple.
WEDDING_HEIGHT = {
    # The biggest thing on screen, and the only one that has to FRAME the
    # rest rather than stand among it. The artwork is close to square, so
    # this height also sets how wide the arch is: at 0.62 it comes out about
    # 0.34 of the screen across and 0.19..0.81 down it, which puts its
    # flowered legs either side of the couple and its lettered banner clear
    # above the priest. Smaller and it floats over their heads like a
    # picture hung on nothing.
    "arch": 0.62,
    "priest": 0.22,
    "bride": 0.23,
    "groom": 0.23,
    "couple": 0.27,
    # All four the same, so the front row is a row -- the even x-centres
    # in WEDDING_LAYOUT are computed from this one number. See the table
    # above WEDDING_CROWD_H before changing it.
    "crowd_left": WEDDING_CROWD_H,
    "crowd_mid_left": WEDDING_CROWD_H,
    "crowd_mid_right": WEDDING_CROWD_H,
    "crowd_right": WEDDING_CROWD_H,
}

# Seconds to cross-fade a pose swap. A hard cut between two poses of the
# same character reads as a glitch; a third of a second reads as a move.
WEDDING_CROSSFADE = 0.3

WEDDING_FADE_IN = 1.5        # seconds for the whole cast to appear at t=0
WEDDING_FADE_OUT = 3.0       # seconds for it to leave again
WEDDING_FADE_OUT_AT = 50.0   # when that fade begins
WEDDING_TOTAL = 53.0         # ceremony length; the event is stretched to fit

# How opaque the cast is once it has finished fading in.
#
# This is the fix for a cast that looked like a ghost. The overlay window
# used to carry OVERLAY_ALPHA as a global constant, which capped EVERY pixel
# on screen at 70% however solid it was drawn; that dimming now lives in the
# renderer and is applied to the weather only (see OVERLAY_ALPHA), so the
# wedding can be flushed at full strength and the characters read as solid
# cartoon figures standing in front of the shower rather than inside it.
# Turn it down if they ever look pasted on.
WEDDING_OPACITY = 1.0

# Sunshower borrows rainy's bed at SUNSHOWER_AMBIENT_VOLUME already; while
# the march is playing it drops to this much OF THAT, so the vows carry over
# the rain without the rain ever going away -- a sunshower you cannot hear
# is not a sunshower. Lifted at the finale by the timeline's second "duck"
# entry, so the tail of the event is light rain at full level again.
WEDDING_RAIN_DUCK = 0.45

# --- The timeline ---------------------------------------------------------
# Times are seconds from the start of the CEREMONY -- which is itself
# WEDDING_START_DELAY into the event -- and this tuple is the whole score:
# every pose change and every audio cue is one entry, fired once, in order.
# Retuning the ceremony means editing this and nothing else.
#
#   t       when it fires
#   set     slot -> sprite key (None clears the slot)
#   move    slot -> position override, or None to go back to WEDDING_LAYOUT
#   audio   a WEDDING_ONESHOT_CUES name
#   music   "start" begins the looping march, "fade" rides it out
#   duck    new multiplier on the weather bed (1.0 lifts the duck)
#   hearts  True starts the floating hearts over the couple
#   fade    True begins the cast fade-out
WEDDING_TIMELINE = (
    {"t": 0.0, "audio": "bell",                      # ring 1 of 4
     "set": {"arch": "wedding_arch",
             "bride": "hen_stand", "groom": "fox_plain", "couple": None,
             "priest": "goat_priest",
             "crowd_left": "crowd_sit", "crowd_mid_left": "crowd3_sit",
             "crowd_mid_right": "crowd4_sit", "crowd_right": "crowd2_sit"}},
    {"t": 2.0, "audio": "bell"},                     # ring 2
    {"t": 4.0, "audio": "bell"},                     # ring 3
    {"t": 6.0, "audio": "bell"},                     # ring 4
    {"t": 7.5, "music": "start", "duck": WEDDING_RAIN_DUCK},
    {"t": 9.0, "set": {"groom": "fox_stand"}},       # he has the ring now
    {"t": 13.0, "set": {"couple": "couple_ring"}},   # putting it on her hand
    {"t": 18.0, "set": {"couple": "couple_hold", "priest": "goat_bless"}},
    {"t": 18.3, "audio": "priest"},                  # "I pronounce you..."
    {"t": 24.0, "audio": "maykiss"},                 # "you may kiss the bride"
    {"t": 28.0, "set": {"couple": None, "bride": "hen_kiss", "groom": "fox_kiss",
                        "priest": "goat_priest"},    # both leaning in,
     "move": "kiss"},                                # and stepping together
    {"t": 30.5, "audio": "cheer", "hearts": True,
     "set": {"couple": "couple_kiss",
             "crowd_left": "crowd_cheer", "crowd_mid_left": "crowd3_cheer",
             "crowd_mid_right": "crowd4_cheer", "crowd_right": "crowd2_cheer"}},
    {"t": 35.0, "set": {"couple": "couple_celebrate", "priest": "goat_cheer"}},
    # The march rides out under the finale and the shower comes back up to
    # its own level, so the tail of the event is light rain again.
    {"t": WEDDING_FADE_OUT_AT, "music": "fade", "duck": 1.0, "fade": True},
)

# --- Motion ---------------------------------------------------------------
# A crowd showing its _cheer sprite bobs up and down, so it reads as jumping
# rather than as a still picture of people mid-cheer. The four groups are
# given different phases, because four rows jumping in lockstep read as one
# sprite drawn four times.
WEDDING_CROWD_BOB_HZ = 2.1
WEDDING_CROWD_BOB = 0.013      # fraction of screen height, ground to peak
WEDDING_CROWD_BOB_PHASE = {
    "crowd_left": 0.0,
    "crowd_mid_left": 2.2,
    "crowd_mid_right": 4.1,
    "crowd_right": 1.1,
}

# Hearts rising off the couple during the kiss and the cheer. Drawn, not
# loaded -- three primitives each -- unless assets/images/wedding/hearts.png
# turns up, in which case that is used instead.
WEDDING_HEARTS_ENABLED = True
WEDDING_HEART_SPRITE = "hearts.png"   # optional; drawn from primitives if absent
WEDDING_HEART_ORIGIN = (0.50, 0.62)   # they start around the couple's head
WEDDING_HEART_SPREAD = 0.06           # horizontal scatter, fraction of width
WEDDING_HEART_RATE = 2.6              # spawned per second
WEDDING_HEART_MAX = 14                # hard cap, so this can never run away
WEDDING_HEART_LIFE = (1.8, 3.2)       # seconds from spawn to gone
WEDDING_HEART_RISE = (0.055, 0.105)   # fraction of screen height per second
WEDDING_HEART_SIZE = (0.018, 0.034)   # fraction of screen height
WEDDING_HEART_SWAY = (0.008, 0.022)   # sideways wander, fraction of width
WEDDING_HEART_COLOR = (255, 126, 158)
WEDDING_HEART_ALPHA = 220

# --- What the ceremony asks of the sunshower ------------------------------
# The event has to outlast the delay AND the ceremony, or --demo (30s
# events) cuts the bride off before the ring. The scene takes the LARGER of
# delay + WEDDING_TOTAL + tail and its normal duration -- it never shortens
# one. The tail is also what keeps light rain falling for a while after the
# party has gone.
WEDDING_EVENT_TAIL = 6.0

# Sunshower's brightness sweep normally runs the full floor-to-ceiling band,
# which pulses the cast dark twice every eight seconds. During the ceremony
# the sweep is narrowed and re-centred toward the bright end. Both numbers
# are fractions of the SAME band, and the result is clamped into it, so this
# can only ever make the sweep gentler -- it cannot reach past
# BRIGHTNESS_FLOOR / BRIGHTNESS_CEILING.
WEDDING_BRIGHTNESS_AMPLITUDE = 0.35   # of the full floor..ceiling span
WEDDING_BRIGHTNESS_CENTRE = 0.72      # where the sweep sits inside that band

# The sunshower's own art, turned down further while the wedding runs, so
# the cast reads clearly instead of competing with two weather washes. The
# extra cloud stride also buys back the frame time the sprites cost. The
# weather is turned DOWN, never off: sun and rain stay at full envelope
# strength for the whole ceremony and the tail after it.
WEDDING_SUN_TINT = 0.34
WEDDING_RAIN_TINT = 0.26
WEDDING_CLOUD_THIN = 2       # extra stride on both cloud decks (1 = no change)

# --- Audio ----------------------------------------------------------------
# The march is a looping BED on a dedicated music channel, exactly like an
# ambient weather loop: the ceremony decides when it stops and the length of
# the clip never does. The other four are one-shots, each on its own
# reserved channel, so the weather's thunder and gust can never steal one
# and neither can the mixer's free-channel pool.
WEDDING_ONESHOT_CUES = ("bell", "priest", "maykiss", "cheer")
WEDDING_AUDIO_FILES = {            # in assets/sounds
    "bell": "bell.mp3",            # 2s -- rung four times
    "priest": "priest.mp3",        # "I pronounce you husband and wife"
    "maykiss": "maykiss.mp3",      # "you may kiss the bride"
    "cheer": "cheers.mp3",         # 18s crowd
}
WEDDING_MUSIC_CUE = "wedding_march"
WEDDING_MUSIC_FILE = "wedding.mp3"     # in assets/music -- Mendelssohn, long
WEDDING_MUSIC_FADE_IN_MS = 1200
WEDDING_MUSIC_FADE_OUT_MS = 3000       # matches WEDDING_FADE_OUT

# Multiplied by AUDIO_MASTER_VOLUME like every other cue, which is what
# makes Ctrl+Shift+M and the tray's sound toggle reach these for free.
WEDDING_CUE_VOLUME = {
    "bell": 0.85,
    "wedding_march": 0.55,   # a bed, under the speech -- it must not lead
    "priest": 1.0,
    "maykiss": 1.0,
    "cheer": 0.8,
}
AUDIO_CUE_VOLUME.update(WEDDING_CUE_VOLUME)

# --------------------------------------------------------------------------
# Debug
# --------------------------------------------------------------------------
DEBUG = True               # print state transitions to the console
# Per-frame timing breakdown printed about once a second (profiler.py). Off
# by default; `main.py --profile` turns it on for one run. When False the
# profiler is never imported and costs nothing.
PROFILE = False


assert abs(sum(WEATHER_WEIGHTS.values()) - 1.0) < 1e-9, (
    "Weather probabilities must sum to 1.0 "
    f"(got {sum(WEATHER_WEIGHTS.values())})"
)
assert set(WEATHER_WEIGHTS) <= set(FORCE_HOTKEYS.values()), (
    "Every weather needs a force hotkey: the overlay never takes focus, so a "
    "scene with no hotkey and no tray entry cannot be reached on purpose"
)
assert IDLE_MIN <= IDLE_MAX, "IDLE_MIN must not exceed IDLE_MAX"
assert 0 <= BRIGHTNESS_FLOOR <= BRIGHTNESS_CEILING <= 100, (
    "Brightness bounds must satisfy 0 <= FLOOR <= CEILING <= 100"
)
assert 0.0 <= BRIGHTNESS_MIN_INTERVAL <= 2.0, (
    "The brightness write interval is a throttle, not a schedule: it may not "
    "be negative, and holding a value back for more than a couple of seconds "
    "would turn the sweep into a staircase"
)
assert 0 < LIGHTNING_ALPHA <= 200, (
    "Keep the lightning flash well under full white -- it must stay tasteful"
)
assert LIGHTNING_GAP[0] >= 3.0, (
    "Lightning must stay occasional; a short gap risks a strobing effect"
)
assert RAIN_LAYERS, "At least one rain layer is required"
assert 0 < WARMUP_MAX_SECONDS <= 120, (
    "The warm-up must stay short and self-limiting (<= 120s per event)"
)
assert 40 <= WARMUP_TEMP_CAP <= 85, (
    "The warm-up temperature cap must stay well below any thermal limit"
)
assert 0 < OVERLAY_ALPHA <= 255, "OVERLAY_ALPHA must be 1-255"
assert 0 < OVERLAY_WINDOW_ALPHA <= 255, "OVERLAY_WINDOW_ALPHA must be 1-255"
assert 0.0 < SNOW_PILE_MAX <= 0.25, (
    "The snow drift must stay a drift -- it may never climb the screen"
)
assert SNOW_LAYERS, "At least one snow layer is required"
assert BRIGHTNESS_FLOOR <= WINDY_BRIGHTNESS <= BRIGHTNESS_CEILING, (
    "WINDY_BRIGHTNESS has to sit inside the safe brightness band"
)
assert 0 < FOG_OPACITY <= 190, (
    "The condensation has to stay a haze -- work underneath must stay legible"
)
assert FOG_REFOG_SECONDS >= 1.0, (
    "Fog that returns instantly is unwipeable, which is not a joke, just a wall"
)
assert FOG_MASK_DIV >= 1, "FOG_MASK_DIV is a resolution divisor, so at least 1"
assert 0.0 < MIRAGE_HEIGHT <= 0.6, (
    "The mirage belongs to the lower screen; above that it is just a wobble"
)
assert DESKTOP_REFRESH_MIN_INTERVAL >= 1.0, (
    "Rate-limit the desktop refresh; back-to-back strikes must not spam the shell"
)
assert 0.0 <= AUDIO_MASTER_VOLUME <= 1.0, "AUDIO_MASTER_VOLUME must be 0.0-1.0"
assert all(0.0 <= v <= 1.0 for v in AUDIO_CUE_VOLUME.values()), (
    "Per-cue volumes are multipliers on the master level, so 0.0-1.0"
)
assert set(AMBIENT_BY_SCENE) <= set(WEATHER_WEIGHTS), (
    "AMBIENT_BY_SCENE keys are scene names; an unknown one would never play"
)
assert set(AMBIENT_BY_SCENE.values()) <= set(AUDIO_FILES), (
    "Every ambient bed needs a file entry, or the scene can only ever be silent"
)
assert set(ONESHOT_CUES) <= set(AUDIO_FILES), (
    "Every one-shot cue needs a file entry"
)
assert AUDIO_CHANNELS >= 2 + len(ONESHOT_CUES) + len(WEDDING_ONESHOT_CUES), (
    "The mixer pool has to hold the ambient channel, one per weather one-shot, "
    "the wedding music channel and one per wedding one-shot"
)
assert 0.0 <= THUNDER_DELAY <= 3.0, (
    "Thunder trails its own lightning by a beat; past a few seconds the flash "
    "and the clap stop reading as the same event"
)
assert all(v >= 0 for v in ONESHOT_MAXTIME_MS.values()), (
    "ONESHOT_MAXTIME_MS is a cap in milliseconds; 0 means play the whole clip"
)

# -- the wedding easter egg -------------------------------------------------
assert set(WEDDING_SLOTS) <= set(WEDDING_LAYOUT), (
    "Every slot needs a position, or it could never be drawn"
)
assert set(WEDDING_SLOTS) <= set(WEDDING_HEIGHT), (
    "Every slot needs an on-screen height"
)
assert set(WEDDING_ANCHOR) == set(WEDDING_LAYOUT), (
    "Every laid-out slot needs an anchor, and vice versa"
)
assert set(WEDDING_ANCHOR.values()) <= {"center", "bottom"}, (
    "An anchor is 'center' or 'bottom' -- nothing else is implemented"
)
assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
           for x, y in WEDDING_LAYOUT.values()), (
    "Wedding layout positions are screen fractions, so 0.0-1.0"
)
assert all(0.0 < h <= 0.5 for slot, h in WEDDING_HEIGHT.items()
           if slot != "arch"), (
    "The CAST is meant to be small: no character may fill half the screen"
)
assert 0.0 < WEDDING_HEIGHT["arch"] <= 0.85, (
    "The arch is scenery rather than cast and is allowed to be the biggest "
    "thing on screen -- but it still has to leave room above and below it"
)
assert list(WEDDING_TIMELINE) == sorted(WEDDING_TIMELINE, key=lambda e: e["t"]), (
    "The timeline is fired in order and each entry once, so it has to be "
    "written in time order"
)
assert WEDDING_TIMELINE[0]["t"] >= 0.0, "The ceremony clock starts at t=0"
assert WEDDING_FADE_OUT_AT + WEDDING_FADE_OUT <= WEDDING_TOTAL, (
    "WEDDING_TOTAL has to cover the fade-out, or the cast is cut off mid-fade"
)
assert set(WEDDING_CROWD_SLOTS) <= set(WEDDING_SLOTS), (
    "Every crowd group has to be a real slot"
)
assert set(WEDDING_CROWD_BOB_PHASE) == set(WEDDING_CROWD_SLOTS), (
    "Every crowd group needs its own bob phase -- rows jumping in lockstep "
    "read as one sprite drawn four times"
)
assert len({WEDDING_LAYOUT[slot][1] for slot in WEDDING_CROWD_SLOTS}) == 1, (
    "The four crowd groups have to share ONE ground line, or the front row "
    "reads as stepped ranks of unrelated pictures"
)
assert len({WEDDING_HEIGHT[slot] for slot in WEDDING_CROWD_SLOTS}) == 1, (
    "The four crowd groups have to share one height: the even x-centres in "
    "WEDDING_LAYOUT are computed from it, so a group drawn taller than its "
    "neighbours is also drawn wider and runs into them"
)
assert (sorted(WEDDING_LAYOUT[s][0] for s in WEDDING_CROWD_SLOTS)
        == [WEDDING_LAYOUT[s][0] for s in WEDDING_CROWD_SLOTS]), (
    "WEDDING_CROWD_SLOTS is left-to-right, and the bob phases and the "
    "timeline are read in that order -- keep the x-centres ascending"
)
assert set(WEDDING_LAYOUT_KISS) <= set(WEDDING_SLOTS), (
    "The kiss-beat override can only move a real slot"
)
assert WEDDING_START_DELAY >= 0, (
    "The delay only ever pushes the ceremony later into the event"
)
assert 0.0 < WEDDING_OPACITY <= 1.0, (
    "The cast's held opacity is a fraction; 1.0 is fully solid"
)
assert all(entry.get("move") in (None, "kiss") for entry in WEDDING_TIMELINE), (
    "The only position override implemented is 'kiss' (or None to clear it)"
)
assert all(0.0 <= entry["duck"] <= 1.0
           for entry in WEDDING_TIMELINE if "duck" in entry), (
    "A duck entry is a multiplier on the weather bed, so 0.0-1.0"
)
assert WEDDING_TIMELINE[-1]["t"] <= WEDDING_FADE_OUT_AT, (
    "A keyframe after the fade-out has begun would never be seen"
)
assert all(set(entry["set"]) <= set(WEDDING_SLOTS)
           for entry in WEDDING_TIMELINE if "set" in entry), (
    "A timeline entry can only set a known slot"
)
assert all(key is None or key in WEDDING_SPRITES
           for entry in WEDDING_TIMELINE for key in entry.get("set", {}).values()), (
    "Every sprite named by the timeline needs a file entry"
)
assert all(entry["audio"] in WEDDING_ONESHOT_CUES
           for entry in WEDDING_TIMELINE if "audio" in entry), (
    "Every wedding cue the timeline fires has to be a wedding one-shot"
)
assert set(WEDDING_ONESHOT_CUES) == set(WEDDING_AUDIO_FILES), (
    "Every wedding one-shot needs a file entry, and vice versa"
)
assert not (set(WEDDING_ONESHOT_CUES) & set(AUDIO_FILES)), (
    "A wedding cue sharing a name with a weather cue would share its channel"
)
assert WEDDING_MUSIC_CUE not in AUDIO_FILES, (
    "The march has its own dedicated channel; it must not collide with a "
    "weather cue name"
)
assert all(0.0 <= v <= 1.0 for v in WEDDING_CUE_VOLUME.values()), (
    "Wedding cue volumes are multipliers on the master level, so 0.0-1.0"
)
assert 0.0 <= WEDDING_RAIN_DUCK <= 1.0, (
    "The duck is a multiplier on the ambient bed, so 0.0-1.0"
)
assert 0.0 <= WEDDING_BRIGHTNESS_AMPLITUDE <= 1.0, (
    "The ceremony may only NARROW the sunshower sweep, never widen it"
)
assert 0.0 <= WEDDING_BRIGHTNESS_CENTRE <= 1.0, (
    "The sweep centre is a position inside the safe band, so 0.0-1.0"
)
assert (0.0 <= WEDDING_BRIGHTNESS_CENTRE - WEDDING_BRIGHTNESS_AMPLITUDE / 2
        and WEDDING_BRIGHTNESS_CENTRE + WEDDING_BRIGHTNESS_AMPLITUDE / 2 <= 1.0), (
    "The narrowed sweep has to stay inside BRIGHTNESS_FLOOR..CEILING"
)
assert WEDDING_CROSSFADE > 0, "A zero cross-fade is a hard cut; use a small one"
assert WEDDING_HEART_MAX >= 1, "The heart cap has to leave room for one heart"
assert WEDDING_CLOUD_THIN >= 1, (
    "WEDDING_CLOUD_THIN is a list stride, so at least 1 (1 = no change)"
)
assert WEDDING_EVENT_TAIL >= 0, (
    "The tail only ever lengthens the event; a negative one would cut the "
    "ceremony short"
)
