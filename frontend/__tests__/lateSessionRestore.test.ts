/**
 * P2 #26 · 冷启动超时后登录态迟到必须生效。
 *
 * 缺陷链路：`runColdStart` 4s 超时就放行 → AuthProvider 此时才挂载，其 useState
 * 初始值读的是 `getAuthUser()`（SecureStore 还没返回，等于 null）→ 之后
 * `loadSession()` 终于把 access token 写进 api 模块的变量，但没有任何人能收到通知
 * ⇒ 已登录用户整场会话都显示游客，直到进程重启。
 *
 * 判据分三层：
 * ① `loadSession` 真恢复出 token 时广播一次；没有 token 时不许广播（反向证人）；
 * ② 冷启动超时**之后**才落定的 loadSession 仍能触发广播（本缺陷的原形状）；
 * ③ AuthContext 订阅了这个广播并把 React 状态重读成内存里的用户。
 */
import fs from 'fs';
import path from 'path';

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void };

const mockSecureStore: {
  values: Record<string, string | null>;
  override: ((key: string) => Promise<string | null>) | null;
} = { values: {}, override: null };

jest.mock('react-native', () => ({
  Platform: { select: (opts: Record<string, string>) => opts.default },
}));
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async (key: string) =>
    mockSecureStore.override
      ? mockSecureStore.override(key)
      : (mockSecureStore.values[key] ?? null)),
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
jest.mock('expo-localization', () => ({
  getLocales: () => [{ languageCode: 'en', regionCode: 'US' }],
}));
jest.mock('../src/i18n', () => ({
  __esModule: true,
  default: { language: 'en', t: (key: string) => key },
}));
jest.mock('@sentry/react-native', () => ({
  init: jest.fn(),
  captureException: jest.fn(),
  flush: jest.fn(() => Promise.resolve(true)),
}));

const ACCESS = '@closenai/access_token';
const REFRESH = '@closenai/refresh_token';
const USER = '@closenai/auth_user';

function freshApi(): typeof import('../src/services/api') {
  jest.resetModules();
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  return require('../src/services/api') as typeof import('../src/services/api');
}

async function flush(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

beforeEach(() => {
  mockSecureStore.values = {};
  mockSecureStore.override = null;
});

describe('① loadSession 的广播口径', () => {
  it('恢复出 access token 时广播一次，isLoggedIn/getAuthUser 同步就位', async () => {
    mockSecureStore.values = {
      [ACCESS]: 'at-1',
      [REFRESH]: 'rt-1',
      [USER]: JSON.stringify({ id: 'u1', name: 'N' }),
    };
    const api = freshApi();
    const restored = jest.fn();
    api.onSessionRestored(restored);

    await api.loadSession();

    expect(restored).toHaveBeenCalledTimes(1);
    expect(api.isLoggedIn()).toBe(true);
    expect(api.getAuthUser()?.id).toBe('u1');
  });

  it('没有 token 时不广播（不是「loadSession 结束就通知」）', async () => {
    const api = freshApi();
    const restored = jest.fn();
    api.onSessionRestored(restored);

    await api.loadSession();

    expect(restored).not.toHaveBeenCalled();
    expect(api.isLoggedIn()).toBe(false);
  });

  it('取消订阅后不再收到', async () => {
    mockSecureStore.values = { [ACCESS]: 'at-2', [REFRESH]: 'rt-2', [USER]: null };
    const api = freshApi();
    const restored = jest.fn();
    api.onSessionRestored(restored)();
    await api.loadSession();
    expect(restored).not.toHaveBeenCalled();
  });
});

describe('② 冷启动超时后才落定的登录态仍然生效', () => {
  it('先放行 ready（token 仍未到），SecureStore 返回后补广播', async () => {
    jest.useFakeTimers();
    try {
      let resolveAccess!: (v: string | null) => void;
      const accessGate = new Promise<string | null>((res) => {
        resolveAccess = res;
      });
      mockSecureStore.override = (key) => {
        if (key === ACCESS) return accessGate;
        if (key === REFRESH) return Promise.resolve('rt-late');
        return Promise.resolve(JSON.stringify({ id: 'u-late' }));
      };

      const api = freshApi();
      const restored = jest.fn();
      api.onSessionRestored(restored);

      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { runColdStart, COLD_START_TIMEOUT_MS } = require('../src/services/coldStart');
      const ready = jest.fn();
      runColdStart({
        initI18n: () => Promise.resolve(),
        loadSession: api.loadSession,
        readOnboarded: () => Promise.resolve('1'),
        initDatabase: () => Promise.resolve(),
        onReady: ready,
        onOnboarded: jest.fn(),
      });

      jest.advanceTimersByTime(COLD_START_TIMEOUT_MS);
      expect(ready).toHaveBeenCalledTimes(1);
      expect(api.isLoggedIn()).toBe(false); // 门闸放行那一刻确实还是游客
      expect(restored).not.toHaveBeenCalled();

      resolveAccess('at-late');
      await flush();

      expect(api.isLoggedIn()).toBe(true);
      expect(restored).toHaveBeenCalledTimes(1);
      expect(api.getAuthUser()?.id).toBe('u-late');
    } finally {
      jest.useRealTimers();
    }
  });
});

describe('③ AuthContext 接上了这条广播', () => {
  const ROOT = path.join(__dirname, '..');
  const src = fs.readFileSync(path.join(ROOT, 'src/context/AuthContext.tsx'), 'utf8');

  it('挂载时先补读一次内存态（render 与 loadSession 之间的窗口）', () => {
    expect(src).toContain('setUser(getAuthUser());');
  });

  it('订阅 onSessionRestored 并重读 getAuthUser', () => {
    expect(src).toMatch(/onSessionRestored\(\(\)\s*=>\s*setUser\(getAuthUser\(\)\)\)/);
  });
});
