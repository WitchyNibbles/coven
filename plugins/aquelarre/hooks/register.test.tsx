import type { FsEntry, On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine, FoundElement, Mounted, MockClock } from 'claude-code/testing'

const PANE = {
  plugin: 'aquelarre',
  component: 'Pane',
  requestId: 'aquelarre',
  props: {
    title: 'Aquelarre',
    isFocused: false,
    bodyColumns: 80,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const THEME_KEYS = [
  'text',
  'inverseText',
  'inactive',
  'subtle',
  'suggestion',
  'remember',
  'success',
  'error',
  'warning',
  'merged',
  'claude',
  'permission',
  'planMode',
  'autoAccept',
  'promptBorder',
  'bashBorder',
  'ide',
  'diffAdded',
  'diffRemoved',
  'diffAddedDimmed',
  'diffRemovedDimmed',
  'diffAddedWord',
  'diffRemovedWord',
]

// Local time on purpose: the footer prints local time, so the test is the same in every timezone.
const NOON = new Date(2026, 10, 17, 12, 0, 5).getTime()
const TICK_MS = 5_000
// A tick of an open pane looks every 5 s, but a read younger than 7.5 s stops the command: the next one is two ticks on.
const READ_MS = 2 * TICK_MS
const STATUS_TIMEOUT_MS = 20_000
const MINUTE_MS = 60_000

type Reply = { exitCode: number; stdout: string; stderr: string }
type Mount = Mounted<'desktop', 'Pane'>

type PaneSeat = { isShown: boolean; isPlaced: boolean }
const SEATED: PaneSeat = { isShown: true, isPlaced: true }

type Stage = {
  clock: MockClock
  calls: string[][]
  reply: Reply
  /** Answers by command, ahead of `reply`: `{ coven: exits 127 }`. */
  replies: Record<string, Reply>
  missing: Set<string>
  /** Commands that cannot start and give no reason: `process.run` is denied with an empty one. */
  silent: Set<string>
  /** Commands that hang until their timeout, then reject. */
  hangs: Set<string>
  /** The open panes by id, as the engine's own record keeps them. */
  panes: Map<string, PaneSeat>
  /** Every `$.ui.toast` the mod raised, in order. */
  toasts: { text: string; timeoutMs: number | undefined }[]
  /** What each `$.ui.open` asked of the pane's look and size. */
  opens: { backgroundColor: unknown; columns: unknown }[]
  /** When set, `$.ui.panes()` rejects with it. */
  panesError: string
  /** How long the next `$.ui.panes()` answers late, in ms of the mock clock; it is spent by that one call. */
  panesLag: number
  /** Holds every `process.run` until the returned function is called. */
  hold: () => () => void
  /** The files the mod can see, by absolute path; a test may change them between reads. */
  files: Record<string, string>
  /** Every path the mod listed or read for a plan, in order; the change check's own paths are in `looked`. */
  touched: string[]
  /** Every path the change check listed or stat'ed under the jobs folder, in order. */
  looked: string[]
  /** The modification time `$.fs.stat` answers per file in `files`; 1 for a file with none. A test may change it. */
  mtimes: Record<string, number>
  /** Paths whose list, stat, read or write rejects with an I/O error, though the file is there. */
  broken: Set<string>
  /** Every `$.fs.write`, in order. */
  writes: { path: string; text: string }[]
  /** Every path under a cache folder the mod listed, stat'ed or read; kept out of `touched` and `looked`. */
  cached: string[]
  /** After the mod writes a path, the file holds this text instead: another window wrote it right after. */
  overwrites: Record<string, string>
}

type StageOptions = {
  stdout?: string
  /** `COVEN_SESSION_ROLE`; unset when not given, set (even to `''`) when given. */
  role?: string
  /** `CLAUDE_CONFIG_DIR`; unset when not given. */
  configDir?: string
  /** `HOME`; `/home/user` when not given, unset when `''`. */
  home?: string
  /** `XDG_CACHE_HOME`; unset when not given. */
  xdgCache?: string
  /** What `$.session.id()` answers; `window-1` when not given. `''` makes the call fail. */
  sessionId?: string
  /** Absolute path to content. A folder is any prefix of a path; a missing path rejects with ENOENT. */
  files?: Record<string, string>
  /** The mtime of a file in `files`, by absolute path. */
  mtimes?: Record<string, number>
}

const HOME = '/home/user'

/** The entries of a folder in `files`: the first name after the folder's path, a dir when more follows. */
function listing(files: Record<string, string>, folder: string): FsEntry[] | undefined {
  const prefix = `${folder.replace(/\/+$/, '')}/`
  const kinds = new Map<string, FsEntry['kind']>()
  for (const path of Object.keys(files)) {
    if (!path.startsWith(prefix)) continue
    const [name = '', ...deeper] = path.slice(prefix.length).split('/')
    kinds.set(name, deeper.length > 0 ? 'dir' : 'file')
  }
  if (kinds.size === 0) return undefined
  return [...kinds].map(([name, kind]) => ({ name, kind, size: 0, mtimeMs: 0, isLink: false }))
}

/** Every outside call the mod can make is answered here: the coven CLI, env and the clock. */
function stage(on: On, options: StageOptions = {}): Stage {
  const world: Stage = {
    clock: mock.clock(on, { now: NOON }),
    calls: [],
    reply: { exitCode: 0, stdout: options.stdout ?? '', stderr: '' },
    replies: {},
    missing: new Set(),
    silent: new Set(),
    hangs: new Set(),
    panes: new Map(),
    toasts: [],
    opens: [],
    panesError: '',
    panesLag: 0,
    files: { ...options.files },
    touched: [],
    looked: [],
    mtimes: { ...options.mtimes },
    broken: new Set(),
    writes: [],
    cached: [],
    overwrites: {},
    hold: () => {
      let release = () => {}
      gate = new Promise<void>(resolve => {
        release = resolve
      })
      return release
    },
  }
  staged = world
  let gate: Promise<void> = Promise.resolve()
  const home = options.home ?? HOME
  mock.env(on, {
    ...(home ? { HOME: home } : {}),
    ...(options.configDir ? { CLAUDE_CONFIG_DIR: options.configDir } : {}),
    ...(options.xdgCache ? { XDG_CACHE_HOME: options.xdgCache } : {}),
    ...(options.role === undefined ? {} : { COVEN_SESSION_ROLE: options.role }),
  })
  const record = (path: string) => {
    if (path.includes('/aquelarre/')) return world.cached.push(path)
    return (/\/jobs(\/|$)/.test(path) ? world.looked : world.touched).push(path)
  }
  on('fs.list', (_, e) => {
    record(e.path)
    if (world.broken.has(e.path)) return { deny: `EIO: i/o error, scandir '${e.path}'` }
    const entries = listing(world.files, e.path)
    return entries ? { value: entries } : { deny: `ENOENT: no such file or directory, scandir '${e.path}'` }
  })
  on('fs.stat', (_, e) => {
    record(e.path)
    if (world.broken.has(e.path)) return { deny: `EIO: i/o error, stat '${e.path}'` }
    const text = world.files[e.path]
    if (text === undefined) return { deny: `ENOENT: no such file or directory, stat '${e.path}'` }
    return { value: { kind: 'file', size: text.length, mtimeMs: world.mtimes[e.path] ?? 1, isLink: false } }
  })
  on('fs.read', (_, e) => {
    record(e.path)
    if (world.broken.has(e.path)) return { deny: `EIO: i/o error, open '${e.path}'` }
    const text = world.files[e.path]
    return text === undefined ? { deny: `ENOENT: no such file or directory, open '${e.path}'` } : { value: text }
  })
  // The mod's own writes land in `files`, so a later read sees them, as it would on disk.
  on('fs.write', (_, e) => {
    world.writes.push({ path: e.path, text: e.text })
    if (world.broken.has(e.path)) return { deny: `EIO: i/o error, write '${e.path}'` }
    world.files[e.path] = world.overwrites[e.path] ?? e.text
    return { value: undefined }
  })
  on('session.id', () =>
    options.sessionId === '' ? { deny: 'no session id' } : { value: options.sessionId ?? 'window-1' },
  )
  on('command.register', () => ({ value: { command: 'aquelarre' } }))
  on('ui.open', (_, e) => {
    world.opens.push({ backgroundColor: e.backgroundColor, columns: e.columns })
    world.panes.set(e.id, SEATED)
    return { value: { isPlaced: true } }
  })
  on('ui.close', (_, e) => {
    world.panes.delete(e.id)
    return { value: undefined }
  })
  on('ui.panes', async () => {
    const lag = world.panesLag
    world.panesLag = 0
    if (lag > 0) await world.clock.sleep(lag)
    if (world.panesError) return { deny: world.panesError }
    const open = [...world.panes].map(([id, seat]) => ({ id, title: id, isFocused: false, ...seat }))
    return { value: open }
  })
  on('ui.toast', (_, e) => {
    world.toasts.push({ text: e.text, timeoutMs: e.timeoutMs })
    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('process.run', async (_, e) => {
    world.calls.push([...e.argv])
    await gate
    const command = e.argv[0] ?? ''
    if (world.missing.has(command)) return { deny: `spawn ${command} ENOENT` }
    if (world.silent.has(command)) return { deny: '' }
    if (world.hangs.has(command)) {
      await world.clock.sleep(STATUS_TIMEOUT_MS)
      return { deny: `${command} still running after ${STATUS_TIMEOUT_MS} ms` }
    }
    const reply = world.replies[command] ?? world.reply
    return { value: { ...reply, isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return world
}

async function start($: Engine) {
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
}

const RUN = { args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } } as const

async function openPane($: Engine) {
  await $.command.run({ command: 'aquelarre', ...RUN })
}

// `$.ui.open` and `$.ui.close` are not on the test's `$`: a second plugin opens and closes the pane,
// as the engine or the person's close mark would, without going through `/aquelarre`.
const DOOR = {
  name: 'door',
  register: (on: On) => {
    on('command.run', { command: 'open-aquelarre' }, async $ => {
      await $.ui.open({ id: 'aquelarre' })
      return { text: 'opened' }
    })
    on('command.run', { command: 'close-aquelarre' }, async $ => {
      await $.ui.close({ id: 'aquelarre' })
      return { text: 'closed' }
    })
  },
}

async function closePane($: Engine) {
  await $.command.run({ command: 'close-aquelarre', ...RUN })
}

async function openPaneElsewhere($: Engine) {
  await $.command.run({ command: 'open-aquelarre', ...RUN })
}

async function mountPane(
  $: Engine,
  placement: 'dock' | 'widget' = 'dock',
  bodyColumns = 80,
): Promise<Mount> {
  return $.ui.mount({ ...PANE, surface: 'desktop', props: { ...PANE.props, placement, bodyColumns } })
}

/** Everything the pane shows, one string, in drawing order. */
async function screen(ui: Mount): Promise<string> {
  const texts = await ui.findAll({ type: 'Text' })
  return texts.map(found => found.text).join('\n')
}

type Drawn = { type: string; props: Record<string, unknown>; children?: unknown }

function isDrawn(node: unknown): node is Drawn {
  return typeof node === 'object' && node !== null && 'type' in node && 'props' in node
}

function marginRows(box: Drawn, side: 'marginTop' | 'marginBottom'): string[] {
  const margin = box.props[side]
  return typeof margin === 'number' ? Array<string>(margin).fill('') : []
}

function textOf(node: unknown): string {
  if (typeof node === 'string') return node
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (isDrawn(node)) return textOf(node.children)
  return ''
}

function rowsOf(node: unknown): string[] {
  if (Array.isArray(node)) return node.flatMap(rowsOf)
  // An empty string child (`{error && ...}` with no error) draws nothing, not a blank row.
  if (typeof node === 'string') return node === '' ? [] : [node]
  if (!isDrawn(node)) return []
  if (node.type === 'Text') return [textOf(node)]
  const inner = node.props.flexDirection === 'column' ? rowsOf(node.children) : [textOf(node)]
  return [...marginRows(node, 'marginTop'), ...inner, ...marginRows(node, 'marginBottom')]
}

/** The pane as the person reads it, one string per row: a column Box stacks, a margin is a blank row. */
async function rows(ui: Mount): Promise<string[]> {
  return rowsOf(await ui.drawn())
}

async function rowWith(ui: Mount, pattern: RegExp): Promise<string> {
  const found = (await rows(ui)).find(row => pattern.test(row))
  expect(found, `no row matches ${pattern}`).toBeDefined()
  return found ?? ''
}

/** A role session never looks, reads or writes: no command, no write, no look at the jobs, no cache or plan read. */
function expectNothingTouched(world: Stage) {
  expect(world.calls).toEqual([])
  expect(world.writes).toEqual([])
  expect(world.looked).toEqual([])
  expect(world.cached).toEqual([])
  expect(world.touched).toEqual([])
}

/** The stage the running test built: the session-start read is still on its way when `start` returns. */
let staged: Stage | undefined

/**
 * The session started, the read it makes at start finished (the mod does not await it) and forgotten,
 * and the pane opened. The tests that use this are about what `/aquelarre` reads; the start read has its own.
 */
async function started($: Engine) {
  await start($)
  await staged?.clock.settle()
  staged?.calls.splice(0)
  staged?.touched.splice(0)
  staged?.looked.splice(0)
  await openPane($)
}

type Line = { name: string; state: string; age?: string; id?: string; lines?: string[] }

/** One PM as `coven pm status` prints it. */
function pm({ name, state, age = '3m', id = '1333568a', lines = [] }: Line): string {
  const head = `pm-${name}  id=${id}  state=${state} ${age}  since GM: message`
  return [head, ...lines.map(line => `  ${line}`)].join('\n')
}

const status = (...pms: string[]) => `${pms.join('\n')}\n`

const lastOut = (text: string) => `last out: ${text}   [delivered]`

function position(text: string, word: string): number {
  const at = text.indexOf(word)
  expect(at).toBeGreaterThan(-1)
  return at
}

const MIXED = status(
  pm({ name: 'sleepy', state: 'idle', age: '14m', lines: [lastOut('23:57 DONE sleepy/2: hooks moved')] }),
  pm({ name: 'coven', state: 'working', age: '3m', lines: [lastOut('00:14 DONE coven/3: Python 3.14 CI fix is PR #4')] }),
  pm({ name: 'magic', state: 'stale', age: '2h', lines: [lastOut('00:10 BLOCKED magic/1: no repo access')] }),
  pm({ name: 'tower', state: 'blocked', age: '10m', lines: ['waits on GM: QUESTION tower/2 since 09:00'] }),
  pm({ name: 'lilthop', state: 'done', age: '1d' }),
  pm({ name: 'old', state: 'done', age: '2d' }),
  pm({ name: 'older', state: 'done', age: '3d' }),
  pm({ name: 'napper', state: 'stopped', age: '5d' }),
)

test('the summary counts the live PMs by what they are doing', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/◆ 1 espera/)
  expect(shown).toMatch(/✕ 1 problema/)
  expect(shown).toMatch(/✦ 1 trabajando/)
  expect(shown).toMatch(/○ 1 en pausa/)
  await ui.unmount()
})

test('PMs are ordered waits-on-GM, problems, working, idle', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  const order = ['tower', 'magic', 'coven', 'sleepy'].map(name => position(shown, name))
  expect(order).toEqual([...order].sort((a, b) => a - b))
  await ui.unmount()
})

test('a working PM that waits on the GM is listed with the ones waiting', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({ name: 'busy', state: 'working', lines: [] }),
        pm({ name: 'asking', state: 'working', lines: ['waits on GM: QUESTION asking/1 since 08:00'] }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(position(shown, 'asking')).toBeLessThan(position(shown, 'busy'))
  expect(shown).toMatch(/◆ 1 espera/)
  await ui.unmount()
})

test('inside a group the newest PM comes first', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({ name: 'slow', state: 'working', age: '2h' }),
        pm({ name: 'days', state: 'working', age: '1d' }),
        pm({ name: 'fresh', state: 'working', age: '3m' }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  const order = ['fresh', 'slow', 'days'].map(name => position(shown, name))
  expect(order).toEqual([...order].sort((a, b) => a - b))
  await ui.unmount()
})

test('the pm- prefix is dropped from names', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/coven/)
  expect(shown).not.toMatch(/pm-coven/)
  await ui.unmount()
})

const waitsOn = (...asks: string[]) => `waits on GM: ${asks.join(', ')}`

test('a card that waits shows its last message when that message is the first thing it waits on', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({
          name: 'coven',
          state: 'blocked',
          lines: [waitsOn('QUESTION coven/6 since 01:43'), lastOut('01:43 QUESTION coven/6: which logo goes on the README?')],
        }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)

  expect(await rows(ui)).toContain('  QUESTION #6  which logo goes on the README?')
  expect(await screen(ui)).not.toMatch(/esperando respuesta/)
  await ui.unmount()
})

const NOT_THE_FIRST_ASK = [
  ['another ref', '01:30 QUESTION coven/5: an older question'],
  ['another kind', '01:30 DONE coven/6: finished something'],
  ['no last message out', ''],
]

for (const [name, out] of NOT_THE_FIRST_ASK) {
  test(`a card that waits names what it awaits when its last message is not it (${name})`, async ($, on) => {
    const lines = out ? [waitsOn('QUESTION coven/6 since 01:43'), lastOut(out)] : [waitsOn('QUESTION coven/6 since 01:43')]
    stage(on, { stdout: status(pm({ name: 'coven', state: 'blocked', lines })) })
    await started($)

    const ui = await mountPane($)
    const waiting = await ui.find({ type: 'Text', text: /^esperando respuesta$/ })

    expect(await rows(ui)).toContain('  QUESTION #6  esperando respuesta')
    expect(waiting?.props.color).toBe('inactive')
    expect(await screen(ui)).not.toMatch(/older question|finished something/)
    await ui.unmount()
  })
}

test('a card that waits on several things adds how many more', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({
          name: 'coven',
          state: 'blocked',
          lines: [waitsOn('QUESTION coven/6 since 01:43', 'PROD-STEP coven/7 since 01:50', 'DONE coven/8 since ?')],
        }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const more = await ui.find({ type: 'Text', text: /^ \+2 más$/ })

  expect(await rows(ui)).toContain('  QUESTION #6  esperando respuesta +2 más')
  expect(more?.props.color).toBe('inactive')
  await ui.unmount()
})

test('a matching last message is shown without a count of the other things it waits on', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({
          name: 'coven',
          state: 'blocked',
          lines: [waitsOn('QUESTION coven/6 since 01:43', 'DONE coven/8 since ?'), lastOut('01:43 QUESTION coven/6: which logo?')],
        }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)

  expect(await rows(ui)).toContain('  QUESTION #6  which logo?')
  expect(await screen(ui)).not.toMatch(/más/)
  await ui.unmount()
})

