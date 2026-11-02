/**
 * T7-01 · clearAuth 的删除必须逐键独立。
 *
 * 旧实现把三个 SecureStore 删除包在同一个 try 里：refresh_token 删失败会让
 * auth_user 根本不尝试删除，而调用方看到的只是「什么都没发生」。会话重置因此
 * 无法区分「本机已清干净」和「还留着登录态」。
 */
jest.mock('react-native', () => ({
  Platform: { select: (opts: Record<string, string>) => opts.default },
}));
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(async () => null),
    setItem: jest.fn(async () => undefined),
    removeItem: jest.fn(async () => undefined),
  },
}));
jest.mock('expo-localization', () => ({ getLocales: () => [{ languageCode: 'en', regionCode: 'US' }] }));
jest.mock('../src/i18n', () => ({
  __esModule: true,
  default: { language: 'en', t: (key: string) => key },
}));
jest.mock('@sentry/react-native', () => ({
  init: jest.fn(),
  captureException: jest.fn(),
  flush: jest.fn(() => Promise.resolve(true)),
}));

import * as SecureStore from 'expo-secure-store';
import { clearAuth } from '../src/services/api';

const deleteItemAsync = SecureStore.deleteItemAsync as jest.Mock;

describe('clearAuth 逐键删除', () => {
  beforeEach(() => {
    deleteItemAsync.mockReset();
    deleteItemAsync.mockResolvedValue(undefined);
  });

  it('中间一个键删失败仍继续删其余键，并报出失败标签', async () => {
    deleteItemAsync.mockImplementation(async (key: string) => {
      if (key === '@closenai/refresh_token') throw new Error('keychain locked');
    });

    await expect(clearAuth()).resolves.toEqual(['refresh_token']);
    expect(deleteItemAsync.mock.calls.map((c) => c[0])).toEqual([
      '@closenai/access_token',
      '@closenai/refresh_token',
      '@closenai/auth_user',
    ]);
  });

  it('全部删除成功时返回空数组（调用方据此判定本机清理完成）', async () => {
    await expect(clearAuth()).resolves.toEqual([]);
    expect(deleteItemAsync).toHaveBeenCalledTimes(3);
  });
});
