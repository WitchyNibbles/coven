import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Ask, Circle, Step, Witch } from '../types'

const PANE = 'aquelarre'
const TITLE = '☾ Aquelarre'
const PANE_COLUMNS = 64
const REFRESH_MS = 5_000
const FRESH_MS = 2 * 60_000
// With the pane closed only the band wants the read, so it is made at half the pane's rate.
const CLOSED_REFRESH_MS = 10_000
// A read is stamped after host round trips, so a tick 10 s later sees a gap a few ms short of 10 s. Every age
// compared with a stamp (the closed cadence, the safety age, the share ages, the retry of a failed read) is
// short by half a tick, which keeps a closed pane at every other tick instead of every third.
const TICK_SLACK_MS = REFRESH_MS / 2
const STATUS_TIMEOUT_MS = 20_000
// A read is repeated at least this often, whether or not a job changed: a PM that dies writes no file.
const SAFETY_MS = 60_000
// A claim on the shared read older than the status timeout plus 5 s is void: its window died or hung.
const CLAIM_VOID_MS = STATUS_TIMEOUT_MS + 5_000
const CACHE_VERSION = 1
const CACHE_FILE = 'status.json'
const CLAIM_FILE = 'reading.json'
const STATUS_COMMAND = ['coven', 'pm', 'status'] as const

const EMPTY: Circle = { witches: [], checkedAt: '', error: '' }
// Bump the shape tag when Circle's fields change: it declines what an older version stored.
const circle = atom({ plugin: 'aquelarre', key: 'circle' } as const, EMPTY, { shape: 'circle-4' })
const isReading = atom({ plugin: 'aquelarre', key: 'isReading' } as const, false)
// When the last look began, in epoch ms: an atom so that it survives a hot reload.
const readStartedAt = atom({ plugin: 'aquelarre', key: 'readStartedAt' } as const, 0)
// The read this window last took: when the status command finished, what the jobs folder looked like before it
// ran (null when that could not be taken) and whether it failed. An atom, so that it survives a hot reload.
const lastRead = atom(
  { plugin: 'aquelarre', key: 'lastRead' } as const,
  { at: 0, fingerprint: null, isFailed: false },
)

// Colours are theme keys only, so the pane follows the user's Claude Code theme.
const GROUPS = {
  waits: { glyph: '◆', color: 'permission' },
  problem: { glyph: '✕', color: 'error' },
  working: { glyph: '✦', color: 'claude' },
  idle: { glyph: '○', color: 'inactive' },
} as const
type Group = keyof typeof GROUPS
const URGENCY: readonly Group[] = ['waits', 'problem', 'working', 'idle']

type Plural = readonly [one: string, many: string]

/** Every word the mod shows, in one language. Glyphs, times and PM names are not words and stay in the code. */
type Text = Readonly<{
  /** Each group's label in the summary, singular then plural. */
  summary: Readonly<Record<Group, Plural>>
  /** A PM's state as a word, by the state `coven pm status` prints. A state not listed is unknown. */
  stateWords: Readonly<Record<string, string>>
  unknownState: string
  noMessages: string
  awaitingReply: string
  /** The mark on a card that changed lately. */
  fresh: string
  notDelivered: string
  /** The tag on the current step of a card that waits. */
  waitTag: string
  noPlan: string
  since: (time: string) => string
  more: (count: number) => string
  doneSteps: (count: number) => string
  done: Plural
  stopped: Plural
  finished: string
  nobody: string
  reading: string
  paneFailed: string
  retrying: (seconds: number) => string
  noPms: string
  noPmsHint: string
  rolePane: string
  bandWaits: string
  bandNobody: string
  bandReading: string
  bandFailed: string
  exited: (command: string, code: number) => string
  noAnswer: (command: string, seconds: number) => string
  unknownError: string
  commandDescription: string
  opened: string
  noSurface: string
}>

const TEXT: Record<'en' | 'es', Text> = {
  en: {
    summary: { waits: ['waits', 'wait'], problem: ['problem', 'problems'], working: ['working', 'working'], idle: ['paused', 'paused'] },
    stateWords: {
      working: 'working',
      idle: 'paused',
      blocked: 'waits on the GM',
      stale: 'process dead',
      duplicate: 'duplicate',
      failed: 'failed',
    },
    unknownState: 'no data',
    noMessages: 'no messages yet',
    awaitingReply: 'waiting for an answer',
    fresh: 'new',
    notDelivered: 'not delivered',
    waitTag: 'waits for your answer',
    noPlan: 'no plan yet',
    since: time => `since ${time}`,
    more: count => `+${count} more`,
    doneSteps: count => `${count} done`,
    done: ['done', 'done'],
    stopped: ['stopped', 'stopped'],
    finished: 'finished',
    nobody: 'No one working right now.',
    reading: 'Reading the PMs…',
    paneFailed: "Couldn't read the PMs",
    retrying: seconds => `retrying in ${seconds} s`,
    noPms: 'No PMs this week.',
    noPmsHint: 'They show up here when one is launched from the GM.',
    rolePane: "This pane only reads PMs in the GM's session.",
    bandWaits: 'waits: ',
    bandNobody: 'no one working right now',
    bandReading: 'reading the PMs…',
    bandFailed: "✕ couldn't read the PMs",
    exited: (command, code) => `${command} exited with ${code}`,
    noAnswer: (command, seconds) => `${command} did not answer in ${seconds} s`,
    unknownError: 'could not read',
    commandDescription: 'Open the aquelarre pane: the active PMs and what each one is doing',
    opened: 'The aquelarre is open.',
    noSurface: 'There is no surface for the pane right now.',
  },
  es: {
    summary: { waits: ['espera', 'esperan'], problem: ['problema', 'problemas'], working: ['trabajando', 'trabajando'], idle: ['en pausa', 'en pausa'] },
    stateWords: {
      working: 'trabajando',
      idle: 'en pausa',
      blocked: 'espera al GM',
      stale: 'proceso muerto',
      duplicate: 'duplicado',
      failed: 'falló',
    },
    unknownState: 'sin datos',
    noMessages: 'sin mensajes todavía',
    awaitingReply: 'esperando respuesta',
    fresh: 'nuevo',
    notDelivered: 'no entregado',
    waitTag: 'espera tu respuesta',
    noPlan: 'sin plan todavía',
    since: time => `desde ${time}`,
    more: count => `+${count} más`,
    doneSteps: count => `${count} hechas`,
    done: ['terminada', 'terminadas'],
    stopped: ['parada', 'paradas'],
    finished: 'terminó',
    nobody: 'Nadie trabajando ahora.',
    reading: 'Leyendo los PMs…',
    paneFailed: 'No pude leer los PMs',
    retrying: seconds => `reintento en ${seconds} s`,
    noPms: 'No hay PMs esta semana.',
    noPmsHint: 'Aparecen aquí al lanzar uno desde el GM.',
    rolePane: 'Este panel solo lee PMs en la sesión del GM.',
    bandWaits: 'espera: ',
    bandNobody: 'nadie trabajando ahora',
    bandReading: 'leyendo los PMs…',
    bandFailed: '✕ no pude leer los PMs',
    exited: (command, code) => `${command} salió con ${code}`,
    noAnswer: (command, seconds) => `${command} no respondió en ${seconds} s`,
    unknownError: 'no se pudo leer',
    commandDescription: 'Abre el panel del aquelarre: los PMs activos y qué hace cada uno',
    opened: 'El aquelarre está abierto.',
    noSurface: 'No hay superficie para el panel ahora.',
  },
}