test('a dead PM stays a problem even with a waits line, and names what it waited on like a waits card', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({ name: 'busy', state: 'working', lines: [] }),
        pm({ name: 'asking', state: 'working', lines: [waitsOn('QUESTION asking/1 since 08:00')] }),
        pm({
          name: 'dead',
          state: 'stale',
          age: '2h',
          lines: [waitsOn('QUESTION dead/1 since 07:00'), lastOut('06:00 BLOCKED dead/1: no repo access')],
        }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await rows(ui)

  expect(shown[0]).toBe('◆ 1 espera ⋆ ✕ 1 problema ⋆ ✦ 1 trabajando')
  expect(shown.find(row => row.startsWith('✕ dead'))).toMatch(/^✕ dead {2,}proceso muerto {2,}2h$/)
  expect(shown).toContain('  QUESTION #1  esperando respuesta')
  expect(await screen(ui)).not.toMatch(/07:00|no repo access/)
  await ui.unmount()
})

test('a dead PM whose last message is the first thing it waited on shows that message', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({
          name: 'dead',
          state: 'stale',
          age: '2h',
          lines: [waitsOn('QUESTION dead/1 since 07:00'), lastOut('07:00 QUESTION dead/1: which repo?')],
        }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)

  expect(await rows(ui)).toContain('  QUESTION #1  which repo?')
  await ui.unmount()
})

const MESSAGE_KINDS = [
  { kind: 'QUESTION', color: 'permission', line: lastOut('00:01 QUESTION coven/1: hi') },
  { kind: 'PROD-STEP', color: 'warning', line: lastOut('00:01 PROD-STEP coven/1: hi') },
  { kind: 'DRAFT', color: 'suggestion', line: lastOut('00:01 DRAFT coven/1: hi') },
  { kind: 'DONE', color: 'success', line: lastOut('00:01 DONE coven/1: hi') },
  { kind: 'BLOCKED', color: 'error', line: lastOut('00:01 BLOCKED coven/1: hi') },
  { kind: 'ANSWER', color: 'inactive', line: 'last in:  00:01 ANSWER coven/1: hi  (from gm-a5)' },
  { kind: 'APPROVED', color: 'inactive', line: 'last in:  00:01 APPROVED coven/1 ap-7: hi  (from gm-a5)' },
  { kind: 'DENIED', color: 'inactive', line: 'last in:  00:01 DENIED coven/1: hi  (from gm-a5)' },
  { kind: 'TASK', color: 'inactive', line: 'last in:  00:01 TASK coven/1: hi  (from gm-a5)' },
]

for (const { kind, color, line } of MESSAGE_KINDS) {
  test(`a ${kind} message shows its kind in bold ${color} and the rest as a ref and a text`, async ($, on) => {
    stage(on, { stdout: status(pm({ name: 'coven', state: 'working', lines: [line] })) })
    await started($)

    const ui = await mountPane($)
    const chip = await ui.find({ type: 'Text', text: new RegExp(`^${kind}$`) })

    expect(chip?.props.color).toBe(color)
    expect(chip?.props.bold).toBe(true)
    expect(await rows(ui)).toContain(`  ${kind} #1  hi`)
    await ui.unmount()
  })
}

test('a card shows the last message out as kind, ref and text, without its time or delivery flag', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({
          name: 'coven',
          state: 'working',
          lines: [
            lastOut('00:14 DONE coven/3: Python 3.14 CI fix is PR #4'),
            'last in:  23:57 ANSWER coven/3: logo C "Star Circle"  (from gm-a5)',
          ],
        }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(await rows(ui)).toContain('  DONE #3  Python 3.14 CI fix is PR #4')
  expect(shown).not.toMatch(/00:14|\[delivered\]|Star Circle/)
  await ui.unmount()
})

test('a card with only an incoming message shows that one, without its sender', async ($, on) => {
  stage(
    on,
    { stdout: status(pm({ name: 'coven', state: 'working', lines: ['last in:  23:57 ANSWER coven/3: logo C  (from gm-a5)'] })) },
  )
  await started($)

  const ui = await mountPane($)

  expect(await rows(ui)).toContain('  ANSWER #3  logo C')
  expect(await screen(ui)).not.toMatch(/gm-a5/)
  await ui.unmount()
})

test('a message with an unknown clock parses like one with a time', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'coven', state: 'working', lines: [lastOut('? DONE coven/3: no clock')] })) })
  await started($)

  const ui = await mountPane($)

  expect(await rows(ui)).toContain('  DONE #3  no clock')
  await ui.unmount()
})

test('a ref of the own project is #n, and any other ref is kept as written', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({ name: 'coven', state: 'working', age: '1m', lines: [lastOut('00:14 DONE cauldron/2: from elsewhere')] }),
        pm({ name: 'cauldron', state: 'working', age: '2m', lines: [lastOut('00:14 DONE cauldron/9: from home')] }),
        pm({ name: 'cov', state: 'working', age: '3m', lines: [lastOut('00:14 DONE coven/4: a longer project name')] }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await rows(ui)
  const foreign = await ui.find({ type: 'Text', text: /^cauldron\/2$/ })
  const own = await ui.find({ type: 'Text', text: /^#9$/ })

  expect(shown).toContain('  DONE cauldron/2  from elsewhere')
  expect(shown).toContain('  DONE #9  from home')
  expect(shown).toContain('  DONE coven/4  a longer project name')
  expect(foreign?.props.color).toBe('subtle')
  expect(own?.props.color).toBe('subtle')
  await ui.unmount()
})

const KEY_LISTS = [
  ['and', 'a/1 and a/2'],
  ['&', 'a/1 & a/2'],
  ['+', 'a/1 + a/2'],
  ['a comma', 'a/1, a/2'],
]

for (const [joiner, keys] of KEY_LISTS) {
  test(`a message about two tasks joined by ${joiner} shows both as #n`, async ($, on) => {
    stage(on, { stdout: status(pm({ name: 'a', state: 'working', lines: [lastOut(`01:43 DONE ${keys}: shipped`)] })) })
    await started($)

    const ui = await mountPane($)

    expect(await rows(ui)).toContain('  DONE #1 #2  shipped')
    await ui.unmount()
  })
}

test('in a list of tasks the own project shows as #n and any other task is kept as written', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'a', state: 'working', lines: [lastOut('01:43 DONE a/1 and b/2 and a/3: shipped')] })) })
  await started($)

  const ui = await mountPane($)

  expect(await rows(ui)).toContain('  DONE #1 b/2 #3  shipped')
  await ui.unmount()
})

test('a message without a task key shows its kind and the rest as text, not a wrong ref', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'coven', state: 'working', lines: [lastOut('10:00 BLOCKED no access: need token')] })) })
  await started($)

  const ui = await mountPane($)

  expect(await rows(ui)).toContain('  BLOCKED  no access: need token')
  await ui.unmount()
})

test('only an APPROVED message has an approval id after its ref', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({ name: 'coven', state: 'working', age: '1m', lines: [lastOut('10:00 DONE coven/1 ap-9: shipped')] }),
        pm({ name: 'tower', state: 'working', age: '2m', lines: ['last in:  10:00 APPROVED tower/2 ap-9: go  (from gm-a5)'] }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await rows(ui)

  expect(shown).toContain('  DONE  coven/1 ap-9: shipped')
  expect(shown).toContain('  APPROVED #2  go')
  await ui.unmount()
})

test('a waits card compares its last message with the first key of the message', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({
          name: 'a',
          state: 'blocked',
          age: '1m',
          lines: [waitsOn('DONE a/1 since 09:00'), lastOut('09:00 DONE a/1 and a/2: shipped')],
        }),
        pm({
          name: 'b',
          state: 'blocked',
          age: '2m',
          lines: [waitsOn('DONE b/2 since 09:00'), lastOut('09:00 DONE b/1 and b/2: shipped')],
        }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await rows(ui)

  expect(shown).toContain('  DONE #1 #2  shipped')
  expect(shown).toContain('  DONE #2  esperando respuesta')
  await ui.unmount()
})

test('a message cut to 200 characters by coven keeps its text and its ellipsis', async ($, on) => {
  const text = `${'word '.repeat(36)}words…`
  const message = `DONE coven/3: ${text}`
  stage(on, { stdout: status(pm({ name: 'coven', state: 'working', lines: [lastOut(`00:14 ${message}`)] })) })
  await started($)

  const ui = await mountPane($, 'dock', 300)

  expect(message).toHaveLength(200)
  expect(await rows(ui)).toContain(`  DONE #3  ${text}`)
  await ui.unmount()
})

test('a message not delivered carries its flag right after the ref, so a long text cannot push it off', async ($, on) => {
  const long = 'long '.repeat(60)
  stage(on, { stdout: status(pm({ name: 'magic', state: 'working', lines: [`last out: 00:10 BLOCKED magic/1: ${long}  [NOT DELIVERED: x]`] })) })
  await started($)

  const ui = await mountPane($)

  expect((await rows(ui)).find(row => row.includes('BLOCKED'))).toMatch(/^ {2}BLOCKED #1 · no entregado {2}long long/)
  await ui.unmount()
})

test('the not-delivered flag shows on a waits card only when the card shows that last message', async ($, on) => {
  const undelivered = (out: string) => `last out: ${out}   [NOT DELIVERED: never sent]`
  stage(
    on,
    {
      stdout: status(
        pm({
          name: 'shown',
          state: 'blocked',
          age: '1m',
          lines: [waitsOn('QUESTION shown/1 since 09:00'), undelivered('09:00 QUESTION shown/1: which?')],
        }),
        pm({
          name: 'hidden',
          state: 'blocked',
          age: '2m',
          lines: [waitsOn('QUESTION hidden/2 since 09:00'), undelivered('08:00 DONE hidden/1: older')],
        }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await rows(ui)

  expect(shown).toContain('  QUESTION #1 · no entregado  which?')
  expect(shown).toContain('  QUESTION #2  esperando respuesta')
  expect(shown.filter(row => row.includes('no entregado'))).toHaveLength(1)
  await ui.unmount()
})

test('a last message that does not parse shows the not-delivered flag in front, where it cannot be cut off', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'coven', state: 'working', lines: ['last out: something odd happened   [NOT DELIVERED: x]'] })) })
  await started($)

  const ui = await mountPane($)

  expect(await rows(ui)).toContain('  no entregado  something odd happened')
  await ui.unmount()
})

test('the message text is inactive, and the approval id of an APPROVED line is not shown', async ($, on) => {
  stage(
    on,
    { stdout: status(pm({ name: 'coven', state: 'working', lines: ['last in:  00:01 APPROVED coven/4 ap-12: go ahead  (from gm-a5)'] })) },
  )
  await started($)

  const ui = await mountPane($)
  const text = await ui.find({ type: 'Text', text: /^go ahead$/ })

  expect(text?.props.color).toBe('inactive')
  expect(await screen(ui)).not.toMatch(/ap-12/)
  await ui.unmount()
})

const NOT_A_MESSAGE = ['10:00 a note with no kind', 'something odd happened', '10:00 SHOUT coven/1: not a kind we know']

for (const line of NOT_A_MESSAGE) {
  test(`a line that does not parse is shown as written, inactive (${line})`, async ($, on) => {
    stage(on, { stdout: status(pm({ name: 'coven', state: 'working', lines: [lastOut(line)] })) })
    await started($)

    const ui = await mountPane($)
    const shown = await ui.find({ type: 'Text', text: new RegExp(`^${line}$`) })

    expect(await rows(ui)).toContain(`  ${line}`)
    expect(shown?.props.color).toBe('inactive')
    await ui.unmount()
  })
}

test('a message not delivered keeps its text and adds the flag in error', async ($, on) => {
  stage(
    on,
    { stdout: status(pm({ name: 'magic', state: 'working', lines: ['last out: 00:10 BLOCKED magic/1: no repo access   [NOT DELIVERED: never sent]'] })) },
  )
  await started($)

  const ui = await mountPane($)
  const flag = await ui.find({ type: 'Text', text: /^ · no entregado$/ })

  expect(await rows(ui)).toContain('  BLOCKED #1 · no entregado  no repo access')
  expect(flag?.props.color).toBe('error')
  await ui.unmount()
})

test('a message that only quotes a NOT DELIVERED flag is not marked as undelivered', async ($, on) => {
  stage(
    on,
    { stdout: status(pm({ name: 'coven', state: 'working', lines: [lastOut('10:00 DONE coven/1: saw [NOT DELIVERED: old] earlier')] })) },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/saw \[NOT DELIVERED: old\] earlier/)
  expect(shown).not.toMatch(/no entregado/)
  expect(shown).toMatch(/1 trabajando/)
  await ui.unmount()
})

test('a NOT DELIVERED reason that contains a bracket is still cut off the message', async ($, on) => {
  stage(
    on,
    { stdout: status(pm({ name: 'lost', state: 'idle', lines: ['last out: 10:00 DONE x/1: thing   [NOT DELIVERED: bad [x] thing]'] })) },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/DONE x\/1 · no entregado {2}thing/)
  expect(shown).toMatch(/no entregado/)
  expect(shown).not.toMatch(/bad \[x\]/)
  await ui.unmount()
})

test('a line that is not indented and not a header does not feed the PM above it', async ($, on) => {
  stage(
    on,
    {
      stdout: [
        pm({ name: 'coven', state: 'working' }),
        'a stray line from something else',
        '  last in:  09:00 ANSWER coven/9: leaked',
        '',
      ].join('\n'),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/sin mensajes todavía/)
  expect(shown).not.toMatch(/leaked/)
  await ui.unmount()
})

test('a card without messages says so', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  await started($)

  const ui = await mountPane($)

  expect(await screen(ui)).toMatch(/sin mensajes todavía/)
  await ui.unmount()
})

test('the detail line is cut at the pane width, not wrapped', async ($, on) => {
  stage(
    on,
    { stdout: status(pm({ name: 'coven', state: 'working', lines: [lastOut(`00:14 DONE coven/3: ${'long '.repeat(40)}`)] })) },
  )
  await started($)

  const ui = await mountPane($)
  const detail = await ui.find({ type: 'Text', text: /DONE/ })

  expect(detail?.props.wrap).toBe('truncate-end')
  await ui.unmount()
})

