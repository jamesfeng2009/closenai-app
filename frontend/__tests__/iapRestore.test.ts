/**
 * P2 #27：恢复购买只在服务端校验通过时 finishTransaction。
 *
 * finishTransaction 是对 StoreKit 的「这笔交易已处理完」回执；对 valid=false
 * 的收据也回执，StoreKit 就不会再投递它，用户永远没有重试机会。
 */
jest.mock('react-native-iap', () => ({
  initConnection: jest.fn(() => Promise.resolve(true)),
  getAvailablePurchases: jest.fn(() => Promise.resolve([])),
  getSubscriptions: jest.fn(() => Promise.resolve([])),
  getProducts: jest.fn(() => Promise.resolve([])),
  finishTransaction: jest.fn(() => Promise.resolve()),
  purchaseUpdatedListener: jest.fn(() => ({ remove: jest.fn() })),
  purchaseErrorListener: jest.fn(() => ({ remove: jest.fn() })),
  requestSubscription: jest.fn(() => Promise.resolve()),
  requestPurchase: jest.fn(() => Promise.resolve()),
}));

jest.mock('../src/services/api', () => ({
  verifyReceipt: jest.fn(),
}));

import * as RNIap from 'react-native-iap';
import { verifyReceipt } from '../src/services/api';
import { restorePurchases } from '../src/services/iap';

const finish = RNIap.finishTransaction as jest.Mock;
const available = RNIap.getAvailablePurchases as jest.Mock;
const verify = verifyReceipt as jest.Mock;

const purchase = (id: string) => ({
  productId: 'closenai_pro_monthly',
  transactionReceipt: `receipt-${id}`,
});

describe('restorePurchases 与 finishTransaction', () => {
  it('校验失败的收据不完成交易，校验通过的才完成', async () => {
    const bad = purchase('bad');
    const good = purchase('good');
    available.mockResolvedValueOnce([bad, good]);
    verify
      .mockResolvedValueOnce({ valid: false, tier: 'free' })
      .mockResolvedValueOnce({ valid: true, tier: 'pro', kind: 'entitlement' });

    const result = await restorePurchases();

    expect(result).toEqual({ restored: true, entitlementRestored: true });
    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith({ purchase: good, isConsumable: false });
  });

  it('全部校验失败时一笔都不完成，且不报告恢复成功', async () => {
    available.mockResolvedValueOnce([purchase('a'), purchase('b')]);
    verify
      .mockResolvedValueOnce({ valid: false, tier: 'free' })
      .mockResolvedValueOnce({ valid: false, tier: 'free' });

    const result = await restorePurchases();

    expect(result).toEqual({ restored: false, entitlementRestored: false });
    expect(finish).not.toHaveBeenCalled();
  });

  it('校验抛错的那笔同样不完成交易', async () => {
    const broken = purchase('broken');
    available.mockResolvedValueOnce([broken]);
    verify.mockRejectedValueOnce(new Error('network'));

    await expect(restorePurchases()).resolves.toEqual({
      restored: false,
      entitlementRestored: false,
    });
    expect(finish).not.toHaveBeenCalled();
  });
});
