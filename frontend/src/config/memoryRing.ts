/**
 * 回忆卡情绪色环的换算（§10.1 ① C4 第四模板；行动方案「回忆卡·情绪色环」）。
 *
 * 为什么住 config 而不是组件里：SVG 组件在 jest 的 node 环境渲染不了（见
 * __tests__/chartTransforms.test.ts 同口的 E2 先例），而「哪一段亮、什么颜色、
 * 弧怎么闭」正是会静默画错的那一层——画错不会报错，只会出一张情绪与颜色不符的卡。
 *
 * 视觉规格（2026-10-14 立项定稿，静态五段环 + 当前段高亮）：
 * ① 分段：整环五段等分，每段 72°，起始角 −90°（12 点方向）顺时针；段序号 = 情绪值 − 1，
 *    即值 1（低落）在 12 点那段、值 5（开心）在最右偏下——与折线/热力图同向增长。
 * ② 色相：情绪值线性映到 0°–120°（红→橙→黄→黄绿→绿），与既有 HEAT_COLORS /
 *    后端 MOOD_COLORS 的「低落偏红、开心偏绿」方向一致，不另造一套颜色语义。
 * ③ 高亮：当前段 opacity 1 且外半径 +2，其余四段 opacity 0.28；只亮一段，
 *    避免「五段都挺亮＝看不出哪天心情如何」。
 * ④ 无数据：当日没有签到（后端 mood=null）→ 五段一律中性灰、全部低透明，中心不放
 *    emoji。不拿别日的签到冒充当天，也不把 0 当情绪值画进环里。
 * ⑤ 静态：无入场动画（分享卡要能被截图/导出，补间帧不确定＝观感不可复现）。
 */

/** 五段等分（情绪值域 1–5，一段一档）。 */
export const RING_SEGMENTS = 5;

/** 每段圆心角（360 / 5）。 */
export const SEGMENT_DEGREES = 360 / RING_SEGMENTS;

/** 起始角：−90° 让第一段从 12 点方向开始。 */
export const START_ANGLE = -90;

/** 情绪值 → 色相：0°（红，值 1）到 120°（绿，值 5）线性。 */
export const HUE_MIN = 0;
export const HUE_STEP = 30;

/** 饱和/亮度固定，只让色相承载情绪值，避免「亮暗」被误读成强度。 */
const SATURATION = 72;
const LIGHTNESS = 55;

/** 无签到时的中性色（④）。 */
export const NEUTRAL_COLOR = 'rgba(255,255,255,0.18)';

/** 非高亮段的透明度（③）。 */
export const DIM_OPACITY = 0.28;

export interface RingSegment {
  /** 该段代表的情绪值 1–5。 */
  value: number;
  fromAngle: number;
  toAngle: number;
  /** 段配色：有情绪值时为 hsl，无数据时五段同为中性色。 */
  color: string;
  /** 是否为当前情绪所在段。 */
  active: boolean;
  opacity: number;
}

/**
 * 情绪值 → 色相。表外值（后端 MOOD_MAP 之外的 emoji、或脏数据）不猜颜色：
 * 返回 null 由调用方走中性降级。
 */
export function hueForValue(value: number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  if (!Number.isInteger(value) || value < 1 || value > RING_SEGMENTS) return null;
  return HUE_MIN + (value - 1) * HUE_STEP;
}

export function colorForValue(value: number | null | undefined): string {
  const hue = hueForValue(value);
  if (hue === null) return NEUTRAL_COLOR;
  return `hsl(${hue}, ${SATURATION}%, ${LIGHTNESS}%)`;
}

/** 极坐标 → 画布坐标（SVG y 轴向下，故用 cos 求 x、sin 求 y）。 */
export function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

/**
 * 圆环扇形（annular sector）路径：外弧顺时针、内弧逆时针回到起点。
 *
 * 大弧标志按夹角是否超过 180° 取；本环单段 72° 恒为 0，但公式不写死——
 * 将来改成四段/六段也不会画成补角。角度差为 0 时返回空串（不画退化路径）。
 */
