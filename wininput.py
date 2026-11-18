"""Windows helpers for the Mod Editor: list its windows, capture one window, click, keys, clipboard.

Only windows owned by the Mod Editor process are ever captured or sent input, so a
screenshot can never include the rest of the desktop.
"""
import ctypes
import ctypes.wintypes as wt
import re
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
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
user32.GetForegroundWindow.restype = wt.HWND
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.c_void_p]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.ReleaseMutex.argtypes = [wt.HANDLE]


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


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def editor_pids():
    """Process ids of the Mod Editor, windows or not (it keeps running for a while after its windows close). A process
    snapshot, because tasklist shows nothing from some sandboxes."""
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)                 # TH32CS_SNAPPROCESS
    if snap in (None, -1, 0xFFFFFFFFFFFFFFFF):
        return []
    entry = _PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
    found = []
    try:
        if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                if entry.szExeFile.lower().startswith(EDITOR_EXE.lower()):
                    found.append(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return found


def pid_alive(pid):
    """Whether a process with this id is running."""
    h = kernel32.OpenProcess(0x1000, False, int(pid))    # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wt.DWORD()
        return bool(kernel32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259     # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(h)


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def process_memory(pid):
    """(working set, committed) bytes of a process: what it holds in RAM, and what it has asked Windows for in all
    (RAM plus page file). None when the process can't be read."""
    h = kernel32.OpenProcess(0x1000 | 0x0010, False, int(pid))    # QUERY_LIMITED_INFORMATION | VM_READ
    if not h:
        return None
    try:
        c = _PROCESS_MEMORY_COUNTERS_EX()
        c.cb = ctypes.sizeof(c)
        if not kernel32.K32GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            return None
        return int(c.WorkingSetSize), int(c.PrivateUsage)
    finally:
        kernel32.CloseHandle(h)


def system_memory():
    """(commit limit, commit still free, RAM) bytes for the PC. The commit limit is RAM plus page file: when every
    program's committed memory reaches it, the next request fails and the program that made it crashes."""
    m = _MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    return int(m.ullTotalPageFile), int(m.ullAvailPageFile), int(m.ullTotalPhys)


def _rect(h):
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def editor_windows(popups=False):
    """Visible top-level windows owned by the Mod Editor process. With popups, its untitled windows too (menus, and
    tooltips), titled '<popup N>' in z-order with N=1 on top: those are what a context menu or a dropdown is."""
    found = []
    exe_cache = {}

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0 and not popups:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        exe = exe_cache.setdefault(pid.value, _exe_of(pid.value))
        if EDITOR_EXE.lower() not in exe.lower():
            return True
        l, t, r, b = _rect(h)
        if n == 0:
            if r - l > 20 and b - t > 20:
                found.append({"hwnd": int(h), "title": "", "pid": pid.value, "rect": [l, t, r, b], "minimized": False, "popup": True})
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        if r - l > 50 and b - t > 50:
            found.append({"hwnd": int(h), "title": buf.value, "pid": pid.value,
                          "rect": [l, t, r, b], "minimized": bool(user32.IsIconic(h))})
        return True

    user32.EnumWindows(cb, 0)
    for i, w in enumerate(w for w in found if w.get("popup")):
        w["title"] = f"<popup {i + 1}>"
    return found


def popups():
    """The editor's untitled popup windows (menus, tooltips), topmost first."""
    return [w for w in editor_windows(True) if w.get("popup")]


def menu_like(w):
    """A popup shaped like a menu rather than a tooltip: tooltips are wide strips, more than five times wider than tall."""
    return (w["rect"][2] - w["rect"][0]) <= 5 * (w["rect"][3] - w["rect"][1])


def largest_menu(pops):
    """The biggest menu-shaped popup, else the biggest popup, else None."""
    pops = list(pops)
    menus = [w for w in pops if menu_like(w)] or pops
    return max(menus, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1])) if menus else None