/** What the person set in the mod's `userConfig`: the words, and whether the band and the toasts are on. */
type Settings = Readonly<{ t: Text; hasBand: boolean; hasToasts: boolean }>

/** The settings from the options `register` gets: a language other than Spanish is English, and a switch is on unless it is false. */
function settingsFrom(options: Readonly<Record<string, unknown>>): Settings {
  return { t: TEXT[options.language === 'es' ? 'es' : 'en'], hasBand: options.band !== false, hasToasts: options.toasts !== false }
}

const SUMMARY_JOINER = ' ⋆ '
const NAME_GAP = 2
const MAX_NAME_COLUMN = 16
const INDENT = '  '
const MIN_WORD = 2
const MIN_NAME = 2

// The prefix a PM or the GM puts on a message, and its colour. Unlisted kinds are not parsed as messages.
const KIND_COLORS: Record<string, string> = {
  QUESTION: 'permission',
  'PROD-STEP': 'warning',
  DRAFT: 'suggestion',
  DONE: 'success',
  BLOCKED: 'error',
  ANSWER: 'inactive',
  APPROVED: 'inactive',
  DENIED: 'inactive',
  TASK: 'inactive',
}

const HEADER = /^(\S+)\s+id=(\S+)\s+state=(.+?)\s+since GM: (.*)$/
const AGE = /^\d+[mhd]$/
// The flag is the last `[delivered]` or `[NOT DELIVERED: reason]` group at the end of the line. The greedy
// message part makes it the last one, so a message that quotes a flag keeps it, and a reason may hold `]`.
const OUT_WITH_FLAG = /^(.*)\s+\[(delivered|NOT DELIVERED.*)\]\s*$/
const SENDER_SUFFIX = / {2}\(from .*\)\s*$/
// A task key is `project/task`; a message may name several, joined as coven's pm/text.py `_keys` allows.
const KEY = '[a-z0-9][a-z0-9-]*/[A-Za-z0-9][A-Za-z0-9-]*'
const KEYS = `${KEY}(?:(?:\\s+(?:and|&|\\+)\\s+|\\s*,\\s*)${KEY})*`
const KEY_ONLY = new RegExp(KEY, 'g')
const MESSAGE_HEAD = new RegExp(`^(?:\\d\\d:\\d\\d|\\?)\\s+(${Object.keys(KIND_COLORS).join('|')})(?:\\s+(.*))?$`)
const KEYS_THEN_TEXT = new RegExp(`^(${KEYS}):\\s*(.*)$`)
// Only APPROVED has an id between its keys and the colon: `APPROVED proj/task <approval-id>: text`.
const KEYS_APPROVAL_TEXT = new RegExp(`^(${KEYS})(?:\\s+\\S+)?:\\s*(.*)$`)
const ASK = /^(\S+)\s+(\S+)\s+since\s+(\S+)$/
const AGE_MINUTES = { m: 1, h: 60, d: 24 * 60 } as const

/** Parses `coven pm status` text: one header line per PM, indented detail lines under it. */
export function parseStatus(stdout: string): Witch[] {
  const witches: Witch[] = []
  let current: Witch | undefined
  for (const line of stdout.split('\n')) {
    const header = HEADER.exec(line)
    if (header) {
      current = parseHeader(header[1] ?? '', header[2] ?? '', header[3] ?? '')
      witches.push(current)
      continue
    }
    const detail = line.trim()
    if (!detail) continue
    // Detail lines are indented; anything else is not this PM's, so it must not feed the one above.
    if (line === detail) current = undefined
    if (current) readDetail(current, detail)
  }
  return witches
}

function readDetail(witch: Witch, detail: string): void {
  if (detail.startsWith('last out:')) {
    const out = detail.slice('last out:'.length).trim()
    const flagged = OUT_WITH_FLAG.exec(out)
    witch.isUndelivered = flagged?.[2]?.startsWith('NOT DELIVERED') ?? false
    witch.lastOut = (flagged?.[1] ?? out).trim()
  } else if (detail.startsWith('last in:')) {
    witch.lastIn = detail.slice('last in:'.length).replace(SENDER_SUFFIX, '').trim()
  } else if (detail.startsWith('waits on GM:')) {
    witch.asks = parseAsks(detail.slice('waits on GM:'.length))
  }
}

function parseHeader(rawName: string, rawId: string, rawState: string): Witch {
  const words = rawState.split(' ')
  const last = words.at(-1) ?? ''
  const hasAge = words.length > 1 && AGE.test(last)
  if (hasAge) words.pop()
  return {
    name: rawName.replace(/^pm-/, ''),
    // A duplicate prints `id=a,b`: the first is the one whose plan is read.
    id: rawId.split(',')[0] ?? '',
    state: words.join(' '),
    age: hasAge ? last : '',
    lastOut: '',
    isUndelivered: false,
    lastIn: '',
    asks: [],
    plan: [],
    freshUntil: 0,
  }
}

/** `QUESTION coven/6 since 01:43, DONE coven/7 since ?`: one ask per item. An item of another shape keeps its text as the kind. */
function parseAsks(waits: string): Ask[] {
  return waits
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
    .map(item => {
      const match = ASK.exec(item)
      return match ? { kind: match[1] ?? '', ref: match[2] ?? '', time: match[3] ?? '?' } : { kind: item, ref: '', time: '?' }
    })
}

type Message = { kind: string; refs: string[]; text: string }

/** `HH:MM KIND keys: text`. Without keys the message keeps its kind and the rest is its text. */
function parseMessage(line: string): Message | undefined {
  const head = MESSAGE_HEAD.exec(line)
  if (!head) return undefined
  const kind = head[1] ?? ''
  const rest = head[2] ?? ''
  const withKeys = (kind === 'APPROVED' ? KEYS_APPROVAL_TEXT : KEYS_THEN_TEXT).exec(rest)
  if (!withKeys) return { kind, refs: [], text: rest }
  return { kind, refs: (withKeys[1] ?? '').match(KEY_ONLY) ?? [], text: withKeys[2] ?? '' }
}

const isAgeUnit = (unit: string): unit is keyof typeof AGE_MINUTES => Object.hasOwn(AGE_MINUTES, unit)

function ageMinutes(age: string): number {
  const unit = age.slice(-1)
  const count = Number.parseInt(age, 10)
  if (!isAgeUnit(unit) || Number.isNaN(count)) return Number.POSITIVE_INFINITY
  return count * AGE_MINUTES[unit]
}

type Standing = Group | 'resting'

const LIVE_STATES = new Set(['working', 'idle', 'blocked'])

