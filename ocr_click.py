#!/usr/bin/env python3
"""OCR 定位点击：把「找按钮」这件事交给文字识别，不再靠手算坐标。

为什么要这个：群星的自绘 UI 在不同弹窗里按钮位置完全不一样，靠预览图比例换算
去猜坐标，成功率不到三成。而按钮上的字（是／否／确认／取消）是稳定的，
用 OCR 找到那两个字，点它的中心就行。

用法：

    python ocr_click.py 是              # 找到屏幕上第一个「是」，点它
    python ocr_click.py 确认 --dry      # 只报位置，不点
    python ocr_click.py 是 --region 600,900,2000,1500

依赖 rapidocr_onnxruntime，装在 bili-vision-venv 里，所以要用那个解释器跑：

    C:\\Users\\Mors\\.dsh\\bili-vision-venv\\Scripts\\python.exe ocr_click.py 是
"""
from __future__ import annotations

import argparse
import sys
import time

import cv2
import numpy as np


def load_ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def find_text(engine, img, needle: str, min_score: float = 0.55):
    """返回所有匹配 needle 的框：[(score, cx, cy, box), ...]"""
    result, _ = engine(img)
    if not result:
        return []
    hits = []
    for box, text, score in result:
        if needle in text and score >= min_score:
            pts = np.array(box, dtype=np.float32)
            cx = int(pts[:, 0].mean())
            cy = int(pts[:, 1].mean())
            hits.append((float(score), cx, cy, box))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("needle", help="要找的文字，例如 是 / 否 / 确认 / 取消")
    ap.add_argument("--region", default="", help="限定区域 x1,y1,x2,y2（原图像素）")
    ap.add_argument("--dry", action="store_true", help="只报位置，不点击")
    ap.add_argument("--index", type=int, default=0, help="命中多个时点第几个（从 0 起）")
    args = ap.parse_args()

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    import ctrl
    import winshot

    w = ctrl.find_window("Stellaris")
    if w is None:
        print("找不到游戏窗口")
        return 1
    ws = winshot.find("Stellaris")
    img = winshot.grab(ws) if ws else ctrl.grab(w)

    x1 = y1 = 0
    x2, y2 = img.shape[1], img.shape[0]
    if args.region:
        x1, y1, x2, y2 = (int(v) for v in args.region.split(","))
        img = img[y1:y2, x1:x2]

    engine = load_ocr()
    hits = find_text(engine, img, args.needle)
    if not hits:
        print(f"没找到「{args.needle}」")
        return 2

    hits.sort(key=lambda h: (h[2], h[1]))   # 从上到下、从左到右，符合点界面的直觉
    for i, (score, cx, cy, _box) in enumerate(hits):
        print(f"[{i}] {score:.2f}  ({cx + x1}, {cy + y1})")

    if args.dry:
        return 0

    score, cx, cy, _ = hits[min(args.index, len(hits) - 1)]
    ax, ay = cx + x1, cy + y1
    ctrl.focus(w)
    time.sleep(0.3)
    # 交给 sendinput_abs 点：DXVK 下只有 SendInput 的绝对模式能过，
    # 而且它会自己补 DPI 缩放（本机 200%，ClientToScreen 返回逻辑坐标）
    import sendinput_abs
    hwnd = sendinput_abs.find_window(sendinput_abs.TITLE)
    res = sendinput_abs.click(hwnd, int(ax), int(ay))
    print('sendinput:', res)
    print(f"clicked ({ax}, {ay})  score={score:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
