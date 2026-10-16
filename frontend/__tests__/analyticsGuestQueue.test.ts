/**
 * 游客态埋点上送行为取证 · #65。
 *
 * tasks.md:140 登记的口径缺口是「四个 track() 事件只进本地队列，游客态能否 flush
 * 成功未验证」。实测结论（2026-10-14 逐行核过 App.tsx:169/:179 与 analytics.ts:91-114）：
 *
 * - 游客**不发**上送请求：两个 flush 点都在 `if (isLoggedIn())` 里，
 *   所以不会出现「游客必 401 → 队列被误判成已发送」；
 * - 事件也**不会丢**：track() 只看本地 consent 门闸，不看登录态，队列在 SQLite 里等；
 * - 真正的上送有三个点：登录成功那一刻（2026-10-16 补，此前只有下面两个 ⇒ 同日登录的
 *   会话要等下一次前后台切换）、冷启动首帧、回前台，都按 occurred_at 补口径。
 *
 * 这个文件把上面三条钉死，改坏任何一条都会红。
 */
import { createTestDb } from './helpers/testDb';
import { AnalyticsEventsRepository } from '../src/services/db/analyticsEventsRepository';
import { analyticsEventsRepo } from '../src/services/db/analyticsEventsRepository';
import { flushAnalytics } from '../src/services/analytics';
import { trackFunnelShare, trackFunnelOnce, FUNNEL_SOURCES } from '../src/services/funnel';

jest.mock('../src/services/api', () => ({
  postAnalyticsBatch: jest.fn(),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(async () => null),
    setItem: jest.fn(async () => undefined),
    clear: jest.fn(async () => undefined),
  },
}));
import { postAnalyticsBatch } from '../src/services/api';

const mockPost = postAnalyticsBatch as jest.Mock;
const read = (rel: string) =>
  require('fs').readFileSync(require('path').join(__dirname, '..', rel), 'utf8');

beforeEach(() => jest.clearAllMocks());

describe('游客期间：track 入队、绝不发请求', () => {
  it('analytics.ts 里没有任何登录态判断（入队与 token 无关，所以游客事件不会被跳过）', () => {
    const src = read('src/services/analytics.ts');
    expect(src).not.toMatch(/isLoggedIn|getAccessToken|SecureStore/);
  });

  it('App.tsx 的两个上送点都在 isLoggedIn() 门内（游客不会打出必 401 的请求）', () => {
    const src = read('App.tsx');
    const calls = [...src.matchAll(/.*flushAnalytics\(\).*/g)].map((m) => m[0].trim());
    expect(calls).toHaveLength(2);
    for (const line of calls) {
      expect(line).toMatch(/^if \(isLoggedIn\(\)\) flushAnalytics\(\)\.catch\(\(\) => \{\}\);$/);
    }
  });

  it('游客态四个漏斗点位都会落进本地队列（演示漏斗的分子在登录前就在累积）', async () => {
    const spy = jest.spyOn(analyticsEventsRepo, 'enqueue').mockImplementation(() => {});
    // trackFunnelOnce 是 async（一次性门闸要读 AsyncStorage，杀进程重开也门得住）
    await trackFunnelOnce('first_record_created');
    await trackFunnelOnce('first_checkin');
    trackFunnelShare('share_tapped', 'year_review');
    trackFunnelShare('share_completed', 'year_review');

    const events = spy.mock.calls.map((c) => c[0]);
    expect(events).toEqual([
      'funnel_first_record_created',
      'funnel_first_checkin',
      'funnel_share_tapped',
      'funnel_share_completed',
    ]);
    // 属性白名单口径：一次性节点只有 feature，分享环路才有 source
    expect(spy.mock.calls.map((c) => Object.keys(c[1] as object).sort())).toEqual([
      ['feature'],
      ['feature'],
      ['feature', 'source'],
      ['feature', 'source'],
    ]);
    spy.mockRestore();
  });
});

