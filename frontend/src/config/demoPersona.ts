/**
 * M5 演示档案（P1-1 · 架构方案 §二 M5）：虚构人物"小 A"的 30 天数据。
 * 纯前端、零后端、零 LLM。
 *
 * 为什么是"生成"而不是手写 JSON：演示里的每个数字都会被印到断言卡上
 * （「连续 {{weeks}} 周的周五」）。手写数据 + 手写文案迟早对不上，而商店截图
 * （护城河 §6.1）用的就是这张卡。这里 mood 序列由 weekday 基线确定性生成，
 * 断言参数一律从序列反算，于是 __tests__/demoPersona.test.ts 能断言
 * "文案引用的数字 == 数据算出来的数字"。
 *
 * 隔离：本模块不 import api / services / context，也不读 AsyncStorage——
 * 只导出纯常量与纯函数。演示态没有账号，任何请求都会 401，
 * 所以"结构上不可能发请求"比 is_demo 字段更硬（偏离说明见 tasks.md）。
 */

/** 演示窗口天数（护城河 §6.2：「小 A」30 天）。 */
export const DEMO_WINDOW_DAYS = 30;

/** 人物名走 i18n（zh「小 A」/ 其它语言转写），数据层只留键名。 */
export const DEMO_PERSONA_KEY = 'demo.personaName';

const SUNDAY = 0;
const TUESDAY = 2;
const THURSDAY = 4;
const FRIDAY = 5;
const SATURDAY = 6;

/** 未签到留白的个数。 */
const MISS_COUNT = 4;

/** 最多取几条「写了算了、第二天更低」的样本。 */
const MAX_HOLD_IN_SAMPLES = 3;

export type DemoMemoryTag = 'conflict' | 'win' | 'value';

export interface DemoDay {
  /** 窗口内第几天，1..30。断言卡的「依据」用它，避免依赖任何 locale 日期格式。 */
  dayNo: number;
  /** YYYY-MM-DD（本地日，由调用方传入的 today 反推）。 */
  date: string;
  /** 0=周日 … 5=周五。 */
  weekday: number;
  /** 1 低落 … 5 开心；0 = 当天没签到。 */
  mood: number;
}

export interface DemoMemory {
  id: string;
  /** i18n 键（demo.memory.*）：原话必须随语言走，否则 15 语言里只能显示英文。 */
  textKey: string;
  tag: DemoMemoryTag;
  dayNo: number;
  date: string;
}

export interface DemoPattern {
  id: string;
  textKey: string;
  /** 传给 i18next 的参数；每个值都能由 days 反算出来。 */
  params: Record<string, string | number>;
  /** 断言依据（窗口日序），渲染成「依据：第 3 · 10 · 24 天」。 */
  evidence: number[];
}

export interface DemoTimelineItem {
  id: string;
  textKey: string;
  dayNo: number;
  date: string;
}

export interface DemoStats {
  days: number;
  checkins: number;
  moodAvg: number;
  /** 最长连续签到天数。 */
  streak: number;
  bestMood: number;
  weekendAvg: number;
  weekdayAvg: number;
}

