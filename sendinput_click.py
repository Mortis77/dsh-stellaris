"""用 SendInput 发真实的鼠标移动 + 点击。

## 为什么需要这个

DXVK 下群星改用 Vulkan 呈现，自绘 UI 读的是**真实的鼠标事件流**
（RawInput / SendInput），而不是系统光标位置。

`SetCursorPos` 只改「光标在哪」，不发事件——游戏看不见。
`PostMessage(WM_LBUTTONDOWN)` 只进窗口消息队列——自绘 UI 不读消息。

所以要么发真实的相对移动，要么什么都别指望。

## 做法

1. `GetCursorPos` 拿当前位置
2. `SendInput` 发 `MOUSEEVENTF_MOVE`，dx/dy 是**相对位移**
3. 需要的话再发一次 1px 的微移（有些引擎要连续事件才刷新 hover）
4. `SendInput` 发 LEFTDOWN / LEFTUP

    python sendinput_click.py 685 294
    python sendinput_click.py 685 294 --settle 400
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# DPI 感知：不做的话 GetClientRect / ClientToScreen 都是逻辑坐标（960x540），
# 而调用方给的是截图上量出来的物理像素（1920x1080）。
try:
    user32.SetProcessDpiAwarenessContext(-4)      # PER_MONITOR_AWARE_V2
except Exception:                                  # noqa: BLE001
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:                              # noqa: BLE001
        user32.SetProcessDPIAware()

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

TITLE = "Stellaris"


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


def send_mouse(flags: int, dx: int = 0, dy: int = 0) -> int:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, None)
    return int(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


def find_window(title: str) -> int:
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        hwnd = user32.FindWindowW(title, None)
    return int(hwnd or 0)


def click(hwnd: int, cx: int, cy: int, settle_ms: int = 350, hold_ms: int = 90) -> dict:
    """cx, cy：客户区**物理**像素（跟截图上量的一致）。"""
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin))
    tx, ty = origin.x + int(cx), origin.y + int(cy)

    cur = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cur))
    dx, dy = tx - cur.x, ty - cur.y

    # 1. 真实相对移动
    n1 = send_mouse(MOUSEEVENTF_MOVE, dx, dy)
    time.sleep(0.08)
    # 2. 再补两次 1px 微移，让 hover 状态机确定刷新
    send_mouse(MOUSEEVENTF_MOVE, 1, 0)
    time.sleep(0.05)
    send_mouse(MOUSEEVENTF_MOVE, -1, 0)

    if settle_ms:
        time.sleep(settle_ms / 1000.0)

    # 3. 点击
    d = send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(hold_ms / 1000.0)
    u = send_mouse(MOUSEEVENTF_LEFTUP)

    got = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(got))
    return {"moved": n1, "down": d, "up": u,
            "target": (tx, ty), "actual": (got.x, got.y)}


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) < 2:
        print(__doc__)
        return 1
    hwnd = find_window(TITLE)
    if not hwnd:
        print(f"找不到窗口: {TITLE}")
        return 1
    cx, cy = int(argv[0]), int(argv[1])
    settle = int(argv[argv.index("--settle") + 1]) if "--settle" in argv else 350
    res = click(hwnd, cx, cy, settle)
    print("client click", (cx, cy), "->", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
