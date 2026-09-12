"""
The transparent click-through overlay window (Windows only).

WHY THIS IS NOT A COLOUR-KEYED WINDOW
  The obvious way to do this is SetLayeredWindowAttributes with LWA_COLORKEY
  plus a global LWA_ALPHA. It was tried and measured, and it cannot meet the
  goal of "weather floats on top while my work stays readable", because that
  API gives only two states per pixel: exactly-the-key (fully transparent)
  or global-alpha opaque. Concretely, at alpha 180 EVERY painted pixel cuts
  the desktop underneath to 29% brightness, however faint the art is. So a
  light colour grade becomes a 71% grey blanket, and anything with soft
  edges -- the corona, the clouds, the vignette -- ends in a hard visible
  ring where its alpha crosses zero.

  UpdateLayeredWindow with ULW_ALPHA gives TRUE per-pixel alpha instead, so
  a 20%-alpha tint really is 20% and soft glows really are soft. Measured at
  ~89fps for a full-screen 1080p overlay, so it is also cheaper than the
  workarounds the colour-key route would have needed.

HOW IT WORKS
  A 32-bit top-down DIB section is created once. A pygame Surface is aliased
  directly onto the DIB's memory with image.frombuffer, so drawing into that
  Surface writes straight into the bitmap Windows will composite -- no
  per-frame copy. Each frame the half-resolution composite is premultiplied
  (Windows wants premultiplied alpha), scaled into the alias, and handed to
  UpdateLayeredWindow.

  Screen-shake is done by moving the window rather than shifting pixels,
  which is free.

WINDOW STYLES
  WS_EX_LAYERED     per-pixel alpha
  WS_EX_TRANSPARENT click-through: hit-testing passes to whatever is below
  WS_EX_TOOLWINDOW  no taskbar button
  WS_EX_NOACTIVATE  never takes focus, so typing always goes to real work

  Because of TRANSPARENT and NOACTIVATE this window can never receive a key
  press. Every control must be a global hotkey or a tray item.
"""

import ctypes
import sys
from ctypes import wintypes

import pygame

IS_WINDOWS = sys.platform == "win32"

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000

_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010

_SW_HIDE = 0
_SW_SHOWNOACTIVATE = 4

_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_ULW_ALPHA = 0x00000002

_BI_RGB = 0

#: Correct compositing for a premultiplied source; falls back to an additive
#: blit on the (unlikely) build without it.
_PREMUL_BLEND = getattr(pygame, "BLEND_PREMULTIPLIED", pygame.BLEND_RGBA_ADD)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte),
    ]