function classify(witch: Witch): Standing {
  if (witch.state === 'done' || witch.state === 'stopped') return 'resting'
  // A dead, duplicated, failed or unknown PM is a problem before anything it may still be waiting on.
  if (!LIVE_STATES.has(witch.state)) return 'problem'
  if (witch.state === 'blocked' || witch.asks.length > 0) return 'waits'
  if (witch.isUndelivered) return 'problem'
  return witch.state === 'working' ? 'working' : 'idle'
}

type Live = { witch: Witch; group: Group }

/** The live PMs, most urgent first; inside a group the newest first. */
function liveByUrgency(witches: readonly Witch[]): Live[] {
  const live: Live[] = []
  for (const witch of witches) {
    const group = classify(witch)
    if (group !== 'resting') live.push({ witch, group })
  }
  return live.sort(
    (a, b) =>
      URGENCY.indexOf(a.group) - URGENCY.indexOf(b.group) || ageMinutes(a.witch.age) - ageMinutes(b.witch.age),
  )
}

function countByState(witches: readonly Witch[], state: string): number {
  return witches.filter(witch => witch.state === state).length
}

const plural = (count: number, [one, many]: Plural): string => `${count} ${count === 1 ? one : many}`

function restingLine(witches: readonly Witch[], t: Text): string {
  const done = countByState(witches, 'done')
  const stopped = countByState(witches, 'stopped')
  const parts = [done > 0 && plural(done, t.done), stopped > 0 && plural(stopped, t.stopped)].filter(Boolean)
  return parts.length > 0 ? `⋆ ${parts.join(' · ')}` : ''
}

const currentSubject = (witch: Witch): string => witch.plan.find(step => step.status === 'now')?.subject ?? ''

// What a read changes about a PM: when any differs from the last read, the card is marked new.
function sameNews(a: Witch, b: Witch): boolean {
  return a.state === b.state && a.lastOut === b.lastOut && currentSubject(a) === currentSubject(b)
}

/** Carries each PM's "new" mark over from the previous read, or starts one when it changed. */
function markChanges(previous: readonly Witch[], current: Witch[], now: number): Witch[] {
  return current.map(witch => {
    const before = previous.find(old => old.name === witch.name)
    // A PM that is new since a non-empty read is news; the first read, with nothing before it, marks nothing.
    if (!before) return previous.length > 0 ? { ...witch, freshUntil: now + FRESH_MS } : witch
    return { ...witch, freshUntil: sameNews(before, witch) ? before.freshUntil : now + FRESH_MS }
  })
}

function clockTime(ms: number): string {
  return new Date(ms).toTimeString().slice(0, 8)
}

function firstLine(text: string): string {
  return text.trim().split('\n')[0] ?? ''
}

function errorLine(err: unknown): string {
  return firstLine(err instanceof Error ? err.message : String(err))
}

type Attempt = { kind: 'read'; stdout: string } | { kind: 'failed'; error: string }

async function attempt($: EngineInterface, argv: readonly string[], t: Text): Promise<Attempt> {
  const startedAt = await $.clock.now()
  try {
    const { exitCode, stdout, stderr } = await $.process.run(argv, { timeoutMs: STATUS_TIMEOUT_MS })
    if (exitCode === 0) return { kind: 'read', stdout }
    const error = firstLine(stderr) || t.exited(argv[0] ?? '', exitCode)
    return { kind: 'failed', error }
  } catch (err) {
    // `process.run` rejects both when the command cannot start and when it outlives its timeout, with
    // nothing to tell them apart: a rejection that came after the full timeout is the hung one.
    const isTimeout = (await $.clock.now()) - startedAt >= STATUS_TIMEOUT_MS
    if (isTimeout) return { kind: 'failed', error: t.noAnswer(argv[0] ?? '', STATUS_TIMEOUT_MS / 1000) }
    return { kind: 'failed', error: errorLine(err) }
  }
}

/** Where Claude Code keeps its state: `CLAUDE_CONFIG_DIR`, else `.claude` under `HOME`; empty when neither is set. */
async function configDir($: EngineInterface): Promise<string> {
  const configured = await $.env.get('CLAUDE_CONFIG_DIR')
  if (configured) return configured
  const home = await $.env.get('HOME')
  return home ? `${home}/.claude` : ''
}

const STEP_FILE = /^(\d+)\.json$/
const STEP_STATUS: Record<string, Step['status']> = { completed: 'done', in_progress: 'now', pending: 'next' }

/** A task file as a step; undefined for text that is not JSON, has no subject or has a status the pane skips. */
function parseStep(text: string): Step | undefined {
  let task: unknown
  try {
    task = JSON.parse(text)
  } catch {
    return undefined
  }
  if (typeof task !== 'object' || task === null) return undefined
  const subject: unknown = Reflect.get(task, 'subject')
  const status: unknown = Reflect.get(task, 'status')
  const known = typeof status === 'string' ? STEP_STATUS[status] : undefined
  return typeof subject === 'string' && subject !== '' && known ? { subject, status: known } : undefined
}

/** The steps in a PM's task folder, by task number. A folder that cannot be listed is no plan; a file that cannot be read is skipped. */
async function readPlan($: EngineInterface, folder: string): Promise<Step[]> {
  try {
    const files = (await $.fs.list(folder))
      .flatMap(({ name }) => {
        const number = STEP_FILE.exec(name)?.[1]
        return number === undefined ? [] : [{ name, number: Number(number) }]
      })
      .sort((a, b) => a.number - b.number)
    const texts = await Promise.all(files.map(({ name }) => $.fs.read(`${folder}/${name}`).catch(() => '')))
    return texts.flatMap(text => parseStep(text) ?? [])
  } catch {
    return []
  }
}

/** A PM whose plan is read: live, and with an id to find its folder by. */
const wantsPlan = (witch: Witch): boolean => witch.id !== '' && classify(witch) !== 'resting'

/** Each live PM's plan from its task folder, `<config>/tasks/<id>-…`; every other PM, and any plan that fails, gets none. */
async function readPlans($: EngineInterface, witches: Witch[]): Promise<Witch[]> {
  const root = witches.some(wantsPlan) ? await configDir($).catch(() => '') : ''
  if (!root) return witches
  const tasks = `${root}/tasks`
  const folders = await $.fs.list(tasks).then(
    entries => entries.map(({ name }) => name).sort(),
    () => [],
  )
  return Promise.all(
    witches.map(async witch => {
      const folder = wantsPlan(witch) ? folders.find(name => name.startsWith(`${witch.id}-`)) : undefined
      return folder === undefined ? witch : { ...witch, plan: await readPlan($, `${tasks}/${folder}`) }
    }),
  )
}

// coven's own hooks treat any value of COVEN_SESSION_ROLE as set, an empty one too.
async function isRoleSession($: EngineInterface): Promise<boolean> {
  return (await $.env.get('COVEN_SESSION_ROLE')) !== undefined
}

const TOAST_MESSAGE_WIDTH = 60
const TOAST_URGENT_MS = 8000
const TOAST_CALM_MS = 4000
const TOAST_JOINER = ' ⋆ '

type Entry = { text: string; isUrgent: boolean }

