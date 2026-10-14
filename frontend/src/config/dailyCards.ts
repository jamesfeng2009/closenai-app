/**
 * 首页每日卡与睡前复盘的取数判定（C4 记忆考古 / C2 本周主题 / C1 睡前复盘）。
 *
 * 为什么独立成 config 而不是写在 HomeScreen 里：这三条判断全是「什么时候出哪张卡、
 * 出哪一条」的硬约定，失效方式都是改一行条件就静默错卡（比如把已读卡又端上来），
 * 而 jest 跑在 node 环境下渲染不了 RN 组件（见 jest.config.js testEnvironment），
 * 只有纯函数能被断言。同 M5 偏离 2/3 的先例。
 *
 * 日期口径：周年、revealed、当日窗口全部由服务端按**用户注册时区**判定后下发
 * （years_ago / revealed / bedtime 的 date 字段），本模块一律不自己算今天——
 * 设备时区 ≠ 注册时区时两端各算一份必然分叉（同 getYearInReview 的缺省年份理由）。
 */
import type {
  BedtimeCrisis, BedtimeRecord, BedtimeStepResponse,
  CurriculumDayCard, OnThisDayCandidate,
} from '../types';

// ═══ 记忆考古 · 首页每日主位（§10.1 ①）═══

/**
 * 首页主位一次最多端几张候选。
 *
 * 服务端每年最多给 5 条、近 3 年（`ARCHAEOLOGY_MAX_YEARS`），全量最多 15 条——
 * 首页卡片不是列表页，超出的留给后续的记忆页，不在这儿铺满屏。
 */
export const ARCHIVE_CARD_LIMIT = 3;

/** 卡片可见候选：保持服务端顺序（年份升序，1 年前优先），丢弃空白原话。 */
export function archiveCards(candidates: OnThisDayCandidate[]): OnThisDayCandidate[] {
  return candidates
    .filter((c) => !!c.content && !!c.content.trim() && !!c.memory_id)
    .slice(0, ARCHIVE_CARD_LIMIT);
}

/**
 * 周年标题的文案键。
 *
 * 「1 years ago」在 en/de/es/… 是硬伤，中文与单形态语言却要统一一句，
 * 所以调用方传 `count: years_ago` 交给 i18next 的 CLDR 复数选档，
 * 而不是前端自己拼「1 年」/「N 年」两套串。
 */
export const ARCHIVE_TITLE_KEY = 'home.archiveTitle';

/** 分享文本：原话 + 带本人推荐码的深链，两段空行分隔；空段丢弃不造假行。 */
export function archiveShareMessage(content: string, link: string): string {
  return [content, link].map((s) => (s ?? '').trim()).filter(Boolean).join('\n\n');
}

// ═══ Growth Curriculum · 首页次位（§5.17）═══

/** 一周槽位数（后端 WEEK_DAYS 同口径：days 里未生成的天不出行，所以要补齐成 7 格）。 */
export const WEEK_SLOTS = 7;

/**
 * 次位卡取哪一张：已揭晓里日期最大的那张未读卡。
 *
 * 不比较「是不是今天」：revealed 已由服务端按用户时区判过，取 max(date) 就是
 * 今天或最近一天的卡。全周读完 → null（卡片隐藏，不端旧卡骚扰）。
 */
export function pickThemeCard(days: CurriculumDayCard[]): CurriculumDayCard | null {
  const unread = days.filter((d) => d.revealed && !d.read && !!d.id);
  if (unread.length === 0) return null;
  return unread.reduce((acc, d) => (d.date > acc.date ? d : acc));
}

/**
 * 周内序（0–6，周一为 0）。
 *
 * 后端 day_no = ISO 周序*7 + 周内序（curriculum_service.get_week 用 `day_no % WEEK_DAYS`
 * 还原槽位），前端读同一份编码而不是自己按 date 算周——两端各算一份迟早分叉。
 */
export function weekSlot(dayNo: number): number {
  return ((dayNo % WEEK_SLOTS) + WEEK_SLOTS) % WEEK_SLOTS;
}

/** 7 格进度：read=已读、unread=已揭晓未读、future=未来日或未生成。 */
export type WeekCell = 'read' | 'unread' | 'future';

export function weekCells(days: CurriculumDayCard[]): WeekCell[] {
  const bySlot = new Map<number, CurriculumDayCard>();
  days.forEach((d) => {
    const slot = weekSlot(d.day_no);
    const prev = bySlot.get(slot);
    // 同槽位后写覆盖前写（与 get_week 的 by_day[idx] = e 一致）
    if (!prev || d.date >= prev.date) bySlot.set(slot, d);
  });
  return Array.from({ length: WEEK_SLOTS }, (_, slot) => {
    const card = bySlot.get(slot);
    if (!card || !card.revealed) return 'future';
    return card.read ? 'read' : 'unread';
  });
}

// ═══ 睡前复盘 · 首页晚间卡与三步流程（§5.16 S1 / UI Screen 26）═══

/** 首页兜底卡起点：本地 22:00（UI 原型 Screen 26 设计规范「固定时点 22:00–23:30」）。 */
export const BEDTIME_CARD_START_MINUTES = 22 * 60;

/** 危机分支：出参无 step 键，只有 intercepted/crisis——不判型就会把转介当 step1 成功。 */
export function isCrisisResponse(
  res: BedtimeStepResponse,
): res is { intercepted: true; crisis: BedtimeCrisis } {
  return (res as { intercepted?: boolean }).intercepted === true;
}

/**
 * 复盘页阶段。
 *
 * 「半程恢复」是这条流程唯一的复杂点：中途退出再进来不能从第 1 步重头走——
 * 重发 step1 会把用户上一句原话覆盖掉（服务端「改口覆盖」是有意行为），
 * 所以按已落库的最深一步续接。
 */
export type BedtimePhase = 'step1' | 'step2' | 'step3' | 'done';

export function resumePhase(record: BedtimeRecord | null): BedtimePhase {
  const steps = record?.steps ?? {};
  if (steps.step3 || record?.completed) return 'done';
  if (steps.step2) return 'step3';
  if (steps.step1) return 'step2';
  return 'step1';
}

/** 提交成功后的下一阶段（危机分支由调用方先判 isCrisisResponse）。 */
export function nextPhase(current: BedtimePhase, res: BedtimeStepResponse): BedtimePhase {
  if (isCrisisResponse(res)) return current; // 拦截即停在原地，输入框内容留给用户改
  if ('step' in res) {
    if (res.step === 1) return 'step2';
    if (res.step === 2) return 'step3';
    return 'done';
  }
  return current;
}

/**
 * 首页晚间兜底卡是否出（「未授权推送则显示为首页顶部卡片」的兜底面）。
 *
 * 只看两件事：本地时刻进窗 + 今日未完成。已完成后整卡消失——晚间重复打扰的闸门
 * 就是完成态本身，不依赖推送授权状态（前端只有系统级 permission，判不出这条
 * 推送是否真送达，见 src/services/push.ts 只落库不发通知）。
 * minutes 由调用方按设备本地时刻传入：跨午夜后当日窗口结束，复盘自动归为昨日。
 */
export function showBedtimeCard(
  minutesOfDay: number,
  completedToday: boolean,
): boolean {
  return minutesOfDay >= BEDTIME_CARD_START_MINUTES && !completedToday;
}
