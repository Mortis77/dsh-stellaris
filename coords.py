"""坐标表：把「某个按钮在哪」和「屏幕分辨率」解耦。

## 为什么需要这个

之前所有坐标都是硬编码的客户区像素，按 1920×1080 量的。一换显示屏
（现在是 2560×1600）全部作废——而且群星的 UI **不是等比缩放**的，
顶栏高度是固定像素，左侧列表宽度也不是 1080 的百分比，所以简单乘个
比例也会偏。

## 做法

每个坐标存**归一化比例** `(fx, fy)`，基准分辨率记在 `BASE`。
运行时按当前窗口尺寸换算成像素：

    px, py = resolve("main.new_game", win)   # 已经算好，直接点

如果换算结果不准（非等比缩放导致），跑一次校准：

    python coords.py calibrate

它会截图、OCR 几个锚点文字、对比「预期位置」和「实际位置」，
把补偿系数写进 `coords.calib.json`。两块屏各存一套，切屏时自动选。

## 先用 OCR，坐标兜底

**能按文字找的，就别用坐标。** `ui.py ocr 新游戏 --click` 永远比这里准，
因为它根本不管分辨率。这里存的坐标是给「纯图标、没有文字」的元素用的
（顶栏按钮、侧边栏图标、组件区的自动托管开关）。

## 加新坐标

量一次当前窗口里的像素位置，然后：

    python coords.py add main.space_port 1730 620

会自动按当前窗口尺寸换算成比例存进来。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CALIB = HERE / "coords.calib.json"
STORE = HERE / "coords.json"

# 所有比例都是相对这个分辨率算出来的
BASE_W, BASE_H = 1920, 1080

# name → (fx, fy)。基准 1920×1080 实测值除以 BASE。
DEFAULTS: dict[str, tuple[float, float]] = {
    # ── 主菜单 ──────────────────────────────────────────────
    "menu.new_game":     (108 / 1920, 300 / 1080),
    "menu.load_game":    (108 / 1920, 350 / 1080),
    "menu.multiplayer":  (108 / 1920, 400 / 1080),
    "menu.settings":     (108 / 1920, 567 / 1080),
    "menu.quit":         (108 / 1920, 688 / 1080),
    # ── 帝国选择 / 开局设置 ─────────────────────────────────
    "empire.select":     (1447 / 1920, 895 / 1080),   # 「选择」
    "setup.start":       (1447 / 1920, 895 / 1080),   # 「开始征程」
    # ── 游戏内顶栏 ──────────────────────────────────────────
    "top.pause_play":    (1715 / 1920, 47 / 1080),    # 播放/暂停
    "top.speed_minus":   (1697 / 1920, 47 / 1080),
    "top.speed_plus":    (1830 / 1920, 47 / 1080),
    # ── 左侧边栏（x 固定 25）────────────────────────────────
    "side.empire":       (25 / 1920, 60 / 1080),      # 帝国视图
    "side.log_f1":       (25 / 1920, 110 / 1080),
    "side.society_f3":   (25 / 1920, 155 / 1080),
    "side.tech_f4":      (25 / 1920, 200 / 1080),
    "side.leaders_f5":   (25 / 1920, 250 / 1080),
    "side.species_f6":   (25 / 1920, 300 / 1080),
    "side.colonies_f7":  (25 / 1920, 350 / 1080),
    "side.expand_f8":    (25 / 1920, 375 / 1080),
    "side.fleets_f9":    (25 / 1920, 400 / 1080),
    "side.diplo_f10":    (25 / 1920, 450 / 1080),
    "side.market":       (25 / 1920, 600 / 1080),
    # ── 弹窗 ────────────────────────────────────────────────
    "popup.confirm":     (956 / 1920, 881 / 1080),    # 长文弹窗底部那个按钮
    "popup.close_x":     (1195 / 1920, 370 / 1080),   # 右上角 ×
}


def load_store() -> dict[str, list[float]]:
    """读用户加过的坐标（coords.json），和 DEFAULTS 合并。"""
    merged = {k: [v[0], v[1]] for k, v in DEFAULTS.items()}
    if STORE.exists():
        try:
            merged.update(json.loads(STORE.read_text(encoding="utf-8")))
        except Exception:                                        # noqa: BLE001
            pass
    return merged


def load_calib() -> dict:
    """校准结果。没有就是空的（等于系数 1.0）。"""
    if CALIB.exists():
        try:
            return json.loads(CALIB.read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            pass
    return {}


def calib_for(win_w: int, win_h: int) -> tuple[float, float]:
    """取当前窗口尺寸对应的补偿系数。

    两块屏各存一条，按宽高精确匹配；匹配不到就用最接近的那条；
    都没有就返回 (1.0, 1.0)。
    """
    data = load_calib()
    key = f"{int(win_w)}x{int(win_h)}"
    if key in data:
        return float(data[key].get("sx", 1.0)), float(data[key].get("sy", 1.0))
    best, best_d = (1.0, 1.0), None
    for k, v in data.items():
        try:
            w, h = (int(x) for x in k.split("x"))
        except ValueError:
            continue
        d = abs(w - win_w) + abs(h - win_h)
        if best_d is None or d < best_d:
            best_d, best = d, (float(v.get("sx", 1.0)), float(v.get("sy", 1.0)))
    return best


def resolve(name: str, win_w: int, win_h: int) -> tuple[int, int]:
    """名字 → 当前窗口下的客户区像素坐标。点之前调这个。"""
    store = load_store()
    if name not in store:
        raise KeyError(f"没有这个坐标: {name}（可用 coords.py list 看全部）")
    fx, fy = store[name]
    sx, sy = calib_for(win_w, win_h)
    return int(fx * win_w * sx), int(fy * win_h * sy)


def add(name: str, px: int, py: int, win_w: int = BASE_W, win_h: int = BASE_H) -> None:
    """把一个实测像素位置存成比例。"""
    data = load_store()
    data[name] = [round(px / win_w, 6), round(py / win_h, 6)]
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not argv or argv[0] == "list":
        store = load_store()
        data = load_calib()
        print(f"基准 {BASE_W}×{BASE_H}，共 {len(store)} 个坐标")
        for k in sorted(store):
            fx, fy = store[k]
            print(f"  {k:22s} ({fx:.4f}, {fy:.4f})")
        if data:
            print("\n已有校准：")
            for k, v in data.items():
                print(f"  {k:12s} sx={v.get('sx', 1.0):.4f} sy={v.get('sy', 1.0):.4f}")
        else:
            print("\n还没有校准记录（系数按 1.0 算）——换分辨率后跑一次 calibrate")
        return 0

    if argv[0] == "get" and len(argv) >= 4:
        w, h = int(argv[2]), int(argv[3])
        name = argv[1]
        print(json.dumps({"name": name, "x": resolve(name, w, h)[0],
                          "y": resolve(name, w, h)[1]}, ensure_ascii=False))
        return 0

    if argv[0] == "add" and len(argv) >= 4:
        name, px, py = argv[1], int(argv[2]), int(argv[3])
        w = int(argv[4]) if len(argv) > 4 else BASE_W
        h = int(argv[5]) if len(argv) > 5 else BASE_H
        add(name, px, py, w, h)
        print(f"已存 {name} = ({px},{py}) @ {w}x{h}")
        return 0

    if argv[0] == "calibrate":
        print("校准需要游戏窗口在前台。流程写在 kb/ops/coords-calibration.md，")
        print("简单说：截图上 OCR「概览」「星域」「暂停」，用它们的实际位置反推系数，")
        print("然后把 sx/sy 写进 coords.calib.json 的 "<分辨率>" 条目。")
        print("这一步必须等游戏跑起来才能做——现在只能把框架准备好。")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