/** A message line as plain text, the way a card prints it: `QUESTION #6 which logo?`. */
function plainMessage(line: string, own: string): string {
  const message = parseMessage(line)
  if (!message) return line
  const refs = message.refs.map(ref => shortRef(ref, own)).join(' ')
  return [message.kind, refs, message.text].filter(Boolean).join(' ')
}

/** `head`, then two spaces and `message` cut to its width, when there is one; `suffix` follows the cut message. */
function entryText(head: string, message: string, suffix = ''): string {
  return message ? `${head}  ${cut(message, TOAST_MESSAGE_WIDTH)}${suffix}` : head
}

/** The message a toast for a PM that waits carries: what its card shows, as plain text. */
function awaitedMessage(witch: Witch, t: Text): string {
  if (witch.asks.length === 0) return ''
  const item = awaitedItem(witch)
  if (!item) return plainMessage(witch.lastOut, witch.name)
  return [item.ask.kind, item.ask.ref && shortRef(item.ask.ref, witch.name), t.awaitingReply].filter(Boolean).join(' ')
}

const isDeadState = (witch: Witch) => classify(witch) === 'problem' && !LIVE_STATES.has(witch.state)

/** The first entry that matches one PM in this read, or none. `before` is undefined for a PM new since the last read. */
function entryFor(before: Witch | undefined, witch: Witch, t: Text): Entry | undefined {
  const { name } = witch
  if (before && isDeadState(witch) && before.state !== witch.state) {
    return { text: `✕ ${name} ${t.stateWords[witch.state] ?? t.unknownState}`, isUrgent: true }
  }
  if (classify(witch) === 'waits' && (!before || classify(before) !== 'waits')) {
    return { text: entryText(`◆ ${name} ${t.stateWords.blocked}`, awaitedMessage(witch, t)), isUrgent: true }
  }
  const isDoneOut = parseMessage(witch.lastOut)?.kind === 'DONE'
  const isFinished = witch.state === 'done' ? before?.state !== 'done' : isDoneOut && before?.lastOut !== witch.lastOut
  if (before && isFinished) {
    const message = isDoneOut ? plainMessage(witch.lastOut, name) : ''
    const flag = isDoneOut && witch.isUndelivered ? ` · ${t.notDelivered}` : ''
    return { text: entryText(`✓ ${name} ${t.finished}`, message, flag), isUrgent: false }
  }
  return undefined
}

/** One toast for what changed between two good reads: PMs that died, started waiting or finished. */
export function toastFor(
  previous: readonly Witch[],
  current: readonly Witch[],
  t: Text,
): { text: string; timeoutMs: number } | undefined {
  const entries = current.flatMap(witch => entryFor(previous.find(old => old.name === witch.name), witch, t) ?? [])
  if (entries.length === 0) return undefined
  return {
    text: entries.map(entry => entry.text).join(TOAST_JOINER),
    timeoutMs: entries.some(entry => entry.isUrgent) ? TOAST_URGENT_MS : TOAST_CALM_MS,
  }
}

/**
 * What the jobs folder looked like: the sorted `id:mtimeMs` of every job's `state.json`, which Claude Code
 * rewrites when the job's state or activity changes. A job folder with no `state.json` is left out.
 * Undefined when the folder cannot be listed, which callers treat as "changed".
 */
async function jobsFingerprint($: EngineInterface): Promise<string | undefined> {
  try {
    const root = await configDir($)
    if (!root) return undefined
    const folder = `${root}/jobs`
    const stamps = await Promise.all(
      (await $.fs.list(folder)).map(async ({ name }) => {
        const stat = await $.fs.stat(`${folder}/${name}/state.json`).catch(() => undefined)
        return stat && `${name}:${stat.mtimeMs}`
      }),
    )
    return stamps.filter(stamp => stamp !== undefined).sort().join(',')
  } catch {
    return undefined
  }
}

/** What a window knows of a read: when it finished, the jobs' fingerprint before it (null when none) and whether it failed. */
type ReadStamp = { at: number; fingerprint: string | null; isFailed: boolean }

/**
 * The status command is due when the jobs changed since the last read (or cannot be checked), that read failed
 * and a tick has passed, or it is a safety age old. Half a tick of slack, as the closed-pane cadence has: stamps
 * are made after host round trips.
 */
function isReadNeeded(now: number, last: ReadStamp, fingerprint: string | undefined): boolean {
  if (fingerprint === undefined || fingerprint !== last.fingerprint) return true
  const age = now - last.at
  // A time in the future, from a clock stepped back, is an old read.
  if (age < 0) return true
  if (last.isFailed && age >= REFRESH_MS - TICK_SLACK_MS) return true
  return age >= SAFETY_MS - TICK_SLACK_MS
}

/** One status read as every window shares it through the cache file. */
type SharedRead = {
  version: typeof CACHE_VERSION
  /** When the read began, by `$.clock.now()`: the share age counts from it. A cache written without it has `at`. */
  began: number
  /** When the read finished, by `$.clock.now()`. */
  at: number
  /** The jobs' fingerprint taken before the command ran; null when none could be taken. */
  fingerprint: string | null
  /** The command's output; empty when the read failed. */
  stdout: string
  /** Why the read failed; empty when it worked. */
  error: string
}

/** The folder the windows share: `XDG_CACHE_HOME`, else `.cache` under `HOME`, then `aquelarre`; empty when neither is set. */
async function cacheFolder($: EngineInterface): Promise<string> {
  const xdg = await $.env.get('XDG_CACHE_HOME')
  if (xdg) return `${xdg}/aquelarre`
  const home = await $.env.get('HOME')
  return home ? `${home}/.cache/aquelarre` : ''
}

/** The file that holds the last shared read; undefined when there is no cache folder. */
async function cachePath($: EngineInterface): Promise<string | undefined> {
  const folder = await cacheFolder($)
  return folder ? `${folder}/${CACHE_FILE}` : undefined
}

async function claimPath($: EngineInterface): Promise<string | undefined> {
  const folder = await cacheFolder($)
  return folder ? `${folder}/${CLAIM_FILE}` : undefined
}

/** `text` as a JSON object, or undefined when it does not parse or is not one. */
function parseObject(text: string): Record<string, unknown> | undefined {
  try {
    const value: unknown = JSON.parse(text)
    return typeof value === 'object' && value !== null ? Object.fromEntries(Object.entries(value)) : undefined
  } catch {
    return undefined
  }
}

/** The shared read, or undefined when the file is missing, cannot be read, does not parse or has another version. */
async function readCache($: EngineInterface): Promise<SharedRead | undefined> {
  try {
    const path = await cachePath($)
    const entry = path === undefined ? undefined : parseObject(String(await $.fs.read(path)))
    if (!entry) return undefined
    const { version, began, at, fingerprint, stdout, error } = entry
    const hasShape =
      version === CACHE_VERSION &&
      typeof at === 'number' &&
      (began === undefined || typeof began === 'number') &&
      (typeof fingerprint === 'string' || fingerprint === null) &&
      typeof stdout === 'string' &&
      typeof error === 'string'
    return hasShape ? { version, began: began ?? at, at, fingerprint, stdout, error } : undefined
  } catch {
    return undefined
  }
}

