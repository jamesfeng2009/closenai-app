import { createTestDb } from './helpers/testDb';
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

  it('flush 成功：上送后标记 synced 并清理', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    testRepo.enqueue('screen_view', { screen: 'Home' }, '2026-08-01T00:00:00Z');
    testRepo.enqueue('checkin', { feature: 'mood' }, '2026-08-02T00:00:00Z');

    const listSpy = jest.spyOn(analyticsEventsRepo, 'listPending').mockImplementation(() => testRepo.listPending());
    const markSpy = jest.spyOn(analyticsEventsRepo, 'markSynced').mockImplementation((ids) => testRepo.markSynced(ids));
    const cleanSpy = jest.spyOn(analyticsEventsRepo, 'cleanupSynced').mockImplementation(() => testRepo.cleanupSynced());
    mockPost.mockResolvedValue({ relayed: 2, dropped: 0 });

    const res = await flushAnalytics();
    expect(res).toEqual({ relayed: 2, sent: 2 });
    expect(mockPost).toHaveBeenCalledTimes(1);
    expect(mockPost).toHaveBeenCalledWith([
      { event: 'screen_view', properties: { screen: 'Home' }, occurred_at: '2026-08-01T00:00:00Z' },
      { event: 'checkin', properties: { feature: 'mood' }, occurred_at: '2026-08-02T00:00:00Z' },
    ]);
    expect(markSpy).toHaveBeenCalled();
    expect(cleanSpy).toHaveBeenCalled();
    expect(testRepo.countPending()).toBe(0);

    listSpy.mockRestore();
    markSpy.mockRestore();
    cleanSpy.mockRestore();
    db.closeSync();
  });

  it('flush 网络失败：保留事件待重试，不标记 synced', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    testRepo.enqueue('checkin', null, '2026-08-01T00:00:00Z');

    const listSpy = jest.spyOn(analyticsEventsRepo, 'listPending').mockImplementation(() => testRepo.listPending());
    const markSpy = jest.spyOn(analyticsEventsRepo, 'markSynced').mockImplementation((ids) => testRepo.markSynced(ids));
    mockPost.mockRejectedValue(new Error('network'));

    const res = await flushAnalytics();
    expect(res).toEqual({ relayed: 0, sent: 0 });
    expect(markSpy).not.toHaveBeenCalled();
    expect(testRepo.countPending()).toBe(1);

    listSpy.mockRestore();
    markSpy.mockRestore();
    db.closeSync();
  });

  it('无待发送事件时直接返回', async () => {
    const db = createTestDb();
    const testRepo = new AnalyticsEventsRepository(db);
    const listSpy = jest.spyOn(analyticsEventsRepo, 'listPending').mockImplementation(() => testRepo.listPending());
    const res = await flushAnalytics();
    expect(res).toEqual({ relayed: 0, sent: 0 });
    expect(mockPost).not.toHaveBeenCalled();
    listSpy.mockRestore();
    db.closeSync();
  });
});
