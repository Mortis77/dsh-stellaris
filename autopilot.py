"""Keep the game running unattended.

判断「游戏有没有在推进」的办法：**直接比较顶栏日期区域的像素**。
比读颜色可靠得多——顶栏背景会随界面变，颜色判据会误判，一旦把 running 判成
paused 就去点播放键，反而把游戏按停了。2026-10-28 凌晨就是这么把一小时打水漂的。

所以现在的逻辑是：
  1. 每轮截一次日期区域，算 hash
  2. hash 变了 = 在跑，什么都不做
  3. hash 没变 = 停了，先看有没有弹窗；有就点选项，没有就点播放键
"""
from __future__ import annotations

import argparse
import hashlib
import time
from datetime import datetime
from pathlib import Path

import cv2

import ctrl

HERE = Path(__file__).resolve().parent
LOG = HERE / "autopilot.log"
SHOTS = HERE / "shots" / "auto"
# 人（或 LLM）在手动操作时，放一个这个文件让 autopilot 停手，避免抢点击
PAUSE_FLAG = HERE / "autopilot.pause"

# 顶栏播放/暂停按钮（2560x1600 实测）
PLAY_BTN = (2170, 32)
# 日期区域：只要这段像素不变，就说明时间没在走
DATE_BOX = (2288, 8, 2412, 30)   # 只框日期数字，别框到下面会闪的「暂停」

CHOICE_POINTS = [
    (1293, 1065),
    (1293, 1126),
    (1293, 1156),
    (1293, 1185),
    (1293, 1215),
    (1293, 1245),
    (1293, 1275),
]
CLOSE_BTN = (1722, 391)


def grab(w):
    return ctrl.grab(w)


def date_hash(img) -> str:
    x1, y1, x2, y2 = DATE_BOX
    return hashlib.md5(img[y1:y2, x1:x2].tobytes()).hexdigest()


def has_popup(img) -> bool:
    h, w_ = img.shape[:2]
    patch = img[int(h * 0.32):int(h * 0.55), int(w_ * 0.40):int(w_ * 0.62)]
    if patch.size == 0:
        return False
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    return float(gray.std()) < 12.0 and 15 < float(gray.mean()) < 90


def log(line: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}  {line}\n")


def save_frame(img, tag: str) -> Path:
    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / f"{datetime.now():%H%M%S}_{tag}.png"
    cv2.imwrite(str(p), img)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=999)
    ap.add_argument("--interval", type=float, default=6.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    deadline = time.time() + args.minutes * 60
    log(f"=== autopilot start, {args.minutes:g} min ===")

    w = ctrl.find_window("Stellaris")
    if w is None:
        log("no game window")
        return 1
    if w.left < -1000 or w.width < 800:
        w = ctrl.normalize(w)

    prev = date_hash(grab(w))
    stuck = 0
    while time.time() < deadline:
        time.sleep(args.interval)
        if PAUSE_FLAG.exists():
            continue
        w = ctrl.find_window("Stellaris")
        if w is None:
            log("game window gone")
            return 1
        img = grab(w)
        cur = date_hash(img)

        if cur != prev:                      # 时间在走，正常
            prev = cur
            stuck = 0
            if args.once:
                return 0
            continue

        # 时间没动 -> 卡住了
        stuck += 1
        log(f"time stalled ({stuck})")
        ctrl.focus(w)

        if has_popup(img):
            p = save_frame(img, "popup")
            for (x, y) in CHOICE_POINTS:
                ctrl.click(w, x / w.width, y / w.height)
                time.sleep(0.75)
                if date_hash(grab(w)) != cur:
                    log(f"popup -> choice@({x},{y}) {p.name}")
                    break
            else:
                ctrl.click(w, CLOSE_BTN[0] / w.width, CLOSE_BTN[1] / w.height)
                time.sleep(0.7)
                log(f"popup -> closed {p.name}")
        else:
            ctrl.focus(w)          # 没焦点时 SendInput 打不到游戏，这是 v2 的头号 bug
            ctrl.click(w, PLAY_BTN[0] / w.width, PLAY_BTN[1] / w.height)
            time.sleep(1.2)
            log("pressed play")

        prev = date_hash(grab(w))
        if args.once:
            return 0

    log("=== autopilot done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
