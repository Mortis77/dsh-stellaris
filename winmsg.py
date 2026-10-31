"""往窗口直接投鼠标消息，不经过系统输入队列。

为什么需要：装上 DXVK 之后游戏改用 Vulkan 呈现，`SetCursorPos` + `mouse_event`
这套走系统输入队列的方式对它无效——光标位置是对的，按键也发了，
但游戏读不到（很可能改成了 RawInput，只看 HID 设备的原始报告）。

`PostMessage` 把 `WM_LBUTTONDOWN` 直接塞进窗口消息队列，绕过整条输入栈。
有些引擎（含部分 Unity 和 SDL 游戏）照样收。

    python winmsg.py click 67 326
    python winmsg.py click --screen 387 586
"""
from __future__ import annotations

import sys
import time

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# DPI 感知：不做的话 GetClientRect 返回逻辑尺寸（960x540），
# 而截图和调用方给的都是物理像素（1920x1080），差一倍。
try:
    user32.SetProcessDpiAwarenessContext(-4)      # PER_MONITOR_AWARE_V2
except Exception:                                  # noqa: BLE001
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:                              # noqa: BLE001
        user32.SetProcessDPIAware()
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

TITLE = "Stellaris"


def find_window(title: str) -> int:
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        hwnd = user32.FindWindowW(title, None)
    return int(hwnd or 0)


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = wintypes.POINT(x, y)
    user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt))
    return pt.x, pt.y


def post_click(hwnd: int, cx: int, cy: int, hold_ms: int = 60) -> dict:
    """cx, cy 是客户区坐标。"""
    # 先投一个 MOVE，很多引擎要先收到 move 才认 down
    lp = (int(cy) << 16) | (int(cx) & 0xFFFF)
    ok_move = int(user32.PostMessageW(wintypes.HWND(hwnd), WM_MOUSEMOVE, 0, lp))
    time.sleep(0.05)
    ok_down = int(user32.PostMessageW(wintypes.HWND(hwnd), WM_LBUTTONDOWN, MK_LBUTTON, lp))
    time.sleep(hold_ms / 1000.0)
    ok_up = int(user32.PostMessageW(wintypes.HWND(hwnd), WM_LBUTTONUP, 0, lp))
    return {"move": ok_move, "down": ok_down, "up": ok_up}


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not argv or argv[0] != "click":
        print(__doc__)
        return 1

    hwnd = find_window(TITLE)
    if not hwnd:
        print(f"找不到窗口: {TITLE}")
        return 1

    rc = wintypes.RECT()
    user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rc))
    print(f"hwnd={hwnd} client={rc.right}x{rc.bottom}")

    rest = argv[1:]
    if rest and rest[0] == "--screen":
        sx, sy = int(rest[1]), int(rest[2])
        # 屏幕坐标反推客户区
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin))
        cx, cy = sx - origin.x, sy - origin.y
    else:
        cx, cy = int(rest[0]), int(rest[1])

    print(f"client click ({cx}, {cy})")
    res = post_click(hwnd, cx, cy)
    print("result:", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