/** Shares a read with the other windows. A cache that cannot be written is no cache: nothing is thrown. */
async function writeCache($: EngineInterface, entry: SharedRead): Promise<void> {
  try {
    const path = await cachePath($)
    if (path !== undefined) await $.fs.write(path, JSON.stringify(entry))
  } catch {
    // The window has its own read; the others read for themselves.
  }
}

/** Who is reading and since when, or undefined when no claim can be read. */
async function readClaim($: EngineInterface, path: string): Promise<{ by: string; at: number } | undefined> {
  const claim = parseObject(String(await $.fs.read(path).catch(() => '')))
  return typeof claim?.by === 'string' && typeof claim.at === 'number' ? { by: claim.by, at: claim.at } : undefined
}

/**
 * Best effort, as `$.fs` has no lock: claims the status read by writing the claim file and reading it back.
 * False when another window holds a claim younger than CLAIM_VOID_MS, or won the write. True when this window
 * may read, which includes every case where a claim cannot be read or written.
 */
async function claimRead($: EngineInterface, by: string, now: number): Promise<boolean> {
  try {
    const path = await claimPath($)
    if (path === undefined) return true
    const held = await readClaim($, path)
    // A claim stamped in the future, from a clock stepped back, is void as an old one is.
    const isLive = held && held.by !== '' && held.by !== by && now >= held.at && now - held.at < CLAIM_VOID_MS
    if (isLive) return false
    await $.fs.write(path, JSON.stringify({ by, at: now }))
    const back = await readClaim($, path)
    return back === undefined || back.by === by
  } catch {
    return true
  }
}

/** Releases this window's own claim. A claim another window holds now, or none made, is left as it is. */
async function clearClaim($: EngineInterface, by: string): Promise<void> {
  if (by === '') return
  try {
    const path = await claimPath($)
    if (path === undefined || (await readClaim($, path))?.by !== by) return
    await $.fs.write(path, JSON.stringify({ by: '', at: 0 }))
  } catch {
    // A claim that stays is void within CLAIM_VOID_MS.
  }
}

/**
 * Takes a read, this window's own or one from the cache: parses it, reads the plans, marks what changed,
 * remembers it and raises the toast. A failed read sets the error and keeps the last good cards.
 */
async function takeRead($: EngineInterface, shared: SharedRead, { t, hasToasts }: Settings): Promise<void> {
  const isFailed = shared.error !== ''
  const parsed = isFailed ? [] : await readPlans($, parseStatus(shared.stdout))
  const now = await $.clock.now()
  let toast: ReturnType<typeof toastFor>
  await update($, circle, previous => {
    // This callback may run again when a version misses: the toast kept is the last run's.
    toast = undefined
    if (isFailed) return { ...previous, error: shared.error }
    // The first good read has nothing to compare with, so it is never news.
    if (hasToasts && previous.checkedAt !== '') toast = toastFor(previous.witches, parsed, t)
    return {
      witches: markChanges(previous.witches, parsed, now),
      checkedAt: clockTime(now),
      error: '',
    }
  })
  // Last: a read that throws before it is drawn is not taken, and the next due tick tries it again.
  await update($, lastRead, () => stampOf(shared))
  if (toast) $.ui.toast(toast.text, { timeoutMs: toast.timeoutMs })
}

/** A read this window has not taken: not stamped in the future, and newer than its last one, which counts as old when it is stamped in the future. */
const isNewRead = (now: number, shared: SharedRead, last: ReadStamp): boolean =>
  shared.at <= now && (last.at > now || shared.at > last.at)

/** Takes the cache when this window has not taken a read as new as it. */
async function takeCached($: EngineInterface, now: number, cached: SharedRead | undefined, settings: Settings): Promise<void> {
  if (cached && isNewRead(now, cached, await read($, lastRead))) await takeRead($, cached, settings)
}

const stampOf = ({ at, fingerprint, error }: SharedRead): ReadStamp => ({ at, fingerprint, isFailed: error !== '' })

/**
 * A read younger than this stops every window from running the command, whatever the jobs' fingerprint says. A
 * good read is shared for one closed cadence, so that windows with live PMs together read about every 10 s; a
 * failed one for one tick, so that its retry is the next tick. Less the slack, as stamps are made after round trips.
 */
const shareAge = ({ error }: SharedRead): number => (error === '' ? CLOSED_REFRESH_MS : REFRESH_MS) - TICK_SLACK_MS

/** Younger than its share age, counted from when it began. A read stamped in the future is not young. */
function isShared(now: number, shared: SharedRead): boolean {
  const age = now - shared.began
  return age >= 0 && age < shareAge(shared)
}

/** The status command's result in the shape the windows share. */
function sharedRead(result: Attempt, began: number, at: number, fingerprint: string | undefined, t: Text): SharedRead {
  const isRead = result.kind === 'read'
  return {
    version: CACHE_VERSION,
    began,
    at,
    fingerprint: fingerprint ?? null,
    stdout: isRead ? result.stdout : '',
    // A failure always has a reason: an empty one would read back as a good read.
    error: isRead ? '' : result.error || t.unknownError,
  }
}

/**
 * One look. Takes the jobs' fingerprint, then the cache: a read younger than its share age stops the command in
 * every window, and a read of the same jobs under the safety age is taken as it is. Otherwise the window claims
 * the read, runs the status command, shares it and takes it. When another window holds the claim, it takes the
 * cache if there is one and leaves the rest to the next due tick.
 * `isAsked` is `/aquelarre`: it skips the change check and takes a cache only when it is newer than its last read.
 */
async function look($: EngineInterface, startedAt: number, isAsked: boolean, settings: Settings): Promise<void> {
  // Taken before the command: a job that changes while it runs is read again on the next due tick.
  const fingerprint = await jobsFingerprint($)
  const cached = await readCache($)
  const last = await read($, lastRead)
  const isUsable = isAsked
    ? cached && isNewRead(startedAt, cached, last)
    : cached && (isShared(startedAt, cached) || !isReadNeeded(startedAt, stampOf(cached), fingerprint))
  if (cached && isUsable) return takeCached($, startedAt, cached, settings)
  if (!isAsked && !isReadNeeded(startedAt, last, fingerprint)) {
    // A failed read newer than this window's own is news, even when its own jobs are unchanged.
    if (cached?.error) await takeCached($, startedAt, cached, settings)
    return
  }
  const by = await $.session.id().catch(() => '')
  if (by !== '' && !(await claimRead($, by, startedAt))) return takeCached($, startedAt, cached, settings)
  const began = await $.clock.now()
  const result = await attempt($, STATUS_COMMAND, settings.t)
  const shared = sharedRead(result, began, await $.clock.now(), fingerprint, settings.t)
  await writeCache($, shared)
  await clearClaim($, by)
  await takeRead($, shared, settings)
}

/** A look that sets the read flag for its length, so that no second one runs beside it. */
async function readCircle($: EngineInterface, settings: Settings, isAsked = false): Promise<void> {
  let isClaimed = false
  const startedAt = await $.clock.now()
  await update($, isReading, was => {
    isClaimed = !was
    return true
  })
  if (!isClaimed) return
  try {
    await update($, readStartedAt, () => startedAt)
    await look($, startedAt, isAsked, settings)
  } finally {
    await update($, isReading, () => false)
  }
}

