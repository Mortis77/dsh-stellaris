"""按窗口抓图：PrintWindow，不碰屏幕。

为什么不直接用 mss 截屏幕：
  1. 窗口被别的程序挡住就截到别人的画面（今早星铁盖住群星，截出来是星铁）
  2. 分辨率一变，所有硬编码坐标全废
  3. 抢前台会打断用户

PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT) 让窗口自己把自己画到一块内存位图上，
DWM 合成的内容也能拿到。被遮挡、最小化以外的状态都能截。

    import winshot
    w = winshot.find('Stellaris')
    img = winshot.grab(w)      # BGR ndarray，尺寸 = 客户区
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.SetProcessDPIAware()

PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0


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


@dataclass
class Win:
    hwnd: int
    title: str
    width: int
    height: int

    def __str__(self) -> str:
        return f"<{self.title!r} {self.width}x{self.height}>"


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
        out.append(Win(hwnd, buf.value, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def find(title: str, min_width: int = 400) -> Win | None:
    cands = [w for w in _enum() if title.lower() in w.title.lower() and w.width >= min_width]
    if not cands:
        return None
    return max(cands, key=lambda w: w.width * w.height)


def grab(win: Win, flags: int = PW_RENDERFULLCONTENT) -> np.ndarray:
    """返回客户区的 BGR 图。全空的图（全黑）说明这个窗口不吃 PrintWindow。"""
    w, h = win.width, win.height
    if w <= 0 or h <= 0:
        raise ValueError(f"窗口尺寸非法: {w}x{h}")

    hdc = user32.GetDC(win.hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(memdc, bmp)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    try:
        ok = user32.PrintWindow(win.hwnd, memdc, flags)
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        if got == 0:
            raise RuntimeError("GetDIBits 失败")
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        return arr[:, :, :3].copy()
    finally:
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(win.hwnd, hdc)


def looks_blank(img: np.ndarray) -> bool:
    """全黑或者几乎全黑 —— 说明这个窗口不能用 PrintWindow 抓。"""
    return float(img.mean()) < 3.0


if __name__ == "__main__":
    import cv2
    w = find("Stellaris")
    if w is None:
        print("没找到窗口")
        raise SystemExit(1)
    print("window:", w)
    for flag, name in ((PW_RENDERFULLCONTENT, "full"), (PW_CLIENTONLY, "client")):
        try:
            img = grab(w, flag)
            blank = looks_blank(img)
            print(f"  {name}: shape={img.shape} mean={img.mean():.1f} blank={blank}")
            cv2.imwrite(f"shots/pw_{name}.png", img)
        except Exception as exc:                                  # noqa: BLE001
            print(f"  {name}: 失败 {type(exc).__name__}: {exc}")
