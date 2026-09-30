/**
 * App Store 优惠信息解析（F2 · PRD §5.21 Pro 主订阅 7 天免费试用）。
 *
 * 独立纯函数模块（不 import react-native-iap）：jest 为 node 环境且只收 .test.ts，
 * 坐标/解析类计算全部拆到这里，原生模块类型在调用方（PaywallScreen）收窄。
 *
 * 字段口径（react-native-iap，SK1/SK2 映射结果一致）：
 * - introductoryPricePaymentModeIOS: 'FREETRIAL'（免费试用）| 'PAYASYOUGO' | 'PAYUPFRONT'
 * - introductoryPriceNumberOfPeriodsIOS: 周期数（string，如 "7"）
 * - introductoryPriceSubscriptionPeriodIOS: 'DAY' | 'WEEK' | 'MONTH' | 'YEAR'（大小写不敏感）
 * 无 intro offer（或拉价失败）返回 null，调用方回退普通订阅按钮。
 */

const UNIT_DAYS: Record<string, number> = { day: 1, week: 7, month: 30, year: 365 };

export interface IntroTrialOffer {
  /** 免费试用总天数（按周期折算：day×n / week×7n / month×30n / year×365n）。 */
  days: number;
}

export function parseIntroTrialOffer(sub: unknown): IntroTrialOffer | null {
  const s = sub as
    | {
        introductoryPricePaymentModeIOS?: string;
        introductoryPriceNumberOfPeriodsIOS?: string | number;
        introductoryPriceSubscriptionPeriodIOS?: string;
      }
    | null
    | undefined;
  if (!s) return null;
  if ((s.introductoryPricePaymentModeIOS || '').toUpperCase() !== 'FREETRIAL') return null;
  const count = parseInt(String(s.introductoryPriceNumberOfPeriodsIOS ?? ''), 10);
  if (!Number.isFinite(count) || count <= 0) return null;
  const factor = UNIT_DAYS[(s.introductoryPriceSubscriptionPeriodIOS || '').toLowerCase()];
  if (!factor) return null;
  return { days: count * factor };
}
