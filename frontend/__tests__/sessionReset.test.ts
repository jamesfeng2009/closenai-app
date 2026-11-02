/**
 * 会话重置的本地清理口径（J3-③：注销要连本机行为队列一起清）。
 *
 * 断言的是"两种重置的差别"：
 * - resetSession（登出/token 失效）保留 analytics_events —— 同设备换账号还要继续上送；
 * - resetSessionFull（注销）必须清空 —— 未上送事件的 distinct_id 属于那个已被
 *   物理删除的 user_id，留着既不等于"全部记录彻底删除"的承诺，也没有用途。
 */
import { createTestDb } from './helpers/testDb';

const testDb = createTestDb();
const calls: {
  authCleared: number;
  resetNotified: number;
  authFailures: string[];
  dbFailures: number;
  onboardingFailures: number;
} = {
  authCleared: 0,
  resetNotified: 0,
  authFailures: [],
  dbFailures: 0,
  onboardingFailures: 0,
};

jest.mock('../src/services/api', () => ({
  clearAuth: jest.fn(async () => {
    calls.authCleared += 1;
    return calls.authFailures;
  }),
  notifySessionReset: jest.fn(() => {
    calls.resetNotified += 1;
  }),
  // session.ts 会 re-export 这个订阅函数，mock 里必须存在该绑定
  onSessionReset: jest.fn(() => () => undefined),
}));

jest.mock('../src/services/db/database', () => ({
  getDatabase: () => {
    if (calls.dbFailures > 0) {
      calls.dbFailures -= 1;
      return {
        ...testDb,
        execSync: () => {
          throw new Error('sqlite unavailable');
        },
      };
    }
    return testDb;
  },
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  removeItem: jest.fn(async () => {
    if (calls.onboardingFailures > 0) {
      calls.onboardingFailures -= 1;
      throw new Error('async storage unavailable');
    }
  }),
  getItem: jest.fn(async () => null),
  setItem: jest.fn(async () => undefined),
}));

// 必须在 mock 之后再 require，否则 session.ts 会拿到真的 api / database
import { LocalCleanupError, resetSession, resetSessionFull } from '../src/services/session';

function seed(): void {
  testDb.execSync(`
    DELETE FROM analytics_events;
    DELETE FROM mood_checkins;
    DELETE FROM user_profile;
  `);
  testDb.runSync(
    "INSERT INTO analytics_events (event, props_json, occurred_at, synced) VALUES ('screen_view', '{\"screen\":\"Home\"}', '2026-09-01T00:00:00Z', 0)"
  );
  testDb.runSync(
    "INSERT INTO analytics_events (event, props_json, occurred_at, synced) VALUES ('checkin', NULL, '2026-09-02T00:00:00Z', 0)"
  );
}

function eventCount(): number {
  return (testDb.getFirstSync<{ count: number }>('SELECT COUNT(*) AS count FROM analytics_events') ?? { count: -1 }).count;
}

describe('resetSession / resetSessionFull 的本地清理', () => {
  beforeEach(() => {
    seed();
    calls.authCleared = 0;
    calls.resetNotified = 0;
    calls.authFailures = [];
    calls.dbFailures = 0;
    calls.onboardingFailures = 0;
  });

  it('登出：清用户级表但保留行为事件队列', async () => {
    await resetSession();
    expect(eventCount()).toBe(2);
    expect(calls.authCleared).toBe(1);
    expect(calls.resetNotified).toBe(1);
  });

  it('注销：行为事件队列一并清空', async () => {
    await resetSessionFull();
    expect(eventCount()).toBe(0);
  });

  it('注销：用户级表同样被清（防止只清队列的假象）', async () => {
    testDb.runSync(
      "INSERT INTO user_profile (id, email, display_name) VALUES ('u1', 'a@b.co', 'A')"
    );
    await resetSessionFull();
    expect(
      (testDb.getFirstSync<{ count: number }>('SELECT COUNT(*) AS count FROM user_profile') ?? { count: -1 }).count
    ).toBe(0);
  });

  it('注销：同步水位复位（新账号不能复用上一账号的 last_synced_at）', async () => {
    testDb.runSync(
      "UPDATE sync_metadata SET last_synced_at = '2026-09-01T00:00:00Z', record_count = 7 WHERE table_name = 'mood_checkins'"
    );
    await resetSessionFull();
    const row = testDb.getFirstSync<{ last_synced_at: string | null; record_count: number }>(
      "SELECT last_synced_at, record_count FROM sync_metadata WHERE table_name = 'mood_checkins'"
    );
    expect(row?.last_synced_at).toBeNull();
    expect(row?.record_count).toBe(0);
  });
});

describe('本机清理失败必须可感知、可重试（T7-01）', () => {
  beforeEach(() => {
    seed();
    calls.authCleared = 0;
    calls.resetNotified = 0;
    calls.authFailures = [];
    calls.dbFailures = 0;
    calls.onboardingFailures = 0;
  });

  it('SecureStore 没删干净：抛 LocalCleanupError(secure_store)，仍然通知回登录页', async () => {
    calls.authFailures = ['refresh_token'];

    await expect(resetSession()).rejects.toMatchObject({
      name: 'LocalCleanupError',
      failures: ['secure_store'],
    });
    // 内存态已清、不能把用户留在已登录界面
    expect(calls.resetNotified).toBe(1);
  });

  it('SQLite 清理失败：抛 sqlite 失败、数据保持原样；重试成功后清空', async () => {
    calls.dbFailures = 1;
    let caught: unknown;
    try {
      await resetSession();
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(LocalCleanupError);
    expect((caught as LocalCleanupError).failures).toEqual(['sqlite']);
    expect(eventCount()).toBe(2); // 失败没有被说成「已清空」

    await resetSession(); // dbFailures 已归零 → 重试
    expect(eventCount()).toBe(2); // 登出仍保留设备级队列
    expect(
      (testDb.getFirstSync<{ count: number }>('SELECT COUNT(*) AS count FROM user_profile') ?? { count: -1 }).count
    ).toBe(0);
  });

  it('注销：引导标记删失败也计入失败，重试成功后才算清完', async () => {
    calls.onboardingFailures = 1;

    await expect(resetSessionFull()).rejects.toMatchObject({
      name: 'LocalCleanupError',
      failures: ['onboarding'],
    });

    await resetSessionFull();
    expect(eventCount()).toBe(0);
  });

  it('多项同时失败时逐条报告落点', async () => {
    calls.authFailures = ['access_token', 'user'];
    calls.dbFailures = 1;
    calls.onboardingFailures = 1;

    await expect(resetSessionFull()).rejects.toMatchObject({
      failures: ['secure_store', 'sqlite', 'onboarding'],
    });
  });
});

afterAll(() => testDb.closeSync());