// The engine's record of open panes outlives a reload and covers a pane restored in a new process. A tab
// behind another pane, or a pane waiting undrawn, is not looked at, so it is not worth a spawn every 5 s;
// a closed pane is still read about every 10 s, for the band.
async function isPaneOpen($: EngineInterface): Promise<boolean> {
  return (await $.ui.panes()).some(pane => pane.id === PANE && pane.isPlaced && pane.isShown)
}

/** A read is due: every tick while the pane is open, else once CLOSED_REFRESH_MS, less TICK_SLACK_MS, has passed since the last began. */
async function isReadDue($: EngineInterface): Promise<boolean> {
  if (await isPaneOpen($)) return true
  const elapsed = (await $.clock.now()) - (await read($, readStartedAt))
  // A last look stamped in the future, from a clock stepped back, is an old one.
  return elapsed < 0 || elapsed >= CLOSED_REFRESH_MS - TICK_SLACK_MS
}

/** One timer period: reads when one is due. A failure becomes the circle's error, never a loose rejection. */
async function tick($: EngineInterface, settings: Settings): Promise<void> {
  try {
    if (await isReadDue($)) await readCircle($, settings)
  } catch (err) {
    const error = errorLine(err)
    // The timer must go on and there is no one left to tell: if even recording the failure throws, drop it.
    await update($, circle, previous => ({ ...previous, error })).catch(() => undefined)
  }
}

/** What the draw functions share: the engine's `Box` and `Text`, and the words to print. */
type Parts = Pick<ReturnType<EngineInterface['ui']['resolve']>, 'Box' | 'Text'> & { t: Text }

// shortcut: widths are string lengths, right for the single-width glyphs and Latin names a PM has; upgrade when a name is wide text.
/** `text` cut to `width`, with `…` as its last character when something went. */
function cut(text: string, width: number): string {
  if (text.length <= width) return text
  return width > 0 ? `${text.slice(0, width - 1)}…` : ''
}

/** The spaces that push the right-hand text of a row to the edge, at least one, given the width both sides use. */
function gapBetween(usedWidth: number, columns: number): string {
  return ' '.repeat(Math.max(1, columns - usedWidth))
}

/** Words and counts per group when they fit `columns`, else glyph and count only: the cards carry the words. */
function summaryLine({ Text, t }: Parts, live: readonly Live[], columns: number) {
  if (live.length === 0) return <Text color="inactive">{t.nobody}</Text>
  const present = URGENCY.flatMap(group => {
    const count = live.filter(one => one.group === group).length
    if (count === 0) return []
    const { glyph, color } = GROUPS[group]
    return [{ color, short: `${glyph} ${count}`, long: `${glyph} ${plural(count, t.summary[group])}` }]
  })
  const isWide = present.map(({ long }) => long).join(SUMMARY_JOINER).length <= columns
  return (
    <Text wrap="truncate-end">
      {present.flatMap(({ color, short, long }, index) => {
        const entry = <Text color={color}>{isWide ? long : short}</Text>
        return index > 0 ? [<Text color="subtle">{SUMMARY_JOINER}</Text>, entry] : [entry]
      })}
    </Text>
  )
}

/** `#6` for a ref in the card's own project, any other ref as written. */
function shortRef(ref: string, own: string): string {
  return ref.startsWith(`${own}/`) ? `#${ref.slice(own.length + 1)}` : ref
}

/** What the card's right edge says: since when it waits, else how long the PM has been in its state. */
function rightEdge({ witch, group }: Live, t: Text): string {
  const since = group === 'waits' ? witch.asks[0]?.time : undefined
  return since && since !== '?' ? t.since(since) : witch.age
}

function stateWord({ witch, group }: Live, t: Text): string {
  return group === 'waits' ? t.stateWords.blocked : (t.stateWords[witch.state] ?? t.unknownState)
}

/** The longest live name plus a gap, at most MAX_NAME_COLUMN. */
function nameColumn(live: readonly Live[]): number {
  const longest = Math.max(...live.map(({ witch }) => witch.name.length))
  return Math.min(longest + NAME_GAP, MAX_NAME_COLUMN)
}

type Fitted = { name: string; word: string; mark: string }

/**
 * What of a card's left side fits `room`: the state word is cut first, then the new mark goes, then
 * the name is cut. The name column pads the name only while a state word follows it.
 */
function fitLeft(name: string, word: string, mark: string, nameWidth: number, room: number): Fitted {
  const nameRoom = nameWidth - NAME_GAP
  const ruledName = cut(name, nameRoom)
  const wordRoom = room - nameWidth - mark.length
  if (wordRoom >= MIN_WORD) return { name: ruledName.padEnd(nameWidth), word: cut(word, wordRoom), mark }
  if (ruledName.length + mark.length <= room) return { name: ruledName, word: '', mark }
  return { name: cut(name, Math.max(MIN_NAME, Math.min(room, nameRoom))), word: '', mark: '' }
}

function headline({ Text, t }: Parts, one: Live, now: number, columns: number, nameWidth: number) {
  const { witch, group } = one
  const look = GROUPS[group]
  const glyph = `${look.glyph} `
  const edge = rightEdge(one, t)
  // The glyph and its space, and a gap of at least one space before the right edge, are always kept.
  const room = columns - glyph.length - edge.length - 1
  const left = fitLeft(witch.name, stateWord(one, t), witch.freshUntil > now ? `  • ${t.fresh}` : '', nameWidth, room)
  const leftWidth = glyph.length + left.name.length + left.word.length + left.mark.length
  return (
    <Text wrap="truncate-end">
      <Text color={look.color}>{glyph}</Text>
      <Text bold color="claude">
        {left.name}
      </Text>
      <Text color={look.color}>{left.word}</Text>
      {left.mark && <Text color="claude">{left.mark}</Text>}
      {gapBetween(leftWidth + edge.length, columns)}
      <Text color="subtle">{edge}</Text>
    </Text>
  )
}

/** `KIND refs · not delivered  `: the kind in its colour, the refs in `subtle`, the flag right after them. */
function messageHead({ Text, t }: Parts, kind: string, refs: readonly string[], own: string, isUndelivered: boolean) {
  return [
    <Text bold color={KIND_COLORS[kind] ?? 'inactive'}>
      {kind}
    </Text>,
    ...(refs.length > 0 ? [' ', <Text color="subtle">{refs.map(ref => shortRef(ref, own)).join(' ')}</Text>] : []),
    isUndelivered && <Text color="error">{` · ${t.notDelivered}`}</Text>,
    '  ',
  ]
}

/** A message line as a card shows it: parsed into its parts, or as written when it does not parse. */
function printed(parts: Parts, line: string, own: string, isUndelivered: boolean) {
  const { Text, t } = parts
  const message = parseMessage(line)
  if (message) {
    return [...messageHead(parts, message.kind, message.refs, own, isUndelivered), <Text color="inactive">{message.text}</Text>]
  }
  return [isUndelivered && <Text color="error">{`${t.notDelivered}  `}</Text>, <Text color="inactive">{line}</Text>]
}