test('a message that was not delivered says "no entregado" and counts as a problem', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({ name: 'working', state: 'working' }),
        pm({ name: 'lost', state: 'idle', lines: ['last out: 10:00 DONE x/1: thing   [NOT DELIVERED: never sent]'] }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)
  const flags = await ui.findAll({ type: 'Text', text: /no entregado/ })

  expect(flags.map(flag => flag.props.color)).toContain('error')
  expect(shown).not.toMatch(/NOT DELIVERED/)
  expect(position(shown, 'lost')).toBeLessThan(position(shown, 'working'))
  expect(shown).toMatch(/✕ 1 problema/)
  await ui.unmount()
})

test('a state the pane does not know shows as "sin datos" with the raw state as detail', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'ghost', state: 'unknown (no transcript)', age: '' })) })
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/sin datos/)
  expect(shown).toMatch(/unknown \(no transcript\)/)
  await ui.unmount()
})

test('lines the pane has no use for do not break the cards', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({ name: 'coven', state: 'working', lines: ['skipped lines: 4', 'transcript format unknown: read its log'] }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($)

  expect(await screen(ui)).toMatch(/sin mensajes todavía/)
  await ui.unmount()
})

test('done and stopped PMs show only as counts', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/3 terminadas · 1 parada/)
  for (const name of ['lilthop', 'old', 'older', 'napper']) expect(shown).not.toMatch(new RegExp(name))
  await ui.unmount()
})

test('the pane opens with the terminal background showing and asks the dock for 64 columns', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  await started($)

  expect(world.opens).toEqual([{ backgroundColor: 'transparent', columns: 64 }])
})

const FULL_SUMMARY = '◆ 1 espera ⋆ ✕ 1 problema ⋆ ✦ 1 trabajando ⋆ ○ 1 en pausa'
const COMPACT_SUMMARY = '◆ 1 ⋆ ✕ 1 ⋆ ✦ 1 ⋆ ○ 1'
const ONE_OF_EACH = status(
  pm({ name: 'asks', state: 'blocked' }),
  pm({ name: 'dead', state: 'stale' }),
  pm({ name: 'busy', state: 'working' }),
  pm({ name: 'nap', state: 'idle' }),
)

test('the summary spells the words out, joined by a subtle ⋆, when they fit the pane', async ($, on) => {
  stage(on, { stdout: ONE_OF_EACH })
  await started($)

  const ui = await mountPane($, 'dock', 80)
  const entries = await ui.findAll({ type: 'Text', text: /^[◆✕✦○] 1 [^⋆]+$/ })
  const joiners = await ui.findAll({ type: 'Text', text: /^ ⋆ $/ })

  expect((await rows(ui))[0]).toBe(FULL_SUMMARY)
  expect(entries.map(entry => entry.props.color)).toEqual(['permission', 'error', 'claude', 'inactive'])
  expect(joiners.map(joiner => joiner.props.color)).toEqual(['subtle', 'subtle', 'subtle'])
  await ui.unmount()
})

test('the summary uses the plural labels for more than one', async ($, on) => {
  const two = (name: string, state: string) => [pm({ name: `${name}1`, state }), pm({ name: `${name}2`, state })]
  stage(on, { stdout: status(...two('asks', 'blocked'), ...two('dead', 'stale'), ...two('busy', 'working'), ...two('nap', 'idle')) })
  await started($)

  const ui = await mountPane($, 'dock', 80)

  expect((await rows(ui))[0]).toBe('◆ 2 esperan ⋆ ✕ 2 problemas ⋆ ✦ 2 trabajando ⋆ ○ 2 en pausa')
  await ui.unmount()
})

test('the summary shrinks to glyph and count, in its colours, when the words do not fit', async ($, on) => {
  stage(on, { stdout: ONE_OF_EACH })
  await started($)

  const ui = await mountPane($, 'dock', 40)
  const entries = await ui.findAll({ type: 'Text', text: /^[◆✕✦○] 1$/ })
  const joiners = await ui.findAll({ type: 'Text', text: /^ ⋆ $/ })

  expect((await rows(ui))[0]).toBe(COMPACT_SUMMARY)
  expect(entries.map(entry => entry.props.color)).toEqual(['permission', 'error', 'claude', 'inactive'])
  expect(joiners.map(joiner => joiner.props.color)).toEqual(['subtle', 'subtle', 'subtle'])
  await ui.unmount()
})

test('the summary keeps its words at exactly the width they take, and shrinks one column below', async ($, on) => {
  stage(on, { stdout: ONE_OF_EACH })
  await started($)

  const exact = await mountPane($, 'dock', FULL_SUMMARY.length)
  expect((await rows(exact))[0]).toBe(FULL_SUMMARY)
  await exact.unmount()

  const narrower = await mountPane($, 'dock', FULL_SUMMARY.length - 1)
  expect((await rows(narrower))[0]).toBe(COMPACT_SUMMARY)
  await narrower.unmount()
})

test('with nobody live the summary says nobody works', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'lilthop', state: 'done', age: '1d' })) })
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/Nadie trabajando ahora\./)
  expect(shown).toMatch(/1 terminada/)
  await ui.unmount()
})

/** A row with `left` at the left edge and `right` at the right edge of `width` columns. */
function edges(left: string, right: string, width: number): string {
  return left + ' '.repeat(width - left.length - right.length) + right
}

const NAME_COLUMN = (name: string) => `${name.padEnd(13)}`

/** The rule above and below a card: a star, dashes the rest of the width, a star. */
const starRule = (width: number) => `⋆${'─'.repeat(width - 2)}⋆`

for (const width of [61, 40, 100]) {
  test(`the name column and the right edge line up at ${width} columns`, async ($, on) => {
    stage(
      on,
      {
        stdout: status(
          pm({
            name: 'coven',
            state: 'blocked',
            age: '17m',
            lines: [waitsOn('QUESTION coven/6 since 01:43'), lastOut('01:43 QUESTION coven/6: which logo?')],
          }),
          pm({ name: 'north-tower', state: 'stale', age: '2h' }),
          pm({ name: 'cauldron', state: 'working', age: '12m' }),
        ),
      },
    )
    await started($)

    const ui = await mountPane($, 'dock', width)
    const shown = await rows(ui)

    expect(shown).toContain(edges(`◆ ${NAME_COLUMN('coven')}espera al GM`, 'desde 01:43', width))
    expect(shown).toContain(edges(`✕ ${NAME_COLUMN('north-tower')}proceso muerto`, '2h', width))
    expect(shown).toContain(edges(`✦ ${NAME_COLUMN('cauldron')}trabajando`, '12m', width))
    await ui.unmount()
  })
}

test('a name longer than the name column is cut with an ellipsis', async ($, on) => {
  stage(
    on,
    { stdout: status(pm({ name: 'a-very-long-project-name', state: 'working', age: '3m' }), pm({ name: 'ab', state: 'working', age: '9m' })) },
  )
  await started($)

  const ui = await mountPane($, 'dock', 61)
  const shown = await rows(ui)

  expect(shown).toContain(edges('✦ a-very-long-p…  trabajando', '3m', 61))
  expect(shown).toContain(edges('✦ ab              trabajando', '9m', 61))
  await ui.unmount()
})

test('when the row does not fit the gap shrinks to one space and the state word is cut first', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'coven', state: 'blocked', lines: [waitsOn('QUESTION coven/6 since 09:00')] })) })
  await started($)

  const ui = await mountPane($, 'dock', 30)

  expect(await rows(ui)).toContain('◆ coven  espera a… desde 09:00')
  await ui.unmount()
})

const ASKING = (name: string, extra: string[] = []) =>
  pm({ name, state: 'blocked', age: '17m', lines: [waitsOn(`QUESTION ${name}/6 since 01:43`), ...extra] })

/** Reads again with a new last message, so the PM is marked nuevo. */
async function makeNew(world: Stage, stdout: string) {
  world.reply.stdout = stdout
  await world.clock.advance(READ_MS)
}

test('at 24 columns the time stays whole and the state word goes', async ($, on) => {
  stage(on, { stdout: status(ASKING('aquelarre')) })
  await started($)

  const ui = await mountPane($, 'dock', 24)

  expect(await rows(ui)).toContain(edges('◆ aquelarre', 'desde 01:43', 24))
  await ui.unmount()
})

test('at 24 columns a new mark goes too, and the time still stays whole', async ($, on) => {
  const world = stage(on, { stdout: status(ASKING('aquelarre', [lastOut('01:00 DONE aquelarre/1: one')])) })
  await started($)
  const ui = await mountPane($, 'dock', 24)

  await makeNew(world, status(ASKING('aquelarre', [lastOut('01:10 DONE aquelarre/2: two')])))

  const shown = await rows(ui)
  expect(shown).toContain(edges('◆ aquelarre', 'desde 01:43', 24))
  expect(shown.join('\n')).not.toMatch(/nuevo/)
  await ui.unmount()
})

test('a row that is too tight cuts the state word first, then the new mark, then the name', async ($, on) => {
  const world = stage(on, { stdout: status(ASKING('aquelarre', [lastOut('01:00 DONE aquelarre/1: one')])) })
  await started($)
  await makeNew(world, status(ASKING('aquelarre', [lastOut('01:10 DONE aquelarre/2: two')])))
  const rowAt = async (width: number) => {
    const narrow = await mountPane($, 'dock', width)
    const found = (await rows(narrow)).find(row => row.endsWith('desde 01:43'))
    await narrow.unmount()
    return found
  }

  expect(await rowAt(46)).toBe(edges('◆ aquelarre  espera al GM  • nuevo', 'desde 01:43', 46))
  expect(await rowAt(45)).toBe(edges('◆ aquelarre  espera al …  • nuevo', 'desde 01:43', 45))
  expect(await rowAt(36)).toBe(edges('◆ aquelarre  e…  • nuevo', 'desde 01:43', 36))
  expect(await rowAt(35)).toBe(edges('◆ aquelarre  • nuevo', 'desde 01:43', 35))
  expect(await rowAt(32)).toBe(edges('◆ aquelarre  • nuevo', 'desde 01:43', 32))
  expect(await rowAt(31)).toBe(edges('◆ aquelarre', 'desde 01:43', 31))
})

test('a long name is cut last, down to one character and an ellipsis, and the time is never cut', async ($, on) => {
  stage(on, { stdout: status(ASKING('a-very-long-project-name')) })
  await started($)
  const rowAt = async (width: number) => {
    const ui = await mountPane($, 'dock', width)
    const found = (await rows(ui)).find(row => row.endsWith('desde 01:43'))
    await ui.unmount()
    return found
  }

  expect(await rowAt(24)).toBe(edges('◆ a-very-lo…', 'desde 01:43', 24))
  expect(await rowAt(18)).toBe(edges('◆ a-v…', 'desde 01:43', 18))
  expect(await rowAt(16)).toBe(edges('◆ a…', 'desde 01:43', 16))
})

test('at 100 columns the card keeps its layout, with the time at the right edge', async ($, on) => {
  stage(on, { stdout: status(ASKING('coven', [lastOut('01:43 QUESTION coven/6: which logo?')]), pm({ name: 'north-tower', state: 'stale', age: '2h' })) })
  await started($)

  const ui = await mountPane($, 'dock', 100)
  const shown = await rows(ui)

  expect(shown).toContain(edges(`◆ ${NAME_COLUMN('coven')}espera al GM`, 'desde 01:43', 100))
  expect(shown).toContain(edges(`✕ ${NAME_COLUMN('north-tower')}proceso muerto`, '2h', 100))
  await ui.unmount()
})

const NARROW_WIDTHS = [24, 30, 40, 61, 100]

function crowded(tag: string): string {
  return status(
    ASKING('a-very-long-project-name', [lastOut(`01:43 QUESTION a-very-long-project-name/6: ${tag}`)]),
    pm({ name: 'north-tower', state: 'stale', age: '2h', lines: [lastOut(`00:10 BLOCKED north-tower/1: ${tag}`)] }),
    pm({ name: 'aquelarre', state: 'working', age: '0m', lines: [lastOut(`00:14 DONE aquelarre/2: ${tag} ${'long '.repeat(60)}`)] }),
    pm({ name: 'old', state: 'done', age: '1d' }),
    pm({ name: 'napper', state: 'stopped', age: '5d' }),
  )
}

for (const width of NARROW_WIDTHS) {
  test(`no row of the pane is wider than ${width} columns, new marks and long names included`, async ($, on) => {
    const world = stage(on, { stdout: crowded('one') })
    await started($)
    const ui = await mountPane($, 'dock', width)
    await makeNew(world, crowded('two'))

    const shown = await rows(ui)
    // A message line is cut by the surface (truncate-end, tested above); every other row must already fit.
    const fixed = shown.filter(row => !row.startsWith('  '))
    // The crowded fixture really holds the marks the invariant is about, wherever they still fit.
    if (width >= 40) expect(shown.join('\n')).toMatch(/• nuevo/)
    expect(fixed.some(row => row.includes('a-very'))).toBe(true)
    for (const row of fixed) expect(row.length).toBeLessThanOrEqual(width)
    await ui.unmount()
  })
}

test('the footer clips the resting counts first and keeps the time whole', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($, 'dock', 24)

  expect((await rows(ui)).at(-1)).toBe(edges('⋆ 3 terminad…', '↻ 12:00:05', 24))
  await ui.unmount()
})

test('a waits card shows since when on the right, and every other card its age', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({ name: 'tower', state: 'blocked', age: '10m', lines: [waitsOn('QUESTION tower/2 since 09:00, DONE tower/3 since 10:00')] }),
        pm({ name: 'unclocked', state: 'blocked', age: '8m', lines: [waitsOn('QUESTION unclocked/1 since ?')] }),
        pm({ name: 'dead', state: 'stale', age: '2h', lines: [waitsOn('QUESTION dead/1 since 07:00')] }),
        pm({ name: 'busy', state: 'working', age: '3m' }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($, 'dock', 61)
  const shown = await rows(ui)
  const card = (name: string) => shown.find(row => row.slice(2).startsWith(name)) ?? ''
  const since = await ui.find({ type: 'Text', text: /^desde 09:00$/ })

  expect(card('tower')).toMatch(/ desde 09:00$/)
  expect(card('unclocked')).toMatch(/ 8m$/)
  expect(card('dead')).toMatch(/ 2h$/)
  expect(card('busy')).toMatch(/ 3m$/)
  expect(since?.props.color).toBe('subtle')
  await ui.unmount()
})

test('a card that is new says so after its state word, in the candle colour', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working', age: '1m' })) })
  await started($)
  const ui = await mountPane($, 'dock', 61)

  world.reply.stdout = status(pm({ name: 'coven', state: 'blocked', age: '2m' }))
  await world.clock.advance(READ_MS)

  expect(await rows(ui)).toContain(edges('◆ coven  espera al GM  • nuevo', '2m', 61))
  await ui.unmount()
})

test('the footer has the resting counts on the left and the time of the last read on the right', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($, 'dock', 61)
  const shown = await rows(ui)
  const footer = await ui.find({ type: 'Text', text: /↻ 12:00:05/ })

  expect(shown.at(-1)).toBe(edges('⋆ 3 terminadas · 1 parada', '↻ 12:00:05', 61))
  expect(footer?.props.color).toBe('subtle')
  expect(await screen(ui)).not.toMatch(/cada 5 s/)
  await ui.unmount()
})

test('with nothing resting the footer is the time alone, at the right edge', async ($, on) => {
  stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  await started($)

  const ui = await mountPane($, 'dock', 61)

  expect((await rows(ui)).at(-1)).toBe(edges('', '↻ 12:00:05', 61))
  await ui.unmount()
})

