"""
Reads from the Windows desktop, and one carefully scoped poke at it.

Two unrelated-looking things live here because they are the same kind of
thing: neither draws anything, and both talk to the shell rather than to
hardware. Nothing in this module touches power, the fan, the embedded
controller, or thermal behaviour -- see the safety rule in config.py.

1. cursor_pos()
   The overlay is WS_EX_TRANSPARENT, so it can never receive a mouse
   event: every click, drag and hover goes straight to whatever app is
   underneath, which is the whole point. GetCursorPos is a global READ and
   works fine through a click-through layered window, so the condensation
   fog watches where the pointer has been rather than capturing it. No
   input is intercepted, swallowed, or synthesised.

2. refresh_desktop()
   Makes the desktop icons flicker in time with a lightning strike.

   SCOPE, because this one deserves the paragraph. The refresh is a single
   WM_COMMAND 0x7103 ("refresh") POSTED to the desktop's own
   SHELLDLL_DefView window -- the same message Explorer sends itself when
   you right-click the desktop and choose Refresh. It goes to that one
   window handle and nowhere else:

     * NO SendInput, and no synthesised keystroke of any kind. A global F5
       would land in whatever the user is typing into -- a browser, an
       editor, a form half filled in -- and that is exactly the thing this
       must never do.
     * NO broadcast (HWND_BROADCAST / WM_SETTINGCHANGE). Other applications
       are never messaged.
     * PostMessage, not SendMessage, so a busy or hung Explorer can never
       block our render loop.
     * Rate-limited by config.DESKTOP_REFRESH_MIN_INTERVAL, so a
       double-pulse strike or a burst of them yields one refresh.
     * Every call is wrapped: if the desktop window cannot be found -- and
       it genuinely moves, see below -- the whole thing is skipped.

   FINDING IT. Normally SHELLDLL_DefView is a child of Progman. With a
   wallpaper slideshow (or after some Explorer restarts) the shell reparents
   it under one of several WorkerW windows instead, so the Progman lookup
   comes back empty and the WorkerW walk is the fallback. The handle is
   cached and revalidated with IsWindow, because it changes when Explorer
   restarts.
"""

import ctypes
import sys
import time
from ctypes import wintypes

import config

IS_WINDOWS = sys.platform == "win32"

_WM_COMMAND = 0x0111
#: Explorer's own "Refresh" command id, as sent by the desktop context menu.
_SHELL_REFRESH = 0x7103

_user32 = ctypes.windll.user32 if IS_WINDOWS else None

if IS_WINDOWS:
    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowExW.restype = wintypes.HWND
    _user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                      wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

_defview = None            # cached SHELLDLL_DefView handle
_last_refresh = 0.0        # monotonic clock of the last refresh we posted
_warned = False            # log the "cannot find it" line at most once


# --------------------------------------------------------------------------
# 1. Reading the cursor
# --------------------------------------------------------------------------
def cursor_pos():
    """The global cursor position, or None if it cannot be read.

    A read, never a capture: the overlay stays click-through and the user
    keeps clicking straight through it.
    """
    if not IS_WINDOWS:
        return None
    try:
        point = wintypes.POINT()
        if _user32.GetCursorPos(ctypes.byref(point)):
            return (point.x, point.y)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# 2. Poking the desktop
# --------------------------------------------------------------------------
def _find_defview():
    """The desktop's SHELLDLL_DefView, under Progman or under a WorkerW."""
    progman = _user32.FindWindowW("Progman", None)
    if progman:
        found = _user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if found:
            return found

    # Wallpaper slideshow: the shell reparents the view under a WorkerW.
    result = []
    buf = ctypes.create_unicode_buffer(64)

    def visit(hwnd, _lparam):
        _user32.GetClassNameW(hwnd, buf, 64)
        if buf.value == "WorkerW":
            child = _user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if child:
                result.append(child)
                return False        # stop enumerating
        return True

    _user32.EnumWindows(_ENUM_PROC(visit), 0)
    return result[0] if result else None


def _defview_handle():
    """The cached handle, revalidated -- Explorer restarts invalidate it."""
    global _defview
    if _defview and _user32.IsWindow(_defview):
        return _defview
    _defview = _find_defview()
    return _defview


def refresh_desktop(log=None, force=False):
    """Ask the desktop -- and only the desktop -- to redraw its icons.

    Returns True if a refresh was actually posted. Rate-limited, silent on
    failure, and safe to call from a scene's update loop.
    """
    global _last_refresh, _warned
    if not (IS_WINDOWS and config.DESKTOP_REFRESH_ON_LIGHTNING):
        return False

    now = time.monotonic()
    if not force and now - _last_refresh < config.DESKTOP_REFRESH_MIN_INTERVAL:
        return False

    try:
        hwnd = _defview_handle()
        if not hwnd:
            if log is not None and not _warned:
                _warned = True
                log("desktop: no SHELLDLL_DefView found -- skipping the "
                    "lightning refresh (everything else is unaffected)")
            return False
        # Posted, not sent: a busy Explorer must never stall the frame.
        _user32.PostMessageW(hwnd, _WM_COMMAND, _SHELL_REFRESH, 0)
        _last_refresh = now
        return True
    except Exception as exc:
        if log is not None and not _warned:
            _warned = True
            log(f"desktop: refresh unavailable, skipping it ({exc})")
        return False
