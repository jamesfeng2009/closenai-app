import { createTestDb } from './helpers/testDb';
import { PersonalityProfileRepository } from '../src/services/db/personalityProfileRepository';
import { ComboReportRepository } from '../src/services/db/comboReportRepository';
import { DailyDiscoveryRepository } from '../src/services/db/dailyDiscoveryRepository';
import { SubscriptionStatusRepository } from '../src/services/db/subscriptionStatusRepository';

describe('Tier 2 / Tier 3 缓存仓储', () => {
  it('personality_profile：缓存并可读最新一份', async () => {
    const db = createTestDb();
    const repo = new PersonalityProfileRepository(db);
    await repo.cache({
      id: 'u1', mbti: 'INTP', zodiac_sign: 'Leo', enneagram: '5',
      summary: '理性分析型', refreshed_at: '2026-08-01T00:00:00Z',
      created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
    });
    const got = await repo.getLatest();
    expect(got?.mbti).toBe('INTP');
    db.closeSync();
  });

  it('combo_report：缓存仅保留最新 1 份', async () => {
    const db = createTestDb();
    const repo = new ComboReportRepository(db);
    await repo.cache({ id: 'a', period: 'monthly', report: '{"v":1}', generated_at: null, updated_at: '2026-07-01T00:00:00Z' });
    await repo.cache({ id: 'b', period: 'monthly', report: '{"v":2}', generated_at: null, updated_at: '2026-08-01T00:00:00Z' });
    const all = db.getAllSync('SELECT * FROM combo_report');
    expect(all).toHaveLength(1);
    expect(all[0].id).toBe('b');
    db.closeSync();
  });

  it('daily_discovery：按日期读写 + 最近 N 天', async () => {
    const db = createTestDb();
    const repo = new DailyDiscoveryRepository(db);
    await repo.cache({ id: 'd1', date: '2026-08-01', content: '{"c":1}', created_at: '2026-08-01T00:00:00Z' });
    await repo.cache({ id: 'd2', date: '2026-08-02', content: '{"c":2}', created_at: '2026-08-02T00:00:00Z' });
    expect((await repo.getByDate('2026-08-01'))?.id).toBe('d1');
    expect((await repo.getRecent(1)).map((r) => r.date)).toEqual(['2026-08-02']);
    db.closeSync();
  });

  it('subscription_status：展示副本读写（不参与 gate）', async () => {
    const db = createTestDb();
    const repo = new SubscriptionStatusRepository(db);
    expect(await repo.getCurrent()).toBeNull();
    await repo.cache({ id: 'current', plan: 'pro', expires_at: '2026-12-31T00:00:00Z', is_active: 1, cached_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z' });
    const got = await repo.getCurrent();
    expect(got?.plan).toBe('pro');
    expect(got?.is_active).toBe(1);
    db.closeSync();
  });
});
