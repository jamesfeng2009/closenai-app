/**
 * 三层次情绪分析的前端纯逻辑（KG-28）。
 *
 * 和 `reflectionPrompts.ts` 同一条理由单独成 `.ts`：`.tsx` 在本机 jest 的 node 环境里
 * 渲染不了，判定留在组件里就等于没有测试。这里放的是**会判错人**的那几格：
 * 入口开不开、四种业务码各该怎么答、额度怎么读、「这不像我」正文怎么拼、埋点能带哪些键。
 *
 * 三档的参数（窗口天数／最少几条证据／最多引几条原文）在这里写了一份，
 * 真值在 `backend/services/mood_analysis_service.py`。两边各写各的就会出现
 * 「前端告诉用户深潜要看 2 条原文，后端按 3 条判降级」——`moodAnalysis.test.ts`
 * 拿后端源码里的字面量跟这份表对，改了后端没改前端（或反之）当场报红。
 */
import type { GrowthFeatures, MoodAnalysisDepth, MoodAnalysisQuota } from '../types';

/** 展示顺序＝由浅入深；不许出现第四个值（后端 Literal 只认这三个）。 */
export const DEPTH_ORDER: MoodAnalysisDepth[] = ['light', 'explore', 'deep'];

export interface DepthSpec {
  key: MoodAnalysisDepth;
  /** 服务端判「证据够不够」用的最小逐字引用条数（light 为 0：它不出结论）。 */
  minEvidence: number;
  /** 喂给模型的上下文窗口（天）。 */
  windowDays: number;
  /** 窗口内最多取几段原文。 */
  maxMaterials: number;
  /** 最多给几条替代解释。 */
  maxAlternatives: number;
}

export const DEPTH_SPECS: Record<MoodAnalysisDepth, DepthSpec> = {
  light: { key: 'light', minEvidence: 0, windowDays: 1, maxMaterials: 3, maxAlternatives: 0 },
  explore: { key: 'explore', minEvidence: 1, windowDays: 7, maxMaterials: 8, maxAlternatives: 1 },
  deep: { key: 'deep', minEvidence: 2, windowDays: 30, maxMaterials: 20, maxAlternatives: 2 },
};

export function depthSpec(depth: MoodAnalysisDepth): DepthSpec {
  return DEPTH_SPECS[depth];
}

/** 只认后端 Literal 里的三个值；其余（含 undefined）一律回落 light。 */
export function asDepth(value: unknown): MoodAnalysisDepth {
  return DEPTH_ORDER.includes(value as MoodAnalysisDepth) ? (value as MoodAnalysisDepth) : 'light';
}

/**
 * 入口可见性＝服务端开关快照，不看本地缓存里那句旧值。
 *
 * `=== true` 而不是真值判断：仪表盘有 24h 本地缓存，升级前落盘的那份**没有** `features`，
 * `undefined` 必须判「关」——否则开关关着（端点 40402）时入口还在，用户点了才报错。
 */
export function showMoodAnalysisEntry(features?: GrowthFeatures | null): boolean {
  return features?.mood_analysis_depths === true;
}

// ═══ 业务错误码 → 用户答复 ═══

/**
 * `not_available` 与 `no_checkin` 在后端是**同一个码**（40402）：
 * 开关关时端点在业务判定之前就 40402，避免暴露功能存在。
 * 前端不区分——它只在 `features.mood_analysis_depths` 为真时才把入口放出去，
 * 所以真走到这一格的 40402 实际含义就是「今天还没签到」。
 */
export type MoodAnalysisErrorKind =
  | 'invalid'
  | 'consent'
  | 'quota'
  | 'no_checkin'
  | 'network';

const CODE_TO_KIND: Record<number, MoodAnalysisErrorKind> = {
  40001: 'invalid',
  40301: 'quota',
  40303: 'consent',
  40402: 'no_checkin',
};

export function errorKindForCode(code: unknown): MoodAnalysisErrorKind {
  if (typeof code === 'number' && code in CODE_TO_KIND) return CODE_TO_KIND[code];
  return 'network';
}