def find_window(title=None):
    """Editor window whose title contains `title` (case-insensitive); default = the main editor window."""
    if title and title.lower().lstrip("<").startswith("popup"):
        pops = popups()
        if not pops:
            raise WinError("No editor popup (menu) is open.")
        m = re.search(r"(\d+)", title)
        if m:                                   # '<popup N>': that one, in z-order
            return pops[min(int(m.group(1)), len(pops)) - 1]
        return largest_menu(pops)                                       # 'popup': the menu, not a tooltip
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


def foreground():
    """The window in front: {'hwnd', 'title', 'editor': whether the Mod Editor owns it}."""
    h = user32.GetForegroundWindow()
    if not h:
        return {"hwnd": 0, "title": "", "editor": False}
    n = user32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return {"hwnd": int(h), "title": buf.value, "editor": EDITOR_EXE.lower() in _exe_of(pid.value).lower()}


def foreground_is_editor():
    return foreground()["editor"]


def _check_front(expect, what):
    """Keys go to whichever window is in front. With `expect` (a window from find_window) that has to be this exact
    window, not just any editor window: Ctrl+A and Delete that reach the level editor select and delete its actors."""
    if not foreground_is_editor():
        raise WinError(f"The Mod Editor is not the foreground window; not {what}.")
    if expect is not None and not expect.get("popup"):
        front = foreground()
        if front["hwnd"] != expect["hwnd"]:
            raise WinError(f"'{expect['title']}' is not the window in front ('{front['title']}' is); not {what}. "
                           "A dialog may have opened over it: see editor_windows.")


# ---- capture -------------------------------------------------------------

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def pixels(win):
    """Raw BGRA pixels of one editor window (PrintWindow, so other windows on top don't matter): (bytes, width, height)."""
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
        return buf.raw, w, hgt
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(h, hdc)


def pixel(raw, w, x, y):
    """(r, g, b) at a window pixel of a pixels() buffer."""
    i = (y * w + x) * 4
    return raw[i + 2], raw[i + 1], raw[i]


def capture(win, max_width=1600):
    """PNG bytes of one editor window and the integer downscale factor used."""
    raw, w, hgt = pixels(win)
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
      "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE, "backquote": 0xC0,
      "semicolon": 0xBA, "equals": 0xBB, "slash": 0xBF, "lbracket": 0xDB, "backslash": 0xDC, "rbracket": 0xDD,
      "quote": 0xDE, "capslock": 0x14,
      "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63, "numpad4": 0x64,
      "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67, "numpad8": 0x68, "numpad9": 0x69}
# A punctuation character names the key that types it on a US keyboard. Its character code is not a key code:
# as one, '.' is Delete, '-' is Insert and '[' is the Windows key.
PUNCTUATION = {";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
               "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE}
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28}


def key_codes(combo):
    """Virtual-key codes for a combo such as 'ctrl+v', 'alt+p', 'delete', 'w', '['. A plus sign is 'plus'."""
    codes = []
    for p in combo.lower().split("+"):
        p = p.strip()
        if p in VK:
            codes.append(VK[p])
        elif p in PUNCTUATION:
            codes.append(PUNCTUATION[p])
        elif len(p) == 1 and p.isascii() and p.isalnum():
            codes.append(ord(p.upper()))
        else:
            raise WinError(f"Unknown key {p!r} in {combo!r}. Known: a letter, a digit, one of {' '.join(sorted(PUNCTUATION))}, "
                           f"or a name: {', '.join(sorted(VK))}.")
    return codes


def _bring_or_check(win, focus_first):
    """Focus the window, or, to keep an open menu open (focusing closes it), only check the editor is already in front."""
    if focus_first and not win.get("popup"):
        focus(win)
    elif not foreground_is_editor():
        raise WinError("The Mod Editor is not the foreground window, and bringing it to the front would close any open menu; "
                       "click without keep_menu first.")


