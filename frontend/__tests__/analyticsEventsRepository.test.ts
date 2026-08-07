import { createTestDb } from './helpers/testDb';
import { AnalyticsEventsRepository } from '../src/services/db/analyticsEventsRepository';

describe('AnalyticsEventsRepository (分析事件队列)', () => {
  it('入队行为事件并可列出待上送', () => {
    const db = createTestDb();
    const repo = new AnalyticsEventsRepository(db);
    repo.enqueue('screen_view', { screen: 'Home', tier: 'free' }, '2026-08-01T00:00:00Z');
    repo.enqueue('checkin', { feature: 'mood' }, '2026-08-02T00:00:00Z');
    const pending = repo.listPending();
    expect(pending).toHaveLength(2);
    expect(pending[0]).toMatchObject({ event: 'screen_view', properties: { screen: 'Home', tier: 'free' } });
    // 按发生时间升序
    expect(pending[0].occurred_at < pending[1].occurred_at).toBe(true);
    db.closeSync();
  });

  it('markSynced + cleanupSynced 清理已上送事件', () => {
    const db = createTestDb();
    const repo = new AnalyticsEventsRepository(db);
    repo.enqueue('a', null, '2026-08-01T00:00:00Z');
    repo.enqueue('b', null, '2026-08-02T00:00:00Z');
    const pending = repo.listPending();
    repo.markSynced(pending.map((p) => p.id));
    expect(repo.countPending()).toBe(0);
    // cleanup 前仍物理存在
    expect(db.getAllSync('SELECT * FROM analytics_events')).toHaveLength(2);
    repo.cleanupSynced();
    expect(db.getAllSync('SELECT * FROM analytics_events')).toHaveLength(0);
    db.closeSync();
  });

  it('countPending 统计未上送数量', () => {
    const db = createTestDb();
    const repo = new AnalyticsEventsRepository(db);
    expect(repo.countPending()).toBe(0);
    repo.enqueue('a', null, '2026-08-01T00:00:00Z');
    repo.enqueue('b', null, '2026-08-02T00:00:00Z');
    expect(repo.countPending()).toBe(2);
    db.closeSync();
  });
});