/**
 * The item a PM that waits shows instead of its last message out: the first thing it waits on, with how
 * many others wait behind it. Undefined when it waits on nothing, or its last out already is that item.
 */
function awaitedItem(witch: Witch): { ask: Ask; others: number } | undefined {
  const [ask, ...others] = witch.asks
  if (!ask) return undefined
  const out = parseMessage(witch.lastOut)
  const isAwaitedOut = out?.kind === ask.kind && out.refs[0] === ask.ref
  return isAwaitedOut ? undefined : { ask, others: others.length }
}

/**
 * The message a card shows. A card that waits on the GM (a dead one too) shows its last message out
 * when that is the first thing it waits on, else the awaited item itself. Any other card shows its
 * last out, else its last in. The not-delivered flag belongs to the last out, so only that line carries it.
 */
function messageBody(parts: Parts, { witch }: Live) {
  const { Text, t } = parts
  const item = awaitedItem(witch)
  if (item) {
    return [
      ...messageHead(parts, item.ask.kind, [item.ask.ref], witch.name, false),
      <Text color="inactive">{t.awaitingReply}</Text>,
      item.others > 0 && <Text color="inactive">{` ${t.more(item.others)}`}</Text>,
    ]
  }
  if (witch.lastOut) return printed(parts, witch.lastOut, witch.name, witch.isUndelivered)
  if (witch.lastIn) return printed(parts, witch.lastIn, witch.name, false)
  const fallback = t.stateWords[witch.state] === undefined ? witch.state : t.noMessages
  return [<Text color="inactive">{fallback}</Text>]
}

function detailLine(parts: Parts, one: Live) {
  const { Text } = parts
  return (
    <Text wrap="truncate-end">
      {INDENT}
      {messageBody(parts, one)}
    </Text>
  )
}

const STEP_MARKS = { done: '✓', now: '▸', next: '·' } as const
/** Where a step's subject starts: the indent, the mark and a space. */
const SUBJECT_COLUMN = `${INDENT}${STEP_MARKS.now} `.length
const MAX_DONE_SHOWN = 2
const MAX_NEXT_SHOWN = 3
const SHORT_WAIT_TAG = `${INDENT}◆`
const MIN_SUBJECT = 20

/** The tag on a waiting card's first current step; the short one when the full tag leaves the subject too little. */
function waitTag(columns: number, t: Text): string {
  const whole = `${INDENT}◆ ${t.waitTag}`
  return columns - SUBJECT_COLUMN - whole.length >= MIN_SUBJECT ? whole : SHORT_WAIT_TAG
}

function stepLine({ Text }: Parts, step: Step, columns: number, tag = '') {
  const text = cut(`${INDENT}${STEP_MARKS[step.status]} ${step.subject}`, columns - tag.length)
  if (step.status !== 'now') {
    return (
      <Text color={step.status === 'done' ? 'subtle' : 'suggestion'} wrap="truncate-end">
        {text}
      </Text>
    )
  }
  return (
    <Text wrap="truncate-end">
      <Text bold color="claude">
        {text}
      </Text>
      {tag && <Text color="permission">{tag}</Text>}
    </Text>
  )
}

/** The plan's rows: done steps (folded past two), the current ones, the next three and how many more. */
function planLines(parts: Parts, { witch, group }: Live, columns: number) {
  const { Text, t } = parts
  const byStatus = (status: Step['status']) => witch.plan.filter(step => step.status === status)
  const finished = byStatus('done')
  const nowSteps = byStatus('now')
  const upcoming = byStatus('next')
  const tag = group === 'waits' ? waitTag(columns, t) : ''
  const folded = finished.length > MAX_DONE_SHOWN
  return [
    folded ? (
      <Text color="subtle" wrap="truncate-end">
        {cut(`${INDENT}${STEP_MARKS.done} ${t.doneSteps(finished.length)}`, columns)}
      </Text>
    ) : (
      finished.map(step => stepLine(parts, step, columns))
    ),
    nowSteps.map((step, index) => stepLine(parts, step, columns, index === 0 ? tag : '')),
    upcoming.slice(0, MAX_NEXT_SHOWN).map(step => stepLine(parts, step, columns)),
    upcoming.length > MAX_NEXT_SHOWN && (
      <Text color="suggestion" wrap="truncate-end">
        {cut(`${' '.repeat(SUBJECT_COLUMN)}${t.more(upcoming.length - MAX_NEXT_SHOWN)}`, columns)}
      </Text>
    ),
  ]
}

/** A card's body: its plan, or its `noPlan` text; then the message line when there is no plan, it waits or it has asks. */
function cardBody(parts: Parts, one: Live, columns: number) {
  const { Text, t } = parts
  const hasPlan = one.witch.plan.length > 0
  const hasMessage = !hasPlan || one.group === 'waits' || one.witch.asks.length > 0
  return [
    hasPlan ? planLines(parts, one, columns) : (
      <Text color="subtle" wrap="truncate-end">
        {`${INDENT}${t.noPlan}`}
      </Text>
    ),
    hasMessage && detailLine(parts, one),
  ]
}

/** The rule above and below a card: a star, dashes the rest of the width, a star, in `subtle`. */
function rule({ Text }: Parts, columns: number) {
  return <Text color="subtle">{`⋆${'─'.repeat(Math.max(0, columns - 2))}⋆`}</Text>
}

/** One card per live PM between star rules, the names in one column. */
function cards(parts: Parts, live: readonly Live[], now: number, columns: number) {
  const { Box } = parts
  const nameWidth = nameColumn(live)
  return (
    <Box flexDirection="column">
      {rule(parts, columns)}
      {live.map(one => (
        <Box flexDirection="column">
          {headline(parts, one, now, columns, nameWidth)}
          {cardBody(parts, one, columns)}
          {rule(parts, columns)}
        </Box>
      ))}
    </Box>
  )
}

/** The resting counts at the left edge and the time of the last read at the right. */
function footerLine({ Text }: Parts, resting: string, checkedAt: string, columns: number) {
  const time = `↻ ${checkedAt}`
  // The counts give way before the time does.
  const counts = cut(resting, columns - time.length - 1)
  return (
    <Text color="subtle" wrap="truncate-end">
      {`${counts}${gapBetween(counts.length + time.length, columns)}${time}`}
    </Text>
  )
}

function errorBlock({ Box, Text, t }: Parts, error: string) {
  return (
    <Box flexDirection="column">
      <Text color="error">{`✕ ${t.paneFailed}`}</Text>
      <Text color="inactive" wrap="truncate-end">
        {error}
      </Text>
      <Text color="subtle">{t.retrying(REFRESH_MS / 1000)}</Text>
    </Box>
  )
}

function emptyBlock({ Box, Text, t }: Parts) {
  return (
    <Box flexDirection="column">
      <Text color="inactive">{t.noPms}</Text>
      <Text color="inactive">{t.noPmsHint}</Text>
    </Box>
  )
}