def hover(win, x, y):
    """Move the mouse to a window pixel with a one-pixel wiggle, so the editor registers the hover."""
    l, t, _, _ = _rect(win["hwnd"])
    user32.SetCursorPos(l + x, t + y)
    time.sleep(0.12)
    user32.SetCursorPos(l + x + 1, t + y)
    time.sleep(0.12)
    user32.SetCursorPos(l + x, t + y)
    time.sleep(0.15)


def click(win, x, y, button="left", double=False, focus_first=True):
    """Click at window-relative pixel (x, y). focus_first=False keeps an open menu open: a popup window never takes
    focus first, and a normal window is only clicked when the editor is already in front."""
    l, t, r, b = _rect(win["hwnd"])
    if not (0 <= x < r - l and 0 <= y < b - t):
        raise WinError(f"({x}, {y}) is outside the window ({r - l}x{b - t})")
    _bring_or_check(win, focus_first)
    user32.SetCursorPos(l + x, t + y)
    time.sleep(0.15)
    down, up = (0x2, 0x4) if button == "left" else (0x8, 0x10)
    for _ in range(2 if double else 1):
        user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.08)


def drag(win, x1, y1, x2, y2, button="left", focus_first=True, steps=12):
    """Drag with the mouse from one window pixel to another, in steps, as a hand would: wires a pin to a pin, pans."""
    l, t, r, b = _rect(win["hwnd"])
    for x, y in ((x1, y1), (x2, y2)):
        if not (0 <= x < r - l and 0 <= y < b - t):
            raise WinError(f"({x}, {y}) is outside the window ({r - l}x{b - t})")
    _bring_or_check(win, focus_first)
    hover(win, x1, y1)
    down, up = (0x2, 0x4) if button == "left" else (0x8, 0x10)
    user32.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.15)
    for s in range(1, steps + 1):
        user32.SetCursorPos(l + x1 + (x2 - x1) * s // steps, t + y1 + (y2 - y1) * s // steps)
        time.sleep(0.03)
    time.sleep(0.2)
    user32.mouse_event(up, 0, 0, 0, 0)
    time.sleep(0.4)


def keys(combo, hold_ms=50, expect=None):
    """Press a combo such as 'ctrl+v', 'alt+p', 'delete', 'w'. Refuses unless the editor is in front, and with
    `expect` (a window) unless that exact window is."""
    codes = key_codes(combo)
    _check_front(expect, f"sending {combo}")
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


def type_text(text, expect=None):
    """Type Unicode text into the focused editor control."""
    _check_front(expect, "typing")
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


def close_main_window(win):
    """Ask the main editor window to close (WM_CLOSE), the way the title bar's X does. Only close_editor uses this."""
    user32.PostMessageW(win["hwnd"], 0x0010, 0, 0)
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


# ---- one driver at a time -------------------------------------------------

class InputLock:
    """A named mutex shared by every abf-mod-editor-mcp server process in this Windows session. One of them holds it
    while a tool drives the editor (mouse, keys, a close), so two chats never type into it at once. Windows frees it
    when its owner exits, so a server that dies in the middle never blocks the others."""

    NAME = "Local\\abf-mod-editor-mcp-driver"

    def __init__(self, name=None):
        self.handle = kernel32.CreateMutexW(None, False, name or self.NAME)
        self.held = 0

    def acquire(self, timeout=120.0):
        """True when this process now drives. False after `timeout` seconds of another process holding the lock."""
        if self.held:                           # a tool that calls another tool
            self.held += 1
            return True
        if not self.handle:                     # no mutex could be made: don't block the tool over it
            self.held = 1
            return True
        got = kernel32.WaitForSingleObject(self.handle, int(max(0.0, timeout) * 1000))
        if got in (0x0, 0x80):                  # WAIT_OBJECT_0, or WAIT_ABANDONED: the last owner exited while holding it
            self.held = 1
            return True
        return False

    def release(self):
        if self.held:
            self.held -= 1
            if not self.held and self.handle:
                kernel32.ReleaseMutex(self.handle)
