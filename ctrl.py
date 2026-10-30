"""Window + input + capture, ctypes only.

为什么不用 pydirectinput / pygetwindow：本机三个 python 都没装，而 pip 拉这两个
包要连带 pyscreeze / pygetwindow / mouseinfo 一堆。SendInput 自己写 60 行就够，
而且能直接发 scancode —— 游戏走 DirectInput 时比 virtual-key 稳。

    import ctrl
    w = ctrl.find_window("Stellaris")
    ctrl.focus(w)
    ctrl.shot(w, "shots/a.png")
    ctrl.click(w, 0.5, 0.5)          # 窗口内相对坐标
    ctrl.key("space")
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

import mss
import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDPIAware()

# ── SendInput 结构 ──────────────────────────────────────────────────────────
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


def _send(*inputs: INPUT) -> int:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def _mouse(dx: int, dy: int, flags: int, data: int = 0) -> None:
    mi = MOUSEINPUT(dx, dy, data, flags, 0, 0)
    inp = INPUT(INPUT_MOUSE)
    inp.u.mi = mi
    _send(inp)


# virtual-key → scancode（只列用得上的）
_VK = {
    "esc": 0x01, "return": 0x1C, "enter": 0x1C, "space": 0x39, "tab": 0x0F,
    "backspace": 0x0E, "lshift": 0x2A, "lctrl": 0x1D, "lalt": 0x38,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
    "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "f11": 0x57, "f12": 0x58,
    "up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D,
    "delete": 0x53, "home": 0x47,
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31, "m": 0x32,
}
_EXTENDED = {0x48, 0x50, 0x4B, 0x4D, 0x53, 0x47}


def key(name: str, up: bool = False) -> None:
    scan = _VK[name.lower()]
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    if scan in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    ki = KEYBDINPUT(0, scan, flags, 0, 0)
    inp = INPUT(INPUT_KEYBOARD)
    inp.u.ki = ki
    _send(inp)


def press(name: str, hold: float = 0.05) -> None:
    key(name)
    time.sleep(hold)
    key(name, up=True)


# ── 窗口 ────────────────────────────────────────────────────────────────────
@dataclass
class Win:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int

    def __str__(self) -> str:
        return f"<{self.title!r} {self.width}x{self.height} @({self.left},{self.top})>"


def _enum() -> list[Win]:
    out: list[Win] = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        r = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        # 客户区 → 屏幕坐标
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        out.append(Win(hwnd, buf.value, pt.x, pt.y, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def find_window(title: str = "Stellaris", min_width: int = 400) -> Win | None:
    cands = [w for w in _enum() if title.lower() in w.title.lower() and w.width >= min_width]
    if not cands:
        return None
    return max(cands, key=lambda w: w.width * w.height)


def focus(win: Win) -> bool:
    """抢前台并把窗口钉在最上层。

    SetForegroundWindow 单独用没用——Windows 有前台锁，非前台进程调它只闪一下
    任务栏。两条绕过都要上：
      1. 先假装按了一下 Alt，让本次调用取得"用户输入"豁免；
      2. AttachThreadInput 挂到当前前台线程上，共享它的前台权限。
    最后 SetWindowPos(HWND_TOPMOST)：本机 Marvis 会周期性弹窗抢前台，钉住之后
    即使它冒出来也压不住游戏。
    """
    if user32.IsIconic(win.hwnd):
        user32.ShowWindow(win.hwnd, 9)      # SW_RESTORE

    # 1) Alt 豁免
    _key_raw(0x38, up=False)
    _key_raw(0x38, up=True)

    # 2) 挂前台线程
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != my_tid:
        attached = bool(user32.AttachThreadInput(my_tid, fg_tid, True))

    try:
        user32.BringWindowToTop(win.hwnd)
        user32.SetForegroundWindow(win.hwnd)
        user32.SetFocus(win.hwnd)
        user32.SwitchToThisWindow(win.hwnd, True)
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)

    pin_on_top(win)
    time.sleep(0.25)
    return user32.GetForegroundWindow() == win.hwnd


HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010


def pin_on_top(win: Win, on: bool = True) -> None:
    user32.SetWindowPos(win.hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST,
                        0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def normalize(win: Win, x: int = 0, y: int = 0) -> Win:
    """把窗口拉回主屏 (x,y)、钉最上层，并把 Win 里的矩形更新成当前真实值。

    最小化过的无边框窗口坐标会跑到 -32000 甚至 -64000（高 DPI 下翻倍），
    这时候 mss 抓出来全是黑的——所以每次动窗口后都要重新量一遍。
    """
    user32.ShowWindow(win.hwnd, 9)                  # SW_RESTORE
    time.sleep(0.3)
    user32.SetWindowPos(win.hwnd, HWND_TOPMOST, x, y, 0, 0,
                        SWP_NOSIZE | SWP_NOACTIVATE | 0x0040)   # SWP_SHOWWINDOW
    time.sleep(0.3)
    r = wintypes.RECT()
    user32.GetClientRect(win.hwnd, ctypes.byref(r))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(win.hwnd, ctypes.byref(pt))
    win.left, win.top = pt.x, pt.y
    win.width, win.height = r.right - r.left, r.bottom - r.top
    return win


def _key_raw(vk: int, up: bool) -> None:
    flags = 0x0002 if up else 0
    user32.keybd_event(vk, 0, flags, 0)


def foreground_title() -> str:
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


# ── 截图 ────────────────────────────────────────────────────────────────────
_SCT = mss.mss()


def grab(win: Win) -> np.ndarray:
    """窗口客户区的 BGR 图。"""
    box = {"left": win.left, "top": win.top, "width": win.width, "height": win.height}
    raw = _SCT.grab(box)
    return np.array(raw)[:, :, :3]


def shot(win: Win, path: str) -> np.ndarray:
    import cv2
    img = grab(win)
    cv2.imwrite(path, img)
    return img


# ── 鼠标 ────────────────────────────────────────────────────────────────────
def _to_abs(win: Win, fx: float, fy: float) -> tuple[int, int]:
    x = win.left + int(win.width * fx)
    y = win.top + int(win.height * fy)
    sw = user32.GetSystemMetrics(78)     # SM_CXVIRTUALSCREEN
    sh = user32.GetSystemMetrics(79)
    sl = user32.GetSystemMetrics(76)
    st = user32.GetSystemMetrics(77)
    ax = int((x - sl) * 65535 / (sw - 1))
    ay = int((y - st) * 65535 / (sh - 1))
    return ax, ay


def move(win: Win, fx: float, fy: float) -> None:
    """把光标移到窗口内的相对位置。

    优先用 SetCursorPos —— 它直接设物理像素，不经过 SendInput 的归一化换算。
    之前用 MOUSEEVENTF_ABSOLUTE|VIRTUALDESK 把 (x,y) 折成 0..65535 再发，
    在某些环境（多显示器/缩放/有游戏占了 RawInput）下会被静默吞掉，
    表现是「光标一动不动，但点击照样发出」→ 点到的地方全是上一个位置。
    归一化路径留作退路，SetCursorPos 失败时才用。
    """
    x = win.left + int(win.width * fx)
    y = win.top + int(win.height * fy)
    if user32.SetCursorPos(int(x), int(y)):
        return
    ax, ay = _to_abs(win, fx, fy)
    _mouse(ax, ay, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)


def click(win: Win, fx: float, fy: float, drag: int = 0, settle: float = 0.0) -> None:
    """点客户区相对位置。

    ⚠ 两个默认值都是 0，这是刻意的：

    - `settle=0`：`SetCursorPos` 之后**不能等**。这台机器常年跑着 Alas / MAA 这类
      安卓自动化，它们会不停模拟点击。光标设进去之后哪怕等 0.06 秒，也可能已经
      被别人挪走，于是 LEFT DOWN 落在错误的位置。真人是「移到→瞬间按下」，
      中间没有缝。
    - `drag=0`：微移本来是给思潮圆阵那种自绘控件用的，但它走
      `MOUSEEVENTF_ABSOLUTE` 归一化路径，在部分环境下会被静默吞掉。
      确实需要微移时显式传 drag=2。
    """
    move(win, fx, fy)
    if settle:
        time.sleep(settle)
    _mouse(0, 0, MOUSEEVENTF_LEFTDOWN)
    if drag:
        time.sleep(0.04)
        ax, ay = _to_abs(win, fx + drag / win.width, fy + drag / win.height)
        _mouse(ax, ay, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    _mouse(0, 0, MOUSEEVENTF_LEFTUP)


def right_click(win: Win, fx: float, fy: float) -> None:
    move(win, fx, fy)
    time.sleep(0.06)
    _mouse(0, 0, MOUSEEVENTF_RIGHTDOWN)
    time.sleep(0.04)
    _mouse(0, 0, MOUSEEVENTF_RIGHTUP)
    time.sleep(0.08)


def scroll(win: Win, fx: float, fy: float, clicks: int) -> None:
    move(win, fx, fy)
    time.sleep(0.05)
    _mouse(0, 0, MOUSEEVENTF_WHEEL, data=(-120 * clicks) & 0xFFFFFFFF)


# ── 文字输入（中文走剪贴板）────────────────────────────────────────────────
def chord(*keys: str, gap: float = 0.05) -> None:
    """依次按住 → 松开最后一个。chord('lctrl','a')。"""
    for k in keys:
        key(k)
        time.sleep(gap)
    key(keys[-1], up=True)
    time.sleep(gap)
    for k in reversed(keys[:-1]):
        key(k, up=True)
        time.sleep(gap)


def set_clipboard(text: str) -> None:
    """直接写 Windows 剪贴板（CF_UNICODETEXT）。

    不用 PowerShell 的 Set-Clipboard：这条链上它要 STA 而且要等 profile 加载，
    非零退出过一次就白等。Win32 剪贴板 API 三步就完事。
    """
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(None):
        raise OSError("OpenClipboard 失败")
    try:
        user32.EmptyClipboard()
        h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        user32.CloseClipboard()
    time.sleep(0.1)


def paste(win: Win, fx: float, fy: float, text: str, clear: bool = True) -> None:
    """点进输入框 → 清空 → 粘贴。

    群星的输入框三条脾气：点击不微移才认、聚焦要等半秒、Ctrl+A 不生效。
    所以清空靠 End + 一串 Backspace。
    """
    click(win, fx, fy, drag=0)
    time.sleep(0.45)
    if clear:
        press("delete")
        for _ in range(48):
            key("backspace")
            key("backspace", up=True)
        time.sleep(0.15)
    set_clipboard(text)
    time.sleep(0.15)
    chord("lctrl", "v")
    time.sleep(0.3)


if __name__ == "__main__":
    for w in _enum():
        if w.width > 500:
            print(w, "fg" if user32.GetForegroundWindow() == w.hwnd else "")
