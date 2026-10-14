/**
 * §5.19.5 获客漏斗埋点（H3）。
 *
 * PRD 的五段漏斗：初读卡 → 第一条记录 → 首次签到 → 第一周报告 → 分享。
 * 加本模块前全站只有 8 个 track() 事件，中间四段没有专属事件，段间转化率算不出来。
 *
 * 口径：
 * - 前四段是**一次性**事件（一台设备只报第一次达成）。签到/发消息每天都可能重复，
 *   不门闸的话分母会被 DAU 波动淹没，转化率就没有可读性。
 * - share_tapped / share_completed 不一次性：分享次数是病毒环路的分子，要按物料累计。
 * - source 命名的是「哪张卡」，PostHog 侧按它拆开才知道哪类物料带来安装。
 *
 * 属性只用 feature / source —— 双端白名单都收这两个键
 * （前端 analytics.ts ALLOWED_PROPERTY_KEYS、后端 analytics_service.py 同名常量）。
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

import { track } from './analytics';

/** 一次性节点：漏斗的四段台阶。 */
export const ONE_TIME_STEPS = [
  'first_read_opened',
  'first_record_created',
  'first_checkin',
  'week1_report_opened',
] as const;

/** 分享环路事件：可重复，按 source 分物料。 */
export const SHARE_STEPS = ['share_tapped', 'share_completed'] as const;

export type OneTimeStep = (typeof ONE_TIME_STEPS)[number];
export type ShareStep = (typeof SHARE_STEPS)[number];
export type FunnelStep = OneTimeStep | ShareStep;

/** 分享物料来源。字面量收敛在常量里，避免各屏随手写出 'card' / 'share_card' 两套口径。 */
export const FUNNEL_SOURCES = [
  'first_read',
  'personality_card',
  'period_report',
  'year_review',
  'memory',
  'life_archive',
] as const;

export type FunnelSource = (typeof FUNNEL_SOURCES)[number];

export function funnelEventName(step: FunnelStep): string {
  return `funnel_${step}`;
}

export function funnelProperties(source?: FunnelSource): { feature: 'funnel'; source?: FunnelSource } {
  return source ? { feature: 'funnel', source } : { feature: 'funnel' };
}

/** 一次性门闸的本地键：杀进程重开也不能重复上报，所以不能用内存标志。 */
export function onceKey(step: OneTimeStep): string {
  return `@closenai/funnel_once_${step}`;
}

/** 记录一次性节点；已报过则返回 false 且不入队。Storage 抖动时按「未报过」处理（宁可少口径也不丢整个节点）。 */
export async function trackFunnelOnce(step: OneTimeStep): Promise<boolean> {
  try {
    if (await AsyncStorage.getItem(onceKey(step))) return false;
  } catch {
    // 读失败继续上报
  }
  track(funnelEventName(step), funnelProperties());
  try {
    await AsyncStorage.setItem(onceKey(step), new Date().toISOString());
  } catch {
    // 写失败：下次可能重复上报一条，比整个节点永久哑火好
  }
  return true;
}

/** 记录分享环路事件（同一设备可多次，按物料 source 区分）。 */
export function trackFunnelShare(step: ShareStep, source: FunnelSource): void {
  track(funnelEventName(step), funnelProperties(source));
}
