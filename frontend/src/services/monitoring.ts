/**
 * 崩溃监控（Sentry）。
 *
 * 配置约定与 api.ts:36 的 API_BASE 同一套：本项目没有 dotenv / expo-constants，
 * 所以 DSN 是模块内单一常量，**留空 = 整个模块 no-op**（不 init、不发请求、不抛错）。
 * DSN 是客户端公开值（Sentry 的 public key），不是密钥，可以进仓库；
 * 未申请 Sentry 项目前保持空串即可，代码路径与未接 SDK 完全一致。
 *
 * 三条隐私硬约束（这个 App 存的是用户最私密的自我觉察数据，比一般 App 的崩溃上报多一层风险）：
 * 1. 不绑定身份：全程不调用 setUser，sendDefaultPii 保持默认 false，
 *    崩溃报告与「哪位用户的哪段反思」不可关联。
 * 2. 用户内容不出端：console breadcrumb 整条丢弃（业务里的 console 可能带对话/反思原文），
 *    http breadcrumb 去掉 query string 与请求/响应体。
 * 3. extra 一律清空：setExtra 是最容易把对象整个塞进报告的入口。
 *
 * 为什么脱敏要在 beforeSend 再做一遍：enableNative 为 true 时 breadcrumb 由原生侧
 * 直接拼进事件，不一定经过 JS 的 beforeBreadcrumb 钩子。
 */

import * as Sentry from '@sentry/react-native';
import type { Breadcrumb, Event } from '@sentry/react-native';

/** Sentry 项目 DSN；留空即整个模块 no-op。 */
export const SENTRY_DSN = '';

/** 不上传的 breadcrumb 类别（console 可能带用户原文）。 */
export const DROPPED_BREADCRUMB_CATEGORIES: ReadonlySet<string> = new Set(['console']);

/** 已配置 DSN（纯函数，便于在 node 环境断言门控）。 */
export function isMonitoringConfigured(dsn: string = SENTRY_DSN): boolean {
  return typeof dsn === 'string' && dsn.trim().length > 0;
}

/** 去掉 URL 的 query 与 fragment（路径本身不含用户内容，只有资源 id）。 */
export function stripUrlQuery(url: string): string {
  return url.split(/[?#]/, 1)[0];
}

/**
 * 单条 breadcrumb 脱敏；返回 null 表示丢弃。
 * 只处理有实际暴露面的类别，其余原样放过（否则崩溃报告将没有诊断价值）。
 */
export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb | null {
  const category = breadcrumb.category ?? '';
  if (DROPPED_BREADCRUMB_CATEGORIES.has(category)) return null;
  if (category !== 'http' && category !== 'fetch') return breadcrumb;

  const data: Record<string, unknown> = { ...breadcrumb.data };
  // 请求体 / 响应体 / 表单：接口请求里就是用户的反思与对话原文
  delete data.request_body;
  delete data.response_body;
  delete data.form_data;
  delete data.json_data;
  delete data.text;
  const url = data.url;
  if (typeof url === 'string') data.url = stripUrlQuery(url);
  return { ...breadcrumb, data };
}

/** 事件级脱敏（beforeSend 与 breadcrumb 双保险的其中一道）。 */
export function scrubEvent(event: Event): Event {
  const next: Event = { ...event };
  delete next.extra;
  delete next.user;
  if (next.breadcrumbs) {
    const kept = next.breadcrumbs.map(scrubBreadcrumb).filter((b): b is Breadcrumb => b !== null);
    if (kept.length > 0) next.breadcrumbs = kept;
    else delete next.breadcrumbs;
  }
  return next;
}

let started = false;

/**
 * 初始化崩溃监控。App 入口尽早调用（模块顶层、渲染之前）。
 * 未配置 DSN 时直接返回，不做任何事。
 */
export function startMonitoring(): void {
  if (started || !isMonitoringConfigured()) return;
  started = true;
  Sentry.init({
    dsn: SENTRY_DSN,
    maxBreadcrumbs: 20,
    // 原生 SDK 未接入时（Expo Go / 未 prebuild）不要弹黄色告警框刷屏
    enableNativeNagger: false,
    beforeSend: (event) => scrubEvent(event),
    beforeBreadcrumb: (breadcrumb) => scrubBreadcrumb(breadcrumb),
  });
}

/**
 * 上报一条异常。tags 只放枚举型元信息（环节名、平台），
 * 不要放任何来自用户数据的字符串——那类值应当走 analytics 的行为白名单。
 */
export function reportError(error: unknown, tags?: Record<string, string | number | boolean>): void {
  if (!started) return;
  Sentry.captureException(error, tags && Object.keys(tags).length > 0 ? { tags } : undefined);
}

/** 立即把待发事件推出去（ErrorBoundary 兜底页展示后用，避免进程随后退出导致报告丢失）。 */
export function flushMonitoring(): void {
  if (!started) return;
  Sentry.flush().catch(() => {});
}