describe('登录之后：队列一次性 drain，失败也不丢', () => {
  it('游客期攒下的事件在登录后第一次 flush 全部上送并清零', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    const listSpy = jest.spyOn(analyticsEventsRepo, 'listPending')
      .mockImplementation(() => testRepo.listPending());
    const markSpy = jest.spyOn(analyticsEventsRepo, 'markSynced')
      .mockImplementation((ids) => testRepo.markSynced(ids));
    const cleanSpy = jest.spyOn(analyticsEventsRepo, 'cleanupSynced')
      .mockImplementation(() => testRepo.cleanupSynced());

    // 游客态：只入队，不 flush（App.tsx 的门决定了这一点，上面已拍死）
    testRepo.enqueue('funnel_first_read_opened', { feature: 'funnel' }, '2026-10-13T01:00:00Z');
    testRepo.enqueue('funnel_share_tapped', { feature: 'funnel', source: 'year_review' }, '2026-10-13T02:00:00Z');
    testRepo.enqueue('funnel_first_checkin', { feature: 'funnel' }, '2026-10-13T03:00:00Z');
    expect(mockPost).not.toHaveBeenCalled();
    expect(testRepo.countPending()).toBe(3);

    // 登录后的那一次 flush
    mockPost.mockResolvedValue({ relayed: 3, dropped: 0 });
    const res = await flushAnalytics();
    expect(res).toEqual({ relayed: 3, sent: 3 });
    expect(mockPost).toHaveBeenCalledTimes(1);
    expect(mockPost.mock.calls[0][0]).toHaveLength(3);
    expect(testRepo.countPending()).toBe(0);

    listSpy.mockRestore(); markSpy.mockRestore(); cleanSpy.mockRestore();
    db.closeSync();
  });

  it('后端整批丢弃（consent 关 / 未知用户，dropped=3）也算已送：本地队列照样清，不留重试风暴', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    testRepo.enqueue('funnel_first_checkin', { feature: 'funnel' }, '2026-10-13T01:00:00Z');
    const listSpy = jest.spyOn(analyticsEventsRepo, 'listPending')
      .mockImplementation(() => testRepo.listPending());
    const markSpy = jest.spyOn(analyticsEventsRepo, 'markSynced')
      .mockImplementation((ids) => testRepo.markSynced(ids));

    mockPost.mockResolvedValue({ relayed: 0, dropped: 1 });
    expect(await flushAnalytics()).toEqual({ relayed: 0, sent: 1 });
    expect(testRepo.countPending()).toBe(0);

    listSpy.mockRestore(); markSpy.mockRestore();
    db.closeSync();
  });

  it('401/网络异常时事件留在队列，下一次成功仍带全量（登录失败不产生口径损失）', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    testRepo.enqueue('funnel_share_tapped', { feature: 'funnel', source: 'year_review' }, '2026-10-13T01:00:00Z');
    testRepo.enqueue('funnel_share_completed', { feature: 'funnel', source: 'year_review' }, '2026-10-13T02:00:00Z');
    const listSpy = jest.spyOn(analyticsEventsRepo, 'listPending')
      .mockImplementation(() => testRepo.listPending());
    const markSpy = jest.spyOn(analyticsEventsRepo, 'markSynced')
      .mockImplementation((ids) => testRepo.markSynced(ids));

    mockPost.mockRejectedValue(new Error('401'));
    expect(await flushAnalytics()).toEqual({ relayed: 0, sent: 0 });
    expect(testRepo.countPending()).toBe(2);
    expect(markSpy).not.toHaveBeenCalled();

    mockPost.mockResolvedValue({ relayed: 2, dropped: 0 });
    expect(await flushAnalytics()).toEqual({ relayed: 2, sent: 2 });
    expect(mockPost.mock.calls[1][0]).toHaveLength(2);
    expect(testRepo.countPending()).toBe(0);

    listSpy.mockRestore(); markSpy.mockRestore();
    db.closeSync();
  });
});

describe('口径边界（登记为事实，不许悄悄改）', () => {
  it('登录成功那一刻 drain 一次：flush 挂在 login 里，另有冷启动首帧与回前台两处', () => {
    // 2026-10-14 这条还是负向闸（`not.toContain`），钉的是「登录不 drain」这个现状；
    // 同日登记的缺陷（tasks.md 那条「登录成功那一刻不 drain 游客期队列」）修掉后翻成正向。
    const src = read('src/context/AuthContext.tsx');
    const calls = [...src.matchAll(/^\s*(.*flushAnalytics\(\).*);$/gm)].map((m) => m[1].trim());
    expect(calls).toEqual(["flushAnalytics().catch(() => {})"]);
    // 不许变成阻塞位：既不 await，也不嵌进别的 await 链
    expect(src).not.toMatch(/await flushAnalytics/);
    const login = src.slice(src.indexOf('const login = useCallback'));
    expect(login.slice(0, login.indexOf('\n  }, [')).includes('flushAnalytics')).toBe(true);
  });

  it('year_review 已进 FUNNEL_SOURCES（新增物料必须先进枚举，否则后端按未知 source 吞掉）', () => {
    expect([...FUNNEL_SOURCES]).toContain('year_review');
  });
});
