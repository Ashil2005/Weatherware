"""
Control panel: system-tray icon and the runtime switches behind it.

This is not a convenience -- it is the backup control surface. The overlay is
click-through and never takes focus, so if the global hotkeys do not work on
a given machine, the tray is the only way to force weather or quit.

The tray runs on its own thread and only ever sets flags on the engine or
calls into the effects layer; it never touches hardware itself. Every path
through here leads to a clean exit that restores brightness and power mode.

SAFETY: the panel exposes no fan or cooling control, because the app has
none. The real toggles it offers are the display brightness and the Windows
Power Mode overlay, and switching either off restores the user's own setting
immediately. The two sound toggles are not hardware at all -- "Mute sound"
silences the mixer while the weather runs on, "Sound effects" stops playback
outright -- and neither can reach any of the effects above.
"""

import threading

import config

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:                                   # pragma: no cover
    pystray = None
    Image = ImageDraw = None


def _make_icon_image(size=64):
    """A small sun-behind-cloud glyph, drawn rather than shipped as an asset."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 40, 40), fill=config.COLOR_SUNNY + (255,))
    d.ellipse((20, 26, 56, 52), fill=(226, 232, 244, 255))
    d.ellipse((10, 32, 38, 54), fill=(200, 210, 226, 255))
    return img


class ControlPanel:
    """Tray front-end. Safe to construct even when pystray is unavailable."""

    def __init__(self, engine):
        self.engine = engine
        self.icon = None
        self.thread = None
        self.available = pystray is not None

    # -- menu actions ------------------------------------------------------
    def _force(self, name):
        def handler(icon=None, item=None):
            self.engine.force_weather(name)
        return handler

    def _toggle_brightness(self, icon=None, item=None):
        config.BRIGHTNESS_ENABLED = not config.BRIGHTNESS_ENABLED
        effects = self.engine.effects
        effects.brightness_available = (
            config.BRIGHTNESS_ENABLED and effects.original_brightness is not None)
        if not config.BRIGHTNESS_ENABLED:
            effects.restore_brightness()
        self.engine.log(
            f"brightness effects: {'on' if config.BRIGHTNESS_ENABLED else 'off'}")

    def _toggle_power(self, icon=None, item=None):
        """Turning this off restores the user's own Power mode at once."""
        self.engine.effects.set_power_enabled(
            not self.engine.effects.power_enabled)

    def _toggle_lenovo(self, icon=None, item=None):
        """Turning this off restores the user's own Fn+Q preset at once."""
        self.engine.effects.set_lenovo_enabled(
            not self.engine.effects.lenovo_enabled)

    def _toggle_mute(self, icon=None, item=None):
        """Instant silence. The loop keeps running underneath, so unmuting
        drops straight back into the weather rather than restarting it."""
        self.engine.audio.toggle_mute()

    def _toggle_sound(self, icon=None, item=None):
        """Harder than mute: stops playback. Switching it back on restarts
        the bed for whatever event is on screen at that moment."""
        audio = self.engine.audio
        audio.set_enabled(not audio.enabled)
        if audio.enabled:
            scene = self.engine.scene
            audio.resume_scene(scene.name if scene is not None else None)

    def _restore_brightness(self, icon=None, item=None):
        self.engine.effects.restore_brightness()

    def _restore_power(self, icon=None, item=None):
        self.engine.effects.restore_power_mode()

    def _restore_lenovo(self, icon=None, item=None):
        self.engine.effects.restore_lenovo()

    def _quit(self, icon=None, item=None):
        self.engine.request_exit("tray menu")
        self.stop()

    def _power_label(self, item=None):
        """Shows the Windows power mode we are currently sitting in."""
        import power as power_api
        current = self.engine.effects.power.current
        return f"Power mode: {power_api.describe(current)}"

    def _lenovo_label(self, item=None):
        """Shows the Lenovo fan preset, or why it is unavailable."""
        import lenovo as lenovo_api
        thermal = self.engine.effects.lenovo
        if not thermal.available:
            return ("Lenovo fan: needs admin" if not thermal.admin
                    else "Lenovo fan: unavailable")
        return f"Lenovo fan: {lenovo_api.describe(thermal.current)}"

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(f"{config.APP_NAME} {config.VERSION}", None, enabled=False),
            pystray.MenuItem(self._power_label, None, enabled=False),
            pystray.MenuItem(self._lenovo_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            # Built from the hotkey table rather than listed by hand: the
            # tray is the backup control surface, so a new scene must never
            # be reachable by hotkey but missing from here.
            *(pystray.MenuItem(f"Force {name}", self._force(name))
              for name in dict.fromkeys(config.FORCE_HOTKEYS.values())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Brightness effects", self._toggle_brightness,
                checked=lambda item: config.BRIGHTNESS_ENABLED),
            pystray.MenuItem(
                "Power mode effects", self._toggle_power,
                checked=lambda item: self.engine.effects.power_enabled),
            pystray.MenuItem(
                "Lenovo fan effects", self._toggle_lenovo,
                checked=lambda item: self.engine.effects.lenovo_enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Sound effects", self._toggle_sound,
                checked=lambda item: self.engine.audio.enabled),
            pystray.MenuItem(
                f"Mute sound ({config.MUTE_HOTKEY.upper()})", self._toggle_mute,
                checked=lambda item: self.engine.audio.muted),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restore brightness now", self._restore_brightness),
            pystray.MenuItem("Restore power mode now", self._restore_power),
            pystray.MenuItem("Restore Lenovo fan mode now", self._restore_lenovo),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                f"Quit ({config.KILL_SWITCH_HOTKEY.upper()})", self._quit),
        )

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if not self.available:
            self.engine.log("tray: pystray unavailable, running without a tray icon")
            return
        try:
            self.icon = pystray.Icon(
                config.APP_NAME, _make_icon_image(), config.APP_NAME, self._menu())
            self.thread = threading.Thread(
                target=self.icon.run, name="weatherware-tray", daemon=True)
            self.thread.start()
            self.engine.log("tray: icon running")
        except Exception as exc:
            self.available = False
            self.engine.log(f"tray: failed to start ({exc})")

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
