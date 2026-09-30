/** F2 · PRD §5.21 intro offer（7 天免费试用）解析纯函数。 */
import { parseIntroTrialOffer } from '../src/services/introOffer';

describe('parseIntroTrialOffer', () => {
  it('FREETRIAL 7 天 → days=7（SK2 映射全大写单位）', () => {
    expect(
      parseIntroTrialOffer({
        introductoryPricePaymentModeIOS: 'FREETRIAL',
        introductoryPriceNumberOfPeriodsIOS: '7',
        introductoryPriceSubscriptionPeriodIOS: 'DAY',
      }),
    ).toEqual({ days: 7 });
  });

  it('SK2 小写单位同样可解析（week → 7n）', () => {
    expect(
      parseIntroTrialOffer({
        introductoryPricePaymentModeIOS: 'FREETRIAL',
        introductoryPriceNumberOfPeriodsIOS: '1',
        introductoryPriceSubscriptionPeriodIOS: 'week',
      }),
    ).toEqual({ days: 7 });
  });

  it('付费优惠（PAYASYOUGO）不是免费试用 → null', () => {
    expect(
      parseIntroTrialOffer({
        introductoryPricePaymentModeIOS: 'PAYASYOUGO',
        introductoryPriceNumberOfPeriodsIOS: '3',
        introductoryPriceSubscriptionPeriodIOS: 'MONTH',
      }),
    ).toBeNull();
  });

  it('无 intro offer 字段 / 周期数非法 / 未知单位 → null', () => {
    expect(parseIntroTrialOffer({})).toBeNull();
    expect(parseIntroTrialOffer(null)).toBeNull();
    expect(
      parseIntroTrialOffer({
        introductoryPricePaymentModeIOS: 'FREETRIAL',
        introductoryPriceNumberOfPeriodsIOS: 'abc',
        introductoryPriceSubscriptionPeriodIOS: 'DAY',
      }),
    ).toBeNull();
    expect(
      parseIntroTrialOffer({
        introductoryPricePaymentModeIOS: 'FREETRIAL',
        introductoryPriceNumberOfPeriodsIOS: '1',
        introductoryPriceSubscriptionPeriodIOS: 'FORTNIGHT',
      }),
    ).toBeNull();
  });
});