test('the pane reads as a summary, cards between star rules and a footer, with a blank row around the cards', async ($, on) => {
  stage(
    on,
    {
      stdout: status(
        pm({
          name: 'coven',
          state: 'blocked',
          age: '17m',
          lines: [waitsOn('QUESTION coven/6 since 01:43'), lastOut('01:43 QUESTION coven/6: which logo goes on the README?')],
        }),
        pm({
          name: 'north-tower',
          state: 'stale',
          age: '2h',
          lines: ['last out: 00:10 BLOCKED north-tower/1: no repo access   [NOT DELIVERED: never sent]'],
        }),
        pm({ name: 'cauldron', state: 'working', age: '12m', lines: [lastOut('00:14 DONE cauldron/2: hooks moved')] }),
        pm({ name: 'old', state: 'done', age: '1d' }),
        pm({ name: 'older', state: 'done', age: '2d' }),
        pm({ name: 'napper', state: 'stopped', age: '5d' }),
      ),
    },
  )
  await started($)

  const ui = await mountPane($, 'dock', 61)

  expect(await rows(ui)).toEqual([
    '◆ 1 espera ⋆ ✕ 1 problema ⋆ ✦ 1 trabajando',
    '',
    starRule(61),
    edges(`◆ ${NAME_COLUMN('coven')}espera al GM`, 'desde 01:43', 61),
    '  sin plan todavía',
    '  QUESTION #6  which logo goes on the README?',
    starRule(61),
    edges(`✕ ${NAME_COLUMN('north-tower')}proceso muerto`, '2h', 61),
    '  sin plan todavía',
    '  BLOCKED #1 · no entregado  no repo access',
    starRule(61),
    edges(`✦ ${NAME_COLUMN('cauldron')}trabajando`, '12m', 61),
    '  sin plan todavía',
    '  DONE #2  hooks moved',
    starRule(61),
    '',
    edges('⋆ 2 terminadas · 1 parada', '↻ 12:00:05', 61),
  ])
  await ui.unmount()
})

test('each live PM sits between star rules', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($, 'dock', 61)
  const shown = await rows(ui)
  const body = shown.slice(2, -2)

  expect(body.filter(row => row === starRule(61))).toHaveLength(5)
  expect(body[0]).toBe(starRule(61))
  expect(body.at(-1)).toBe(starRule(61))
  expect(body).not.toContain('')
  expect(shown[1]).toBe('')
  expect(shown.at(-2)).toBe('')
  await ui.unmount()
})

test('the rule is subtle and the full width', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($, 'dock', 34)
  const rules = await ui.findAll({ type: 'Text', text: /^⋆─+⋆$/ })

  expect(rules).toHaveLength(5)
  for (const found of rules) {
    expect(found.text).toBe(starRule(34))
    expect(found.text).toHaveLength(34)
    expect(found.props.color).toBe('subtle')
  }
  await ui.unmount()
})

test('the PM name is bold gold', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($)
  for (const name of ['tower', 'coven']) {
    const found = await ui.find({ type: 'Text', text: new RegExp(`^${name}\\s*$`) })
    expect(found?.props.bold).toBe(true)
    expect(found?.props.color).toBe('claude')
  }
  await ui.unmount()
})

const PM_ID = '1333568a'
const TASKS = `${HOME}/.claude/tasks/${PM_ID}-0000-4000-8000-000000000000`

type TaskSpec = [subject: string, status: string]
const done = (subject: string): TaskSpec => [subject, 'completed']
const current = (subject: string): TaskSpec => [subject, 'in_progress']
const next = (subject: string): TaskSpec => [subject, 'pending']

const taskFile = (folder: string, n: number | string, task: unknown): [string, string] => [
  `${folder}/${n}.json`,
  typeof task === 'string' ? task : JSON.stringify(task),
]

/** The task files of one PM, numbered from 1 in the order given, as Claude Code writes them. */
function plan(steps: TaskSpec[], folder = TASKS): Record<string, string> {
  return Object.fromEntries(
    steps.map(([subject, status], index) => taskFile(folder, index + 1, { id: String(index + 1), subject, status })),
  )
}

