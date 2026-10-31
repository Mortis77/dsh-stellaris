#!/usr/bin/env python3
"""游戏 UI 操作后端：截图 / 点击 / 按键 / OCR 定位。

index.js 里的 stellaris_* 工具都 spawn 到这里。每个子命令输出**一行 JSON**。

    ui.py shot [--dir PATH]
    ui.py click X Y [--double] [--abs]
    ui.py key NAME [--times N]
    ui.py ocr TEXT [--click] [--index N]

为什么要独立一个入口：截图和点击是两套完全不同的坑——
截图必须走 PrintWindow（窗口被挡也能抓），点击必须走 SendInput 且需要前台。
放在一起好统一处理「先抢焦点再点击」这件事。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ctrl
import winshot

# ── DPI 感知（必须在任何窗口/坐标 API 之前调用）────────────────────────────
# 这台机器 200% 缩放：窗口物理尺寸 1920×1080，但不做 DPI 感知时
# GetClientRect 返回的是逻辑尺寸 960×540，而截图是物理像素。
# 两边差一倍，坐标全错——表现是「点击发出去了但界面没反应」。
# 声明 PER_MONITOR_AWARE_V2 之后，所有坐标统一到物理像素。
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)   # PER_MONITOR_AWARE_V2
except Exception:                                            # noqa: BLE001
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)       # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:                                        # noqa: BLE001
        ctypes.windll.user32.SetProcessDPIAware()

TITLE = "Stellaris"
# OCR 需要 rapidocr。默认先试当前解释器，再试几个常见位置；
# 都不行就设环境变量 STELLARIS_OCR_PYTHON 指一个装了 rapidocr 的 python。
def _pick_ocr_python() -> str:
    env = os.environ.get("STELLARIS_OCR_PYTHON")
    if env:
        return env
    cands = [sys.executable]
    home = os.path.expanduser("~")
    for rel in (
        os.path.join(".dsh", "bili-vision-venv", "Scripts", "python.exe"),
        os.path.join(".venv", "Scripts", "python.exe"),
    ):
        cands.append(os.path.join(home, rel))
    for c in cands:
        if c and os.path.exists(c):
            try:
                import subprocess as _sp
                r = _sp.run([c, "-c", "import rapidocr_onnxruntime"],
                            capture_output=True, timeout=10)
                if r.returncode == 0:
                    return c
            except Exception:                                    # noqa: BLE001
                continue
    return sys.executable


OCR_PYTHON = _pick_ocr_python()


def _out(payload: dict) -> None:
    # 控制台默认 GBK 时中文会炸 UnicodeEncodeError，强制走 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass
    print(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _win() -> winshot.Win | None:
    return winshot.find(TITLE)


def cmd_shot(args) -> int:
    w = _win()
    if w is None:
        _out({"ok": False, "error": "找不到群星窗口"})
        return 1
    # PrintWindow 对最小化的窗口抓出来是全黑，必须先恢复；截完再放回去，
    # 免得把用户正在用的别的游戏挡住。
    was_minimized = bool(ctrl.user32.IsIconic(w.hwnd))
    if was_minimized:
        ctrl.user32.ShowWindow(w.hwnd, 9)          # SW_RESTORE
        time.sleep(0.45)
    try:
        img = winshot.grab(w)
    except Exception as exc:                                     # noqa: BLE001
        _out({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        if was_minimized and not args.keep_visible:
            ctrl.user32.ShowWindow(w.hwnd, 6)      # SW_MINIMIZE
    if winshot.looks_blank(img):
        _out({"ok": False, "error": "截到全黑：全屏独占模式下 PrintWindow 抓不到；"
                                    "把 settings.txt 的 fullScreen 改成 no、borderless 改成 yes"})
        return 1

    out_dir = Path(args.dir) if args.dir else HERE / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"shot_{time.strftime('%H%M%S')}.png"
    import cv2
    cv2.imwrite(str(path), img)
    _out({"ok": True, "path": str(path), "width": img.shape[1], "height": img.shape[0]})
    return 0


def _raw_click(x: int, y: int, double: bool = False,
               settle_ms: int = 250, hold_ms: int = 80,
               origin: tuple[int, int] = (0, 0)) -> None:
    """点一下。`x, y` 是**客户区**坐标，`origin` 是客户区左上角在屏幕上的位置。

    ⚠ 2026-10-31 修的一个隐蔽 bug：`SetCursorPos` 要的是**屏幕坐标**，
    而这里传进来的是客户区坐标。窗口只要不在 (0,0)，点击就会整体偏移
    ——游戏窗口在 (320,260) 时，客户区 (67,326) 实际点到了屏幕 (67,326)，
    落在窗口外面。表现是「点击发出去了、前台还是游戏、但界面毫无反应」。

    所以必须加上 origin。调用方从 `w.left/w.top` 取。

    另外两条经验（来自 dsh-game-stack 与本机实测）：

    **要点一：等 250ms 让 hover 生效。** 游戏的按钮大多吃 hover——
    指针进了按钮区域要等一帧才变亮/变可点。不等就点，会落在「还没激活」
    的按钮上：回执一切正常，界面毫无反应。

    **要点二：别把 settle 归零。** 曾经怀疑 Alas/MAA 抢光标所以把等待删了，
    但实测 `set(x,y) → readback(x,y) delta=0`，位置从来没错。
    真被抢占时 delta 会变大，那时该处理的是那些脚本，不是删等待。
    """
    u = ctrl.user32
    ox, oy = origin
    sx, sy = int(x) + ox, int(y) + oy
    u.SetCursorPos(sx, sy)
    if settle_ms:
        time.sleep(settle_ms / 1000.0)     # 等 hover 激活
    u.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
    if hold_ms:
        time.sleep(hold_ms / 1000.0)       # 按住一会儿，有些引擎要
    u.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP
    if double:
        time.sleep(0.10)
        u.SetCursorPos(sx, sy)
        u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.06)
        u.mouse_event(0x0004, 0, 0, 0, 0)


def cmd_click(args) -> int:
    w = ctrl.find_window(TITLE)
    if w is None:
        _out({"ok": False, "error": "找不到群星窗口"})
        return 1

    # --named 走坐标表：名字 → 当前窗口尺寸下的像素，换分辨率也能用
    if getattr(args, "named", ""):
        try:
            import coords
            x, y = coords.resolve(args.named, w.width, w.height)
        except KeyError as e:
            _out({"ok": False, "error": str(e)})
            return 1
    else:
        if args.x is None or args.y is None:
            _out({"ok": False, "error": "要么给 x y，要么给 --named NAME"})
            return 1
        x, y = args.x, args.y

    # 借前台：点击只能落到前台窗口，借完还回去
    prev_fg = ctrl.user32.GetForegroundWindow()
    need_restore = bool(prev_fg) and prev_fg != w.hwnd
    fg_ok = ctrl.focus(w)
    time.sleep(0.25)
    settle = int(getattr(args, "settle", 250))
    hold = int(getattr(args, "hold", 80))
    _raw_click(x, y, bool(args.double), settle_ms=settle, hold_ms=hold,
               origin=(w.left, w.top))
    # 点完读一次真实前台，回执里带出来——判断「点没点到」的第一手证据
    after_fg = int(ctrl.user32.GetForegroundWindow() or 0)
    time.sleep(0.4)
    if need_restore:
        ctrl.user32.SetForegroundWindow(prev_fg)
    _out({"ok": True, "x": x, "y": y, "named": getattr(args, "named", "") or None,
          "win": f"{w.width}x{w.height}", "double": bool(args.double),
          "settle_ms": settle, "hold_ms": hold,
          "focus_ok": bool(fg_ok), "fg_after": after_fg, "fg_is_game": after_fg == w.hwnd,
          "note": "点不动先把 settle 调到 400 试试：游戏内 UI 吃 hover，光标进去要等一帧才变可点"})
    return 0


def cmd_key(args) -> int:
    w = ctrl.find_window(TITLE)
    if w is None:
        _out({"ok": False, "error": "找不到群星窗口"})
        return 1
    prev_fg = ctrl.user32.GetForegroundWindow()
    need_restore = bool(prev_fg) and prev_fg != w.hwnd
    ctrl.focus(w)
    time.sleep(0.2)
    for _ in range(max(1, args.times)):
        ctrl.press(args.name)
        time.sleep(0.08)
    if need_restore:
        time.sleep(0.15)
        ctrl.user32.SetForegroundWindow(prev_fg)
    _out({"ok": True, "key": args.name, "times": max(1, args.times)})
    return 0


def cmd_probe(args) -> int:
    """把光标放到 (x,y)，等 tooltip 出来，读它的文字。

    为什么要先探：群星里同一个位置在不同状态下是不同的按钮，图标也不带文字。
    「点一下试试」等于赌 —— 今晚在科研船的自动调查上赌掉了四十分钟，
    而光标放上去两秒就知道那是什么。

    用法：
        ui.py probe 495 325            # 单个位置
        ui.py probe 495,325 495,487    # 一次探多个
    """
    w = ctrl.find_window(TITLE)
    if w is None:
        _out({"ok": False, "error": "找不到群星窗口"})
        return 1
    if not Path(OCR_PYTHON).exists():
        _out({"ok": False, "error": f"OCR 解释器不存在: {OCR_PYTHON}"})
        return 1

    prev_fg = ctrl.user32.GetForegroundWindow()
    need_restore = bool(prev_fg) and prev_fg != w.hwnd
    ctrl.focus(w)
    time.sleep(0.3)

    results = []
    for spec in args.points:
        try:
            sx, sy = spec.split(",")
            x, y = int(sx), int(sy)
        except ValueError:
            results.append({"point": spec, "error": "格式应为 x,y"})
            continue

        # 先移开，让上一个 tooltip 彻底消失，否则读到的是上一次的
        ctrl.move(w, 0.75, 0.30)
        time.sleep(1.4)
        ctrl.move(w, x / w.width, y / w.height)
        time.sleep(args.wait)

        img = winshot.grab(w)
        crop = img[max(0, y - 20):min(img.shape[0], y + 380),
                   max(0, x - 40):min(img.shape[1], x + 640)]
        tmp = HERE / f"_probe_{x}_{y}.png"
        import cv2
        cv2.imwrite(str(tmp), crop)

        # 用 OCR 读这块区域里的文字
        proc = subprocess.run(
            [OCR_PYTHON, str(HERE / "ocr_click.py"), "--read-image", str(tmp)],
            cwd=str(HERE), capture_output=True, timeout=60,
        )
        text = proc.stdout.decode("utf-8", "replace").strip()
        results.append({"point": f"{x},{y}", "tooltip": text[:600]})
        tmp.unlink(missing_ok=True)

    if need_restore:
        time.sleep(0.2)
        ctrl.user32.SetForegroundWindow(prev_fg)
    _out({"ok": True, "results": results})
    return 0


def cmd_ocr(args) -> int:
    """OCR 定位。走另一个解释器（那边有 rapidocr）。"""
    if not Path(OCR_PYTHON).exists():
        _out({"ok": False, "error": f"OCR 解释器不存在: {OCR_PYTHON}；"
                                    f"用环境变量 STELLARIS_OCR_PYTHON 指一个装了 rapidocr 的 python"})
        return 1
    script = HERE / "ocr_click.py"
    argv = [OCR_PYTHON, str(script), args.text, "--dry", "--index", str(args.index)]

    # OCR 抓的是窗口内容，最小化时是黑的 —— 先恢复，做完再放回去
    w = ctrl.find_window(TITLE)
    restored = False
    if w is not None and ctrl.user32.IsIconic(w.hwnd):
        ctrl.user32.ShowWindow(w.hwnd, 9)
        time.sleep(0.5)
        restored = True
    try:
        proc = subprocess.run(argv, cwd=str(HERE), capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        _out({"ok": False, "error": "OCR 超时"})
        return 1
    finally:
        if restored and w is not None:
            ctrl.user32.ShowWindow(w.hwnd, 6)

    text = proc.stdout.decode("utf-8", "replace").strip()
    import re
    hits = []
    # 形如 "[0] 1.00  (1816, 26)" —— 括号里有空格，不能直接 split()
    for m in re.finditer(r"\[(\d+)\]\s+([\d.]+)\s+\((\d+),\s*(\d+)\)", text):
        hits.append({"index": int(m.group(1)), "score": float(m.group(2)),
                     "x": int(m.group(3)), "y": int(m.group(4))})

    if not hits:
        _out({"ok": False, "error": f"屏幕上没有「{args.text}」",
              "raw": text[-300:]})
        return 2

    payload = {"ok": True, "text": args.text, "hits": hits}
    if args.click:
        w = ctrl.find_window(TITLE)
        ctrl.focus(w)
        time.sleep(0.25)
        target = hits[min(args.index, len(hits) - 1)]
        ctrl.click(w, target["x"] / w.width, target["y"] / w.height,
                   drag=0)
        time.sleep(0.4)
        payload["clicked"] = {"x": target["x"], "y": target["y"]}
    _out(payload)
    return 0


def _pid_alive(pid: int) -> bool:
    """Windows 上没有 os.kill(pid, 0) 那套，用 tasklist 探活。"""
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, timeout=10)
        return str(pid) in out.stdout.decode("utf-8", "replace")
    except Exception:                                            # noqa: BLE001
        return False


def cmd_autopilot(args) -> int:
    pid_file = HERE / "autopilot.pid"
    script = HERE / "autopilot.py"
    log_file = HERE / "autopilot.log"

    if args.action == "start":
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                if _pid_alive(pid):
                    _out({"ok": False, "error": f"autopilot 已经在跑 (pid {pid})。"
                                                f"先 stop 再 start"})
                    return 1
            except Exception:                                    # noqa: BLE001
                pass
            pid_file.unlink(missing_ok=True)

        flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            [sys.executable, str(script), "--minutes", str(args.minutes)],
            cwd=str(HERE), creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        _out({"ok": True, "action": "start", "pid": proc.pid, "minutes": args.minutes,
              "log": str(log_file)})
        return 0

    if args.action == "stop":
        if not pid_file.exists():
            _out({"ok": True, "action": "stop", "note": "本来就没在跑"})
            return 0
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except Exception:                                        # noqa: BLE001
            pid_file.unlink(missing_ok=True)
            _out({"ok": True, "action": "stop", "note": "pid 文件损坏，已删"})
            return 0
        alive = _pid_alive(pid)
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
        pid_file.unlink(missing_ok=True)
        _out({"ok": True, "action": "stop", "pid": pid, "was_alive": alive})
        return 0

    # status
    running = False
    pid = None
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            running = _pid_alive(pid)
        except Exception:                                        # noqa: BLE001
            pid = None
    tail = ""
    if log_file.exists():
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-6:])
    _out({"ok": True, "action": "status", "running": running, "pid": pid,
          "pause_flag": (HERE / "autopilot.pause").exists(), "log_tail": tail})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("shot")
    p.add_argument("--dir", default="")
    p.add_argument("--keep-visible", action="store_true",
                   help="窗口原本是最小化的，截完不要放回去（调试用）")
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("click")
    p.add_argument("x", type=int, nargs="?", default=None)
    p.add_argument("y", type=int, nargs="?", default=None)
    p.add_argument("--named", default="",
                   help="用坐标表里的名字点（coords.py list 看全部），换分辨率也能用")
    p.add_argument("--double", action="store_true")
    p.add_argument("--settle", type=int, default=250,
                   help="移到位置后等多久再按下（毫秒）。游戏内 UI 吃 hover，默认 250；点不动试 400")
    p.add_argument("--hold", type=int, default=80, help="按住多久（毫秒）")
    p.add_argument("--abs", action="store_true", help="占位：目前坐标一律按客户区像素算")
    p.set_defaults(func=cmd_click)

    p = sub.add_parser("key")
    p.add_argument("name")
    p.add_argument("--times", type=int, default=1)
    p.set_defaults(func=cmd_key)

    p = sub.add_parser("ocr")
    p.add_argument("text")
    p.add_argument("--click", action="store_true")
    p.add_argument("--index", type=int, default=0)
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("probe")
    p.add_argument("points", nargs="+", help="一个或多个 x,y（客户区像素）")
    p.add_argument("--wait", type=float, default=2.6, help="每个点等 tooltip 的秒数")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("autopilot")
    p.add_argument("action", choices=["start", "stop", "status"])
    p.add_argument("--minutes", type=float, default=120)
    p.set_defaults(func=cmd_autopilot)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