/** The pane's dock and inline view: summary, cards by urgency, then the footer with the resting counts. */
function fullView(parts: Parts, { witches, checkedAt, error }: Circle, now: number, columns: number) {
  const { Box, Text, t } = parts
  const live = liveByUrgency(witches)
  const resting = restingLine(witches, t)
  const hasRead = checkedAt !== ''
  return (
    <Box flexDirection="column">
      {error && errorBlock(parts, error)}
      {!hasRead && !error && <Text color="inactive">{t.reading}</Text>}
      {hasRead && witches.length === 0 && emptyBlock(parts)}
      {witches.length > 0 && <Box marginTop={error ? 1 : 0}>{summaryLine(parts, live, columns)}</Box>}
      {live.length > 0 && (
        <Box flexDirection="column" marginTop={1}>
          {cards(parts, live, now, columns)}
        </Box>
      )}
      {hasRead && <Box marginTop={1}>{footerLine(parts, resting, checkedAt, columns)}</Box>}
    </Box>
  )
}

/** The desktop widget: only the summary and the time of the last read, or why there is none. */
function widgetView(parts: Parts, { witches, checkedAt, error }: Circle, columns: number) {
  const { Box, Text, t } = parts
  const hasRead = checkedAt !== ''
  return (
    <Box flexDirection="column">
      {error && <Text color="error">{`✕ ${t.paneFailed}`}</Text>}
      {!hasRead && !error && <Text color="inactive">{t.reading}</Text>}
      {hasRead && witches.length === 0 && <Text color="inactive">{t.noPms}</Text>}
      {witches.length > 0 && summaryLine(parts, liveByUrgency(witches), columns)}
      {hasRead && <Text color="subtle">{`↻ ${checkedAt}`}</Text>}
    </Box>
  )
}

/** A run of the band's one row, in one colour. */
type Piece = { text: string; color: string; isBold?: boolean }

const BAND_MARK = '☾ '
const BAND_JOINER = '  '

const piecesWidth = (pieces: readonly Piece[]): number => pieces.reduce((sum, { text }) => sum + text.length, 0)

/** One group's entry: the names for who waits or has a problem, the count for the rest. */
function bandEntry(group: Group, live: readonly Live[], isWide: boolean, t: Text): Piece {
  const { glyph, color } = GROUPS[group]
  const inGroup = live.filter(one => one.group === group)
  const names = inGroup.map(({ witch }) => witch.name).join(', ')
  if (group === 'waits') return { text: `${glyph} ${isWide ? t.bandWaits : ''}${names}`, color, isBold: true }
  if (isWide && group === 'problem') return { text: `${glyph} ${names}`, color }
  const count = inGroup.length
  return { text: `${glyph} ${isWide ? plural(count, t.summary[group]) : count}`, color }
}

/** The left side of the band: the mark, then what the circle says, in the wide or the compact form. */
function bandLeft({ witches, checkedAt, error }: Circle, isWide: boolean, t: Text): Piece[] {
  const mark: Piece = { text: BAND_MARK, color: 'suggestion' }
  if (checkedAt === '') return [mark, error ? { text: t.bandFailed, color: 'error' } : { text: t.bandReading, color: 'subtle' }]
  const live = liveByUrgency(witches)
  if (live.length === 0) return [mark, { text: t.bandNobody, color: 'subtle' }]
  const groups = URGENCY.filter(group => live.some(one => one.group === group))
  const joiner: Piece = { text: isWide ? SUMMARY_JOINER : BAND_JOINER, color: 'subtle' }
  return [mark, ...groups.flatMap((group, index) => [...(index > 0 ? [joiner] : []), bandEntry(group, live, isWide, t)])]
}

/** The time of the last good read; `✕` in place of `↻` when the read after it failed. HH:MM when compact. */
function bandTime({ checkedAt, error }: Circle, isWide: boolean): Piece | undefined {
  if (checkedAt === '') return undefined
  const time = isWide ? checkedAt : checkedAt.slice(0, 'HH:MM'.length)
  if (error) return { text: `✕ ${time}`, color: 'error' }
  return { text: isWide ? `↻ ${time}` : time, color: 'subtle' }
}

/** `pieces` cut to `width`, with `…` as the last character when something went. */
function clipPieces(pieces: readonly Piece[], width: number): Piece[] {
  const shown = cut(pieces.map(({ text }) => text).join(''), width)
  let offset = 0
  return pieces.flatMap(piece => {
    const text = shown.slice(offset, offset + piece.text.length)
    offset += piece.text.length
    return text === '' ? [] : [{ ...piece, text }]
  })
}

type Form = { left: Piece[]; time: Piece | undefined }

const formWidth = ({ left, time }: Form): number => piecesWidth(left) + (time ? time.text.length + 1 : 0)

/**
 * The band's one row. The wide form is used when it and its time fit `columns`, else the compact one;
 * when the compact one does not fit with its time the time goes, and when it does not fit alone it is cut.
 */
function bandView({ Text, t }: Parts, state: Circle, columns: number) {
  const formOf = (isWide: boolean): Form => ({ left: bandLeft(state, isWide, t), time: bandTime(state, isWide) })
  const wide = formOf(true)
  const form = formWidth(wide) <= columns ? wide : formOf(false)
  const time = formWidth(form) <= columns ? form.time : undefined
  const left = clipPieces(form.left, columns)
  const draw = ({ text, color, isBold }: Piece) => (
    <Text color={color} {...(isBold ? { bold: true } : {})}>
      {text}
    </Text>
  )
  return (
    <Text wrap="truncate-end">
      {left.map(draw)}
      {time && gapBetween(piecesWidth(left) + time.text.length, columns)}
      {time && draw(time)}
    </Text>
  )
}

export const register: Register = (on, options) => {
  const settings = settingsFrom(options)
  const { t } = settings

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'aquelarre', description: t.commandDescription })
    // A reload can leave isReading set with no read behind it.
    await update($, isReading, () => false)
    // A role session cannot run `pm status`: it never polls.
    if (!(await isRoleSession($))) {
      $.clock.every(REFRESH_MS, () => tick($, settings))
      // The band should not say it is reading for a whole period. Not awaited: `tick` never rejects, and
      // the session does not wait for the read.
      void tick($, settings)
    }
    return next(e)
  })

  on('command.run', { command: 'aquelarre' }, async $ => {
    // Open first: the pane says it is reading at once instead of after the whole read.
    const opened = await $.ui.open({ id: PANE, title: TITLE, backgroundColor: 'transparent', columns: PANE_COLUMNS })
    if (!(await isRoleSession($))) await readCircle($, settings, true)
    return { text: opened.isPlaced ? t.opened : t.noSurface }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // The band switched off, another plugin's row, a survey, and a role session (which never reads) are not the band's to draw.
    if (!settings.hasBand || e.plugin !== 'aquelarre' || e.props.hasSurvey || (await isRoleSession($))) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    return bandView({ Box, Text, t }, await read($, circle), e.props.bodyColumns)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const parts: Parts = { Box, Text, t }
    if (await isRoleSession($)) return <Text color="inactive">{t.rolePane}</Text>
    const state = await read($, circle)
    const now = await $.clock.now()
    const { placement, bodyColumns } = e.props
    return placement === 'widget' ? widgetView(parts, state, bodyColumns) : fullView(parts, state, now, bodyColumns)
  })
}
