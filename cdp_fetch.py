"""用 Edge/CDP 抓 JS 渲染后的页面正文。

为什么不用 web_fetch：小黑盒的帖子页是 bbs-post_share_v2，纯 SPA，
服务端只回 <div id="app"></div> 骨架，正文全靠 JS 渲染。
curl / web_fetch / web_source 拿到的都是空壳。

为什么不用 headless chrome 工具：这台机器没装 Chrome，只有 Edge。
Edge 的 CDP 协议和 Chrome 一样，所以直接起 Edge --headless=new 带调试端口。

    python cdp_fetch.py <url> [--out file.txt] [--wait 6000]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

import websocket


def cdp_targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
        return json.load(r)


def new_tab(port: int, url: str) -> str:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["id"]


class Session:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.n = 0

    def call(self, method: str, **params):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.time() + 30
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg
        raise TimeoutError(f"{method} 超时")

    def close(self):
        try:
            self.ws.close()
        except Exception:                                       # noqa: BLE001
            pass


def fetch(url: str, port: int, wait_ms: int) -> str:
    tab = new_tab(port, "about:blank")
    ws_url = next(t["webSocketDebuggerUrl"] for t in cdp_targets(port) if t["id"] == tab)
    s = Session(ws_url)
    try:
        s.call("Page.enable")
        s.call("Runtime.enable")
        s.call("Page.navigate", url=url)
        time.sleep(wait_ms / 1000.0)
        # 等 innerText 里出现点实质内容，最多再等 10 秒
        for _ in range(10):
            r = s.call("Runtime.evaluate",
                       expression="document.body ? document.body.innerText : ''",
                       returnByValue=True)
            text = (r.get("result", {}).get("result", {}) or {}).get("value") or ""
            if len(text.strip()) > 200:
                return text
            time.sleep(1.0)
        return text
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--wait", type=int, default=6000)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    import urllib.parse                                        # noqa: F401
    globals()["urllib"] = urllib

    text = fetch(args.url, args.port, args.wait)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"saved {len(text)} chars -> {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
