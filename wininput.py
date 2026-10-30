"""Windows helpers for the Mod Editor: list its windows, capture one window, click, keys, clipboard.

Only windows owned by the Mod Editor process are ever captured or sent input, so a
screenshot can never include the rest of the desktop.
"""
import ctypes
import ctypes.wintypes as wt
import struct
import time
import zlib

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor v2: real pixels
except Exception:
    user32.SetProcessDPIAware()

EDITOR_EXE = "AbioticFactorEditorCooked"

user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wt.HWND]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]


class WinError(RuntimeError):
    pass


def _exe_of(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def _rect(h):
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def editor_windows():
    """Visible top-level windows owned by the Mod Editor process."""
    found = []
    exe_cache = {}

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        exe = exe_cache.setdefault(pid.value, _exe_of(pid.value))
        if EDITOR_EXE.lower() not in exe.lower():
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        l, t, r, b = _rect(h)
        if r - l > 50 and b - t > 50:
            found.append({"hwnd": int(h), "title": buf.value, "pid": pid.value,
                          "rect": [l, t, r, b], "minimized": bool(user32.IsIconic(h))})
        return True

    user32.EnumWindows(cb, 0)
    return found


def find_window(title=None):
    """Editor window whose title contains `title` (case-insensitive); default = the main editor window."""
    wins = editor_windows()
    if not wins:
        raise WinError("No Mod Editor window is open.")
    if title:
        match = [w for w in wins if title.lower() in w["title"].lower()]
        if not match:
            raise WinError(f"No editor window titled like {title!r}. Open: {[w['title'] for w in wins]}")
        return match[0]
    main = [w for w in wins if "Unreal Editor" in w["title"]]
    return (main or sorted(wins, key=lambda w: -(w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1])))[0]


def ui_scale(win):
    """Editor UI pixels per layout pixel on the window's monitor (1.0 at 100% Windows scaling)."""
    try:
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        monitor = user32.MonitorFromWindow(win["hwnd"], 2)
        x, y = ctypes.c_uint(), ctypes.c_uint()
        if ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(monitor), 0, ctypes.byref(x), ctypes.byref(y)) == 0:
            return x.value / 96.0
    except (OSError, AttributeError):
        pass
    return 1.0


def focus(win):
    h = win["hwnd"]
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)                              # SW_RESTORE
    # An Alt tap lets SetForegroundWindow succeed from a background process.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(h)
    time.sleep(0.35)


def foreground_is_editor():
    h = user32.GetForegroundWindow()
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return EDITOR_EXE.lower() in _exe_of(pid.value).lower()


# ---- capture -------------------------------------------------------------

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def capture(win, max_width=1600):
    """PNG bytes of one editor window (PrintWindow, so other windows on top don't matter)
    and the integer downscale factor used."""
    h = win["hwnd"]
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)
        time.sleep(0.4)
    l, t, r, b = _rect(h)
    w, hgt = r - l, b - t
    hdc = user32.GetDC(h)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, hgt)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(h, mem, 2):               # PW_RENDERFULLCONTENT (D3D windows)
            raise WinError("PrintWindow failed")
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, -hgt, 1, 32
        buf = ctypes.create_string_buffer(w * hgt * 4)
        gdi32.GetDIBits(mem, bmp, 0, hgt, buf, ctypes.byref(bi), 0)
        raw = buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(h, hdc)
    f = max(1, -(-w // max_width))
    ow, oh = len(range(0, w, f)), len(range(0, hgt, f))
    rows = []
    stride = w * 4
    for y in range(0, hgt, f):
        row = raw[y * stride:(y + 1) * stride]
        px = bytearray(ow * 3)
        px[0::3] = row[2::4 * f][:ow]
        px[1::3] = row[1::4 * f][:ow]
        px[2::3] = row[0::4 * f][:ow]
        rows.append(b"\x00" + bytes(px))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", ow, oh, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))
    return png, f, (w, hgt)


# ---- input ---------------------------------------------------------------

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
      "escape": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
      "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "left": 0x25,
      "up": 0x26, "right": 0x27, "down": 0x28, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
      "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
      "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE, "backquote": 0xC0}
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28}


def click(win, x, y, button="left", double=False):
    """Click at window-relative pixel (x, y)."""
    l, t, r, b = _rect(win["hwnd"])
    if not (0 <= x < r - l and 0 <= y < b - t):
        raise WinError(f"({x}, {y}) is outside the window ({r - l}x{b - t})")
    focus(win)
    user32.SetCursorPos(l + x, t + y)
    time.sleep(0.15)
    down, up = (0x2, 0x4) if button == "left" else (0x8, 0x10)
    for _ in range(2 if double else 1):
        user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.08)


def keys(combo, hold_ms=50):
    """Press a combo such as 'ctrl+v', 'alt+p', 'delete', 'w'. Refuses unless the editor is in front."""
    if not foreground_is_editor():
        raise WinError("The Mod Editor is not the foreground window; not sending keys.")
    codes = []
    for p in combo.lower().split("+"):
        p = p.strip()
        if p in VK:
            codes.append(VK[p])
        elif len(p) == 1:
            codes.append(ord(p.upper()))
        else:
            raise WinError(f"Unknown key {p!r}")
    for c in codes:
        user32.keybd_event(c, 0, 1 if c in EXTENDED else 0, 0)
        time.sleep(0.04)
    time.sleep(hold_ms / 1000.0)
    for c in reversed(codes):
        user32.keybd_event(c, 0, (1 if c in EXTENDED else 0) | 2, 0)
        time.sleep(0.04)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def type_text(text):
    """Type Unicode text into the focused editor control."""
    if not foreground_is_editor():
        raise WinError("The Mod Editor is not the foreground window; not typing.")
    for ch in text:
        for flags in (0x4, 0x4 | 0x2):                       # KEYEVENTF_UNICODE, + KEYUP
            inp = INPUT(type=1)
            inp.u.ki = KEYBDINPUT(0, ord(ch), flags, 0, 0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.005)


def close_window(win):
    """Ask one editor window (a dialog, Message Log, asset editor) to close. Refuses the main editor window."""
    if "Unreal Editor" in win["title"]:
        raise WinError("Refusing to close the main editor window; use the editor's own quit.")
    user32.PostMessageW(win["hwnd"], 0x0010, 0, 0)       # WM_CLOSE
    time.sleep(0.5)


def set_clipboard(text):
    data = (text + "\0").encode("utf-16-le")
    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise WinError("Could not open the clipboard")
    try:
        user32.EmptyClipboard()
        hmem = kernel32.GlobalAlloc(0x2, len(data))          # GMEM_MOVEABLE
        ptr = kernel32.GlobalLock(hmem)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(hmem)
        if not user32.SetClipboardData(13, hmem):            # CF_UNICODETEXT
            raise WinError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()