/**
 * 可重试的只有「这次请求没成」：网络／未知。
 *
 * 额度、未同意、没签到这三格重试是同一条拒绝，只会让用户以为按钮坏了；
 * 40001 是请求体本身不对，重试也是白试。
 */
export function canRetryAfter(kind: MoodAnalysisErrorKind): boolean {
  return kind === 'network';
}

/** 需要跳出面板去办的两格（去同意／先去签到）；其余留在原地读文案即可。 */
export function isActionableElsewhere(kind: MoodAnalysisErrorKind): boolean {
  return kind === 'consent' || kind === 'quota' || kind === 'no_checkin';
}

// ═══ 额度读数 ═══

/** 剩余次数；服务端危机出口不给计数器，那就返回 null（渲染侧据此**不显示**额度行）。 */
export function quotaRemaining(quota?: MoodAnalysisQuota | null): number | null {
  if (!quota) return null;
  return Math.max(quota.limit - quota.used, 0);
}

/** null（没给计数器）不算耗尽——危机出口本来就没有额度这回事。 */
export function isQuotaExhausted(quota?: MoodAnalysisQuota | null): boolean {
  if (!quota) return false;
  return quota.used >= quota.limit;
}

// ═══ 「这不像我」→ 存为反思 ═══

/**
 * 纠正正文＝`前缀 + 用户自己写的话`。
 *
 * 前缀**不裁尾空格**：拉丁语系的分隔靠文案里那一个空格（"This isn't me: "），
 * 中文／日文用全角冒号且不该有空格（"这不像我："）。所以分隔符是**每个语言自己定的**，
 * 代码不猜——`moodAnalysis.test.ts` 逐语言检查前缀必须以半角或全角冒号（后跟或不跟空格）收尾。
 *
 * 用户一个字都没写时返回 null：只含前缀的句子会真的存进成长记录、进时间线计数、
 * 进周报「你写了 N 条」，而它不是用户的任何表达（同 KG-26 的空内容闸）。
 */
export function correctionContent(prefix: string, raw: string): string | null {
  const body = (raw || '').trim();
  if (body.length === 0) return null;
  return `${prefix}${body}`;
}

/** 结果区「保存为反思」的正文：取服务端那句解释，但必须是用户已读过的 summary/pattern。 */
export function savableAnalysisText(summary: string | null | undefined): string | null {
  const text = (summary || '').trim();
  return text.length > 0 ? text : null;
}

// ═══ 埋点 ═══

export const MOOD_ANALYSIS_EVENTS = {
  entry: 'mood_analysis_entry',
  requested: 'mood_analysis_requested',
  completed: 'mood_analysis_completed',
  evidenceOpened: 'mood_analysis_evidence_opened',
  corrected: 'mood_analysis_corrected',
  saved: 'mood_analysis_saved',
} as const;

/** 白名单外的键一律不组：属性里出现正文/excerpt 就等于把用户原话送上分析面。 */
export interface AnalysisTrackProps {
  depth?: MoodAnalysisDepth;
  result?: string;
  durationMs?: number;
  count?: number;
  reason?: string;
}

const PROPERTY_BY_KEY: Record<keyof AnalysisTrackProps, string> = {
  depth: 'action',
  result: 'result',
  durationMs: 'duration_ms',
  count: 'count',
  reason: 'reason',
};

export function analysisTrackProps(
  props: AnalysisTrackProps,
  feature: string = 'mood_analysis'
): Record<string, unknown> {
  const out: Record<string, unknown> = { feature };
  for (const [key, wireName] of Object.entries(PROPERTY_BY_KEY) as [keyof AnalysisTrackProps, string][]) {
    const value = props[key];
    if (value !== undefined && value !== null) out[wireName] = value;
  }
  return out;
}

/** 结果三态里的那个 `result` 值（与分析响应的 safety_status 同名，不另起一套词）。 */
export function resultOf(safetyStatus: string | null | undefined): string {
  return safetyStatus === 'ok' || safetyStatus === 'degraded' || safetyStatus === 'crisis'
    ? safetyStatus
    : 'failed';
}
