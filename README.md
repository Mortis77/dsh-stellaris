# dsh-stellaris

让 DeepSeek Harness 会玩《群星》。

两条完全不同的路，别混：

| | 怎么干 | 快慢 | 需要 |
|---|---|---|---|
| **读** | 解析存档、查状态 | 毫秒 | 存档文件 |
| **看和点** | 窗口截图、OCR、真实鼠标键盘 | 秒级，但和真人一样 | 游戏窗口 + 前台 |

可以混用：读出来做决策，点下去执行。

---

## 装

插件放在 profile 的 `node_modules/dsh-stellaris`，并在 profile 的
`package.json` 里加一行 `"dsh-stellaris"` 到 `dsh.profile.bundles`。

`cordis.patch.yml` 里的 `name` 必须是裸包名 `dsh-stellaris`，写成别的会静默不激活。

`index.js` 改了**要重启 DSH** 才会重新加载。

### 依赖

```bash
pip install mss opencv-python numpy
pip install rapidocr_onnxruntime      # 只有 OCR / probe 需要
```

Python 3.10+。只支持 **Windows**——`ctrl.py` / `winshot.py` 全是 Win32 API。

### 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `STELLARIS_SAVE_DIR` | 存档目录 | `~/Documents/Paradox Interactive/Stellaris/save games` |
| `STELLARIS_OCR_PYTHON` | 装了 rapidocr 的解释器 | 自动探测 |
| `STELLARIS_PYTHON` | 跑后端脚本的解释器 | 自动探测 |

---

## 工具（11 个）

### 读

| 工具 | 作用 | 要游戏在跑 |
|---|---|---|
| `stellaris_state` | 日期、月收支、研究、殖民地、舰队分类 | 否（读存档） |
| `stellaris_fleets` | 舰队列表，可按名字/舰种/星系过滤 | 否 |
| `stellaris_systems` | 星系 id 查询 | 否 |
| `stellaris_advice` | 规则引擎给建议（负收支、闲置舰船等） | 否 |

### 写

| 工具 | 作用 | 要游戏在跑 |
|---|---|---|
| `stellaris_act` | 原生命令：移动舰队 / 选科研 / 殖民 / 开关建筑 | 是 + DLL 注入 |

**注意**：`act` 依赖第三方注入的 `iag_stellaris_native_runtime.dll`，
那个 DLL 不在本仓库里，而且**版本敏感**——游戏补丁号一变，hook 就可能失效。

### 看和点

| 工具 | 作用 |
|---|---|
| `stellaris_shot` | 截窗口（`PrintWindow`，不受遮挡影响），返回路径 |
| `stellaris_click` | 在客户区像素坐标点一下，支持 `--named` 用坐标表 |
| `stellaris_key` | 发按键（scancode，DirectX 也认） |
| `stellaris_ocr` | 按文字找按钮，可直接点 |
| `stellaris_probe` | 光标移到位置，等 tooltip 出来读文字 |
| `stellaris_autopilot` | 保活开关：时间卡住且没弹窗时点播放 |

---

## 坐标表

`coords.py` 把「按钮在哪」和「屏幕分辨率」解耦。

```bash
python coords.py list                          # 看全部 23 条
python coords.py get menu.new_game 2560 1600   # 换算成当前窗口的像素
python ui.py click --named menu.new_game       # 直接用名字点
```

坐标存归一化比例，基准 1920×1080。换显示屏不用改代码。

**能用 OCR 就别用坐标**——`stellaris_ocr 新游戏 --click` 永远比坐标准，
因为它根本不管分辨率。坐标表只服务于纯图标元素。

---

## 踩过的坑（都是真的）

### 1. 点击要在 `SetCursorPos` 之后**立刻**按下，不能等

机器上常年跑着 Alas / MAA 这类安卓自动化，它们会不停模拟点击。
光标设进去之后哪怕等 0.06 秒，也可能被拨走，于是 LEFT DOWN 落在错误位置。

```python
u.SetCursorPos(x, y)
u.mouse_event(0x0002, 0, 0, 0, 0)   # 不等
u.mouse_event(0x0004, 0, 0, 0, 0)
```

`ctrl.click` 的 `settle` 和 `drag` 默认值都是 0，就是为了这个。

### 2. `MOUSEEVENTF_ABSOLUTE` 的归一化路径会被静默吞掉

`ctrl.move()` 优先用 `SetCursorPos`（直接设物理像素），失败才退回归一化路径。
归一化在某些环境下不报错也不生效，表现为「光标不动但点击照发」。

### 3. `multi_sampling=4` 会让 50 系显卡创不了 DX9 设备

```
gfx_dx9.cpp:4598  Failed to create a device [D3DCREATE_HARDWARE_VERTEXPROCESSING]
```

游戏卡在 1024×768 / 181MB，永远出不来。

**`renderer` 不是渲染后端开关**——填 0/1/2 全都走 `gfx_dx9`。
真正的开关是 `multi_sampling`，改成 `0` 就好。

### 4. 渲染表面可能和窗口尺寸对不上

改分辨率或切全屏后，画面只占左上角一块。**游戏内按 Alt+Enter** 让它重建。
改 `settings.txt` 的 `size` 没用，游戏不一定读。

### 5. 长文弹窗的按钮点了不关，`Enter` 反而有效

开场那种「我们的征途是星辰大海」，按钮位置对了、点击也发了，就是不关。
直接发 `Enter`。

### 6. OCR 给的坐标要拿探针复核

OCR 的文字框有时把旁边一行也框进去，框中心就偏了。
`ui.py probe x,y` 读的是「光标正下方那一段文字」，比 OCR 框准。

实测：OCR 报「新游戏」在 y=329，探针说 y=300 才是。

---

## 文件

```
index.js          工具注册（11 个）
sx.py             读 / 写那半边的入口（state/fleets/systems/advice/act）
ui.py             看和点那半边的入口（shot/click/key/ocr/probe/autopilot）
coords.py         坐标表 + 换分辨率校准
save_parser.py    Clausewitz 存档解析
state.py          名字 ↔ ID 互查
advisor.py        规则引擎
actions.py        原生命令下发
loc.py            localisation key → 中文
ctrl.py           窗口 / 输入（SendInput、scancode）
winshot.py        PrintWindow 截图
ocr_click.py      OCR 定位
autopilot.py      保活
cdp_fetch.py      抓 JS 渲染页面（Edge headless + CDP，独立小工具）
```

## License

MIT