class LayeredOverlay:
    """Presents a per-pixel-alpha, click-through overlay.

    `available` is False on any failure -- a non-Windows platform, no window
    handle, or a GDI call that did not work. The caller must fall back to
    ordinary windowed presentation rather than assume this worked.
    """

    def __init__(self, hwnd, size, alpha=255, log=print):
        self.log = log
        self.hwnd = hwnd
        self.width, self.height = size
        self.alpha = max(0, min(255, int(alpha)))
        self.available = False

        self.surface = None          # pygame Surface aliasing the DIB memory
        self._hbmp = None
        self._hdc = None
        self._user32 = None
        self._gdi32 = None
        self._hint = None            # optional full-res premultiplied overlay

        if not (IS_WINDOWS and hwnd):
            self.log("overlay: no layered window (not Windows, or no handle)")
            return
        try:
            self._bind_api()
            self._apply_styles()
            self._make_dib()
            self.available = True
            self.log(f"overlay: per-pixel alpha layered window, "
                     f"global alpha {self.alpha}, click-through, no taskbar button")
        except Exception as exc:
            self.log(f"overlay: setup failed, falling back to a plain window ({exc})")
            self.destroy()

    # ------------------------------------------------------------------
    def _bind_api(self):
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        g, u = self._gdi32, self._user32

        g.CreateDIBSection.restype = ctypes.c_void_p
        g.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.c_void_p, wintypes.DWORD]
        g.CreateCompatibleDC.restype = ctypes.c_void_p
        g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        g.SelectObject.restype = ctypes.c_void_p
        g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        g.DeleteObject.argtypes = [ctypes.c_void_p]
        g.DeleteDC.argtypes = [ctypes.c_void_p]

        self._get_long = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        self._set_long = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        self._get_long.restype = ctypes.c_ssize_t
        self._get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._set_long.restype = ctypes.c_ssize_t
        self._set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]

        u.UpdateLayeredWindow.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(wintypes.SIZE), ctypes.c_void_p,
            ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
            ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint]
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]

    def _apply_styles(self):
        u = self._user32
        # TOOLWINDOW only reliably drops the taskbar button if the window is
        # hidden while the style changes, so cycle it.
        u.ShowWindow(self.hwnd, _SW_HIDE)
        style = self._get_long(self.hwnd, _GWL_EXSTYLE)
        style |= (_WS_EX_LAYERED | _WS_EX_TRANSPARENT
                  | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)
        self._set_long(self.hwnd, _GWL_EXSTYLE, style)
        u.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
        self.set_topmost()

    def _make_dib(self):
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = self.width
        info.bmiHeader.biHeight = -self.height      # negative = top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = _BI_RGB

        bits = ctypes.c_void_p()
        self._hbmp = self._gdi32.CreateDIBSection(
            None, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not self._hbmp or not bits.value:
            raise OSError("CreateDIBSection failed")
        self._hdc = self._gdi32.CreateCompatibleDC(None)
        if not self._hdc:
            raise OSError("CreateCompatibleDC failed")
        self._gdi32.SelectObject(self._hdc, self._hbmp)

        # Alias the bitmap's own memory, so drawing writes straight into it.
        raw = (ctypes.c_ubyte * (self.width * self.height * 4)).from_address(bits.value)
        self.surface = pygame.image.frombuffer(
            memoryview(raw).cast("B"), (self.width, self.height), "BGRA")

        # SourceConstantAlpha is a single number for the WHOLE window, so
        # anything below 255 caps how solid any pixel on screen can ever be
        # -- which is why the caller now passes 255 and dims the weather in
        # the renderer instead. Left as a parameter rather than hardcoded:
        # the mechanism is still correct, it is just no longer the place the
        # app chooses to dim from. See OVERLAY_ALPHA in config.py.
        self._blend = BLENDFUNCTION(_AC_SRC_OVER, 0, self.alpha, _AC_SRC_ALPHA)
        self._size = wintypes.SIZE(self.width, self.height)
        self._src = wintypes.POINT(0, 0)

    # ------------------------------------------------------------------
    def set_topmost(self):
        """Other apps going fullscreen can steal the top slot, so this is
        re-asserted on a timer rather than only at startup."""
        if not (IS_WINDOWS and self.hwnd):
            return
        try:
            self._user32.SetWindowPos(
                self.hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
        except Exception as exc:
            self.log(f"overlay: could not set always-on-top ({exc})")

    def set_hint(self, hint_surface):
        """A small premultiplied surface composited at full resolution, so
        the one bit of text on screen stays crisp instead of being upscaled
        with the artwork."""
        self._hint = hint_surface

    def present(self, composite, shake=(0, 0), foreground=None):
        """Push `composite` (a per-pixel-alpha surface, any size) to screen.

        Windows wants premultiplied alpha, so the composite is premultiplied
        at its own -- usually half -- resolution and then scaled straight
        into the bitmap. Shake moves the window instead of the pixels.

        `foreground`, if given, is called with the full-size bitmap right
        after that upscale: Renderer.draw_foreground, which puts the wedding
        cast on at full resolution so its sprites are never upscaled.
        """
        if not self.available:
            return False
        try:
            premultiplied = composite.premul_alpha()
            if premultiplied.get_size() == (self.width, self.height):
                self.surface.blit(premultiplied, (0, 0))
            else:
                # Writes scaled pixels directly; no blending, so the
                # premultiplied values land intact.
                pygame.transform.scale(premultiplied,
                                       (self.width, self.height), self.surface)
            if foreground is not None:
                foreground(self.surface)
            if self._hint is not None:
                self.surface.blit(self._hint, self._hint_pos, special_flags=_PREMUL_BLEND)

            dst = wintypes.POINT(int(shake[0]), int(shake[1]))
            return bool(self._user32.UpdateLayeredWindow(
                self.hwnd, None, ctypes.byref(dst), ctypes.byref(self._size),
                self._hdc, ctypes.byref(self._src), 0,
                ctypes.byref(self._blend), _ULW_ALPHA))
        except Exception as exc:
            self.log(f"overlay: present failed, disabling ({exc})")
            self.available = False
            return False

    def set_hint_position(self, pos):
        self._hint_pos = pos

    def hide(self):
        if IS_WINDOWS and self.hwnd:
            try:
                self._user32.ShowWindow(self.hwnd, _SW_HIDE)
            except Exception:
                pass

    def destroy(self):
        """Idempotent GDI cleanup."""
        self.available = False
        self.surface = None
        try:
            if self._hbmp:
                self._gdi32.DeleteObject(self._hbmp)
            if self._hdc:
                self._gdi32.DeleteDC(self._hdc)
        except Exception:
            pass
        self._hbmp = None
        self._hdc = None
