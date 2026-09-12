"""
Windows Power Mode control -- the REAL fan effect.

This wraps the power *overlay* API in powrprof.dll. That is the exact same
setting as the "Power mode" dropdown in Settings > System > Power & battery
(the slider that reads Best power efficiency / Balanced / Best performance).

Why this is the safe way to move a fan:

  * It needs no administrator rights.
  * It does NOT disable, override, or reprogram the firmware fan curve. The
    embedded controller keeps full authority over cooling, so the hardware's
    thermal protection stays completely intact.
  * It is a documented, user-facing OS setting. Anything the user could do
    with the Settings slider, and nothing more.

What this module must never grow into: direct fan or EC access, vendor fan
control drivers, disabling thermal protection, or anything that stops the
machine from cooling itself. See the safety rule in config.py.

Every call is defensive: on an older Windows without the overlay API, each
function logs once and becomes a no-op rather than raising.
"""

import ctypes
from ctypes import wintypes

# --------------------------------------------------------------------------
# Power overlay GUIDs (the three Power mode positions)
# --------------------------------------------------------------------------
BEST_EFFICIENCY = "961cc777-2547-4f9d-8174-7d86181b8a7a"
BALANCED = "00000000-0000-0000-0000-000000000000"
BEST_PERFORMANCE = "ded574b5-45a0-4f42-8737-46345c09c238"

NAMES = {
    BEST_EFFICIENCY: "Best power efficiency",
    BALANCED: "Balanced",
    BEST_PERFORMANCE: "Best performance",
}


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


def parse_guid(text):
    """'961cc777-2547-...' -> GUID structure."""
    raw = text.replace("-", "").replace("{", "").replace("}", "")
    if len(raw) != 32:
        raise ValueError(f"not a GUID: {text!r}")
    return GUID(
        int(raw[0:8], 16),
        int(raw[8:12], 16),
        int(raw[12:16], 16),
        (ctypes.c_ubyte * 8)(*bytes.fromhex(raw[16:32])),
    )


def format_guid(guid):
    """GUID structure -> canonical lowercase string."""
    tail = bytes(guid.Data4).hex()
    return (f"{guid.Data1:08x}-{guid.Data2:04x}-{guid.Data3:04x}-"
            f"{tail[:4]}-{tail[4:]}")


def describe(text):
    """Human name for a GUID string, falling back to the GUID itself."""
    return NAMES.get(text, text or "unknown")


class PowerMode:
    """Reads and sets the Windows Power Mode overlay.

    Construct once, call capture_original() at startup, and always call
    restore() on the way out. restore() is idempotent and safe to call from
    any exit path, including a crash handler.
    """

    def __init__(self, log=print):
        self.log = log
        self.available = False
        self.original = None      # GUID string captured at startup
        self.current = None       # GUID string we last set
        self._warned = False
        self._powrprof = None
        self._kernel32 = None

        try:
            self._powrprof = ctypes.WinDLL("powrprof.dll")
            self._kernel32 = ctypes.WinDLL("kernel32.dll")
        except Exception as exc:
            self._warn(f"power: powrprof.dll unavailable ({exc})")
            return

        # Both entry points are Windows 10 1809+. Their absence is the normal
        # "old Windows" case, not an error worth crashing over.
        try:
            self._get = self._powrprof.PowerGetActualOverlayScheme
            self._get.argtypes = [ctypes.POINTER(GUID)]
            self._get.restype = wintypes.DWORD

            self._set = self._powrprof.PowerSetActiveOverlayScheme
            self._set.argtypes = [GUID]
            self._set.restype = wintypes.DWORD

            self._status = self._kernel32.GetSystemPowerStatus
            self._status.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]
            self._status.restype = wintypes.BOOL

            self.available = True
        except AttributeError as exc:
            self._warn(f"power: overlay API missing, power mode disabled ({exc})")

    # -- logging -----------------------------------------------------------
    def _warn(self, message):
        """Log a failure once; after that stay silent and no-op."""
        if not self._warned:
            self._warned = True
            self.log(message)

    # -- reads -------------------------------------------------------------
    def get_active(self):
        """Current overlay as a GUID string, or None if unreadable."""
        if not self.available:
            return None
        try:
            guid = GUID()
            rc = self._get(ctypes.byref(guid))
            if rc != 0:
                self._warn(f"power: PowerGetActualOverlayScheme returned {rc}")
                return None
            return format_guid(guid)
        except Exception as exc:
            self._warn(f"power: read failed ({exc})")
            return None

    def on_ac_power(self):
        """True if plugged in, False on battery, None if unknown."""
        if not self.available:
            return None
        try:
            status = SYSTEM_POWER_STATUS()
            if not self._status(ctypes.byref(status)):
                return None
            if status.ACLineStatus == 1:
                return True
            if status.ACLineStatus == 0:
                return False
            return None                     # 255 = unknown
        except Exception:
            return None

    # -- writes ------------------------------------------------------------
    def capture_original(self):
        """Remember the user's own Power mode so we can put it back."""
        self.original = self.get_active()
        if self.original is None:
            self.available = False
            self.log("power: could not read the current mode, "
                     "power-mode effects disabled")
            return None
        self.current = self.original
        self.log(f"power: original mode = {describe(self.original)} "
                 f"({self.original})")
        return self.original

    def set_mode(self, guid_text):
        """Set the overlay. Returns True on success. Never raises."""
        if not self.available or not guid_text:
            return False
        if guid_text == self.current:
            return True                     # already there; don't churn
        try:
            rc = self._set(parse_guid(guid_text))
            if rc != 0:
                self._warn(f"power: PowerSetActiveOverlayScheme returned {rc}")
                return False
            self.current = guid_text
            self.log(f"power: mode -> {describe(guid_text)}")
            return True
        except Exception as exc:
            self._warn(f"power: set failed ({exc})")
            return False

    def restore(self):
        """Put the user's original Power mode back. Idempotent."""
        if not self.available or self.original is None:
            return False
        if self.current == self.original:
            return True
        return self.set_mode(self.original)
