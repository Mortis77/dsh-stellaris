/** dsh-stellaris: read Stellaris state and issue native commands through the
 *  injected OGAS runtime. The Python side does all the parsing; this file only
 *  registers tools and shuttles JSON across. */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { StringDecoder } from 'node:string_decoder'
import { homedir } from 'node:os'

const HERE = dirname(fileURLToPath(import.meta.url))
const SX_PY = join(HERE, 'sx.py')
const UI_PY = join(HERE, 'ui.py')

export const name = 'stellaris'
export const inject = ['tools']

/** Locate a usable Python 3.10+. Delayed until first call so a missing
 *  interpreter never breaks DSH boot. */
export function findPython(configured = '') {
  const home = process.env.DSH_HOME || join(homedir(), '.dsh')
  const exe = process.platform === 'win32' ? 'python.exe' : 'python3'
  const candidates = [
    configured,
    process.env.STELLARIS_PYTHON,
    join(home, 'stellaris-venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    join(home, 'dsh-runtimes', 'dsh-primary-runtime', 'dependencies', 'python', exe),
    exe,
    'python',
  ]
  for (const candidate of candidates.filter(Boolean)) {
    if ((candidate.includes('/') || candidate.includes('\\')) && !existsSync(candidate)) continue
    const probe = spawnSync(candidate, ['-c', 'import sys; sys.exit(sys.version_info < (3, 10))'], {
      windowsHide: true, timeout: 8000, stdio: 'ignore',
    })
    if (!probe.error && probe.status === 0) return candidate
  }
  throw new Error('dsh-stellaris 需要 Python 3.10+；设置 STELLARIS_PYTHON 指向一个可用解释器')
}

/** Run a backend script and parse its single-line JSON reply.
 *
 *  Two backends share this: `sx.py` (save parsing / native runtime) and
 *  `ui.py` (screenshot / click / key / OCR / autopilot). They both print one
 *  JSON object on the last line, so the plumbing is identical.
 */
export function runScript(python, scriptPath, args, { timeoutMs = 120000, signal } = {}) {
  signal?.throwIfAborted()
  return new Promise((resolve, reject) => {
    const child = spawn(python, [scriptPath, ...args], {
      cwd: HERE,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    const out = new StringDecoder('utf8')
    const err = new StringDecoder('utf8')
    let stdout = ''
    let stderr = ''
    let settled = false

    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      child.kill()
      reject(new Error(`sx.py 超时 (${timeoutMs}ms)`))
    }, timeoutMs)

    const abort = () => { if (!settled) { settled = true; clearTimeout(timer); child.kill(); reject(new Error('已取消')) } }
    signal?.addEventListener('abort', abort, { once: true })

    child.stdout.on('data', chunk => { stdout += out.write(chunk) })
    child.stderr.on('data', chunk => { stderr += err.write(chunk) })
    child.on('error', error => {
      if (settled) return
      settled = true; clearTimeout(timer)
      reject(error)
    })
    child.on('close', code => {
      if (settled) return
      settled = true; clearTimeout(timer)
      const line = stdout.trim().split('\n').filter(Boolean).pop() || ''
      try {
        resolve({ code, data: line ? JSON.parse(line) : null, stderr: stderr.trim() })
      } catch {
        reject(new Error(`${scriptPath.split(/[\\/]/).pop()} 输出不是 JSON: ${line.slice(0, 300)}${stderr ? `\n${stderr.slice(-400)}` : ''}`))
      }
    })
  })
}

/** sx.py: save parsing and native runtime commands. */
export function runPy(python, args, opts = {}) {
  return runScript(python, SX_PY, args, opts)
}

/** Turn a backend payload into a text tool result. */
function render(data, stderr) {
  if (!data) return stderr || '(无输出)'
  if (data.ok === false) {
    const parts = [`失败：${data.error || '未知错误'}`]
    if (data.runtime === 'unavailable') {
      parts.push('提示：先启动群星，再用 loader 注入 iag_stellaris_native_runtime.dll。')
    }
    return parts.join('\n')
  }
  return JSON.stringify(data, null, 2)
}

const textOutput = {
  schema: { type: 'object', required: ['text'], additionalProperties: false, properties: { text: { type: 'string' } } },
  render: (_a, value) => [{ type: 'text', text: value.text }],
}

export function apply(ctx, config = {}) {
  let python
  const timeoutMs = Number.isFinite(config.timeoutMs) && config.timeoutMs > 0 ? config.timeoutMs : 120000
  const save = config.save || undefined

  const call = async (args, exec = {}) => {
    python ??= findPython(config.python)
    const withSave = save && !args.includes('--save') ? ['--save', save, ...args] : args
    const { data, stderr } = await runPy(python, withSave, { timeoutMs, signal: exec.signal })
    exec.signal?.throwIfAborted()
    return { text: render(data, stderr) }
  }

  /** Same, but routed to ui.py (the window/input backend). */
  const callUi = async (args, exec = {}, timeout = timeoutMs) => {
    python ??= findPython(config.python)
    const { data, stderr } = await runScript(python, UI_PY, args, { timeoutMs: timeout, signal: exec.signal })
    exec.signal?.throwIfAborted()
    return { text: render(data, stderr) }
  }

  ctx.tools.register({
    name: 'stellaris_state',
    description: '读取群星当前局势：日期、月收支、研究进度、殖民地和舰队分类统计。数据来自存档解析，只读。',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: textOutput, timeoutMs,
    execute: (args, exec) => call(['state'], exec),
  })

  ctx.tools.register({
    name: 'stellaris_fleets',
    description: '列出舰队。可按名称、舰种、所在星系过滤；默认只看玩家自己的。返回 id / 名称 / 舰种 / 所在星系，id 可直接用于 stellaris_act。',
    parameters: {
      type: 'object',
      properties: {
        name: { type: 'string', description: '按名字模糊匹配，例如 素斯娜' },
        class: { type: 'string', description: '按舰种过滤，例如 军事舰船 / 科研船 / 工程船' },
        system: { type: 'string', description: '按所在星系过滤，例如 伊玛卡' },
        all: { type: 'boolean', description: '包含其他国家的舰队，默认只看自己的' },
      },
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => {
      const argv = ['fleets']
      if (args.name) argv.push('--name', args.name)
      if (args.class) argv.push('--class', args.class)
      if (args.system) argv.push('--system', args.system)
      if (args.all) argv.push('--all')
      return call(argv, exec)
    },
  })

  ctx.tools.register({
    name: 'stellaris_systems',
    description: '查星系 id。可用 filter 按名字模糊匹配（例如「伊」），返回 id 和名字。',
    parameters: {
      type: 'object',
      properties: {
        filter: { type: 'string', description: '名字片段' },
        limit: { type: 'number', description: '最多返回多少条，默认 60' },
      },
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => {
      const argv = ['systems']
      if (args.filter) argv.push('--filter', args.filter)
      if (args.limit) argv.push('--limit', String(args.limit))
      return call(argv, exec)
    },
  })

  ctx.tools.register({
    name: 'stellaris_advice',
    description: '跑规则引擎，给出当前该做什么的清单（负收支、闲置舰船、建造队列等）。只读建议，不执行任何操作。',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: textOutput, timeoutMs,
    execute: (args, exec) => call(['advice'], exec),
  })

  ctx.tools.register({
    name: 'stellaris_act',
    description: '向运行中的群星发送一条原生命令。需要游戏已启动且 iag_stellaris_native_runtime.dll 已注入，否则会返回 runtime unavailable。接受名字或 id，名字会在内部解析成 id。',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['move_fleet', 'select_research', 'colonize_planet', 'toggle_building', 'destroy_building'],
          description: '动作类型',
        },
        params: {
          type: 'array',
          items: { type: 'string' },
          description: '按动作传参：move_fleet [舰队名或id, 星系名或id]；select_research [科技key]；colonize_planet [舰队, 星球id]；toggle_building / destroy_building [殖民地名, 建筑id]',
        },
      },
      required: ['action', 'params'],
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => call(['act', args.action, ...(args.params || [])], exec),
  })

  // ── 窗口 / 输入那一半 ──────────────────────────────────────────────────
  // 这些走 ui.py，不读存档、不注入，纯粹模拟人坐在电脑前。
  // 坐标一律是「窗口客户区像素」——和 shot 截出来的图一一对应，
  // 不用管屏幕分辨率，也不用管窗口在屏幕哪个位置。

  ctx.tools.register({
    name: 'stellaris_shot',
    description: '截一张群星窗口的图（PrintWindow，不受遮挡影响），返回保存路径。之后看这张图决定点什么。',
    parameters: {
      type: 'object',
      properties: {
        dir: { type: 'string', description: '保存目录，留空用包内 shots/' },
        keepVisible: { type: 'boolean', description: '保留窗口当前可见状态（默认会先最小化、截完再最小化）' },
      },
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => {
      const argv = ['shot']
      if (args.dir) argv.push('--dir', args.dir)
      if (args.keepVisible) argv.push('--keep-visible')
      return callUi(argv, exec)
    },
  })

  ctx.tools.register({
    name: 'stellaris_click',
    description: '在群星窗口客户区的像素坐标点一下。坐标直接取自 shot 截出来的图。单击没反应时试 double=true——存档列表、建筑列表吃双击。',
    parameters: {
      type: 'object',
      properties: {
        x: { type: 'number', description: '客户区 x 像素' },
        y: { type: 'number', description: '客户区 y 像素' },
        double: { type: 'boolean', description: '双击' },
        settle: { type: 'number', description: '移到位置后等多久再按下（毫秒），默认 250。游戏内 UI 吃 hover，点不动试 400' },
        named: { type: 'string', description: '用坐标表里的名字代替 x/y，比如 top.pause_play。换分辨率也能用' },
      },
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => {
      const argv = ['click']
      if (args.named) argv.push('--named', args.named)
      else argv.push(String(args.x), String(args.y))
      if (args.double) argv.push('--double')
      if (args.settle) argv.push('--settle', String(args.settle))
      return callUi(argv, exec)
    },
  })

  ctx.tools.register({
    name: 'stellaris_key',
    description: '往群星发一个按键（scancode 路径，DirectX 游戏也认）。常用：space 暂停/继续、esc 菜单、enter 关弹窗、f1..f10 各面板。',
    parameters: {
      type: 'object',
      properties: {
        name: { type: 'string', description: '键名，如 space / esc / enter / f4' },
        times: { type: 'number', description: '按几次，默认 1' },
      },
      required: ['name'],
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => {
      const argv = ['key', args.name]
      if (args.times) argv.push('--times', String(args.times))
      return callUi(argv, exec)
    },
  })

  ctx.tools.register({
    name: 'stellaris_ocr',
    description: '在群星的窗口截图里按文字找元素，返回命中坐标和置信度。加 click=true 则直接点第一个（或第 index 个）。比手算坐标可靠得多。',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: '要找的文字，比如「新游戏」「开始征程」' },
        click: { type: 'boolean', description: '找到后直接点' },
        index: { type: 'number', description: '命中多个时点第几个，默认 0' },
      },
      required: ['text'],
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => {
      const argv = ['ocr', args.text]
      if (args.click) argv.push('--click')
      if (args.index) argv.push('--index', String(args.index))
      return callUi(argv, exec, Math.max(timeoutMs, 180000))
    },
  })

  ctx.tools.register({
    name: 'stellaris_probe',
    description: '把光标移到若干位置，等 tooltip 出来再 OCR 读出文字。用来确认「这个图标到底是干什么的」——比点一下试试快得多，也不会误触。',
    parameters: {
      type: 'object',
      properties: {
        points: {
          type: 'array', items: { type: 'string' },
          description: '一个或多个 "x,y"（客户区像素）',
        },
        wait: { type: 'number', description: '每个点等 tooltip 的秒数，默认 2.6' },
      },
      required: ['points'],
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => {
      const argv = ['probe', ...args.points]
      if (args.wait) argv.push('--wait', String(args.wait))
      return callUi(argv, exec, Math.max(timeoutMs, 180000))
    },
  })

  ctx.tools.register({
    name: 'stellaris_autopilot',
    description: '无人值守保活的开关：start / stop / status。只在「时间卡住且没弹窗」时点播放按钮，弹窗会尝试选选项再关掉。',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', enum: ['start', 'stop', 'status'] },
        minutes: { type: 'number', description: '跑多少分钟，默认 120' },
      },
      required: ['action'],
      additionalProperties: false,
    },
    output: textOutput, timeoutMs,
    execute: (args, exec) => {
      const argv = ['autopilot', args.action]
      if (args.minutes) argv.push('--minutes', String(args.minutes))
      return callUi(argv, exec)
    },
  })
}