/** Matches a Text whose whole text is `text`, not one that merely contains it. */
const exactly = (text: string) => new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`)

/** The coloured Text whose whole text is `text`: a row's own, not the Text of the row that holds it. */
async function paintedRow(ui: Mount, text: string): Promise<FoundElement | undefined> {
  return (await ui.findAll({ type: 'Text', text: exactly(text) })).find(found => found.props.color !== undefined)
}

/** The rows of the first card under its headline, up to the rule that closes it. */
async function firstCard(ui: Mount, width = 80): Promise<string[]> {
  const shown = await rows(ui)
  const start = shown.indexOf(starRule(width))
  const end = shown.indexOf(starRule(width), start + 1)
  return shown.slice(start + 2, end)
}

const WORKING = (lines: string[] = []) => status(pm({ name: 'coven', state: 'working', lines }))
const WAITING = status(
  pm({
    name: 'coven',
    state: 'blocked',
    lines: [waitsOn('QUESTION coven/4 since 09:00'), lastOut('09:00 QUESTION coven/4: where should a plan come from?')],
  }),
)
const WAIT_TAG = '  ◆ espera tu respuesta'

test('the plan shows done steps gray, the current one gold and the next ones purple', async ($, on) => {
  stage(on, {
    stdout: WORKING([lastOut('00:14 DONE coven/3: one')]),
    files: plan([done('Spec'), current('Build'), next('Review'), next('PR')]),
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ✓ Spec', '  ▸ Build', '  · Review', '  · PR'])
  const spec = await ui.find({ type: 'Text', text: exactly('  ✓ Spec') })
  const build = await paintedRow(ui, '  ▸ Build')
  const review = await ui.find({ type: 'Text', text: exactly('  · Review') })
  const pr = await ui.find({ type: 'Text', text: exactly('  · PR') })
  expect(spec?.props.color).toBe('subtle')
  expect(build?.props.color).toBe('claude')
  expect(build?.props.bold).toBe(true)
  expect(review?.props.color).toBe('suggestion')
  expect(pr?.props.color).toBe('suggestion')
  await ui.unmount()
})

test('more than two done steps fold into a count', async ($, on) => {
  stage(on, {
    stdout: WORKING(),
    files: plan([done('a'), done('b'), done('c'), done('d'), done('e'), current('Build')]),
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ✓ 5 hechas', '  ▸ Build'])
  expect((await ui.find({ type: 'Text', text: exactly('  ✓ 5 hechas') }))?.props.color).toBe('subtle')
  await ui.unmount()
})

test('two done steps show one by one, and three fold', async ($, on) => {
  const world = stage(on, { stdout: WORKING(), files: plan([done('a'), done('b'), current('Build')]) })
  await started($)
  const ui = await mountPane($)
  expect(await firstCard(ui)).toEqual(['  ✓ a', '  ✓ b', '  ▸ Build'])

  world.files = plan([done('a'), done('b'), done('c'), current('Build')])
  await world.clock.advance(TICK_MS)

  expect(await firstCard(ui)).toEqual(['  ✓ 3 hechas', '  ▸ Build'])
  await ui.unmount()
})

test('only the next three steps show, then how many more', async ($, on) => {
  const world = stage(on, {
    stdout: WORKING(),
    files: plan([next('one'), next('two'), next('three'), next('four'), next('five')]),
  })
  await started($)
  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  · one', '  · two', '  · three', '    +2 más'])
  expect((await ui.find({ type: 'Text', text: exactly('    +2 más') }))?.props.color).toBe('suggestion')

  world.files = plan([next('one'), next('two'), next('three')])
  await world.clock.advance(TICK_MS)
  expect(await firstCard(ui)).toEqual(['  · one', '  · two', '  · three'])
  await ui.unmount()
})

test('steps follow the task numbers', async ($, on) => {
  stage(on, {
    stdout: WORKING(),
    files: {
      ...Object.fromEntries([
        taskFile(TASKS, 10, { subject: 'Ten', status: 'pending' }),
        taskFile(TASKS, 9, { subject: 'Nine', status: 'pending' }),
        taskFile(TASKS, 2, { subject: 'Two', status: 'pending' }),
      ]),
    },
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  · Two', '  · Nine', '  · Ten'])
  await ui.unmount()
})

test("a waiting PM's current step carries the pink tag, and its message line still shows", async ($, on) => {
  stage(on, { stdout: WAITING, files: plan([done('Spec'), current('Ask the user'), next('Write it')]) })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual([
    '  ✓ Spec',
    `  ▸ Ask the user${WAIT_TAG}`,
    '  · Write it',
    '  QUESTION #4  where should a plan come from?',
  ])
  const subject = await ui.find({ type: 'Text', text: exactly('  ▸ Ask the user') })
  const tag = await ui.find({ type: 'Text', text: exactly(WAIT_TAG) })
  expect(subject?.props.color).toBe('claude')
  expect(subject?.props.bold).toBe(true)
  expect(tag?.props.color).toBe('permission')
  expect(tag?.props.bold).not.toBe(true)
  await ui.unmount()
})

test('a card that does not wait has no tag on its current step', async ($, on) => {
  stage(on, { stdout: WORKING(), files: plan([current('Build')]) })
  await started($)

  const ui = await mountPane($)

  expect(await screen(ui)).not.toMatch(/espera tu respuesta|◆ *$/m)
  await ui.unmount()
})

const LONG_SUBJECT = 'Ask the user the open questions about the plan'

test('at a narrow dock the wait tag shrinks and every row fits', async ($, on) => {
  const short = status(pm({ name: 'coven', state: 'blocked', lines: [waitsOn('QUESTION coven/4 since 09:00'), lastOut('09:00 QUESTION coven/4: plan?')] }))
  stage(on, { stdout: short, files: plan([current(LONG_SUBJECT)]) })
  await started($)

  const ui = await mountPane($, 'dock', 34)
  const [step] = await firstCard(ui, 34)

  expect(step).toBe(`  ▸ ${LONG_SUBJECT.slice(0, 26)}…  ◆`)
  for (const row of await rows(ui)) expect(row.length).toBeLessThanOrEqual(34)
  const tag = await ui.find({ type: 'Text', text: exactly('  ◆') })
  expect(tag?.props.color).toBe('permission')
  await ui.unmount()
})

test('the wait tag stays whole while the subject keeps 20 columns, and shrinks below that', async ($, on) => {
  stage(on, { stdout: WAITING, files: plan([current(LONG_SUBJECT)]) })
  await started($)

  const roomy = await mountPane($, 'dock', 47)
  const [whole] = await firstCard(roomy, 47)
  expect(whole).toBe(`  ▸ ${LONG_SUBJECT.slice(0, 19)}…${WAIT_TAG}`)
  expect(whole?.length).toBe(47)
  await roomy.unmount()

  const tight = await mountPane($, 'dock', 46)
  const [short] = await firstCard(tight, 46)
  expect(short).toBe(`  ▸ ${LONG_SUBJECT.slice(0, 38)}…  ◆`)
  await tight.unmount()
})

test('the subject is cut with an ellipsis at the pane width', async ($, on) => {
  stage(on, { stdout: WORKING(), files: plan([done('d'.repeat(60)), next('n'.repeat(60))]) })
  await started($)

  const ui = await mountPane($, 'dock', 30)
  const [donePart, nextPart] = await firstCard(ui, 30)

  expect(donePart).toBe(`  ✓ ${'d'.repeat(25)}…`)
  expect(nextPart).toBe(`  · ${'n'.repeat(25)}…`)
  await ui.unmount()
})

test('a working PM with a plan shows no message line', async ($, on) => {
  stage(on, { stdout: WORKING([lastOut('00:14 DONE coven/3: one')]), files: plan([current('Build')]) })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ▸ Build'])
  expect(await screen(ui)).not.toMatch(/DONE/)
  await ui.unmount()
})

test('a problem PM with a plan and nothing it waits on shows no message line', async ($, on) => {
  stage(on, {
    stdout: status(pm({ name: 'coven', state: 'stale', lines: [lastOut('00:10 BLOCKED coven/1: no repo access')] })),
    files: plan([current('Build')]),
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ▸ Build'])
  await ui.unmount()
})

test('a dead PM that waited on something keeps its message line under the plan', async ($, on) => {
  stage(on, {
    stdout: status(pm({ name: 'coven', state: 'stale', lines: [waitsOn('QUESTION coven/4 since 09:00')] })),
    files: plan([current('Build')]),
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ▸ Build', '  QUESTION #4  esperando respuesta'])
  await ui.unmount()
})

test('a PM with no plan shows sin plan todavía and its last message', async ($, on) => {
  stage(on, { stdout: WORKING([lastOut('00:14 DONE coven/3: one')]) })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  sin plan todavía', '  DONE #3  one'])
  const noPlan = await ui.find({ type: 'Text', text: exactly('  sin plan todavía') })
  expect(noPlan?.props.color).toBe('subtle')
  expect(noPlan?.props.wrap).toBe('truncate-end')
  await ui.unmount()
})

test('a waiting PM with no plan shows sin plan todavía above its message line', async ($, on) => {
  stage(on, { stdout: WAITING })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  sin plan todavía', '  QUESTION #4  where should a plan come from?'])
  await ui.unmount()
})

test('a broken or unknown task file is skipped', async ($, on) => {
  stage(on, {
    stdout: WORKING(),
    files: Object.fromEntries([
      taskFile(TASKS, 1, 'not json {'),
      taskFile(TASKS, 2, { subject: 'Deleted one', status: 'deleted' }),
      taskFile(TASKS, 3, { status: 'pending' }),
      taskFile(TASKS, 4, { subject: '', status: 'pending' }),
      taskFile(TASKS, 5, { subject: 42, status: 'pending' }),
      taskFile(TASKS, 6, 'null'),
      taskFile(TASKS, 7, '[]'),
      [`${TASKS}/.lock`, ''],
      [`${TASKS}/.highwatermark`, '7'],
      [`${TASKS}/notes.json`, JSON.stringify({ subject: 'Not a step', status: 'pending' })],
      taskFile(TASKS, 8, { subject: 'Good', status: 'pending' }),
    ]),
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  · Good'])
  await ui.unmount()
})

test('a plan that cannot be read is no plan and no error', async ($, on) => {
  const world = stage(on, { stdout: WORKING(), files: plan([current('Build')]) })
  world.broken.add(`${HOME}/.claude/tasks`)
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  sin plan todavía', '  sin mensajes todavía'])
  expect(await screen(ui)).not.toMatch(/No pude leer/)
  await ui.unmount()
})

test('a folder that cannot be listed gives that PM no plan, and a task that cannot be read is skipped, the others keep theirs', async ($, on) => {
  const other = `${HOME}/.claude/tasks/2222bbbb-0000-4000-8000-000000000000`
  const third = `${HOME}/.claude/tasks/3333cccc-0000-4000-8000-000000000000`
  const world = stage(on, {
    stdout: status(
      pm({ name: 'listbroken', state: 'working', age: '1m' }),
      pm({ name: 'readbroken', state: 'working', age: '2m', id: '2222bbbb' }),
      pm({ name: 'fine', state: 'working', age: '3m', id: '3333cccc' }),
    ),
    files: {
      ...plan([current('lost')]),
      ...plan([current('lost too'), next('also')], other),
      ...plan([current('kept')], third),
    },
  })
  world.broken.add(TASKS)
  world.broken.add(`${other}/2.json`)
  await started($)

  const ui = await mountPane($)
  const shown = await rows(ui)

  expect(shown.filter(row => row === '  sin plan todavía')).toHaveLength(1)
  expect(shown).toContain('  ▸ kept')
  expect(shown).toContain('  ▸ lost too')
  expect(shown).not.toContain('  · also')
  expect(shown).not.toContain('  ▸ lost')
  await ui.unmount()
})

test('a duplicate PM reads the plan of its first id', async ($, on) => {
  const second = `${HOME}/.claude/tasks/99999999-0000-4000-8000-000000000000`
  const world = stage(on, {
    stdout: status(pm({ name: 'coven', state: 'working', id: '1333568a,99999999' })),
    files: { ...plan([current('First id')]), ...plan([current('Second id')], second) },
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ▸ First id'])
  expect(world.touched.some(path => path.includes('99999999'))).toBe(false)
  await ui.unmount()
})

test('of several folders with the id, the first in name order is read', async ($, on) => {
  stage(on, {
    stdout: WORKING(),
    files: {
      ...plan([current('Second')], `${HOME}/.claude/tasks/${PM_ID}-bbbb`),
      ...plan([current('First')], `${HOME}/.claude/tasks/${PM_ID}-aaaa`),
      ...plan([current('Wrong')], `${HOME}/.claude/tasks/${PM_ID}x-aaaa`),
    },
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ▸ First'])
  await ui.unmount()
})

test('the task folders are listed once per read, not once per PM', async ($, on) => {
  const other = `${HOME}/.claude/tasks/2222bbbb-0000-4000-8000-000000000000`
  const world = stage(on, {
    stdout: status(pm({ name: 'one', state: 'working' }), pm({ name: 'two', state: 'idle', id: '2222bbbb' })),
    files: { ...plan([current('A')]), ...plan([current('B')], other) },
  })
  await started($)

  expect(world.touched.filter(path => path === `${HOME}/.claude/tasks`)).toHaveLength(1)
})

test('CLAUDE_CONFIG_DIR wins over HOME', async ($, on) => {
  stage(on, {
    stdout: WORKING(),
    configDir: '/cfg',
    files: {
      ...plan([current('From the config dir')], `/cfg/tasks/${PM_ID}-0000`),
      ...plan([current('From home')]),
    },
  })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ▸ From the config dir'])
  await ui.unmount()
})

test('without CLAUDE_CONFIG_DIR the plans come from .claude under HOME', async ($, on) => {
  stage(on, { stdout: WORKING(), files: plan([current('From home')]) })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  ▸ From home'])
  await ui.unmount()
})

test('with neither CLAUDE_CONFIG_DIR nor HOME no plan is read', async ($, on) => {
  const world = stage(on, { stdout: WORKING(), home: '', files: plan([current('Never')]) })
  await started($)

  const ui = await mountPane($)

  expect(await firstCard(ui)).toEqual(['  sin plan todavía', '  sin mensajes todavía'])
  expect(world.touched).toEqual([])
  await ui.unmount()
})

test('done and stopped PMs are not read', async ($, on) => {
  const finished = `${HOME}/.claude/tasks/2222bbbb-0000-4000-8000-000000000000`
  const stopped = `${HOME}/.claude/tasks/3333cccc-0000-4000-8000-000000000000`
  const world = stage(on, {
    stdout: status(
      pm({ name: 'live', state: 'working' }),
      pm({ name: 'finished', state: 'done', id: '2222bbbb' }),
      pm({ name: 'napper', state: 'stopped', id: '3333cccc' }),
    ),
    files: { ...plan([current('A')]), ...plan([current('B')], finished), ...plan([current('C')], stopped) },
  })
  await started($)

  expect(world.touched.length).toBeGreaterThan(0)
  expect(world.touched.some(path => path.includes('2222bbbb') || path.includes('3333cccc'))).toBe(false)
})

test('nothing is listed when no PM is live', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'finished', state: 'done' })), files: plan([current('A')]) })
  await started($)

  expect(world.touched).toEqual([])
})

test('a new current step marks the PM nuevo', async ($, on) => {
  const world = stage(on, { stdout: WORKING(), files: plan([current('One'), next('Two')]) })
  await started($)
  const ui = await mountPane($)
  expect(await screen(ui)).not.toMatch(/nuevo/)

  world.files = plan([done('One'), current('Two')])
  await world.clock.advance(TICK_MS)

  expect(await screen(ui)).toMatch(/• nuevo/)
  await ui.unmount()
})

test('a plan that changes but keeps its current step does not mark the PM nuevo', async ($, on) => {
  const world = stage(on, { stdout: WORKING(), files: plan([current('One'), next('Two')]) })
  await started($)
  const ui = await mountPane($)

  world.files = plan([current('One'), next('Two'), next('Three')])
  await world.clock.advance(TICK_MS)

  expect(await screen(ui)).not.toMatch(/nuevo/)
  expect(await firstCard(ui)).toEqual(['  ▸ One', '  · Two', '  · Three'])
  await ui.unmount()
})

test('several current steps all show gold, the tag on the first', async ($, on) => {
  stage(on, { stdout: WAITING, files: plan([current('Ask'), current('Draft'), next('Write')]) })
  await started($)

  const ui = await mountPane($)

  expect((await firstCard(ui)).slice(0, 3)).toEqual([`  ▸ Ask${WAIT_TAG}`, '  ▸ Draft', '  · Write'])
  for (const subject of ['  ▸ Ask', '  ▸ Draft']) {
    const found = await paintedRow(ui, subject)
    expect(found?.props.color).toBe('claude')
    expect(found?.props.bold).toBe(true)
  }
  expect(await ui.findAll({ type: 'Text', text: exactly(WAIT_TAG) })).toHaveLength(1)
  await ui.unmount()
})

test('the plan is drawn between the headline and the closing rule of each card', async ($, on) => {
  stage(on, {
    stdout: status(pm({ name: 'coven', state: 'working' }), pm({ name: 'cauldron', state: 'idle', age: '9m', id: '2222bbbb' })),
    files: plan([current('Build')]),
  })
  await started($)

  const ui = await mountPane($, 'dock', 61)

  expect(await rows(ui)).toEqual([
    '✦ 1 trabajando ⋆ ○ 1 en pausa',
    '',
    starRule(61),
    edges(`✦ ${'coven'.padEnd(10)}trabajando`, '3m', 61),
    '  ▸ Build',
    starRule(61),
    edges(`○ ${'cauldron'.padEnd(10)}en pausa`, '9m', 61),
    '  sin plan todavía',
    '  sin mensajes todavía',
    starRule(61),
    '',
    edges('', '↻ 12:00:05', 61),
  ])
  await ui.unmount()
})

test('a timer tick reads again and the pane shows the new state', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  // No cache, so no window shares a read: the cadence is the pane's own, and the command count shows it.
  world.broken.add(CACHE_FILE)
  await started($)
  const ui = await mountPane($)
  expect(await screen(ui)).toMatch(/1 trabajando/)

  world.reply.stdout = status(pm({ name: 'coven', state: 'blocked' }))
  await world.clock.advance(TICK_MS)

  const shown = await screen(ui)
  expect(shown).toMatch(/◆ 1 espera/)
  expect(shown).not.toMatch(/1 trabajando/)
  expect(shown).toMatch(/↻ 12:00:10/)
  await ui.unmount()
})

test('with the pane never opened, the status is read every 10 s, not every tick', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  await start($)

  for (let tick = 0; tick < 4; tick += 1) await world.clock.advance(TICK_MS)

  // One at start, then at 10 s and at 20 s.
  expect(world.calls).toEqual([
    ['coven', 'pm', 'status'],
    ['coven', 'pm', 'status'],
    ['coven', 'pm', 'status'],
  ])
})

test('with the pane open, every tick looks and a read is shared for 7.5 s: the command runs every other tick', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  await started($)
  world.calls.length = 0

  for (let tick = 0; tick < 4; tick += 1) {
    const looks = world.looked.length
    await world.clock.advance(TICK_MS)
    // One list of the jobs folder per look: a closed pane would look on every other tick only.
    expect(world.looked.length - looks).toBe(1)
  }

  expect(world.calls).toHaveLength(2)
})

test('after the pane is closed, the status is read every 10 s, not every tick', { plugins: [DOOR] }, async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  // No cache, so no window shares a read: the cadence is the pane's own, and the command count shows it.
  world.broken.add(CACHE_FILE)
  await started($)
  await world.clock.advance(TICK_MS)
  const readsWhileOpen = world.calls.length
  expect(readsWhileOpen).toBeGreaterThan(1)

  await closePane($)
  await world.clock.advance(TICK_MS * 6)

  expect(world.calls.length - readsWhileOpen).toBe(3)
})

test('a pane opened by something other than /aquelarre is refreshed too', { plugins: [DOOR] }, async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  // No cache, so no window shares a read: the cadence is the pane's own, and the command count shows it.
  world.broken.add(CACHE_FILE)
  await start($)
  await world.clock.settle()
  world.calls.splice(0)
  await openPaneElsewhere($)

  await world.clock.advance(TICK_MS)

  expect(world.calls).toEqual([['coven', 'pm', 'status']])
})

test('a pane that stays open across a reload keeps refreshing', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  // No cache, so no window shares a read: the cadence is the pane's own, and the command count shows it.
  world.broken.add(CACHE_FILE)
  await started($)
  const ui = await mountPane($)

  await start($)
  world.reply.stdout = status(pm({ name: 'coven', state: 'blocked' }))
  await world.clock.advance(TICK_MS)

  expect(await screen(ui)).toMatch(/◆ 1 espera/)
  await ui.unmount()
})

test('a reload in the middle of a read does not leave the next ticks blocked', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  await start($)
  const release = world.hold()
  const opening = openPane($)
  await world.clock.settle()
  expect(world.calls).toHaveLength(1)

  await start($)
  await world.clock.advance(TICK_MS)

  expect(world.calls).toHaveLength(2)
  release()
  await opening
})

test('a pane that is not shown, or not placed, counts as closed: read every 10 s', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  // No cache, so no window shares a read: the cadence is the pane's own, and the command count shows it.
  world.broken.add(CACHE_FILE)
  await started($)
  world.calls.length = 0

  world.panes.set('aquelarre', { isShown: false, isPlaced: true })
  await world.clock.advance(TICK_MS)
  expect(world.calls).toEqual([])
  world.panes.set('aquelarre', { isShown: true, isPlaced: false })
  await world.clock.advance(TICK_MS)
  expect(world.calls).toEqual([['coven', 'pm', 'status']])
  // Still not placed, on a tick a closed pane has no read due: an open pane would read here.
  await world.clock.advance(TICK_MS)
  expect(world.calls).toEqual([['coven', 'pm', 'status']])

  world.panes.set('aquelarre', SEATED)
  await world.clock.advance(TICK_MS)
  expect(world.calls).toHaveLength(2)
})

test('/aquelarre opens the pane before it reads: the pane is there while the read is still running', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  // The start read first settles and is forgotten: while it is in flight it holds the read flag, and
  // /aquelarre's own read would give way to it, so the one call counted below would be the start read's.
  await start($)
  await world.clock.settle()
  world.calls.splice(0)
  const release = world.hold()
  const opening = openPane($)
  await world.clock.settle()

  expect(world.calls).toHaveLength(1)
  expect(world.panes.has('aquelarre')).toBe(true)
  release()
  await opening
})

test('a tick that fires while a read is still pending does not start a second one', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  await started($)
  world.calls.length = 0

  const release = world.hold()
  await world.clock.advance(TICK_MS * 3)
  expect(world.calls).toHaveLength(1)

  release()
  await world.clock.settle()
  await world.clock.advance(READ_MS)
  expect(world.calls).toHaveLength(2)
})

test('a PM whose state changed between reads is marked nuevo, and the mark fades', async ($, on) => {
  const world = stage(
    on,
    { stdout: status(pm({ name: 'coven', state: 'working' }), pm({ name: 'steady', state: 'working' })) },
  )
  await started($)
  const ui = await mountPane($)
  expect(await screen(ui)).not.toMatch(/nuevo/)

  world.reply.stdout = status(pm({ name: 'coven', state: 'blocked' }), pm({ name: 'steady', state: 'working' }))
  await world.clock.advance(READ_MS)
  const marks = (await ui.findAll({ type: 'Text', text: /nuevo/ })).filter(mark => mark.props.color === 'claude')
  expect(marks).toHaveLength(1)
  expect(marks[0]?.text).toMatch(/• nuevo/)
  expect(await screen(ui)).toMatch(/coven[\s\S]*• nuevo[\s\S]*steady/)

  await world.clock.advance(2 * MINUTE_MS + TICK_MS)
  expect(await screen(ui)).not.toMatch(/nuevo/)
  await ui.unmount()
})

test('a new last message out marks the PM nuevo', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working', lines: [lastOut('00:14 DONE coven/3: one')] })) })
  await started($)
  const ui = await mountPane($)

  world.reply.stdout = status(pm({ name: 'coven', state: 'working', lines: [lastOut('00:20 DONE coven/4: two')] }))
  await world.clock.advance(READ_MS)

  expect(await screen(ui)).toMatch(/• nuevo/)
  await ui.unmount()
})

test('a PM that is new since the previous read is marked nuevo, and the one already there is not', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  await started($)
  const ui = await mountPane($)

  world.reply.stdout = status(pm({ name: 'coven', state: 'working' }), pm({ name: 'newcomer', state: 'working', age: '1m' }))
  await world.clock.advance(READ_MS)

  const marks = (await ui.findAll({ type: 'Text', text: /nuevo/ })).filter(mark => mark.props.color === 'claude')
  expect(marks).toHaveLength(1)
  expect(await screen(ui)).toMatch(/newcomer[\s\S]*• nuevo[\s\S]*coven/)
  await ui.unmount()
})

test('a PM that shows up after an empty read is not marked nuevo', async ($, on) => {
  const world = stage(on, { stdout: 'no project-manager sessions\n' })
  await started($)
  const ui = await mountPane($)

  world.reply.stdout = status(pm({ name: 'coven', state: 'working' }))
  await world.clock.advance(READ_MS)

  const shown = await screen(ui)
  expect(shown).toMatch(/coven/)
  expect(shown).not.toMatch(/nuevo/)
  await ui.unmount()
})

test('a role session never runs a command and says the pane is for the GM', async ($, on) => {
  const world = stage(on, { stdout: MIXED, role: 'pm' })
  await started($)
  const ui = await mountPane($)

  await world.clock.advance(TICK_MS * 6)

  expectNothingTouched(world)
  expect(await screen(ui)).toMatch(/Este panel solo lee PMs en la sesión del GM\./)
  await ui.unmount()
})

test('a failed read shows the error and keeps the cards of the last good read', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  await started($)
  const ui = await mountPane($)

  world.reply = { exitCode: 1, stdout: '', stderr: 'only the general manager may run this\nsecond line' }
  await world.clock.advance(READ_MS)

  const shown = await screen(ui)
  const headline = await ui.find({ type: 'Text', text: /No pude leer los PMs/ })
  expect(headline?.props.color).toBe('error')
  expect(shown).toMatch(/only the general manager may run this/)
  expect(shown).not.toMatch(/second line/)
  expect(shown).toMatch(/reintento en 5 s/)
  expect(shown).toMatch(/coven/)
  expect(shown).toMatch(/↻ 12:00:05/)
  await ui.unmount()
})

test('a failed first read shows the error and no cards', async ($, on) => {
  const world = stage(on)
  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await started($)

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(shown).toMatch(/No pude leer los PMs/)
  expect(shown).toMatch(/boom/)
  expect(shown).not.toMatch(/Leyendo/)
  await ui.unmount()
})

test('the next good read clears the error', async ($, on) => {
  const world = stage(on)
  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await started($)
  const ui = await mountPane($)

  world.reply = { exitCode: 0, stdout: status(pm({ name: 'coven', state: 'working' })), stderr: '' }
  await world.clock.advance(TICK_MS)

  const shown = await screen(ui)
  expect(shown).not.toMatch(/No pude leer/)
  expect(shown).toMatch(/1 trabajando/)
  await ui.unmount()
})

for (const output of ['no project-manager sessions\n', '']) {
  test(`no PMs this week says so and tells how to start one (output ${JSON.stringify(output)})`, async ($, on) => {
    stage(on, { stdout: output })
    await started($)

    const ui = await mountPane($)
    const shown = await screen(ui)

    expect(shown).toMatch(/No hay PMs esta semana\./)
    expect(shown).toMatch(/Aparecen aquí al lanzar uno desde el GM\./)
    await ui.unmount()
  })
}

test('before the first read the pane says it is reading', async ($, on) => {
  stage(on)
  const ui = await mountPane($)

  expect(await screen(ui)).toMatch(/Leyendo los PMs…/)
  await ui.unmount()
})

test('the widget shows only the summary and the time', async ($, on) => {
  stage(on, { stdout: MIXED })
  await started($)

  const ui = await mountPane($, 'widget')
  const shown = await screen(ui)

  expect(shown).toMatch(/1 trabajando/)
  expect(shown).toMatch(/12:00:05/)
  expect(shown).not.toMatch(/coven/)
  expect(shown).not.toMatch(/terminadas/)
  expect(shown).not.toMatch(/cada 5 s/)
  await ui.unmount()
})

test('the widget shows the read error instead of reading forever', async ($, on) => {
  const world = stage(on)
  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await started($)

  const ui = await mountPane($, 'widget')
  const headline = await ui.find({ type: 'Text', text: /No pude leer los PMs/ })

  expect(headline?.props.color).toBe('error')
  expect(await screen(ui)).not.toMatch(/Leyendo/)
  await ui.unmount()
})

test('a pane the engine already holds when the mod starts is refreshed', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  world.panes.set('aquelarre', SEATED)
  // No cache, so no window shares a read: the cadence is the pane's own, and the command count shows it.
  world.broken.add(CACHE_FILE)
  await start($)
  await world.clock.settle()
  world.calls.splice(0)

  await world.clock.advance(TICK_MS)

  expect(world.calls).toEqual([['coven', 'pm', 'status']])
})

test('a tick that fails outright shows as a read error instead of vanishing', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  await started($)
  const ui = await mountPane($)

  world.panesError = 'engine gone'
  await world.clock.advance(TICK_MS)

  const shown = await screen(ui)
  expect(shown).toMatch(/No pude leer los PMs/)
  expect(shown).toMatch(/coven/)
  await ui.unmount()
})

test('coven pm status is the only command run when it works', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  await started($)

  expect(world.calls).toEqual([['coven', 'pm', 'status']])
})

test('a coven that hangs until its timeout shows the error and runs nothing else', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'covenant', state: 'working' })) })
  world.hangs.add('coven')
  await start($)
  const opening = openPane($)
  await world.clock.settle()
  await world.clock.advance(STATUS_TIMEOUT_MS)
  await opening

  const ui = await mountPane($)
  const shown = await screen(ui)

  expect(world.calls.length).toBeGreaterThan(0)
  for (const call of world.calls) expect(call[0]).toBe('coven')
  expect(shown).toMatch(/No pude leer los PMs/)
  expect(shown).not.toMatch(/covenant/)
  await ui.unmount()
})

test('when coven is missing the error is shown', async ($, on) => {
  const world = stage(on)
  world.missing.add('coven')
  await started($)

  const ui = await mountPane($)

  expect(await screen(ui)).toMatch(/No pude leer los PMs/)
  await ui.unmount()
})

test('every colour the pane uses is a theme key', async ($, on) => {
  const lost = pm({ name: 'lost', state: 'idle', lines: ['last out: 10:00 DONE x/1: thing   [NOT DELIVERED: never sent]'] })
  const steps = [done('Spec'), current('Build'), next('a'), next('b'), next('c'), next('d'), next('e')]
  const world = stage(on, { stdout: MIXED + lost, files: plan(steps) })
  await started($)
  const ui = await mountPane($)
  const band = await mountBand($)
  const narrowBand = await mountBand($, 40)
  world.reply.stdout = MIXED.replace('3.14 CI fix', '3.15 CI fix') + lost
  await world.clock.advance(TICK_MS)
  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await world.clock.advance(TICK_MS)

  const painted = [...(await ui.findAll({ type: 'Text' })), ...(await band.findAll({ type: 'Text' })), ...(await narrowBand.findAll({ type: 'Text' }))]
  const colors = painted.map((found: FoundElement) => found.props.color).filter(color => color !== undefined)

  // The screen holds every group, the new mark, the unsent flag, the resting line, the error and the plan rows.
  for (const used of ['permission', 'error', 'claude', 'inactive', 'subtle', 'suggestion']) expect(colors).toContain(used)
  for (const color of colors) expect(THEME_KEYS).toContain(color)
  await ui.unmount()
  await band.unmount()
  await narrowBand.unmount()
})

// The band above the prompt.

type BandMount = Mounted<'desktop', 'AbovePrompt'>

const BAND = {
  plugin: 'aquelarre',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isCollapsed: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 80,
    scroll: { offset: 0, bodyRows: 10 },
    view: {},
  },
} as const

async function mountBand($: Engine, bodyColumns = 80, hasSurvey = false): Promise<BandMount> {
  return $.ui.mount({ ...BAND, surface: 'desktop', props: { ...BAND.props, bodyColumns, hasSurvey } })
}

/** The band's one row. */
async function bandRow(ui: BandMount): Promise<string> {
  const drawn = rowsOf(await ui.drawn())
  expect(drawn).toHaveLength(1)
  return drawn[0] ?? ''
}

const BANDED = status(
  pm({ name: 'tower', state: 'blocked', age: '10m' }),
  pm({ name: 'coven', state: 'blocked', age: '5m' }),
  pm({ name: 'magic', state: 'stale', age: '2h' }),
  pm({ name: 'aquelarre', state: 'working', age: '1m' }),
  pm({ name: 'cauldron', state: 'working', age: '2m' }),
  pm({ name: 'lilthop', state: 'idle', age: '9m' }),
  pm({ name: 'old', state: 'done', age: '2d' }),
)

const WIDE_BAND = '☾ ◆ espera: coven, tower ⋆ ✕ magic ⋆ ✦ 2 trabajando ⋆ ○ 1 en pausa'
const COMPACT_BAND = '☾ ◆ coven, tower  ✕ 1  ✦ 2  ○ 1'

test('the band names who waits and counts the rest, with the time of the last read at the right', async ($, on) => {
  stage(on, { stdout: BANDED })
  await started($)
  const ui = await mountBand($)

  expect(await bandRow(ui)).toBe(edges(WIDE_BAND, '↻ 12:00:05', 80))
  await ui.unmount()
})

test('each part of the wide band has its own colour', async ($, on) => {
  stage(on, { stdout: BANDED })
  await started($)
  const ui = await mountBand($)

  const look = async (text: string) => {
    const found = await ui.find({ type: 'Text', text: exactly(text) })
    expect(found, `no Text ${text}`).toBeDefined()
    return { color: found?.props.color, bold: found?.props.bold }
  }
  expect(await look('☾ ')).toMatchObject({ color: 'suggestion' })
  expect(await look('◆ espera: coven, tower')).toEqual({ color: 'permission', bold: true })
  expect(await look('✕ magic')).toMatchObject({ color: 'error' })
  expect(await look('✦ 2 trabajando')).toMatchObject({ color: 'claude' })
  expect(await look('○ 1 en pausa')).toMatchObject({ color: 'inactive' })
  expect(await look('↻ 12:00:05')).toMatchObject({ color: 'subtle' })
  const joints = await ui.findAll({ type: 'Text', text: exactly(' ⋆ ') })
  expect(joints).toHaveLength(3)
  for (const joint of joints) expect(joint.props.color).toBe('subtle')
  await ui.unmount()
})

test('a narrow band keeps the names of who waits, counts the rest and shows HH:MM', async ($, on) => {
  stage(on, { stdout: BANDED })
  await started($)
  const ui = await mountBand($, 46)

  expect(await bandRow(ui)).toBe(edges(COMPACT_BAND, '12:00', 46))
  await ui.unmount()
})

test('the compact band drops the time when it does not fit with it, then cuts the row', async ($, on) => {
  stage(on, { stdout: BANDED })
  await started($)
  const ui = await mountBand($, COMPACT_BAND.length + 2)

  expect(await bandRow(ui)).toBe(COMPACT_BAND)
  await ui.redraw({ ...BAND.props, bodyColumns: 12 })
  expect(await bandRow(ui)).toBe('☾ ◆ coven, …')
  await ui.unmount()
})

test('the band never draws past bodyColumns, from 20 to 120 columns', async ($, on) => {
  const world = stage(on, { stdout: BANDED })
  await started($)
  const ui = await mountBand($)

  for (let columns = 20; columns <= 120; columns += 1) {
    await ui.redraw({ ...BAND.props, bodyColumns: columns })
    expect((await bandRow(ui)).length, `${columns} columns`).toBeLessThanOrEqual(columns)
  }
  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await world.clock.advance(TICK_MS)
  for (let columns = 20; columns <= 120; columns += 1) {
    await ui.redraw({ ...BAND.props, bodyColumns: columns })
    expect((await bandRow(ui)).length, `${columns} columns, error`).toBeLessThanOrEqual(columns)
  }
  await ui.unmount()
})

test('before the first read the band says it is reading the PMs', async ($, on) => {
  stage(on, { stdout: BANDED })
  const ui = await mountBand($)

  expect(await bandRow(ui)).toBe('☾ leyendo los PMs…')
  const message = await ui.find({ type: 'Text', text: exactly('leyendo los PMs…') })
  expect(message?.props.color).toBe('subtle')
  await ui.unmount()
})

for (const [why, output] of [
  ['every PM is done', status(pm({ name: 'old', state: 'done', age: '2d' }), pm({ name: 'napper', state: 'stopped' }))],
  ['there is no PM at all', ''],
] as const) {
  test(`the band says nobody is working when ${why}`, async ($, on) => {
    stage(on, { stdout: output })
    await started($)
    const ui = await mountBand($)

    expect(await bandRow(ui)).toBe(edges('☾ nadie trabajando ahora', '↻ 12:00:05', 80))
    const message = await ui.find({ type: 'Text', text: exactly('nadie trabajando ahora') })
    expect(message?.props.color).toBe('subtle')
    await ui.unmount()
  })
}

test('a read error with no good read before it fills the band with the error', async ($, on) => {
  const world = stage(on)
  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await started($)
  const ui = await mountBand($)

  expect(await bandRow(ui)).toBe('☾ ✕ no pude leer los PMs')
  const message = await ui.find({ type: 'Text', text: exactly('✕ no pude leer los PMs') })
  expect(message?.props.color).toBe('error')
  await ui.unmount()
})

test('a read error after a good read keeps the counts and shows when the last good read was', async ($, on) => {
  const world = stage(on, { stdout: BANDED })
  await started($)
  const ui = await mountBand($)
  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await world.clock.advance(READ_MS)

  expect(await bandRow(ui)).toBe(edges(WIDE_BAND, '✕ 12:00:05', 80))
  expect((await ui.find({ type: 'Text', text: exactly('✕ 12:00:05') }))?.props.color).toBe('error')
  await ui.redraw({ ...BAND.props, bodyColumns: 46 })
  expect(await bandRow(ui)).toBe(edges(COMPACT_BAND, '✕ 12:00', 46))
  await ui.unmount()
})

test('the band follows each read', async ($, on) => {
  const world = stage(on, { stdout: BANDED })
  await started($)
  const ui = await mountBand($)

  world.reply.stdout = status(pm({ name: 'coven', state: 'working' }))
  await world.clock.advance(READ_MS)

  expect(await bandRow(ui)).toBe(edges('☾ ✦ 1 trabajando', '↻ 12:00:15', 80))
  await ui.unmount()
})

test('the band yields to a survey', async ($, on) => {
  stage(on, { stdout: BANDED })
  await started($)
  const shown = await mountBand($)
  expect(await shown.find({ type: 'Text', text: /☾/ })).toBeDefined()
  await shown.unmount()
  const ui = await mountBand($, 80, true)

  expect(await ui.find({ type: 'Text', text: /☾/ })).toBeUndefined()
  expect(await ui.keepsBeneath()).toBe(true)
  await ui.unmount()
})

test('the band passes in a role session marked by COVEN_SESSION_ROLE', async ($, on) => {
  stage(on, { stdout: BANDED, role: 'pm' })
  await started($)
  const ui = await mountBand($)

  expect(await ui.find({ type: 'Text', text: /☾/ })).toBeUndefined()
  expect(await ui.keepsBeneath()).toBe(true)
  await ui.unmount()
})

test('a COVEN_SESSION_ROLE session never runs a status command', async ($, on) => {
  const world = stage(on, { stdout: BANDED, role: 'pm' })
  await started($)

  await world.clock.advance(TICK_MS * 8)

  expectNothingTouched(world)
})

// Toasts when a PM starts waiting, finishes or dies.

/** One read after `before` was read at start: the PMs as `after` prints them, and the toasts it raised. */
async function toastsAfter($: Engine, on: On, before: string, after: string, options: StageOptions = {}) {
  const world = stage(on, { stdout: before, ...options })
  await started($)
  world.reply.stdout = after
  await world.clock.advance(READ_MS)
  return world.toasts
}

const WAITING_ON_LOGO = (age = '1m') =>
  pm({
    name: 'coven',
    state: 'blocked',
    age,
    lines: [lastOut('00:14 QUESTION coven/6: which logo?'), waitsOn('QUESTION coven/6 since 00:14')],
  })

test('the first read raises no toast, though a PM already waits', async ($, on) => {
  const world = stage(on, { stdout: status(WAITING_ON_LOGO()) })
  await started($)

  expect(world.toasts).toEqual([])
})

test('a PM that starts waiting raises a toast with the question it waits on', async ($, on) => {
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'working' })), status(WAITING_ON_LOGO()))

  expect(toasts).toEqual([{ text: '◆ coven espera al GM  QUESTION #6 which logo?', timeoutMs: 8000 }])
})

test('a toast for a PM that waits on an item it did not send says it awaits a reply', async ($, on) => {
  const asking = pm({ name: 'coven', state: 'blocked', lines: [lastOut('00:14 DONE coven/5: old'), waitsOn('QUESTION coven/6 since 00:14')] })
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'working' })), status(asking))

  expect(toasts).toEqual([{ text: '◆ coven espera al GM  QUESTION #6 esperando respuesta', timeoutMs: 8000 }])
})

test('a PM blocked with nothing it waits on raises a toast without a message', async ($, on) => {
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'working' })), status(pm({ name: 'coven', state: 'blocked' })))

  expect(toasts).toEqual([{ text: '◆ coven espera al GM', timeoutMs: 8000 }])
})

test('a PM new since the last read and already waiting raises a toast', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'tower', state: 'working' })),
    status(pm({ name: 'tower', state: 'working' }), WAITING_ON_LOGO()),
  )

  expect(toasts).toEqual([{ text: '◆ coven espera al GM  QUESTION #6 which logo?', timeoutMs: 8000 }])
})

test('a PM that keeps waiting raises no second toast', async ($, on) => {
  const toasts = await toastsAfter($, on, status(WAITING_ON_LOGO()), status(WAITING_ON_LOGO('2m')))

  expect(toasts).toEqual([])
})

test('a PM new since the last read and not waiting raises no toast', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'tower', state: 'working' })),
    status(pm({ name: 'tower', state: 'working' }), pm({ name: 'coven', state: 'stale' }), pm({ name: 'old', state: 'done' })),
  )

  expect(toasts).toEqual([])
})

test('a live PM whose last message out becomes a DONE raises a short toast', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working', lines: [lastOut('00:14 TASK coven/7: go')] })),
    status(pm({ name: 'coven', state: 'working', lines: [lastOut('00:20 DONE coven/7: shipped')] })),
  )

  expect(toasts).toEqual([{ text: '✓ coven terminó  DONE #7 shipped', timeoutMs: 4000 }])
})

test('a PM that becomes done raises a toast', async ($, on) => {
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'working' })), status(pm({ name: 'coven', state: 'done' })))

  expect(toasts).toEqual([{ text: '✓ coven terminó', timeoutMs: 4000 }])
})

test('a PM that becomes done after a DONE message carries the message', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working' })),
    status(pm({ name: 'coven', state: 'done', lines: [lastOut('00:20 DONE coven/7: shipped')] })),
  )

  expect(toasts).toEqual([{ text: '✓ coven terminó  DONE #7 shipped', timeoutMs: 4000 }])
})

test('a PM that dies raises a toast', async ($, on) => {
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'working' })), status(pm({ name: 'coven', state: 'stale' })))

  expect(toasts).toEqual([{ text: '✕ coven proceso muerto', timeoutMs: 8000 }])
})

for (const [state, word] of [
  ['failed', 'falló'],
  ['duplicate', 'duplicado'],
  ['zombie', 'sin datos'],
] as const) {
  test(`a PM in state ${state} raises a toast saying ${word}`, async ($, on) => {
    const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'working' })), status(pm({ name: 'coven', state })))

    expect(toasts).toEqual([{ text: `✕ coven ${word}`, timeoutMs: 8000 }])
  })
}

test('a PM that stays stale raises no second toast', async ($, on) => {
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'stale' })), status(pm({ name: 'coven', state: 'stale', age: '9m' })))

  expect(toasts).toEqual([])
})

test('a PM that goes from stale to failed raises a toast', async ($, on) => {
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'stale' })), status(pm({ name: 'coven', state: 'failed' })))

  expect(toasts).toEqual([{ text: '✕ coven falló', timeoutMs: 8000 }])
})

test('a PM that goes idle, or sends another message than a DONE, raises no toast', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working' }), pm({ name: 'tower', state: 'working' })),
    status(
      pm({ name: 'coven', state: 'idle' }),
      pm({ name: 'tower', state: 'working', lines: [lastOut('00:20 DRAFT tower/1: a plan')] }),
    ),
  )

  expect(toasts).toEqual([])
})

test('two PMs changing in one read raise one toast, as long as the longer one', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working' }), pm({ name: 'tower', state: 'working' })),
    status(pm({ name: 'coven', state: 'done' }), pm({ name: 'tower', state: 'stale' })),
  )

  expect(toasts).toEqual([{ text: '✓ coven terminó ⋆ ✕ tower proceso muerto', timeoutMs: 8000 }])
})

test('two PMs that finish raise one short toast', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working' }), pm({ name: 'tower', state: 'working' })),
    status(pm({ name: 'coven', state: 'done' }), pm({ name: 'tower', state: 'done' })),
  )

  expect(toasts).toEqual([{ text: '✓ coven terminó ⋆ ✓ tower terminó', timeoutMs: 4000 }])
})

test('a PM that dies and also finishes gives one entry, the dying one', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working' })),
    status(pm({ name: 'coven', state: 'stale', lines: [lastOut('00:20 DONE coven/7: shipped')] })),
  )

  expect(toasts).toEqual([{ text: '✕ coven proceso muerto', timeoutMs: 8000 }])
})

test('the message part of an entry is cut to 60 characters', async ($, on) => {
  const long = 'x'.repeat(80)
  const asking = pm({
    name: 'coven',
    state: 'blocked',
    lines: [lastOut(`00:14 QUESTION coven/6: ${long}`), waitsOn('QUESTION coven/6 since 00:14')],
  })
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'working' })), status(asking))

  expect(toasts).toEqual([{ text: `◆ coven espera al GM  ${`QUESTION #6 ${long}`.slice(0, 59)}…`, timeoutMs: 8000 }])
})

test('a failed read raises no toast, and the next good read compares with the last good one', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })) })
  await started($)

  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await world.clock.advance(TICK_MS)
  expect(world.toasts).toEqual([])

  world.reply = { exitCode: 0, stdout: status(pm({ name: 'coven', state: 'stale' })), stderr: '' }
  await world.clock.advance(TICK_MS)

  expect(world.toasts).toEqual([{ text: '✕ coven proceso muerto', timeoutMs: 8000 }])
})

test('a read after a failed first read raises no toast', async ($, on) => {
  const world = stage(on)
  world.reply = { exitCode: 1, stdout: '', stderr: 'boom' }
  await started($)

  world.reply = { exitCode: 0, stdout: status(WAITING_ON_LOGO()), stderr: '' }
  await world.clock.advance(TICK_MS)

  expect(world.toasts).toEqual([])
})

test('a role session marked by COVEN_SESSION_ROLE raises no toast', async ($, on) => {
  const toasts = await toastsAfter($, on, status(pm({ name: 'coven', state: 'working' })), status(WAITING_ON_LOGO()), { role: 'pm' })

  expect(toasts).toEqual([])
})

// Fixes from the final review.

test('with the pane closed, a read that started a little late does not push the next one to 15 s', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  // The read at session start begins 30 ms after its tick, as host round trips make it.
  world.panesLag = 30
  await start($)
  await world.clock.advance(30)
  expect(world.calls).toHaveLength(1)

  await world.clock.advance(TICK_MS - 30)
  expect(world.calls).toHaveLength(1)
  await world.clock.advance(TICK_MS)

  expect(world.calls).toHaveLength(2)
})

test('a session start reads the PMs once, before any tick', async ($, on) => {
  const world = stage(on, { stdout: MIXED })

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual([['coven', 'pm', 'status']])
})

test('a session start does not wait for its read', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  const release = world.hold()

  await start($)
  await world.clock.settle()
  expect(world.calls).toHaveLength(1)

  release()
  await world.clock.settle()
})

test('a role session reads nothing at start', async ($, on) => {
  const world = stage(on, { stdout: MIXED, role: 'pm' })

  await start($)
  await world.clock.settle()

  expectNothingTouched(world)
})

test('a DONE that was not delivered says so at the end of its toast entry', async ($, on) => {
  const undelivered = (text: string) => `last out: ${text}   [NOT DELIVERED: no route to the GM]`
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working', lines: [lastOut('00:14 TASK coven/7: go')] })),
    status(pm({ name: 'coven', state: 'working', lines: [undelivered('00:20 DONE coven/7: shipped')] })),
  )

  expect(toasts).toEqual([{ text: '✓ coven terminó  DONE #7 shipped · no entregado', timeoutMs: 4000 }])
})

test('the cut to 60 characters comes before the not-delivered suffix', async ($, on) => {
  const long = 'x'.repeat(80)
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working' })),
    status(
      pm({
        name: 'coven',
        state: 'working',
        lines: [`last out: 00:20 DONE coven/7: ${long}   [NOT DELIVERED: no route]`],
      }),
    ),
  )

  expect(toasts).toEqual([
    { text: `✓ coven terminó  ${`DONE #7 ${long}`.slice(0, 59)}… · no entregado`, timeoutMs: 4000 },
  ])
})

test('a PM that is done with an undelivered DONE carries the suffix too', async ($, on) => {
  const toasts = await toastsAfter(
    $,
    on,
    status(pm({ name: 'coven', state: 'working' })),
    status(
      pm({
        name: 'coven',
        state: 'done',
        lines: ['last out: 00:20 DONE coven/7: shipped   [NOT DELIVERED: no route]'],
      }),
    ),
  )

  expect(toasts).toEqual([{ text: '✓ coven terminó  DONE #7 shipped · no entregado', timeoutMs: 4000 }])
})

test('a session with COVEN_SESSION_ROLE set to an empty value is a role session', async ($, on) => {
  const world = stage(on, { stdout: BANDED, role: '' })
  await started($)

  await world.clock.advance(TICK_MS * 8)

  expectNothingTouched(world)
})

test('a PM that waits and then goes stale raises only the dying entry', async ($, on) => {
  const toasts = await toastsAfter($, on, status(WAITING_ON_LOGO()), status(WAITING_ON_LOGO().replace('state=blocked', 'state=stale')))

  expect(toasts).toEqual([{ text: '✕ coven proceso muerto', timeoutMs: 8000 }])
})

const JOBS = `${HOME}/.claude/jobs`
const state = (id: string) => `${JOBS}/${id}/state.json`
const jobs = (...ids: string[]) => Object.fromEntries(ids.map(id => [state(id), '{}']))
const COVEN_STATUS = [['coven', 'pm', 'status']]

/** The session started and its read done and forgotten, with the pane closed: what the ticks below look at. */
async function quiet($: Engine, world: Stage) {
  await start($)
  await world.clock.settle()
  world.calls.splice(0)
  world.looked.splice(0)
}

test('a due tick runs no status command while no job changed, until the read is 60 s old', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a', 'b') })
  await quiet($, world)

  for (let seconds = 10; seconds <= 50; seconds += 10) {
    await world.clock.advance(10_000)
    expect(world.calls, `at ${seconds} s`).toEqual([])
  }
  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a job whose state.json changed gives a read on the next due tick', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a', 'b') })
  await quiet($, world)

  world.mtimes[state('b')] = 5
  await world.clock.advance(TICK_MS)
  expect(world.calls).toEqual([])
  await world.clock.advance(TICK_MS)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('the change that was read is not read again on the next due tick', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a', 'b') })
  await quiet($, world)

  world.mtimes[state('a')] = 5
  await world.clock.advance(10_000)
  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a new job folder gives a read', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)

  Object.assign(world.files, jobs('b'))
  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a job folder with no state.json changes nothing', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)

  world.files[`${JOBS}/c/output.log`] = 'text'
  await world.clock.advance(10_000)

  expect(world.calls).toEqual([])
  expect(world.looked).toContain(`${JOBS}/c/state.json`)
})

test('with no jobs folder every due tick reads, as before', async ($, on) => {
  const world = stage(on, { stdout: MIXED })
  await quiet($, world)

  await world.clock.advance(10_000)
  await world.clock.advance(10_000)
  await world.clock.advance(10_000)

  expect(world.calls).toHaveLength(3)
})

test('with neither CLAUDE_CONFIG_DIR nor HOME there is nothing to check and every due tick reads', async ($, on) => {
  const world = stage(on, { stdout: MIXED, home: '' })
  await quiet($, world)

  await world.clock.advance(10_000)
  await world.clock.advance(10_000)

  expect(world.calls).toHaveLength(2)
  expect(world.looked).toEqual([])
})

test('CLAUDE_CONFIG_DIR is the folder whose jobs are checked', async ($, on) => {
  const world = stage(on, { stdout: MIXED, configDir: '/cfg', files: { '/cfg/jobs/a/state.json': '{}', ...jobs('b') } })
  await quiet($, world)

  await world.clock.advance(10_000)

  expect(world.calls).toEqual([])
  expect(world.looked).toContain('/cfg/jobs/a/state.json')
  expect(world.looked.some(path => path.startsWith(HOME))).toBe(false)
})

test('a jobs listing that fails counts as changed and is never the pane error', async ($, on) => {
  const world = stage(on, { stdout: status(pm({ name: 'coven', state: 'working' })), files: jobs('a') })
  world.broken.add(JOBS)
  await quiet($, world)

  await world.clock.advance(10_000)
  await world.clock.advance(10_000)

  expect(world.calls).toHaveLength(2)
  const ui = await mountPane($)
  expect(await screen(ui)).not.toMatch(/No pude leer/)
  await ui.unmount()
})

test('the fingerprint is taken before the command runs: a change during it gives another read', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  world.mtimes[state('a')] = 5

  const release = world.hold()
  await world.clock.advance(10_000)
  expect(world.calls).toHaveLength(1)
  world.mtimes[state('a')] = 6
  release()
  await world.clock.settle()
  world.calls.splice(0)

  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
})

/** The session started with a status command that fails, and that failed read done and forgotten. */
async function failedAtStart($: Engine, world: Stage) {
  world.reply.exitCode = 1
  world.reply.stderr = 'boom'
  await start($)
  await world.clock.settle()
  world.calls.splice(0)
}

test('a read that failed is retried on the next due tick, not after the 60 s safety age', async ($, on) => {
  const world = stage(on, { stdout: '', files: jobs('a') })
  await failedAtStart($, world)

  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a failed read is retried on the next tick of an open pane, with the jobs unchanged', async ($, on) => {
  const world = stage(on, { stdout: '', files: jobs('a') })
  world.reply.exitCode = 1
  world.reply.stderr = 'boom'
  await started($)
  world.calls.splice(0)

  await world.clock.advance(TICK_MS)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a failed read is retried on the next tick even when there is no cache to say it failed', async ($, on) => {
  const world = stage(on, { stdout: '', files: jobs('a') })
  world.broken.add(CACHE_FILE)
  await failedAtStart($, world)

  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('/aquelarre reads although nothing changed', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)

  await openPane($)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('an open pane runs no status command either while nothing changed', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await started($)
  world.calls.splice(0)

  await world.clock.advance(TICK_MS * 5)

  expect(world.calls).toEqual([])
})

// One status read shared by every open window, through a cache file and a claim beside it.
const CACHE_DIR = `${HOME}/.cache/aquelarre`
const CACHE_FILE = `${CACHE_DIR}/status.json`
const CLAIM_FILE = `${CACHE_DIR}/reading.json`
const NO_CLAIM = JSON.stringify({ by: '', at: 0 })
const CLAIM_VOID_MS = STATUS_TIMEOUT_MS + 5_000
const CACHED_WORKING = status(pm({ name: 'coven', state: 'working' }))

type Shared = {
  version: number
  began?: number
  at: number
  fingerprint: string | null
  stdout: string
  error: string
}

/** The fingerprint of jobs `ids` when every state.json has the staged mtime of 1. */
const fingerprintOf = (...ids: string[]) => ids.map(id => `${id}:1`).join(',')

/** What another window leaves in the cache: a read that finished now, of the same jobs, unless `changes` say otherwise. */
function otherWindowRead(world: Stage, changes: Partial<Shared> = {}) {
  const entry: Shared = {
    version: 1,
    at: world.clock.now(),
    fingerprint: fingerprintOf('a'),
    stdout: CACHED_WORKING,
    error: '',
    ...changes,
  }
  world.files[CACHE_FILE] = JSON.stringify(entry)
}

/** A claim another window made `ago` ms back. */
function otherWindowClaims(world: Stage, ago = 0, by = 'window-2') {
  world.files[CLAIM_FILE] = JSON.stringify({ by, at: world.clock.now() - ago })
}

const cacheFile = (world: Stage): Shared => JSON.parse(world.files[CACHE_FILE] ?? 'null')

test('a window that finds a fresh cache of the same jobs runs no command and draws that read', async ($, on) => {
  const world = stage(on, { stdout: 'this window must not read', files: jobs('a') })
  otherWindowRead(world)

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual([])
  const ui = await mountPane($)
  expect(await screen(ui)).toMatch(/coven/)
  expect(await screen(ui)).not.toMatch(/No pude leer/)
  await ui.unmount()
})

test('a read that fails before it is drawn is not counted as taken: the next due tick takes it again', async ($, on) => {
  let denials = 0
  on('state.set', { plugin: 'aquelarre', key: 'circle' }, (_, e, next) => {
    if (denials === 0) return next(e)
    denials -= 1
    return { deny: 'the store is gone' }
  })
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  await world.clock.advance(TICK_MS)
  otherWindowRead(world, { stdout: CACHED_WORKING })

  denials = 1
  await world.clock.advance(TICK_MS)
  expect(denials).toBe(0)
  expect(world.calls).toEqual([])
  const ui = await mountPane($)
  expect(await screen(ui)).toMatch(/No pude leer/)

  await world.clock.advance(10_000)

  expect(world.calls).toEqual([])
  expect(await screen(ui)).not.toMatch(/No pude leer/)
  expect(await screen(ui)).not.toMatch(/tower/)
  expect(await screen(ui)).toMatch(/coven/)
  await ui.unmount()
})

test('a toast fires once per window for a cached read, and taking the same cache again fires nothing', async ($, on) => {
  const world = stage(on, { stdout: CACHED_WORKING, files: jobs('a') })
  await quiet($, world)

  await world.clock.advance(1_000)
  otherWindowRead(world, { stdout: status(WAITING_ON_LOGO()) })
  await world.clock.advance(10_000)
  expect(world.calls).toEqual([])
  expect(world.toasts).toEqual([{ text: '◆ coven espera al GM  QUESTION #6 which logo?', timeoutMs: 8000 }])

  // The same cache is not taken again: taking it would list the tasks folder for the plans once more.
  world.touched.splice(0)
  await world.clock.advance(TICK_MS)
  await world.clock.advance(TICK_MS)
  expect(world.touched).toEqual([])

  await world.clock.advance(10_000)
  await world.clock.advance(10_000)

  expect(world.calls).toEqual([])
  expect(world.toasts).toHaveLength(1)
})

// A cache younger than the share age stops every window's read, whatever the jobs' fingerprint says.
const SHARE_AFTER_GOOD_MS = 7_500

test('a young cache of other jobs is taken without a command, however often the jobs change', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)

  await world.clock.advance(5_000)
  world.mtimes[state('a')] = 5
  otherWindowRead(world, { stdout: CACHED_WORKING })
  await world.clock.advance(5_000)

  expect(world.calls).toEqual([])
  const ui = await mountPane($)
  expect(await screen(ui)).toMatch(/coven/)
  expect(await screen(ui)).not.toMatch(/tower/)
  await ui.unmount()

  // The cache is 15 s old at the tick after: past the share age, and the jobs changed again.
  world.mtimes[state('a')] = 6
  await world.clock.advance(10_000)
  expect(world.calls).toEqual(COVEN_STATUS)
})

test('with the pane open, a window that read 5 s ago runs no command at the next tick even if a job changed, and reads at the one after', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await started($)
  world.calls.splice(0)

  world.mtimes[state('a')] = 5
  await world.clock.advance(TICK_MS)
  expect(world.calls).toEqual([])

  world.mtimes[state('a')] = 6
  await world.clock.advance(TICK_MS)
  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a young failed cache of other jobs stops a read for 2.5 s only, so the retry is at the next tick', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await started($)
  world.calls.splice(0)
  await world.clock.advance(TICK_MS - 1_000)
  world.mtimes[state('a')] = 5
  otherWindowRead(world, { stdout: '', error: 'boom' })
  await world.clock.advance(1_000)
  expect(world.calls).toEqual([])

  world.mtimes[state('a')] = 6
  await world.clock.advance(TICK_MS)

  // The failed read is 6 s old at this tick: past the 2.5 s a failed cache is shared, short of the 7.5 s of a good one.
  expect(world.calls).toEqual(COVEN_STATUS)
})

test('five closed-pane windows, four of them simulated, with jobs that change every second: 6 to 8 commands a minute, not 30', async ($, on) => {
  // One window is the real one, ticking at 0, 10, 20 s. The other four are simulated, as the engine runs one
  // window per test: each ticks every 10 s at an offset of 2, 4, 6 or 8 s and, as the share age says, reads only
  // when the cache is 7.5 s old or more, then writes the cache as a window does. A simulated read counts as one
  // command. The jobs change before every second, so the fingerprint never allows a read by itself.
  // It measures the real window's part and the share age rule, not four real windows (this run: 2 real + 6 simulated).
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await start($)
  await world.clock.settle()
  const OFFSETS = [2, 4, 6, 8]
  let simulated = 0

  for (let second = 1; second <= 60; second++) {
    world.mtimes[state('a')] = second
    await world.clock.advance(1_000)
    if (!OFFSETS.includes(second % 10)) continue
    if (world.clock.now() - cacheFile(world).at >= SHARE_AFTER_GOOD_MS) {
      simulated++
      otherWindowRead(world, { fingerprint: `a:${second}`, stdout: MIXED })
    }
  }

  const commands = world.calls.length + simulated
  // Each window reading on every due tick would be 5 windows x 6 ticks = 30 commands.
  expect(commands).toBeGreaterThanOrEqual(6)
  expect(commands).toBeLessThanOrEqual(8)
})

test('a cache of other jobs, past its share age, makes the window claim, read, write the cache and clear the claim', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  otherWindowRead(world, { fingerprint: fingerprintOf('z'), at: NOON - SHARE_AFTER_GOOD_MS })

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual(COVEN_STATUS)
  expect(world.writes.map(({ path }) => path)).toEqual([CLAIM_FILE, CACHE_FILE, CLAIM_FILE])
  expect(JSON.parse(world.writes[0]?.text ?? '')).toEqual({ by: 'window-1', at: NOON })
  expect(cacheFile(world)).toEqual({ version: 1, began: NOON, at: NOON, fingerprint: fingerprintOf('a'), stdout: MIXED, error: '' })
  expect(world.files[CLAIM_FILE]).toBe(NO_CLAIM)
})

test('a cache a minute old is read again', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  otherWindowRead(world, { at: NOON - MINUTE_MS })

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual(COVEN_STATUS)
  expect(cacheFile(world).at).toBe(NOON)
})

test('the cache holds the fingerprint taken before the command, not after', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  const release = world.hold()
  await start($)
  await world.clock.settle()
  world.mtimes[state('a')] = 9
  release()
  await world.clock.settle()

  expect(cacheFile(world).fingerprint).toBe(fingerprintOf('a'))
})

test('another window reading now: no command, and the cache is taken', async ($, on) => {
  const world = stage(on, { stdout: 'this window must not read', files: jobs('a') })
  otherWindowRead(world, { fingerprint: fingerprintOf('z') })
  otherWindowClaims(world, 10_000)

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual([])
  const ui = await mountPane($)
  expect(await screen(ui)).toMatch(/coven/)
  await ui.unmount()
})

test('another window reading now and no cache: nothing is taken, and the next due tick looks again', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  otherWindowClaims(world, 10_000)

  await start($)
  await world.clock.settle()
  expect(world.calls).toEqual([])
  expect(world.writes).toEqual([])

  otherWindowRead(world)
  await world.clock.advance(10_000)

  expect(world.calls).toEqual([])
  const ui = await mountPane($)
  expect(await screen(ui)).toMatch(/coven/)
  await ui.unmount()
})

test('a claim of 25 s or more is void, and the window reads', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  otherWindowClaims(world, CLAIM_VOID_MS)

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a claim a moment short of 25 s still holds', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  otherWindowClaims(world, CLAIM_VOID_MS - 1)

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual([])
})

test('when the claim read back is another window\'s, this window does not read', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  world.overwrites[CLAIM_FILE] = JSON.stringify({ by: 'window-2', at: NOON })

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual([])
  expect(world.writes.map(({ path }) => path)).toEqual([CLAIM_FILE])
})

test('a failed status read is written to the cache with its error and no stdout', async ($, on) => {
  const failing = stage(on, { stdout: '', files: jobs('a') })
  failing.reply.exitCode = 1
  failing.reply.stderr = 'boom'
  await start($)
  await failing.clock.settle()
  expect(cacheFile(failing)).toEqual({ version: 1, began: NOON, at: NOON, fingerprint: fingerprintOf('a'), stdout: '', error: 'boom' })
})

const HOUR_MS = 60 * MINUTE_MS

test('a cache stamped an hour ahead is stale: the window reads, and writes its own time over it', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  otherWindowRead(world, { at: NOON + HOUR_MS })

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual(COVEN_STATUS)
  expect(cacheFile(world).at).toBe(NOON)
})

test('a cache stamped an hour ahead does not freeze the safety read of a window that took a good one', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  otherWindowRead(world, { at: NOON + HOUR_MS, stdout: CACHED_WORKING })

  await world.clock.advance(50_000)
  expect(world.calls).toEqual([])
  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a claim stamped an hour ahead is void, and the window reads', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  otherWindowClaims(world, -HOUR_MS)

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual(COVEN_STATUS)
})

/**
 * From the call on, what the mod stores of its own times is an hour ahead of `$.clock.now()`, as a clock
 * stepped back leaves them. The mock clock only moves forward, so a hook over the mod's state writes does it.
 * Returns the switch.
 */
function stampsAhead(on: On): (isAhead: boolean) => void {
  let isAhead = false
  on('state.set', { plugin: 'aquelarre', key: 'readStartedAt' }, (_, e, next) =>
    next(isAhead ? { ...e, value: e.value + HOUR_MS } : e),
  )
  on('state.set', { plugin: 'aquelarre', key: 'lastRead' }, (_, e, next) =>
    next(isAhead ? { ...e, value: { ...e.value, at: e.value.at + HOUR_MS } } : e),
  )
  return value => {
    isAhead = value
  }
}

test('a read that began an hour ahead of the clock does not stop a closed pane from being due', async ($, on) => {
  const setAhead = stampsAhead(on)
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  setAhead(true)
  await world.clock.advance(10_000)
  setAhead(false)
  expect(world.calls).toEqual([])

  world.mtimes[state('a')] = 5
  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a window whose own last read is an hour ahead of the clock still takes a newer cache', async ($, on) => {
  const setAhead = stampsAhead(on)
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  world.mtimes[state('a')] = 5
  setAhead(true)
  await world.clock.advance(10_000)
  setAhead(false)
  expect(world.calls).toEqual(COVEN_STATUS)

  await world.clock.advance(5_000)
  otherWindowRead(world, { fingerprint: 'a:5', stdout: CACHED_WORKING })
  await world.clock.advance(10_000)

  expect(world.calls).toEqual(COVEN_STATUS)
  const ui = await mountPane($)
  expect(await screen(ui)).not.toMatch(/tower/)
  await ui.unmount()
})

// The engine puts `aquelarre: $.process.run:` before a refusal's reason, so a refusal with none reaches the mod with
// a message that is never empty, and the `no se pudo leer` fallback for an empty one cannot be reached from here.
// What this pins is what it guards: a failed read always has a reason, because an empty one reads back as a good read.
test('a command refused with no reason is a failed read with an error: in the pane, in the cache, and the cards stay', async ($, on) => {
  const world = stage(on, { stdout: CACHED_WORKING, files: jobs('a') })
  await started($)
  world.calls.splice(0)
  const ui = await mountPane($)

  world.silent.add('coven')
  world.mtimes[state('a')] = 5
  await world.clock.advance(READ_MS)

  expect(world.calls.length).toBeGreaterThan(0)
  const { error, stdout } = cacheFile(world)
  expect(error).not.toBe('')
  expect(stdout).toBe('')
  const shown = await screen(ui)
  expect(shown).toMatch(/No pude leer los PMs/)
  expect(shown).toContain(error)
  expect(shown).toMatch(/coven/)
  await ui.unmount()
})

test('a window that takes a cached error shows it and keeps the cards of its last good read', async ($, on) => {
  const world = stage(on, { stdout: CACHED_WORKING, files: jobs('a') })
  await started($)
  world.calls.splice(0)
  const ui = await mountPane($)

  // The failed read is 1 s old at the tick, so it also stops this window's own read.
  await world.clock.advance(TICK_MS - 1_000)
  otherWindowRead(world, { stdout: '', error: 'boom' })
  await world.clock.advance(1_000)

  expect(world.calls).toEqual([])
  const shown = await screen(ui)
  expect(shown).toMatch(/No pude leer los PMs/)
  expect(shown).toMatch(/boom/)
  expect(shown).toMatch(/coven/)
  await ui.unmount()
})

for (const [name, broken] of [
  ['does not parse', (world: Stage) => (world.files[CACHE_FILE] = '{"version": 1, "at"')],
  ['is not an object', (world: Stage) => (world.files[CACHE_FILE] = 'null')],
  ['has version 2', (world: Stage) => otherWindowRead(world, { version: 2 })],
  ['has no stdout', (world: Stage) => (world.files[CACHE_FILE] = JSON.stringify({ version: 1, at: NOON, fingerprint: 'a:1' }))],
  ['cannot be read', (world: Stage) => (otherWindowRead(world), world.broken.add(CACHE_FILE))],
  ['cannot be written', (world: Stage) => world.broken.add(CACHE_FILE)],
  ['has a claim that cannot be written', (world: Stage) => world.broken.add(CLAIM_FILE)],
] as const) {
  test(`a cache that ${name}: the window reads by itself, with no pane error`, async ($, on) => {
    const world = stage(on, { stdout: MIXED, files: jobs('a') })
    broken(world)

    await start($)
    await world.clock.settle()

    expect(world.calls).toEqual(COVEN_STATUS)
    const ui = await mountPane($)
    expect(await screen(ui)).not.toMatch(/No pude leer/)
    expect(await screen(ui)).toMatch(/tower/)
    await ui.unmount()
  })
}

test('a window whose session id fails reads without a claim and leaves another window\'s live claim as it is', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a'), sessionId: '' })
  otherWindowClaims(world, 3_000)
  const theirs = world.files[CLAIM_FILE]

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual(COVEN_STATUS)
  expect(world.files[CLAIM_FILE]).toBe(theirs)
  expect(world.writes.map(({ path }) => path)).toEqual([CACHE_FILE])
})

test('a window whose claim another window took over while it read does not clear that window\'s claim', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  const release = world.hold()

  await start($)
  await world.clock.settle()
  expect(JSON.parse(world.files[CLAIM_FILE] ?? '').by).toBe('window-1')
  otherWindowClaims(world)
  const theirs = world.files[CLAIM_FILE]
  release()
  await world.clock.settle()

  expect(world.files[CLAIM_FILE]).toBe(theirs)
  expect(cacheFile(world).at).toBe(NOON)
})

test('a claim that cannot be read is no claim', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  world.files[CLAIM_FILE] = 'not json'

  await start($)
  await world.clock.settle()

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('XDG_CACHE_HOME wins over HOME', async ($, on) => {
  const world = stage(on, { stdout: MIXED, xdgCache: '/xdg', files: jobs('a') })

  await start($)
  await world.clock.settle()

  expect(world.writes.map(({ path }) => path)).toEqual(['/xdg/aquelarre/reading.json', '/xdg/aquelarre/status.json', '/xdg/aquelarre/reading.json'])
  expect(world.files[CACHE_FILE]).toBeUndefined()
})

test('with neither XDG_CACHE_HOME nor HOME there is no cache, no claim and no write', async ($, on) => {
  const world = stage(on, { stdout: MIXED, home: '' })
  await quiet($, world)

  await world.clock.advance(10_000)
  await world.clock.advance(10_000)

  expect(world.calls).toHaveLength(2)
  expect(world.writes).toEqual([])
  expect(world.cached).toEqual([])
})

test('XDG_CACHE_HOME alone is a cache folder, and HOME is not needed', async ($, on) => {
  const world = stage(on, { stdout: MIXED, home: '', xdgCache: '/xdg' })
  await start($)
  await world.clock.settle()

  expect(world.files['/xdg/aquelarre/status.json']).toBeDefined()
})

test('/aquelarre takes a cache fresher than its own last read and runs no command', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  await world.clock.advance(1_000)
  otherWindowRead(world, { stdout: CACHED_WORKING })

  await openPane($)

  expect(world.calls).toEqual([])
  const ui = await mountPane($)
  expect(await screen(ui)).toMatch(/coven/)
  expect(await screen(ui)).not.toMatch(/tower/)
  await ui.unmount()
})

test('/aquelarre reads when there is no cache at all', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  world.broken.add(CACHE_FILE)
  await world.clock.advance(1_000)

  await openPane($)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('/aquelarre reads when the cache is no fresher than its own last read', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  await world.clock.advance(1_000)

  await openPane($)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('with the jobs unchanged, a cache another window wrote at start is taken for a minute: one command at 60 s, then shared again', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  // The other four windows read first, as at the same start.
  otherWindowRead(world, { stdout: MIXED })
  await start($)
  await world.clock.settle()

  for (let seconds = 10; seconds <= 50; seconds += 10) {
    await world.clock.advance(10_000)
    expect(world.calls, `at ${seconds} s`).toEqual([])
  }
  await world.clock.advance(10_000)
  expect(world.calls).toEqual(COVEN_STATUS)

  // The read at the minute is shared: the ticks after it take its cache, as another window would.
  await world.clock.advance(10_000)
  await world.clock.advance(10_000)
  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a read that takes 4 s is shared from when it began: a closed window with changing jobs reads again 10 s on, not 20 s', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  const release = world.hold()
  const starting = start($)
  await world.clock.settle()
  await world.clock.advance(4_000)
  release()
  await starting
  await world.clock.settle()
  world.calls.splice(0)
  expect(cacheFile(world)).toMatchObject({ began: NOON, at: NOON + 4_000 })

  // The tick at 5 s is not due for a closed pane. The jobs change before the tick at 10 s: the cache is 10 s old
  // by when it began, but only 6 s by when it finished, which is short of the 7.5 s share age.
  world.mtimes[state('a')] = 5
  await world.clock.advance(TICK_MS)
  await world.clock.advance(TICK_MS)

  expect(world.calls).toEqual(COVEN_STATUS)
})

test('a cache with no began is shared from when it finished', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  await world.clock.advance(TICK_MS)
  world.mtimes[state('a')] = 5
  // Written by a build that had no began: it finished 5 s ago, so at the tick it is 5 s old and shared.
  otherWindowRead(world, { stdout: CACHED_WORKING })
  expect(cacheFile(world).began).toBeUndefined()

  await world.clock.advance(TICK_MS)

  expect(world.calls).toEqual([])
})

test('a window takes a failed shared read that is 4 s old, though its own jobs are unchanged: it shows the error and keeps its cards', async ($, on) => {
  const world = stage(on, { stdout: CACHED_WORKING, files: jobs('a') })
  await quiet($, world)
  const ui = await mountPane($)
  expect(await screen(ui)).toMatch(/coven/)

  // The tick at 5 s is not due for a closed pane. The failed read is 4 s old at the tick at 10 s: past the 2.5 s
  // it is shared, so it does not stop a read, but it is newer than this window's own.
  await world.clock.advance(6_000)
  otherWindowRead(world, { stdout: '', error: 'boom' })
  await world.clock.advance(4_000)

  expect(world.calls).toEqual([])
  const shown = await screen(ui)
  expect(shown).toMatch(/No pude leer los PMs/)
  expect(shown).toMatch(/boom/)
  expect(shown).toMatch(/coven/)

  // It retries after one tick, as the spec accepts.
  await world.clock.advance(10_000)
  expect(world.calls).toEqual(COVEN_STATUS)
  await ui.unmount()
})

test('a failed shared read that is older than the window\'s own read is not taken', async ($, on) => {
  const world = stage(on, { stdout: CACHED_WORKING, files: jobs('a') })
  await quiet($, world)
  const ui = await mountPane($)
  // A failed read older than this window's own is not news: it must not be shown.
  otherWindowRead(world, { at: NOON - 4_000, stdout: '', error: 'boom' })

  await world.clock.advance(10_000)

  expect(await screen(ui)).not.toMatch(/boom/)
  expect(world.calls).toEqual([])
  await ui.unmount()
})

test('/aquelarre does not take a cache stamped an hour ahead', async ($, on) => {
  const world = stage(on, { stdout: MIXED, files: jobs('a') })
  await quiet($, world)
  otherWindowRead(world, { at: NOON + HOUR_MS, stdout: CACHED_WORKING })

  await openPane($)

  expect(world.calls).toEqual(COVEN_STATUS)
})
