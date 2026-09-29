/**
 * 第一周剧本（P1-B02 · PRD §5.19.3）展示常量与取数判定。
 *
 * 独立于 Week1ReportScreen 组件，便于在 node 环境（jest）直接断言：时间线该列哪些
 * 事件、报告什么时候算「已投递」，都是重复打开页面才会暴露的硬约定。
 */
import type { JourneyEvent } from '../types';

/**
 * 时刻表展示的 6 类事件，即后端 `models/journey.py:JOURNEY_EVENT_KINDS` 的 day* 子集。
 *
 * 不能按 day_no 过滤：level_reward 的 day_no 也是 7（L2 门槛 = 第 7 天），但它没有
 * `journey.day.<kind>` 文案键，进时间线会直接把键名渲染给用户。
 */
export const JOURNEY_TIMELINE_KINDS = [
  'day0_read',
  'day1_checkin_echo',
  'day2_first_outreach',
  'day3_weak_assert',
  'day5_stat',
  'day7_report',
] as const;

/** 已投递：sent 是刚送达，seen 是红点已被 acknowledge 消费过——两者内容都在。 */
const DELIVERED: readonly string[] = ['sent', 'seen'];

/** D7 报告可否展示。二次打开时事件已被回执成 seen，仍必须能读到报告。 */
export function getReport(events: JourneyEvent[]): JourneyEvent | null {
  const report = events.find((e) => e.event_kind === 'day7_report');
  return report && DELIVERED.includes(report.status) ? report : null;
}

/** 时间线行：有文案键的剧本事件，按剧本日序排列。 */
export function getTimeline(events: JourneyEvent[]): JourneyEvent[] {
  return events
    .filter((e) => (JOURNEY_TIMELINE_KINDS as readonly string[]).includes(e.event_kind))
    .slice()
    .sort((a, b) => a.day_no - b.day_no);
}

/** 进入页面时需要回执的事件（清除红点）。 */
export function getAckPending(events: JourneyEvent[]): JourneyEvent[] {
  return events.filter((e) => DELIVERED.includes(e.status) && !e.acknowledged);
}