export interface DemoArchive {
  personaKey: string;
  days: DemoDay[];
  stats: DemoStats;
  patterns: DemoPattern[];
  memories: DemoMemory[];
  timeline: DemoTimelineItem[];
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

function parseIso(date: string): { y: number; m: number; d: number } {
  const parts = date.split('-').map(Number);
  return { y: parts[0], m: parts[1], d: parts[2] };
}

function toIso(y: number, m: number, d: number): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${y}-${pad(m)}-${pad(d)}`;
}

/** 按天偏移（UTC 日历运算，不受宿主时区/夏令时影响）。 */
function shiftDay(date: string, delta: number): string {
  const { y, m, d } = parseIso(date);
  const dt = new Date(Date.UTC(y, m - 1, d + delta));
  return toIso(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate());
}

function weekdayOf(date: string): number {
  const { y, m, d } = parseIso(date);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

/**
 * 未签到留白：落在周二/周四，隔一个取一个 → 恰好 4 个、间隔 2–5 天。
 *
 * 不按固定 dayNo 偏移：30 天窗口里步长 7 会让留白全挤进同一个工作日，
 * 撞周五还会把「连续 N 周周五低落」的 weeks 打断。
 */
function pickMissDayNos(days: DemoDay[]): Set<number> {
  const midweek = days.filter((d) => d.weekday === TUESDAY || d.weekday === THURSDAY);
  return new Set(midweek.filter((_, i) => i % 2 === 0).slice(0, MISS_COUNT).map((d) => d.dayNo));
}

/**
 * 情绪序列。规则（全部可被测试反算）：
 * - 周五：1 或 2，一律全周最低 → 撑起「连续 N 周周五低落」
 * - 周六/周日：恒 4 → 保证 weekendAvg 严格高于 weekdayAvg
 * - 周一~周四：3 ± 周期抖动（周期 4，与星期周期 7 互质，不会锁死成同一形状）
 */
function buildDays(today: string): DemoDay[] {
  const dated: DemoDay[] = [];
  for (let i = 0; i < DEMO_WINDOW_DAYS; i += 1) {
    const date = shiftDay(today, -(DEMO_WINDOW_DAYS - 1 - i));
    const weekday = weekdayOf(date);
    const dayNo = i + 1;
    let mood: number;
    if (weekday === FRIDAY) mood = 1 + (dayNo % 2);
    else if (weekday === SATURDAY || weekday === SUNDAY) mood = 4;
    else mood = clamp(3 + (((dayNo * 5) % 4) - 1), 1, 5);
    dated.push({ dayNo, date, weekday, mood });
  }
  const misses = pickMissDayNos(dated);
  return dated.map((d) => (misses.has(d.dayNo) ? { ...d, mood: 0 } : d));
}

/** 窗口末尾起，连续多少个周五情绪 ≤2。 */
function consecutiveLowFridays(days: DemoDay[]): number {
  let weeks = 0;
  for (let i = days.length - 1; i >= 0; i -= 1) {
    const d = days[i];
    if (d.weekday !== FRIDAY) continue;
    if (d.mood >= 1 && d.mood <= 2) weeks += 1;
    else break;
  }
  return weeks;
}

/** 当天还愿意写、次日明显更低的日子——「算了」那条断言的样本。 */
function holdInDays(days: DemoDay[]): DemoDay[] {
  const picked: DemoDay[] = [];
  for (let i = 0; i < days.length - 1 && picked.length < MAX_HOLD_IN_SAMPLES; i += 1) {
    const day = days[i];
    const next = days[i + 1];
    if (day.mood < 3 || next.mood === 0 || next.mood >= day.mood) continue;
    if (picked.some((p) => Math.abs(p.dayNo - day.dayNo) < 5)) continue;
    picked.push(day);
  }
  return picked;
}

function buildStats(days: DemoDay[]): DemoStats {
  const checked = days.filter((d) => d.mood > 0);
  const weekend = checked.filter((d) => d.weekday === SATURDAY || d.weekday === SUNDAY);
  const weekday = checked.filter((d) => d.weekday !== SATURDAY && d.weekday !== SUNDAY);
  const avg = (list: DemoDay[]) =>
    list.length === 0 ? 0 : round1(list.reduce((s, d) => s + d.mood, 0) / list.length);

  let streak = 0;
  let run = 0;
  for (const d of days) {
    run = d.mood > 0 ? run + 1 : 0;
    streak = Math.max(streak, run);
  }

  return {
    days: days.length,
    checkins: checked.length,
    moodAvg: avg(checked),
    streak,
    bestMood: checked.reduce((m, d) => Math.max(m, d.mood), 0),
    weekendAvg: avg(weekend),
    weekdayAvg: avg(weekday),
  };
}

/** 3 条 Pattern：数字全部来自 days，不从 stats 之外的第二事实源取。 */
function buildPatterns(days: DemoDay[], stats: DemoStats): DemoPattern[] {
  const patterns: DemoPattern[] = [];

  const weeks = consecutiveLowFridays(days);
  const fridays = days.filter((d) => d.weekday === FRIDAY && d.mood > 0 && d.mood <= 2);
  if (weeks >= 3) {
    patterns.push({
      id: 'friday_dip',
      textKey: 'demo.patternFriday',
      params: { weeks },
      evidence: fridays.map((d) => d.dayNo),
    });
  }

  const holds = holdInDays(days);
  if (holds.length >= 2) {
    patterns.push({
      id: 'hold_it_in',
      textKey: 'demo.patternHoldIn',
      params: { n: holds.length },
      evidence: holds.map((d) => d.dayNo),
    });
  }

  const delta = round1(stats.weekendAvg - stats.weekdayAvg);
  if (delta > 0) {
    patterns.push({
      id: 'weekend_lift',
      textKey: 'demo.patternWeekend',
      params: { delta: delta.toFixed(1) },
      evidence: days.filter((d) => d.mood > 0 && (d.weekday === SATURDAY || d.weekday === SUNDAY)).map((d) => d.dayNo),
    });
  }

  return patterns;
}

const CONFLICT_TEXT_KEYS = ['demo.memory.conflict1', 'demo.memory.conflict2', 'demo.memory.conflict3'];

function memory(id: string, textKey: string, tag: DemoMemoryTag, day: DemoDay): DemoMemory {
  return { id, textKey, tag, dayNo: day.dayNo, date: day.date };
}

/** 5 条原话：3 条「算了」（与 hold_in 断言同批日期）+ 1 条高光 + 1 条价值观。 */
function buildMemories(days: DemoDay[]): DemoMemory[] {
  const byDayNo = new Map(days.map((d) => [d.dayNo, d]));
  const holds = holdInDays(days);
  const holdDayNos = new Set(holds.map((d) => d.dayNo));
  // 高光日不能和「算了」撞同一天：同一天既写「算了」又写"成了"，截图一看就是假数据
  const best = days
    .filter((d) => d.mood > 0 && !holdDayNos.has(d.dayNo) && d.dayNo !== 1)
    .slice()
    .sort((a, b) => b.mood - a.mood || b.dayNo - a.dayNo)[0];
  const valueDay = byDayNo.get(1) ?? days[0];

  const list = holds.map((d, i) => memory(`m${i + 1}`, CONFLICT_TEXT_KEYS[i % 3], 'conflict', d));
  if (best) list.push(memory('mbest', 'demo.memory.win', 'win', best));
  list.push(memory('mvalue', 'demo.memory.value', 'value', valueDay));
  return list;
}

/** 2 条时间线卡片：高光那天 + 一条「算了」的当天（PRD：2 条时间线卡片）。 */
function buildTimeline(days: DemoDay[], memories: DemoMemory[]): DemoTimelineItem[] {
  const byDayNo = new Map(days.map((d) => [d.dayNo, d]));
  const win = memories.find((m) => m.tag === 'win');
  const first = memories.find((m) => m.tag === 'conflict') ?? win;
  const items: DemoTimelineItem[] = [];
  if (win) items.push({ id: 't1', textKey: 'demo.timeline.win', dayNo: win.dayNo, date: win.date });
  if (first && first.dayNo !== win?.dayNo) {
    items.push({ id: 't2', textKey: 'demo.timeline.low', dayNo: first.dayNo, date: first.date });
  } else {
    const anchor = byDayNo.get(1) ?? days[0];
    items.push({ id: 't2', textKey: 'demo.timeline.start', dayNo: anchor.dayNo, date: anchor.date });
  }
  return items;
}

/** 「看完率」触底容差：差 32px 就算看完，避免最后一屏因亚像素误差永不触发。 */
export const SCROLL_END_EPSILON = 32;

/**
 * 「看完率」判定：滚动视口底边是否已触到内容底部（PRD P1-1 第 5 条的三个指标之一）。
 *
 * 抽成纯函数的唯一原因：这段数学留在组件里就只能靠真机划一下才知道对不对，
 * 而 jest 的 testMatch 不收 .tsx，所以它必须待在这个零依赖模块里才能被断言。
 * RN 0.74 的 NativeScrollEvent 字段名是 contentSize（更高版本才加出
 * contentContainerSize），这里按结构取参数、不 import RN 类型。
 */
export function isScrollViewAtEnd(event: {
  layoutMeasurement: { height: number };
  contentOffset: { y: number };
  contentSize: { height: number };
}): boolean {
  const { layoutMeasurement, contentOffset, contentSize } = event;
  return layoutMeasurement.height + contentOffset.y + SCROLL_END_EPSILON >= contentSize.height;
}

/**
 * 组装演示档案。
 *
 * @param today YYYY-MM-DD，窗口最后一天；默认取设备本地今天，
 *              测试传固定值以断言"同一天两次构建完全相等"。
 */
export function buildDemoArchive(today?: string): DemoArchive {
  const anchor = today ?? toIso(new Date().getFullYear(), new Date().getMonth() + 1, new Date().getDate());
  const days = buildDays(anchor);
  const stats = buildStats(days);
  const memories = buildMemories(days);
  return {
    personaKey: DEMO_PERSONA_KEY,
    days,
    stats,
    patterns: buildPatterns(days, stats),
    memories,
    timeline: buildTimeline(days, memories),
  };
}
