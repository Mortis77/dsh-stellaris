"""SendInput 绝对模式点击：MOUSEEVENTF_ABSOLUTE + 归一化到 0..65535。

和 sendinput_click.py 的区别：那个用相对移动（MOUSEEVENTF_MOVE + dx/dy），
这个用绝对定位（MOUSEEVENTF_ABSOLUTE + 归一化坐标）。

两者走的是不同分支，在有些引擎里表现不一样——相对移动被吃掉但绝对定位能过，
或者反过来。所以两个都要试。

    python sendinput_abs.py 1205 767
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

try:
    user32.SetProcessDpiAwarenessContext(-4)
except Exception:                                  # noqa: BLE001
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:                              # noqa: BLE001
        user32.SetProcessDPIAware()

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

TITLE = "Stellaris"


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send(flags: int, dx: int = 0, dy: int = 0) -> int:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, None)
    return int(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


def find_window(title: str) -> int:
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        hwnd = user32.FindWindowW(title, None)
    return int(hwnd or 0)


def real_screen_size() -> tuple[int, int]:
    """拿物理分辨率。EnumDisplaySettings 不受 DPI 虚拟化影响，
    而 GetSystemMetrics 在 DPI unaware 进程里返回的是逻辑尺寸。"""
    class DEVMODE(ctypes.Structure):
        _fields_ = [("dmDeviceName", wintypes.WCHAR * 32),
                    ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
                    ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                    ("dmFields", wintypes.DWORD), ("dmPositionX", ctypes.c_long),
                    ("dmPositionY", ctypes.c_long), ("dmDisplayOrientation", wintypes.DWORD),
                    ("dmDisplayFixedOutput", wintypes.DWORD), ("dmColor", ctypes.c_short),
                    ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                    ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                    ("dmFormName", wintypes.WCHAR * 32), ("dmLogPixels", wintypes.WORD),
                    ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                    ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                    ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
                    ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD),
                    ("dmDitherType", wintypes.DWORD), ("dmReserved1", wintypes.DWORD),
                    ("dmReserved2", wintypes.DWORD), ("dmPanningWidth", wintypes.DWORD),
                    ("dmPanningHeight", wintypes.DWORD)]
    dm = DEVMODE(); dm.dmSize = ctypes.sizeof(DEVMODE)
    if user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
        return int(dm.dmPelsWidth), int(dm.dmPelsHeight)
    return 0, 0


def dpi_scale() -> float:
    """物理宽 / 逻辑宽。DPI unaware 进程里 GetSystemMetrics 给逻辑值。"""
    rw, _ = real_screen_size()
    lw = int(user32.GetSystemMetrics(0)) or rw
    if not rw or not lw:
        return 1.0
    return round(rw / lw, 4) if rw > lw else 1.0


def click(hwnd: int, cx: int, cy: int, settle_ms: int = 300, hold_ms: int = 80) -> dict:
    # ClientToScreen 在 DPI unaware 进程里返回逻辑坐标，要乘回缩放比
    sc = dpi_scale()
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin))
    ox, oy = int(origin.x * sc), int(origin.y * sc)
    tx, ty = ox + int(cx), oy + int(cy)

    g = user32.GetSystemMetrics
    vx, vy = int(g(SM_XVIRTUALSCREEN)), int(g(SM_YVIRTUALSCREEN))
    vw, vh = int(g(SM_CXVIRTUALSCREEN)) or 1920, int(g(SM_CYVIRTUALSCREEN)) or 1080

    # 归一化到 0..65535
    ax = int((tx - vx) * 65535 / max(1, vw - 1))
    ay = int((ty - vy) * 65535 / max(1, vh - 1))

    n1 = send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)
    time.sleep(0.10)
    # 补一次 1 单位微动，触发 hover 刷新
    send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax + 1, ay)
    time.sleep(0.05)
    send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay)

    if settle_ms:
        time.sleep(settle_ms / 1000.0)

    d = send(MOUSEEVENTF_LEFTDOWN)
    time.sleep(hold_ms / 1000.0)
    u = send(MOUSEEVENTF_LEFTUP)

    got = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(got))
    return {"moved": n1, "down": d, "up": u, "scale": sc, "origin": (ox, oy),
            "norm": (ax, ay), "target": (tx, ty), "actual": (got.x, got.y)}


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) < 2:
        print(__doc__)
        return 1
    hwnd = find_window(TITLE)
    if not hwnd:
        print(f"找不到窗口: {TITLE}")
        return 1
    res = click(hwnd, int(argv[0]), int(argv[1]))
    print("abs click", (argv[0], argv[1]), "->", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
