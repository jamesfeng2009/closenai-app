import { createTestDb } from './helpers/testDb';
import { bindAnalyticsRepo, restoreAnalyticsRepo } from './helpers/bindAnalyticsRepo';
import { AnalyticsEventsRepository } from '../src/services/db/analyticsEventsRepository';
import { analyticsEventsRepo } from '../src/services/db/analyticsEventsRepository';
import { track, flushAnalytics } from '../src/services/analytics';

// 不加载真实 api.ts（会拉入 react-native / i18n），直接以工厂桩替换模块
jest.mock('../src/services/api', () => ({
  postAnalyticsBatch: jest.fn(),
}));
import { postAnalyticsBatch } from '../src/services/api';

const mockPost = postAnalyticsBatch as jest.Mock;

describe('analytics 服务（track + flush）', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('track 只落行为元数据，剔除敏感内容字段', () => {
    const spy = jest.spyOn(analyticsEventsRepo, 'enqueue').mockImplementation(() => {});
    track('reflection_saved', {
      feature: 'reflection',
      tier: 'free',
      duration_ms: 1200,
      reflection_text: '今天我很焦虑……',
      mood_emoji: '😢',
      message: 'AI 原始回复',
    });
    expect(spy).toHaveBeenCalledWith(
      'reflection_saved',
      { feature: 'reflection', tier: 'free', duration_ms: 1200 },
      expect.any(String)
    );
    spy.mockRestore();
  });

  it('flush 成功：逐条 accepted 后才删除本地行', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    testRepo.enqueue('screen_view', { screen: 'Home' }, '2026-08-01T00:00:00Z');
    testRepo.enqueue('checkin', { feature: 'mood' }, '2026-08-02T00:00:00Z');
    const ids = testRepo.listPending().map((p) => p.event_id);

    const spies = bindAnalyticsRepo(testRepo);
    mockPost.mockResolvedValue({
      relayed: 2, dropped: 0, accepted: 2, duplicate: 0, retryable: 0, permanent: 0,
      results: ids.map((event_id) => ({ event_id, status: 'accepted' })),
    });

    const res = await flushAnalytics();
    expect(res).toEqual({
      relayed: 2, attempted: 2, accepted: 2, duplicate: 0,
      retryable: 0, permanent: 0, dropped: 0, queued: 0,
    });
    expect(mockPost).toHaveBeenCalledTimes(1);
    const payload = mockPost.mock.calls[0][0];
    expect(payload).toHaveLength(2);
    expect(payload.map((e: { event: string }) => e.event)).toEqual(['screen_view', 'checkin']);
    expect(payload.every((e: { event_id?: string }) => !!e.event_id)).toBe(true);
    expect(spies.cleanupSynced).toHaveBeenCalled();
    expect(testRepo.countPending()).toBe(0);

    restoreAnalyticsRepo(spies);
    db.closeSync();
  });

  it('flush 网络失败：保留事件并退避，不计成功', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    testRepo.enqueue('checkin', null, '2026-08-01T00:00:00Z');

    const spies = bindAnalyticsRepo(testRepo);
    mockPost.mockRejectedValue(new Error('network'));

    const res = await flushAnalytics();
    expect(res).toEqual({
      relayed: 0, attempted: 1, accepted: 0, duplicate: 0,
      retryable: 1, permanent: 0, dropped: 1, queued: 1,
    });
    expect(spies.markSynced).not.toHaveBeenCalled();
    expect(testRepo.countPending()).toBe(1);
    const row = db.getFirstSync<{ attempts: number; next_retry_at: string | null }>(
      'SELECT attempts, next_retry_at FROM analytics_events'
    );
    expect(row?.attempts).toBe(1);
    expect(row?.next_retry_at).toBeTruthy();

    restoreAnalyticsRepo(spies);
    db.closeSync();
  });

  it('无待发送事件时直接返回', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    const spies = bindAnalyticsRepo(testRepo);
    const res = await flushAnalytics();
    expect(res).toEqual({
      relayed: 0, attempted: 0, accepted: 0, duplicate: 0,
      retryable: 0, permanent: 0, dropped: 0, queued: 0,
    });
    expect(mockPost).not.toHaveBeenCalled();
    restoreAnalyticsRepo(spies);
    db.closeSync();
  });
});