export function annularSectorPath(
  cx: number,
  cy: number,
  rOuter: number,
  rInner: number,
  startAngle: number,
  endAngle: number,
): string {
  const sweep = endAngle - startAngle;
  if (sweep <= 0) return '';
  const large = sweep > 180 ? 1 : 0;
  const oStart = polarToCartesian(cx, cy, rOuter, startAngle);
  const oEnd = polarToCartesian(cx, cy, rOuter, endAngle);
  const iEnd = polarToCartesian(cx, cy, rInner, endAngle);
  const iStart = polarToCartesian(cx, cy, rInner, startAngle);
  return [
    `M ${oStart.x.toFixed(3)} ${oStart.y.toFixed(3)}`,
    `A ${rOuter} ${rOuter} 0 ${large} 1 ${oEnd.x.toFixed(3)} ${oEnd.y.toFixed(3)}`,
    `L ${iEnd.x.toFixed(3)} ${iEnd.y.toFixed(3)}`,
    `A ${rInner} ${rInner} 0 ${large} 0 ${iStart.x.toFixed(3)} ${iStart.y.toFixed(3)}`,
    'Z',
  ].join(' ');
}

/**
 * 五段环的分段描述：value=null 或表外值 → 五段全中性（④）。
 */
export function ringSegments(value: number | null | undefined): RingSegment[] {
  const hue = hueForValue(value);
  return Array.from({ length: RING_SEGMENTS }, (_, i) => {
    const segValue = i + 1;
    const active = hue !== null && segValue === value;
    return {
      value: segValue,
      fromAngle: START_ANGLE + i * SEGMENT_DEGREES,
      toAngle: START_ANGLE + (i + 1) * SEGMENT_DEGREES,
      color: hue === null ? NEUTRAL_COLOR : colorForValue(segValue),
      active,
      opacity: hue === null ? DIM_OPACITY : active ? 1 : DIM_OPACITY,
    };
  });
}

/** 高亮段序号（找不到即 -1，调用方据此决定中心放不放 emoji）。 */
export function activeSegmentIndex(value: number | null | undefined): number {
  return hueForValue(value) === null ? -1 : (value as number) - 1;
}

/**
 * 回忆卡的「日期大字」：年份 = 服务端下发的「今天」年份 − years_ago，月日沿用今天。
 *
 * 不碰 candidate.created_at：那是 UTC 落库时刻，按设备时区格式化会把「几年前的今天」
 * 画成差一天（同 dailyCards.ts 的周年口径——周年判定只认服务端按用户时区算出的值）。
 * 服务端已按同一本地日界取候选，所以「今天」的月日与原话那天的月日必然相同。
 */
export function memoryCardDate(today: string, yearsAgo: number): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(today ?? '');
  if (!match) return null;
  const year = Number(match[1]) - yearsAgo;
  if (!Number.isInteger(yearsAgo) || yearsAgo < 1) return null;
  return `${year}.${match[2]}.${match[3]}`;
}

/** 卡片正文摘录：超长截断在换行前收口，空段不造假行。 */
export const MEMORY_CARD_EXCERPT_MAX = 220;

export function memoryCardExcerpt(content: string | null | undefined): string {
  const text = (content ?? '').trim();
  if (text.length <= MEMORY_CARD_EXCERPT_MAX) return text;
  return `${text.slice(0, MEMORY_CARD_EXCERPT_MAX).trimEnd()}…`;
}

/**
 * 回忆卡的分享文本：原话 + 带本人推荐码的深链（与首页考古卡同一段口径）。
 *
 * 清单（ShareManifest）与实际发出去的内容必须同源，见 trustPresentation.test.ts。
 */
export function memoryCardShareMessage(content: string, link: string): string {
  return [content, link].map((s) => (s ?? '').trim()).filter(Boolean).join('\n\n');
}
