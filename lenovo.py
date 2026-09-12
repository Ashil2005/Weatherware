"""
Lenovo thermal-mode control -- the fan effect that actually works on a LOQ.

WHY THIS EXISTS
  The Windows Power Mode overlay (power.py) really does switch, and the
  registry confirms it, but on a Lenovo LOQ the fan follows LENOVO's own
  thermal profile -- the Fn+Q Quiet / Balanced / Performance preset -- not
  the Windows slider. So the fan only moves if we drive Lenovo's own
  interface. power.py is kept as the cross-vendor fallback.

WHAT THIS TOUCHES
  Exactly one method: SetSmartFanMode on root\\WMI:LENOVO_GAMEZONE_DATA,
  with Data = 1, 2 or 3. That is the same preset selector as pressing Fn+Q,
  it is temporary and non-persistent, and the original value is captured at
  startup and restored on idle and on every exit path.

=============================================================================
 HARD SAFETY RULES FOR THIS MODULE
=============================================================================
 * SetSmartFanMode may ONLY ever be called with 1, 2 or 3. set_mode()
   refuses anything else.
 * NEVER set a custom fan curve, a custom RPM, "full speed"/255, or any fan
   unlock. This machine also exposes LENOVO_FAN_METHOD, LENOVO_FAN_TABLE_DATA
   and LENOVO_FAN_MAX_SPEED_DATA, plus SetFanCooling / SetThermalTableID on
   this very class. They are deliberately NOT used and must stay that way.
 * NEVER write IntelligentCoolingPerformanceMode or any other BIOS setting.
   We want temporary switching we can undo, not a firmware change.
 * This is a preset selector. Every preset keeps Lenovo's own fan curve and
   thermal protection fully in charge of cooling.
=============================================================================

ADMIN
  Instantiating LENOVO_GAMEZONE_DATA requires elevation: unelevated it fails
  with WBEM_E_ACCESS_DENIED (0x80041003). Without admin this module logs one
  warning and no-ops, and the rest of the app runs normally.
"""

import ctypes
import threading

QUIET = 1
BALANCED = 2
PERFORMANCE = 3

#: The only values SetSmartFanMode is ever allowed to receive.
ALLOWED_MODES = (QUIET, BALANCED, PERFORMANCE)

NAMES = {QUIET: "Quiet", BALANCED: "Balanced", PERFORMANCE: "Performance"}

#: Scene intent -> mode number.
INTENTS = {"quiet": QUIET, "balanced": BALANCED, "performance": PERFORMANCE}

WMI_NAMESPACE = "root\\WMI"
WMI_CLASS = "LENOVO_GAMEZONE_DATA"


def describe(mode):
    if mode is None:
        return "unknown"
    return NAMES.get(mode, f"mode {mode}")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _unwrap(result):
    """The wmi module returns out-params as a tuple; GetSmartFanMode has one."""
    if isinstance(result, (tuple, list)):
        return result[0] if result else None
    return result


class LenovoThermal:
    """Reads and sets the Lenovo smart-fan preset.

    Mirrors power.PowerMode: construct once, capture_original() at startup,
    restore() on the way out. Every method is defensive and idempotent -- on
    a non-Lenovo machine, without admin, or if any COM call fails, it logs
    once and becomes a no-op.
    """

    def __init__(self, log=print):
        self.log = log
        self.available = False
        self.original = None       # mode number captured at startup
        self.current = None        # mode number we last set
        self.supported = None      # IsSupportSmartFan result, if readable
        self.admin = is_admin()

        self._instance = None
        self._lock = threading.Lock()
        self._warned = False

        try:
            import wmi                                  # noqa: F401
            self._wmi = wmi
        except Exception as exc:
            self._warn(f"lenovo: the 'wmi' package is unavailable ({exc})")
            self._wmi = None
            return

        if not self.admin:
            self.log("lenovo: fan control needs an ADMIN terminal "
                     "(LENOVO_GAMEZONE_DATA denies access otherwise); "
                     "Lenovo fan effects will no-op this run")
            return

        self._connect()

    # ------------------------------------------------------------------
    def _warn(self, message):
        """Log a failure once, then stay quiet and no-op."""
        if not self._warned:
            self._warned = True
            self.log(message)

    def _co_init(self):
        """WMI is COM, so any thread that calls in must initialise it. The
        tray runs on its own thread, so this cannot be skipped."""
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass            # already initialised on this thread, or no pywin32

    def _connect(self):
        try:
            self._co_init()
            conn = self._wmi.WMI(namespace=WMI_NAMESPACE)
            instances = getattr(conn, WMI_CLASS)()
            if not instances:
                self._warn(f"lenovo: {WMI_CLASS} exposed no instances")
                return
            self._instance = instances[0]
            self.available = True
            try:
                self.supported = _unwrap(self._instance.IsSupportSmartFan())
            except Exception:
                self.supported = None
            self.log(f"lenovo: {WMI_CLASS} ready "
                     f"(IsSupportSmartFan={self.supported})")
        except Exception as exc:
            self._warn(f"lenovo: unavailable ({exc}); "
                       "not a Lenovo gaming machine, or access denied")

    # -- reads -------------------------------------------------------------
    def get_mode(self):
        """Current smart-fan mode number, or None if unreadable."""
        if not self.available:
            return None
        with self._lock:
            try:
                self._co_init()
                return _unwrap(self._instance.GetSmartFanMode())
            except Exception as exc:
                self._warn(f"lenovo: GetSmartFanMode failed ({exc})")
                return None

    def capture_original(self):
        """Remember the user's own Fn+Q preset so we can put it back."""
        self.original = self.get_mode()
        if self.original is None:
            self.available = False
            self.log("lenovo: could not read the current fan mode, "
                     "Lenovo fan effects disabled")
            return None
        self.current = self.original
        self.log(f"lenovo: original fan mode = {describe(self.original)} "
                 f"({self.original})")
        return self.original

    # -- writes ------------------------------------------------------------
    def set_mode(self, mode):
        """Set one of the three presets. Returns True on success.

        Refuses anything outside ALLOWED_MODES -- see the safety rules at the
        top of this module. Custom curves, RPMs and full-speed values must
        never reach the firmware through here.
        """
        if not self.available:
            return False
        if mode not in ALLOWED_MODES:
            self.log(f"lenovo: REFUSED fan mode {mode!r} -- only "
                     f"{ALLOWED_MODES} are permitted")
            return False
        if mode == self.current:
            return True                     # already there; don't churn
        with self._lock:
            try:
                self._co_init()
                self._instance.SetSmartFanMode(Data=int(mode))
                self.current = mode
                self.log(f"lenovo: fan mode -> {describe(mode)} ({mode})")
                return True
            except Exception as exc:
                self._warn(f"lenovo: SetSmartFanMode failed ({exc})")
                return False

    def set_intent(self, intent):
        """Scene intent ('quiet'/'balanced'/'performance') -> preset."""
        mode = INTENTS.get(intent)
        if mode is None:
            return False
        return self.set_mode(mode)

    def restore(self):
        """Put the user's original preset back. Idempotent."""
        if not self.available or self.original is None:
            return False
        if self.current == self.original:
            return True
        return self.set_mode(self.original)
